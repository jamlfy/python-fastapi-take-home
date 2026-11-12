from __future__ import annotations

import csv
import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest

from app.coffee.loader import load
from app.config import settings

FIXTURE = Path(__file__).parents[1] / "fixtures" / "dirty_rows.csv"
FIXTURE_ID_MIN = 900_000
FIXTURE_ID_MAX = 900_999


def _delete_fixture_rows() -> None:
    with psycopg.connect(settings.database_url) as connection:
        connection.execute(
            "delete from coffee_health where id between %s and %s",
            (FIXTURE_ID_MIN, FIXTURE_ID_MAX),
        )


def test_loader_is_idempotent_and_resolves_conflicts(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("app.coffee.stream.BLOCK_SIZE", 1)
    reject_log = tmp_path / "errores.csv"
    source = tmp_path / "source.csv"
    shutil.copyfile(FIXTURE, source)
    _delete_fixture_rows()

    try:
        first = load(source, reject_log)

        assert first.rows_read == 16
        assert first.loaded == 6
        assert first.corrected == 3
        assert first.rejected == 8
        assert first.exact_duplicates == 1
        assert first.conflicts == 1
        assert first.already_loaded == 0
        assert first.accounted_for == first.rows_read

        with reject_log.open(newline="", encoding="utf-8") as handle:
            reject_lines = list(csv.DictReader(handle))
        assert len(reject_lines) == 8
        assert reject_lines[0]["line"] == "9"
        assert reject_lines[0]["reason"]
        assert reject_lines[0]["raw"]

        with psycopg.connect(settings.database_url) as connection:
            count_row = connection.execute(
                "select count(*) from coffee_health where id between %s and %s",
                (FIXTURE_ID_MIN, FIXTURE_ID_MAX),
            ).fetchone()
            assert count_row is not None
            assert count_row[0] == 6

            normalized = connection.execute(
                """
                select gender, country, coffee_intake, caffeine_mg,
                       smoking, alcohol_consumption
                from coffee_health
                where id = 900003
                """
            ).fetchone()
            assert normalized == (
                "Male",
                "South Korea",
                Decimal("1.0"),
                Decimal("94.1"),
                False,
                False,
            )

            conflict_winner = connection.execute(
                "select age from coffee_health where id = 900002"
            ).fetchone()
            assert conflict_winner is not None
            assert conflict_winner[0] == 30

        second = load(source, reject_log)

        assert second.loaded == first.loaded
        assert second.corrected == first.corrected
        assert second.rejected == 8
        assert second.exact_duplicates == 1
        assert second.conflicts == 1
        assert second.already_loaded == first.already_loaded
        assert second.accounted_for == second.rows_read
    finally:
        _delete_fixture_rows()


def test_parallel_loader_matches_the_sequential_result(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("app.coffee.stream.BLOCK_SIZE", 3)
    sequential_log = tmp_path / "sequential.csv"
    parallel_log = tmp_path / "parallel.csv"
    sequential_source = tmp_path / "sequential-source.csv"
    parallel_source = tmp_path / "parallel-source.csv"
    shutil.copyfile(FIXTURE, sequential_source)
    shutil.copyfile(FIXTURE, parallel_source)
    _delete_fixture_rows()

    try:
        sequential = load(sequential_source, sequential_log, workers=1)
        _delete_fixture_rows()
        parallel = load(parallel_source, parallel_log, workers=4)

        # Same counts and same reject reasons regardless of the worker count.
        assert parallel == sequential
        assert parallel_log.read_text(encoding="utf-8") == sequential_log.read_text(
            encoding="utf-8"
        )

        with psycopg.connect(settings.database_url) as connection:
            count_row = connection.execute(
                "select count(*) from coffee_health where id between %s and %s",
                (FIXTURE_ID_MIN, FIXTURE_ID_MAX),
            ).fetchone()
            assert count_row is not None
            assert count_row[0] == 6

            conflict_winner = connection.execute(
                "select age from coffee_health where id = 900002"
            ).fetchone()
            assert conflict_winner is not None
            assert conflict_winner[0] == 30

        again = load(parallel_source, parallel_log, workers=4)

        assert again.loaded == parallel.loaded
        assert again.already_loaded == parallel.already_loaded
        assert again.rejected == parallel.rejected
        assert again.accounted_for == again.rows_read
    finally:
        _delete_fixture_rows()


def test_resume_after_committed_block(tmp_path: Path, monkeypatch: Any) -> None:
    from app.coffee.repository.imports import CoffeeBlockWriter

    monkeypatch.setattr("app.coffee.stream.BLOCK_SIZE", 2)
    source = tmp_path / "interrupted.csv"
    reject_log = tmp_path / "errors.csv"
    shutil.copyfile(FIXTURE, source)
    _delete_fixture_rows()
    original = CoffeeBlockWriter.save_block
    calls = 0

    def interrupt_after_first(self: CoffeeBlockWriter, rows: Any) -> None:
        nonlocal calls
        if calls == 1:
            raise RuntimeError("simulated interruption")
        original(self, rows)
        calls += 1

    try:
        monkeypatch.setattr(CoffeeBlockWriter, "save_block", interrupt_after_first)
        with pytest.raises(RuntimeError, match="simulated interruption"):
            load(source, reject_log, workers=2)
        monkeypatch.setattr(CoffeeBlockWriter, "save_block", original)
        resumed = load(source, reject_log, workers=2)
        assert resumed.rows_read == 16
        assert resumed.loaded == 6
        assert resumed.rejected == 8
        assert resumed.exact_duplicates == 1
        assert resumed.conflicts == 1
        assert resumed.accounted_for == resumed.rows_read
    finally:
        _delete_fixture_rows()


def test_resume_from_legacy_progress(tmp_path: Path, monkeypatch: Any) -> None:
    from app.coffee.repository.imports import CoffeeBatchWriter, ImportCheckpoint
    from app.coffee.stream.reader import read_records
    from app.coffee.stream.workers import validate_record

    monkeypatch.setattr("app.coffee.stream.BLOCK_SIZE", 2)
    source = tmp_path / "legacy.csv"
    reject_log = tmp_path / "errors.csv"
    shutil.copyfile(FIXTURE, source)
    _delete_fixture_rows()
    stat = source.stat()
    checkpoint = ImportCheckpoint(str(source.resolve()), f"{stat.st_size}:{stat.st_mtime_ns}")
    writer = CoffeeBatchWriter(str(source.resolve()))
    try:
        first_two = list(read_records(source))[:2]
        writer.save_batch([(seq, validate_record(row)) for seq, row in first_two])
    finally:
        writer.close()
        checkpoint.close()

    try:
        summary = load(source, reject_log, workers=2)
        assert summary.rows_read == 16
        assert summary.loaded == 6
        assert summary.exact_duplicates == 1
        assert summary.rejected == 8
        assert summary.accounted_for == summary.rows_read
    finally:
        _delete_fixture_rows()
