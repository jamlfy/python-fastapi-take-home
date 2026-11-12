"""Read-side PostgreSQL queries for the coffee health dataset."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from psycopg import Connection, sql
from psycopg.rows import DictRow

from app.coffee.constants import COLUMNS
from app.coffee.schemas import (
    CoffeeFilters,
    CoffeeListQuery,
    CoffeePage,
    CoffeeRecordOut,
    CoffeeSummary,
    SummaryGroup,
    SummaryGroupBy,
)

CATEGORICAL_FILTERS: Final[tuple[tuple[str, str], ...]] = (
    ("country", "country"),
    ("gender", "gender"),
    ("sleep_quality", "sleep_quality"),
    ("stress_level", "stress_level"),
    ("health_issues", "health_issues"),
    ("occupation", "occupation"),
)
BOOLEAN_FILTERS: Final[tuple[tuple[str, str], ...]] = (
    ("smoking", "smoking"),
    ("alcohol_consumption", "alcohol_consumption"),
)
RANGE_FILTERS: Final[tuple[tuple[str, str], ...]] = (
    ("age", "age"),
    ("coffee_intake", "coffee_intake"),
    ("bmi", "bmi"),
    ("sleep_hours", "sleep_hours"),
)
SUMMARY_COLUMNS: Final[Mapping[SummaryGroupBy, str]] = {
    "country": "country",
    "gender": "gender",
    "sleep_quality": "sleep_quality",
    "stress_level": "stress_level",
    "coffee_intake": "coffee_intake",
    "occupation": "occupation",
}

RECORD_COLUMNS_SQL: Final = sql.SQL(", ").join(sql.Identifier(column) for column in COLUMNS)


def _where_clause(
    filters: CoffeeFilters,
    after_id: int | None = None,
) -> tuple[sql.Composable, list[object]]:
    """Build a parameterized WHERE clause from validated filters."""
    clauses: list[sql.Composable] = []
    params: list[object] = []

    for attribute, column in CATEGORICAL_FILTERS:
        values = getattr(filters, attribute)
        if values:
            clauses.append(sql.SQL("{} = any(%s)").format(sql.Identifier(column)))
            params.append(values)

    for attribute, column in BOOLEAN_FILTERS:
        value = getattr(filters, attribute)
        if value is not None:
            clauses.append(sql.SQL("{} = %s").format(sql.Identifier(column)))
            params.append(value)

    for attribute, column in RANGE_FILTERS:
        minimum = getattr(filters, f"min_{attribute}")
        maximum = getattr(filters, f"max_{attribute}")
        if minimum is not None:
            clauses.append(sql.SQL("{} >= %s").format(sql.Identifier(column)))
            params.append(minimum)
        if maximum is not None:
            clauses.append(sql.SQL("{} <= %s").format(sql.Identifier(column)))
            params.append(maximum)

    if after_id is not None:
        clauses.append(sql.SQL("id > %s"))
        params.append(after_id)

    if not clauses:
        return sql.SQL(""), params
    return sql.SQL(" where ") + sql.SQL(" and ").join(clauses), params


def get_record(conn: Connection[DictRow], record_id: int) -> CoffeeRecordOut | None:
    """Return one record by primary key, or ``None`` when it does not exist."""
    statement = sql.SQL("select {columns} from coffee_health where id = %s").format(
        columns=RECORD_COLUMNS_SQL
    )
    row = conn.execute(statement, (record_id,)).fetchone()
    if row is None:
        return None
    return CoffeeRecordOut.model_validate(row)


def list_records(conn: Connection[DictRow], query: CoffeeListQuery) -> CoffeePage:
    """Return one keyset-paginated page of records ordered by id."""
    where, params = _where_clause(query, query.after_id)
    statement = sql.SQL("select {columns} from coffee_health {where} order by id limit %s").format(
        columns=RECORD_COLUMNS_SQL, where=where
    )

    # Fetch one extra row to know whether another page exists without COUNT(*).
    rows = conn.execute(statement, [*params, query.limit + 1]).fetchall()
    has_more = len(rows) > query.limit
    page_rows = rows[: query.limit]

    items = [CoffeeRecordOut.model_validate(row) for row in page_rows]
    next_cursor = int(page_rows[-1]["id"]) if has_more and page_rows else None
    return CoffeePage(
        items=items,
        limit=query.limit,
        has_more=has_more,
        next_cursor=next_cursor,
    )


def summarize(
    conn: Connection[DictRow],
    filters: CoffeeFilters,
    group_by: SummaryGroupBy,
) -> CoffeeSummary:
    """Aggregate the filtered records by one allowlisted column."""
    column = SUMMARY_COLUMNS.get(group_by)
    if column is None:
        raise ValueError(f"unsupported group_by: {group_by!r}")

    group_identifier = sql.Identifier(column)
    where, params = _where_clause(filters)
    statement = sql.SQL(
        """
        select
          {column}::text as key,
          count(*) as count,
          avg(sleep_hours) as avg_sleep_hours,
          avg(coffee_intake) as avg_coffee_intake,
          avg(caffeine_mg) as avg_caffeine_mg
        from coffee_health
        {where}
        group by {column}
        order by {column}
        """
    ).format(column=group_identifier, where=where)

    rows = conn.execute(statement, params).fetchall()
    groups = [
        SummaryGroup(
            key=str(row["key"]),
            count=int(row["count"]),
            avg_sleep_hours=float(row["avg_sleep_hours"]),
            avg_coffee_intake=float(row["avg_coffee_intake"]),
            avg_caffeine_mg=float(row["avg_caffeine_mg"]),
        )
        for row in rows
    ]
    return CoffeeSummary(group_by=group_by, groups=groups)
