from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import psycopg

from app.coffee.chunks import scan_file
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


def test_loader_is_idempotent_and_resolves_conflicts(tmp_path: Path) -> None:
    reject_log = tmp_path / "rejected_rows.jsonl"
    _delete_fixture_rows()

    try:
        first = load(FIXTURE, reject_log)

        assert first.rows_read == 16
        assert first.loaded == 6
        assert first.corrected == 3
        assert first.rejected == 8
        assert first.exact_duplicates == 1
        assert first.conflicts == 1
        assert first.already_loaded == 0
        assert first.accounted_for == first.rows_read

        reject_lines = reject_log.read_text(encoding="utf-8").splitlines()
        assert len(reject_lines) == 8
        first_reject = json.loads(reject_lines[0])
        assert first_reject["line"] == 9
        assert "reason" in first_reject
        assert isinstance(first_reject["raw"], list)

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

        second = load(FIXTURE, reject_log)

        assert second.loaded == 0
        assert second.corrected == 0
        assert second.rejected == 8
        assert second.exact_duplicates == 1
        assert second.conflicts == 1
        assert second.already_loaded == 6
        assert second.accounted_for == second.rows_read
    finally:
        _delete_fixture_rows()


def test_parallel_loader_matches_the_sequential_result(tmp_path: Path) -> None:
    sequential_log = tmp_path / "sequential.jsonl"
    parallel_log = tmp_path / "parallel.jsonl"
    _delete_fixture_rows()

    try:
        # The fixture has quoted fields on a single line, which must stay safe
        # for line-based splitting; only embedded newlines force a fallback.
        scan = scan_file(FIXTURE, 4)
        assert scan.workers == 4
        assert scan.has_multiline_records is False

        sequential = load(FIXTURE, sequential_log, workers=1)
        _delete_fixture_rows()
        parallel = load(FIXTURE, parallel_log, workers=4)

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

        again = load(FIXTURE, parallel_log, workers=4)

        assert again.loaded == 0
        assert again.already_loaded == 6
        assert again.rejected == parallel.rejected
        assert again.accounted_for == again.rows_read
    finally:
        _delete_fixture_rows()
