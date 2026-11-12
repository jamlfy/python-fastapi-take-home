"""Ordered, bounded CSV import with transactional block checkpoints."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING

from app.coffee.stream.reader import read_records
from app.coffee.stream.workers import validate_record

if TYPE_CHECKING:
    from app.coffee.loader import LoadSummary

BLOCK_SIZE = 10_000


def load(csv_path: Path, reject_path: Path, workers: int | None = None) -> LoadSummary:
    """Validate in order and commit each complete block exactly once."""
    from app.coffee.loader import LoaderError, LoadSummary
    from app.coffee.repository.imports import CoffeeBlockWriter, ImportCheckpoint

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
            checkpoint.advance_through_confirmed_records()
            count = workers or min(os.cpu_count() or 1, 8)
            writer = CoffeeBlockWriter(checkpoint)
            try:
                records = read_records(csv_path)
                with ThreadPoolExecutor(max_workers=count) as pool:
                    while block := list(islice(records, BLOCK_SIZE)):
                        if block[-1][0] <= checkpoint.seq:
                            continue
                        pending = [(seq, item) for seq, item in block if seq > checkpoint.seq]
                        validated = list(pool.map(validate_record, (item for _, item in pending)))
                        writer.save_block(
                            list(zip((seq for seq, _ in pending), validated, strict=True))
                        )
                checkpoint.mark_complete(checkpoint.seq)
            finally:
                writer.close()
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
