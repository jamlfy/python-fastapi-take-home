from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import Any, cast

from app.coffee.stream.reader import RawCsvLines, read_records
from app.coffee.stream.workers import (
    BUFFER_LIMIT,
    Worker,
    WorkerManager,
    WorkerState,
    validate_record,
)


class FakeStore:
    def __init__(self, source: str) -> None:
        self.source = source
        self.batches: list[list[Any]] = []

    def save_batch(self, batch: list[Any]) -> None:
        self.batches.append(list(batch))

    def close(self) -> None:
        pass


class FakeCheckpoint:
    def __init__(self) -> None:
        self.advances = 0

    def advance_through_confirmed_records(self) -> None:
        self.advances += 1


def test_invalid_record_does_not_stop_worker(monkeypatch: Any) -> None:
    monkeypatch.setattr("app.coffee.repository.imports.CoffeeBatchWriter", FakeStore)
    worker = Worker("source")
    assert worker.accept_record(1, (2, ("bad",), '["bad"]')) is False
    assert worker.accept_record(2, (3, (), "[]")) is False
    assert worker.buffer[0][1][4] is not None
    assert worker.buffer[1][1][1] == ()
    assert worker.save_pending_records() is True
    assert len(worker.store.batches) == 1  # type: ignore[union-attr]
    worker.close()


def test_worker_flushes_on_501st_line(monkeypatch: Any) -> None:
    monkeypatch.setattr("app.coffee.repository.imports.CoffeeBatchWriter", FakeStore)
    worker = Worker("source")
    for seq in range(1, BUFFER_LIMIT + 1):
        assert worker.accept_record(seq, (seq + 1, (), "[]")) is False
    assert worker.accept_record(BUFFER_LIMIT + 1, (BUFFER_LIMIT + 2, (), "[]")) is True
    assert worker.buffer == []
    assert len(worker.store.batches[0]) == 501  # type: ignore[union-attr]
    worker.close()


def test_manager_uses_free_workers_and_flushes_at_end(monkeypatch: Any) -> None:
    monkeypatch.setattr("app.coffee.repository.imports.CoffeeBatchWriter", FakeStore)
    checkpoint = FakeCheckpoint()
    manager = WorkerManager("source", 2, checkpoint)  # type: ignore[arg-type]
    for seq in range(1, 6):
        manager.save_record_with_worker(seq, (seq + 1, (str(seq % 2),), "raw"))
    manager.finish()
    manager.close()
    assert all(worker.state is WorkerState.FREE for worker in manager.workers)
    batches = [
        batch for worker in manager.workers for batch in cast(FakeStore, worker.store).batches
    ]
    total = sum(len(batch) for batch in batches)
    assert total == 5
    assert checkpoint.advances >= 1


def test_validate_record_reports_exact_error() -> None:
    result = validate_record((2, ("bad",), '["bad"]'))
    assert result[4] is not None


def test_capture_lines_preserves_multiline_raw_record() -> None:
    import csv

    captured = RawCsvLines(StringIO('a,b\n1,"two\nlines"\n'))
    reader = csv.reader(captured)
    assert next(reader) == ["a", "b"]
    assert captured.take_record() == "a,b\n"
    assert next(reader) == ["1", "two\nlines"]
    assert captured.take_record() == '1,"two\nlines"\n'


def test_read_records_keeps_multiline_data_and_line_numbers(tmp_path: Path) -> None:
    from app.coffee.constants import EXPECTED_HEADER

    source = tmp_path / "input.csv"
    source.write_text(",".join(EXPECTED_HEADER) + '\n1,"two\nlines"\n', encoding="utf-8")
    assert list(read_records(source)) == [(1, (3, ("1", "two\nlines"), '1,"two\nlines"\n'))]
