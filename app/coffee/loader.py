"""Streaming CSV loader for the coffee health dataset.

The loader can run in one process or split the file into contiguous physical
line ranges processed by several worker processes. Both modes share the same
validation, staging table and deterministic merge, so the resulting counts are
identical.

Parallel design:

1. The parent counts the lines and plans one range per worker.
2. It creates a shared ``UNLOGGED`` staging table and takes an advisory lock.
3. Each worker seeks to its range, streams and validates it, and ``COPY``s the
   valid rows into the shared table with their global physical line number.
4. The parent merges once with ``DISTINCT ON (id) ... ORDER BY id, source_line``,
   so the first occurrence in the file always wins regardless of scheduling.
"""

from __future__ import annotations

import csv
import json
import multiprocessing
import sys
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from io import TextIOWrapper
from pathlib import Path
from typing import TextIO

import psycopg
from psycopg import Connection
from psycopg.rows import DictRow, dict_row

from app.coffee import repository
from app.coffee.chunks import ChunkError, FileScan, LineChunk, merge_reject_logs, scan_file
from app.coffee.constants import EXPECTED_HEADER
from app.coffee.normalization import RowError, normalize_row
from app.config import settings
from app.models import CoffeeRecord


class LoaderError(RuntimeError):
    """Raised when the file or database cannot be processed safely."""


@dataclass(frozen=True, slots=True)
class LoadSummary:
    rows_read: int
    blank_lines: int
    loaded: int
    corrected: int
    rejected: int
    exact_duplicates: int
    conflicts: int
    already_loaded: int
    reject_reasons: Mapping[str, int]

    @property
    def accounted_for(self) -> int:
        return (
            self.loaded
            + self.rejected
            + self.exact_duplicates
            + self.conflicts
            + self.already_loaded
        )


@dataclass(frozen=True, slots=True)
class WorkerStats:
    rows_read: int
    blank_lines: int
    rejected: int
    reject_reasons: Mapping[str, int]


def to_db_row(record: CoffeeRecord) -> tuple[object, ...]:
    """Return record values in the order used by COLUMNS and the staging table."""
    return (
        record.id,
        record.age,
        record.gender,
        record.country,
        record.coffee_intake,
        record.caffeine_mg,
        record.sleep_hours,
        record.sleep_quality,
        record.bmi,
        record.heart_rate,
        record.stress_level,
        record.physical_activity_hours,
        record.health_issues,
        record.occupation,
        record.smoking,
        record.alcohol_consumption,
    )


def _validate_header(csv_path: Path) -> None:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = tuple(next(reader))
        except StopIteration as exc:
            raise LoaderError(f"{csv_path} is empty") from exc

    if header != EXPECTED_HEADER:
        raise LoaderError(f"unexpected CSV header: expected {EXPECTED_HEADER!r}, got {header!r}")


