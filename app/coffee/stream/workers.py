"""Bounded worker manager for the coffee CSV import."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from enum import Enum
from typing import TYPE_CHECKING

from app.coffee.normalization import RowError, normalize_row
from app.coffee.stream.reader import CsvRecord
from app.models import CoffeeRecord

if TYPE_CHECKING:
    from app.coffee.repository.imports import CoffeeBatchWriter, ImportCheckpoint

BUFFER_LIMIT = 500
ValidatedRecord = tuple[int, tuple[str, ...], str, tuple[CoffeeRecord, bool] | None, str | None]


class WorkerState(Enum):
    FREE = "free"
    BUSY = "busy"


def validate_record(item: CsvRecord) -> ValidatedRecord:
    line, fields, raw = item
    try:
        record, fixes = normalize_row(fields)
        return line, fields, raw, (record, bool(fixes)), None
    except RowError as exc:
        return line, fields, raw, None, str(exc)


class Worker:
    """Validate one record per task and keep a private bounded buffer."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.state = WorkerState.FREE
        self.buffer: list[tuple[int, ValidatedRecord]] = []
        self.store: CoffeeBatchWriter | None = None

    def accept_record(self, seq: int, item: CsvRecord) -> bool:
        from app.coffee.repository.imports import CoffeeBatchWriter

        if self.store is None:
            self.store = CoffeeBatchWriter(self.source)
        self.buffer.append((seq, validate_record(item)))
        return self.save_pending_records() if len(self.buffer) > BUFFER_LIMIT else False

    def save_pending_records(self) -> bool:
        if not self.buffer:
            return False
        assert self.store is not None
        self.store.save_batch(self.buffer)
        self.buffer.clear()
        return True

    def close(self) -> None:
        if self.store is not None:
            self.store.close()


class WorkerManager:
    """Dispatch one row to a free worker and collect confirmed batches."""

    def __init__(self, source: str, count: int, checkpoint: ImportCheckpoint) -> None:
        self.workers = [Worker(source) for _ in range(count)]
        self.checkpoint = checkpoint
        self.pool = ThreadPoolExecutor(max_workers=count)
        self.pending: dict[Future[bool], Worker] = {}
        self.next_unkeyed = 0

    def _worker_for_record(self, fields: tuple[str, ...]) -> Worker:
        try:
            key = int(fields[0])
        except (IndexError, ValueError):
            key = self.next_unkeyed
            self.next_unkeyed += 1
        return self.workers[key % len(self.workers)]

    def _collect_completed(self) -> None:
        completed, _ = wait(self.pending, return_when=FIRST_COMPLETED)
        for future in completed:
            worker = self.pending.pop(future)
            try:
                saved = future.result()
            finally:
                worker.state = WorkerState.FREE
            if saved:
                self.checkpoint.advance_through_confirmed_records()

    def save_record_with_worker(self, sequence: int, record: CsvRecord) -> None:
        """Send a record to its free worker for validation and buffered saving."""
        worker = self._worker_for_record(record[1])
        while worker.state is WorkerState.BUSY:
            self._collect_completed()
        worker.state = WorkerState.BUSY
        self.pending[self.pool.submit(worker.accept_record, sequence, record)] = worker

    def finish(self) -> None:
        while self.pending:
            self._collect_completed()
        for worker in self.workers:
            if worker.buffer:
                worker.state = WorkerState.BUSY
                self.pending[self.pool.submit(worker.save_pending_records)] = worker
        while self.pending:
            self._collect_completed()
        self.checkpoint.advance_through_confirmed_records()

    def close(self) -> None:
        self.pool.shutdown(wait=True, cancel_futures=True)
        for worker in self.workers:
            worker.close()
