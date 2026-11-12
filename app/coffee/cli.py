"""Command-line entrypoint for the coffee health loader."""

from __future__ import annotations

import argparse
from pathlib import Path

import psycopg

from app.coffee.chunks import ChunkError
from app.coffee.loader import LoaderError, LoadSummary, load


def print_summary(summary: LoadSummary, reject_log_path: Path) -> None:
    print()
    print("Load summary")
    print("------------")
    print(f"Rows read:          {summary.rows_read}")
    print(f"Blank lines skipped:{summary.blank_lines:>4}")
    print(f"Loaded:             {summary.loaded}")
    print(f"Corrected:          {summary.corrected} (of loaded)")
    print(f"Rejected:           {summary.rejected}")
    print(f"Exact duplicates:   {summary.exact_duplicates}")
    print(f"ID conflicts:       {summary.conflicts}")
    print(f"Already loaded:     {summary.already_loaded}")
    print(f"Accounted for:      {summary.accounted_for}")
    print(f"Reject log:         {reject_log_path}")
    if summary.reject_reasons:
        reasons = ", ".join(
            f"{reason}={count}" for reason, count in sorted(summary.reject_reasons.items())
        )
        print(f"Reject reasons:     {reasons}")


def _parse_workers(value: str) -> int | None:
    if value == "auto":
        return None
    try:
        workers = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected 'auto' or an integer, got {value!r}") from exc
    if workers < 1:
        raise argparse.ArgumentTypeError(f"workers must be >= 1, got {workers}")
    return workers


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Load the coffee health CSV into Postgres safely and idempotently."
    )
    parser.add_argument("csv_path", type=Path)
    parser.add_argument(
        "--reject-log",
        type=Path,
        default=Path("data/rejected_rows.jsonl"),
        help="Where to write rejected rows as JSON Lines (default: data/rejected_rows.jsonl)",
    )
    parser.add_argument(
        "--workers",
        type=_parse_workers,
        default=None,
        metavar="auto|N",
        help=(
            "Worker processes. 'auto' (default) stays sequential below 50,000 "
            "lines and then uses min(CPU, 8); N forces N workers."
        ),
    )
    args = parser.parse_args()
    try:
        summary = load(args.csv_path, args.reject_log, workers=args.workers)
    except (LoaderError, ChunkError, psycopg.Error) as exc:
        raise SystemExit(f"load failed: {exc}") from exc
    print_summary(summary, args.reject_log)
