"""Write-side PostgreSQL access for the coffee health CSV import."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Final

from psycopg import Connection, Error
from psycopg.rows import DictRow

from app.coffee.constants import COLUMNS

STAGING_TABLE: Final = "coffee_health_staging"
COPY_SQL: Final = (
    f"copy {STAGING_TABLE} (source_line, needs_fix, " + ", ".join(COLUMNS) + ") from stdin"
)
DROP_STAGING_SQL: Final = f"drop table if exists {STAGING_TABLE}"
CREATE_STAGING_SQL: Final = f"""
create unlogged table {STAGING_TABLE} (
  source_line integer not null,
  needs_fix boolean not null,
  id integer not null,
  age smallint not null,
  gender text not null,
  country text not null,
  coffee_intake numeric(4, 1) not null,
  caffeine_mg numeric(6, 1) not null,
  sleep_hours numeric(3, 1) not null,
  sleep_quality text not null,
  bmi numeric(4, 1) not null,
  heart_rate smallint not null,
  stress_level text not null,
  physical_activity_hours numeric(4, 1) not null,
  health_issues text not null,
  occupation text not null,
  smoking boolean not null,
  alcohol_consumption boolean not null
)
"""
ADVISORY_LOCK_SQL: Final = "select pg_advisory_lock(hashtext('coffee_health.load'))"
ADVISORY_UNLOCK_SQL: Final = "select pg_advisory_unlock(hashtext('coffee_health.load'))"
INSERT_SQL: Final = f"""
with chosen as (
  select distinct on (id) {", ".join(COLUMNS)}
  from {STAGING_TABLE}
  order by id, source_line
),
inserted as (
  insert into coffee_health ({", ".join(COLUMNS)})
  select {", ".join(COLUMNS)}
  from chosen
  on conflict (id) do nothing
  returning 1
)
select count(*) as loaded
from inserted
"""


@dataclass(frozen=True, slots=True)
class ImportReport:
    """Database-side counts for one finished import."""

    staged_rows: int
    distinct_ids: int
    exact_duplicates: int
    conflicts: int
    already_loaded: int
    corrected: int
    loaded: int


@dataclass(frozen=True, slots=True)
class _ImportCounts:
    staged_rows: int
    distinct_ids: int
    exact_duplicates: int
    conflicts: int
    already_loaded: int
    corrected: int


@contextmanager
def import_session(conn: Connection[DictRow]) -> Iterator[None]:
    """Own the import transaction: lock, staging setup, cleanup and unlock."""
    conn.execute(ADVISORY_LOCK_SQL)
    conn.execute(DROP_STAGING_SQL)
    conn.execute(CREATE_STAGING_SQL)
    conn.commit()

    try:
        yield
    finally:
        try:
            conn.rollback()
            conn.execute(DROP_STAGING_SQL)
            conn.commit()
        except Error:
            conn.rollback()

        try:
            conn.execute(ADVISORY_UNLOCK_SQL)
            conn.commit()
        except Error:
            conn.rollback()


def copy_records(conn: Connection[DictRow], rows: Iterable[tuple[object, ...]]) -> None:
    """Stream already-normalized rows into the shared staging table."""
    with conn.cursor() as cursor, cursor.copy(COPY_SQL) as copy:
        for row in rows:
            copy.write_row(row)


def _import_counts(conn: Connection[DictRow]) -> _ImportCounts:
    """Return every database-side count for the current staging contents."""
    row = conn.execute(
        f"""
        with duplicate_ids as (
          select
            id,
            count(*) as row_count,
            count(distinct (
              age, gender, country, coffee_intake, caffeine_mg, sleep_hours,
              sleep_quality, bmi, heart_rate, stress_level,
              physical_activity_hours, health_issues, occupation, smoking,
              alcohol_consumption
            )) as distinct_rows
          from {STAGING_TABLE}
          group by id
          having count(*) > 1
        ),
        chosen as (
          select distinct on (id) id, needs_fix
          from {STAGING_TABLE}
          order by id, source_line
        )
        select
          (select count(*) from {STAGING_TABLE}) as staged_rows,
          (select count(distinct id) from {STAGING_TABLE}) as distinct_ids,
          coalesce(
            (select sum(row_count - distinct_rows) from duplicate_ids),
            0
          ) as exact_duplicates,
          coalesce(
            (select sum(distinct_rows - 1) from duplicate_ids),
            0
          ) as conflicts,
          (
            select count(*)
            from (select distinct id from {STAGING_TABLE}) staged_ids
            where exists (
              select 1 from coffee_health target where target.id = staged_ids.id
            )
          ) as already_loaded,
          (
            select count(*)
            from chosen
            where chosen.needs_fix
              and not exists (
                select 1 from coffee_health target where target.id = chosen.id
              )
          ) as corrected
        """
    ).fetchone()
    if row is None:
        raise RuntimeError("database returned no import counts")

    return _ImportCounts(
        staged_rows=int(row["staged_rows"]),
        distinct_ids=int(row["distinct_ids"]),
        exact_duplicates=int(row["exact_duplicates"]),
        conflicts=int(row["conflicts"]),
        already_loaded=int(row["already_loaded"]),
        corrected=int(row["corrected"]),
    )


def create_records(conn: Connection[DictRow]) -> ImportReport:
    """Merge staging and insert the first occurrence of every ID.

    This is the record-creator used by the CSV import: it classifies duplicates
    and conflicts, inserts new IDs with ``ON CONFLICT DO NOTHING`` and drops the
    staging table. The whole step is atomic.
    """
    counts = _import_counts(conn)
    row = conn.execute(INSERT_SQL).fetchone()
    if row is None:
        raise RuntimeError("database returned no result for the insert")

    conn.execute(DROP_STAGING_SQL)
    conn.commit()

    return ImportReport(
        staged_rows=counts.staged_rows,
        distinct_ids=counts.distinct_ids,
        exact_duplicates=counts.exact_duplicates,
        conflicts=counts.conflicts,
        already_loaded=counts.already_loaded,
        corrected=counts.corrected,
        loaded=int(row["loaded"]),
    )
