"""Single-pass CSV import coordinated by a bounded worker manager."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from app.coffee.stream.reader import read_records
from app.coffee.stream.workers import WorkerManager

if TYPE_CHECKING:
    from app.coffee.loader import LoadSummary


def load(csv_path: Path, reject_path: Path, workers: int | None = None) -> LoadSummary:
    """Read the source once and show final results through LoadSummary."""
    from app.coffee.loader import LoaderError, LoadSummary
    from app.coffee.repository.imports import ImportCheckpoint

    if not csv_path.is_file():
        raise LoaderError(f"CSV file not found: {csv_path}")
    if workers is not None and workers < 1:
        raise LoaderError("workers must be >= 1")
    stat = csv_path.stat()
    source = str(csv_path.resolve())
    signature = f"{stat.st_size}:{stat.st_mtime_ns}"
    checkpoint = ImportCheckpoint(source, signature)
    try:
        if not checkpoint.completed:
            manager = WorkerManager(source, workers or min(os.cpu_count() or 1, 8), checkpoint)
            try:
                last_seq = 0
                for seq, record in read_records(csv_path):
                    last_seq = seq
                    if not checkpoint.is_record_confirmed(seq):
                        manager.save_record_with_worker(seq, record)
                manager.finish()
                checkpoint.mark_complete(last_seq)
            finally:
                manager.close()
        checkpoint.write_errors_csv(reject_path)
        counts = checkpoint.counts
        return LoadSummary(
            rows_read=counts["rows_read"],
            blank_lines=counts["blank_lines"],
            loaded=counts["loaded"],
            corrected=counts["corrected"],
            rejected=counts["rejected"],
            exact_duplicates=counts["exact_duplicates"],
            conflicts=counts["conflicts"],
            already_loaded=counts["already_loaded"],
            reject_reasons=dict(checkpoint.reasons),
        )
    finally:
        checkpoint.close()