def iter_rows(
    csv_path: Path,
    chunk: LineChunk | None = None,
) -> Iterator[tuple[int, tuple[str, ...]]]:
    """Yield (physical_line_number, fields), optionally starting at one chunk."""
    if chunk is None:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            try:
                header = tuple(next(reader))
            except StopIteration as exc:
                raise LoaderError(f"{csv_path} is empty") from exc

            if header != EXPECTED_HEADER:
                raise LoaderError(
                    f"unexpected CSV header: expected {EXPECTED_HEADER!r}, got {header!r}"
                )

            for fields in reader:
                yield reader.line_num, tuple(fields)
        return

    with csv_path.open("rb") as binary:
        binary.seek(chunk.start_offset)
        with TextIOWrapper(binary, encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            for fields in reader:
                line_number = chunk.start_line + reader.line_num - 1
                if line_number >= chunk.end_line:
                    break
                yield line_number, tuple(fields)


@dataclass(slots=True)
class RowCollector:
    """Validate a row stream while yielding only rows ready for COPY."""

    rows_read: int = 0
    blank_lines: int = 0
    rejected: int = 0
    reject_reasons: Counter[str] = field(default_factory=Counter)

    def rows(
        self,
        rows: Iterable[tuple[int, tuple[str, ...]]],
        reject_handle: TextIO,
        *,
        echo_rejects: bool,
    ) -> Iterator[tuple[object, ...]]:
        for line_number, fields in rows:
            if not fields:
                self.blank_lines += 1
                continue

            self.rows_read += 1
            try:
                record, fixes = normalize_row(fields)
            except RowError as exc:
                reason = str(exc)
                self.rejected += 1
                self.reject_reasons[reason.split(":", 1)[0]] += 1
                reject_handle.write(
                    json.dumps(
                        {"line": line_number, "reason": reason, "raw": list(fields)},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                if echo_rejects:
                    print(f"rejected line {line_number}: {reason}", file=sys.stderr)
                continue

            yield (line_number, bool(fixes), *to_db_row(record))

    def stats(self) -> WorkerStats:
        return WorkerStats(
            rows_read=self.rows_read,
            blank_lines=self.blank_lines,
            rejected=self.rejected,
            reject_reasons=dict(self.reject_reasons),
        )


def _copy_sequential(
    connection: Connection[DictRow],
    csv_path: Path,
    reject_log_path: Path,
) -> WorkerStats:
    collector = RowCollector()
    with reject_log_path.open("w", encoding="utf-8") as reject_handle:
        repository.copy_records(
            connection,
            collector.rows(iter_rows(csv_path), reject_handle, echo_rejects=True),
        )
    return collector.stats()


def _process_chunk(chunk: LineChunk, csv_path: Path, reject_part: Path) -> WorkerStats:
    """Worker entrypoint: own process, own connection, shared staging table."""
    collector = RowCollector()
    with (
        reject_part.open("w", encoding="utf-8") as reject_handle,
        psycopg.connect(settings.database_url, row_factory=dict_row) as connection,
    ):
        repository.copy_records(
            connection,
            collector.rows(iter_rows(csv_path, chunk), reject_handle, echo_rejects=False),
        )
    return collector.stats()


def _copy_parallel(
    csv_path: Path,
    reject_log_path: Path,
    scan: FileScan,
) -> WorkerStats:
    parts = [
        reject_log_path.with_name(f"{reject_log_path.name}.part-{chunk.index}")
        for chunk in scan.chunks
    ]

    try:
        context = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=scan.workers, mp_context=context) as pool:
            futures = [
                pool.submit(_process_chunk, chunk, csv_path, part)
                for chunk, part in zip(scan.chunks, parts, strict=True)
            ]
            results = [future.result() for future in futures]

        merge_reject_logs(parts, reject_log_path)
        with reject_log_path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                payload = json.loads(raw_line)
                print(
                    f"rejected line {int(payload['line'])}: {payload['reason']}",
                    file=sys.stderr,
                )

        reasons: Counter[str] = Counter()
        for result in results:
            reasons.update(result.reject_reasons)
        return WorkerStats(
            rows_read=sum(result.rows_read for result in results),
            blank_lines=sum(result.blank_lines for result in results),
            rejected=sum(result.rejected for result in results),
            reject_reasons=dict(reasons),
        )
    finally:
        for part in parts:
            part.unlink(missing_ok=True)


def load(
    csv_path: Path,
    reject_log_path: Path,
    workers: int | None = None,
) -> LoadSummary:
    """Load one CSV file, sequentially or split across processes.

    ``workers=None`` means "auto": one worker below 50,000 data lines and
    ``min(CPU, 8)`` above. Pass ``workers=1`` to force the sequential path.
    """
    if not csv_path.is_file():
        raise LoaderError(f"CSV file not found: {csv_path}")

    reject_log_path.parent.mkdir(parents=True, exist_ok=True)
    _validate_header(csv_path)

    try:
        scan = scan_file(csv_path, workers)
    except ChunkError as exc:
        raise LoaderError(str(exc)) from exc

    if scan.has_multiline_records and scan.workers > 1:
        print(
            "warning: multiline quoted records detected; falling back to one worker",
            file=sys.stderr,
        )
        scan = scan_file(csv_path, 1)

    part_paths = [
        reject_log_path.with_name(f"{reject_log_path.name}.part-{chunk.index}")
        for chunk in scan.chunks
    ]

    try:
        with (
            psycopg.connect(settings.database_url, row_factory=dict_row) as connection,
            repository.import_session(connection),
        ):
            if scan.workers > 1:
                stats = _copy_parallel(csv_path, reject_log_path, scan)
            else:
                stats = _copy_sequential(connection, csv_path, reject_log_path)

            report = repository.create_records(connection)
    finally:
        for part in part_paths:
            part.unlink(missing_ok=True)

    if stats.rows_read != report.staged_rows + stats.rejected:
        raise LoaderError(
            "internal accounting error: "
            f"{stats.rows_read} read != {report.staged_rows} staged + {stats.rejected} rejected"
        )
    if not scan.has_multiline_records and stats.rows_read + stats.blank_lines != scan.data_lines:
        raise LoaderError(
            "internal accounting error: "
            f"{stats.rows_read} rows + {stats.blank_lines} blanks "
            f"!= {scan.data_lines} data lines"
        )
    if report.staged_rows - report.distinct_ids != report.exact_duplicates + report.conflicts:
        raise LoaderError("internal accounting error for duplicate classification")
    if report.distinct_ids != report.loaded + report.already_loaded:
        raise LoaderError(
            "internal accounting error: "
            f"{report.distinct_ids} unique IDs != "
            f"{report.loaded} loaded + {report.already_loaded} existing"
        )
    if report.corrected > report.loaded:
        raise LoaderError("internal accounting error: corrected rows exceed loaded rows")

    return LoadSummary(
        rows_read=stats.rows_read,
        blank_lines=stats.blank_lines,
        loaded=report.loaded,
        corrected=report.corrected,
        rejected=stats.rejected,
        exact_duplicates=report.exact_duplicates,
        conflicts=report.conflicts,
        already_loaded=report.already_loaded,
        reject_reasons=stats.reject_reasons,
    )
