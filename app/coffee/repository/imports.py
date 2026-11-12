"""Transactional storage for the one-pass coffee import."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg
from psycopg import Connection, sql
from psycopg.rows import DictRow, dict_row
from psycopg.types.json import Jsonb

from app.coffee.constants import COLUMNS
from app.config import settings

if TYPE_CHECKING:
    from app.coffee.stream.workers import ValidatedRecord


class ImportCheckpoint:
    """The manager's durable checkpoint and reject log."""

    def __init__(self, source: str, signature: str) -> None:
        self.source = source
        self.conn: Connection[DictRow] = psycopg.connect(
            settings.database_url, row_factory=dict_row, autocommit=True
        )
        self.conn.execute("select pg_advisory_lock(hashtext(%s))", (source,))
        self.conn.execute("""create table if not exists coffee_load_checkpoint (
            source text primary key, signature text not null, seq bigint not null,
            line integer not null, completed boolean not null default false,
            counts jsonb not null default '{}'::jsonb,
            reasons jsonb not null default '{}'::jsonb)""")
        self.conn.execute(
            "alter table coffee_load_checkpoint add column if not exists seq "
            "bigint not null default 0"
        )
        self.conn.execute("""create table if not exists coffee_load_progress (
            source text not null, seq bigint not null, line integer not null,
            kind text not null, corrected boolean not null default false,
            reason text, primary key (source,seq))""")
        self.conn.execute("""create table if not exists coffee_load_reject (
            source text not null, line integer not null, reason text not null,
            raw text not null, primary key (source,line))""")
        self.conn.execute("""create table if not exists coffee_load_seen (
            source text not null, id integer not null, first_row jsonb not null,
            primary key (source,id))""")
        state = self.conn.execute(
            "select * from coffee_load_checkpoint where source=%s", (source,)
        ).fetchone()
        if state and not state["completed"] and state["signature"] != signature:
            from app.coffee.loader import LoaderError

            raise LoaderError("CSV changed since the checkpoint")
        if not state or state["signature"] != signature:
            with self.conn.transaction():
                for table in ("coffee_load_progress", "coffee_load_reject", "coffee_load_seen"):
                    self.conn.execute(
                        sql.SQL("delete from {} where source=%s").format(sql.Identifier(table)),
                        (source,),
                    )
                self.conn.execute(
                    "insert into coffee_load_checkpoint "
                    "(source,signature,seq,line,completed,counts,reasons) "
                    "values (%s,%s,0,0,false,'{}','{}') on conflict(source) do update set "
                    "signature=excluded.signature,seq=0,line=0,completed=false,"
                    "counts='{}',reasons='{}'",
                    (source, signature),
                )
            self.seq = 0
            self.line = 0
            self.counts: Counter[str] = Counter()
            self.reasons: Counter[str] = Counter()
            self.completed = False
        else:
            self.seq = state["seq"]
            self.line = state["line"]
            self.counts = Counter(state["counts"])
            self.reasons = Counter(state["reasons"])
            self.completed = state["completed"]

    def is_record_confirmed(self, seq: int) -> bool:
        if seq <= self.seq:
            return True
        return (
            self.conn.execute(
                "select 1 from coffee_load_progress where source=%s and seq=%s",
                (self.source, seq),
            ).fetchone()
            is not None
        )

    def advance_through_confirmed_records(self) -> None:
        """Move only through a consecutive sequence of committed records."""
        with self.conn.transaction():
            while row := self.conn.execute(
                "select seq,line,kind,corrected,reason from coffee_load_progress "
                "where source=%s and seq=%s",
                (self.source, self.seq + 1),
            ).fetchone():
                self.seq = row["seq"]
                self.line = row["line"]
                kind = row["kind"]
                self.counts[kind] += 1
                if kind != "blank_lines":
                    self.counts["rows_read"] += 1
                if row["corrected"]:
                    self.counts["corrected"] += 1
                if row["reason"]:
                    self.reasons[row["reason"].split(":", 1)[0]] += 1
                self.conn.execute(
                    "delete from coffee_load_progress where source=%s and seq=%s",
                    (self.source, self.seq),
                )
            self.conn.execute(
                "update coffee_load_checkpoint set seq=%s,line=%s,counts=%s,reasons=%s "
                "where source=%s",
                (
                    self.seq,
                    self.line,
                    json.dumps(self.counts),
                    json.dumps(self.reasons),
                    self.source,
                ),
            )

    def mark_complete(self, expected_seq: int) -> None:
        self.advance_through_confirmed_records()
        if self.seq != expected_seq:
            from app.coffee.loader import LoaderError

            raise LoaderError(
                f"cannot complete import: confirmed {self.seq} of {expected_seq} records"
            )
        self.conn.execute(
            "update coffee_load_checkpoint set completed=true where source=%s",
            (self.source,),
        )

    def write_errors_csv(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.conn.transaction(), path.open("w", encoding="utf-8", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(("line", "reason", "raw"))
            with self.conn.cursor(name="coffee_reject_export") as cursor:
                cursor.execute(
                    "select line,reason,raw from coffee_load_reject where source=%s order by line",
                    (self.source,),
                )
                for row in cursor:
                    writer.writerow((row["line"], row["reason"], row["raw"]))

    def close(self) -> None:
        self.conn.close()


class CoffeeBlockWriter:
    """Commit ordered CSV blocks using COPY and set-based classification."""

    def __init__(self, checkpoint: ImportCheckpoint) -> None:
        self.checkpoint = checkpoint
        self.conn = checkpoint.conn
        self.source = checkpoint.source

    def save_block(self, rows: list[tuple[int, ValidatedRecord]]) -> None:
        """Isolate rare database data errors without losing the whole input block."""
        if not rows:
            return
        try:
            self._save_block(rows)
        except (psycopg.DataError, psycopg.IntegrityError) as exc:
            if len(rows) > 1:
                middle = len(rows) // 2
                self.save_block(rows[:middle])
                self.save_block(rows[middle:])
                return
            seq, row = rows[0]
            rejected: ValidatedRecord = (row[0], row[1], row[2], None, str(exc).strip())
            self._save_block([(seq, rejected)])

    def _save_block(self, rows: list[tuple[int, ValidatedRecord]]) -> None:
        if not rows:
            return
        from app.coffee.loader import to_db_row

        start, end = rows[0][0], rows[-1][0]
        with self.conn.transaction():
            self.conn.execute(
                "create temp table _coffee_stage on commit drop as "
                "select null::bigint seq, null::integer line, null::text raw, "
                "null::text kind, null::text reason, null::boolean corrected, "
                "null::jsonb first_row, h.* from coffee_health h with no data"
            )
            self.conn.execute(
                "create temp table _coffee_result "
                "(seq bigint primary key, line integer, kind text, corrected boolean, reason text) "
                "on commit drop"
            )
            # A previous version may have committed worker batches beyond its checkpoint.
            self.conn.execute(
                "insert into _coffee_result "
                "select seq,line,kind,corrected,reason from coffee_load_progress "
                "where source=%s and seq between %s and %s",
                (self.source, start, end),
            )
            columns = sql.SQL(", ").join(map(sql.Identifier, COLUMNS))
            copy_sql = sql.SQL(
                "copy _coffee_stage (seq,line,raw,kind,reason,corrected,first_row,{}) from stdin"
            ).format(columns)
            with self.conn.cursor().copy(copy_sql) as copy:
                for seq, row in rows:
                    line, fields, raw, normalized, error = row
                    values: tuple[object, ...]
                    if not fields:
                        kind, values = "blank_lines", (None,) * len(COLUMNS)
                    elif error or normalized is None:
                        kind, values = "rejected", (None,) * len(COLUMNS)
                    else:
                        kind, values = "valid", to_db_row(normalized[0])
                    first_row = Jsonb([str(value) for value in values]) if kind == "valid" else None
                    copy.write_row(
                        (
                            seq,
                            line,
                            raw,
                            kind,
                            error,
                            bool(normalized and normalized[1]),
                            first_row,
                            *values,
                        )
                    )
            self.conn.execute(
                "insert into _coffee_result "
                "select s.seq,s.line,s.kind,s.corrected,s.reason from _coffee_stage s "
                "where s.kind <> 'valid' and not exists "
                "(select 1 from _coffee_result r where r.seq=s.seq)"
            )
            self.conn.execute(
                "insert into coffee_load_reject(source,line,reason,raw) "
                "select %s,line,reason,raw from _coffee_stage where kind='rejected' "
                "on conflict(source,line) do update set reason=excluded.reason,raw=excluded.raw",
                (self.source,),
            )
            self.conn.execute(
                "create temp table _coffee_ranked on commit drop as "
                "select s.*, p.first_row prior_row, "
                "row_number() over(partition by s.id order by s.seq) row_rank, "
                "first_value(s.first_row) over(partition by s.id order by s.seq) batch_first "
                "from _coffee_stage s left join coffee_load_seen p "
                "on p.source=%s and p.id=s.id "
                "where s.kind='valid' and not exists "
                "(select 1 from _coffee_result r where r.seq=s.seq)",
                (self.source,),
            )
            self.conn.execute(
                "insert into _coffee_result(seq,line,kind,corrected,reason) "
                "select seq,line,case when first_row=coalesce(prior_row,batch_first) "
                "then 'exact_duplicates' else 'conflicts' end,false,null "
                "from _coffee_ranked where prior_row is not null or row_rank>1"
            )
            self.conn.execute(
                "insert into coffee_load_seen(source,id,first_row) "
                "select %s,id,first_row from _coffee_ranked "
                "where prior_row is null and row_rank=1 on conflict do nothing",
                (self.source,),
            )
            insert_columns = sql.SQL(", ").join(map(sql.Identifier, COLUMNS))
            statement = sql.SQL(
                "with inserted as (insert into coffee_health ({0}) "
                "select {0} from _coffee_ranked where prior_row is null and row_rank=1 "
                "on conflict(id) do nothing returning id) "
                "insert into _coffee_result(seq,line,kind,corrected,reason) "
                "select r.seq,r.line,case when i.id is null then 'already_loaded' "
                "else 'loaded' end,r.corrected and i.id is not null,null "
                "from _coffee_ranked r left join inserted i on i.id=r.id "
                "where r.prior_row is null and r.row_rank=1"
            ).format(insert_columns)
            self.conn.execute(statement)
            result = self.conn.execute(
                "select seq,line,kind,corrected,reason from _coffee_result order by seq"
            ).fetchall()
            if len(result) != len(rows):
                raise RuntimeError(f"incomplete block: {len(result)} of {len(rows)} records")
            counts = self.checkpoint.counts.copy()
            reasons = self.checkpoint.reasons.copy()
            for item in result:
                kind = item["kind"]
                counts[kind] += 1
                if kind != "blank_lines":
                    counts["rows_read"] += 1
                if item["corrected"]:
                    counts["corrected"] += 1
                if item["reason"]:
                    reasons[item["reason"].split(":", 1)[0]] += 1
            self.conn.execute(
                "delete from coffee_load_progress where source=%s and seq between %s and %s",
                (self.source, start, end),
            )
            self.conn.execute(
                "update coffee_load_checkpoint set seq=%s,line=%s,counts=%s,reasons=%s "
                "where source=%s",
                (end, result[-1]["line"], json.dumps(counts), json.dumps(reasons), self.source),
            )
        self.checkpoint.seq = end
        self.checkpoint.line = result[-1]["line"]
        self.checkpoint.counts = counts
        self.checkpoint.reasons = reasons

    def close(self) -> None:
        pass


class CoffeeBatchWriter:
    """One worker's connection; a whole batch commits or rolls back."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.conn: Connection[DictRow] = psycopg.connect(
            settings.database_url, row_factory=dict_row, autocommit=True
        )
        columns = sql.SQL(", ").join(map(sql.Identifier, COLUMNS))
        self.single_insert = sql.SQL(
            "insert into coffee_health ({}) values ({}) on conflict (id) do nothing returning id"
        ).format(columns, sql.SQL(", ").join(sql.Placeholder() for _ in COLUMNS))
        self.columns = columns

    def _progress(
        self,
        seq: int,
        row: ValidatedRecord,
        kind: str,
        corrected: bool = False,
        reason: str | None = None,
    ) -> None:
        self.conn.execute(
            "insert into coffee_load_progress(source,seq,line,kind,corrected,reason) "
            "values (%s,%s,%s,%s,%s,%s) on conflict do nothing",
            (self.source, seq, row[0], kind, corrected, reason),
        )
        if kind == "rejected":
            self.conn.execute(
                "insert into coffee_load_reject(source,line,reason,raw) "
                "values (%s,%s,%s,%s) on conflict(source,line) do update set "
                "reason=excluded.reason,raw=excluded.raw",
                (self.source, row[0], reason, row[2]),
            )

    def save_batch(self, rows: list[tuple[int, ValidatedRecord]]) -> None:
        """Try a bulk insert, then retry every row after a data error."""
        try:
            with self.conn.transaction():
                self._save_rows(rows, bulk=True)
        except (psycopg.DataError, psycopg.IntegrityError):
            with self.conn.transaction():
                self._save_rows(rows, bulk=False)

    def _save_rows(self, rows: list[tuple[int, ValidatedRecord]], *, bulk: bool) -> None:
        from app.coffee.loader import to_db_row

        candidates: list[tuple[int, ValidatedRecord, tuple[object, ...], bool]] = []
        for seq, row in rows:
            if not row[1]:
                self._progress(seq, row, "blank_lines")
                continue
            normalized, error = row[3], row[4]
            if error or normalized is None:
                self._progress(seq, row, "rejected", reason=error or "invalid record")
                continue
            record, fixes = normalized
            values = to_db_row(record)
            first_row = [str(value) for value in values]
            canonical = json.dumps(first_row)
            prior = self.conn.execute(
                "insert into coffee_load_seen(source,id,first_row) "
                "values (%s,%s,%s) on conflict do nothing returning id",
                (self.source, record.id, canonical),
            ).fetchone()
            if prior is None:
                previous = self.conn.execute(
                    "select first_row from coffee_load_seen where source=%s and id=%s",
                    (self.source, record.id),
                ).fetchone()
                kind = (
                    "exact_duplicates"
                    if previous and previous["first_row"] == first_row
                    else "conflicts"
                )
                self._progress(seq, row, kind)
                continue
            if bulk:
                candidates.append((seq, row, values, fixes))
                continue
            try:
                with self.conn.transaction():
                    inserted = self.conn.execute(self.single_insert, values).fetchone()
            except (psycopg.DataError, psycopg.IntegrityError) as exc:
                self.conn.execute(
                    "delete from coffee_load_seen where source=%s and id=%s",
                    (self.source, record.id),
                )
                self._progress(seq, row, "rejected", reason=str(exc).strip())
                continue
            self._progress(
                seq,
                row,
                "loaded" if inserted else "already_loaded",
                corrected=bool(fixes and inserted),
            )
        if not candidates:
            return
        statement = sql.SQL(
            "insert into coffee_health ({}) values {} on conflict (id) do nothing returning id"
        ).format(
            self.columns,
            sql.SQL(", ").join(
                sql.SQL("({})").format(sql.SQL(", ").join(sql.Placeholder() for _ in COLUMNS))
                for _ in candidates
            ),
        )
        bulk_inserted = self.conn.execute(
            statement, [value for _, _, values, _ in candidates for value in values]
        ).fetchall()
        ids = {result["id"] for result in bulk_inserted}
        for seq, row, values, fixes in candidates:
            loaded = values[0] in ids
            self._progress(
                seq,
                row,
                "loaded" if loaded else "already_loaded",
                corrected=bool(fixes and loaded),
            )

    def close(self) -> None:
        self.conn.close()
