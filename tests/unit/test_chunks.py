from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.coffee.chunks import (
    MAX_WORKERS,
    MIN_LINES_FOR_AUTO_PARALLEL,
    ChunkError,
    merge_reject_logs,
    plan_chunks,
    resolve_workers,
    scan_file,
    scan_summary,
)


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_plan_chunks_covers_every_line_without_overlap() -> None:
    for data_lines in (1, 2, 7, 10, 11, 1_000):
        for workers in (1, 2, 3, 4, 8, 16):
            chunks = plan_chunks(data_lines, workers)

            assert len(chunks) == min(workers, data_lines)
            assert chunks[0].start_line == 2
            assert chunks[-1].end_line == data_lines + 2

            covered = 0
            for index, chunk in enumerate(chunks):
                assert chunk.index == index
                assert chunk.line_count > 0
                if index > 0:
                    assert chunk.start_line == chunks[index - 1].end_line
                covered += chunk.line_count

            assert covered == data_lines


def test_plan_chunks_spreads_the_remainder_over_the_first_chunks() -> None:
    chunks = plan_chunks(data_lines=10, workers=3)

    assert [chunk.line_count for chunk in chunks] == [4, 3, 3]
    assert [(chunk.start_line, chunk.end_line) for chunk in chunks] == [
        (2, 6),
        (6, 9),
        (9, 12),
    ]


def test_plan_chunks_returns_nothing_for_an_empty_file() -> None:
    assert plan_chunks(data_lines=0, workers=4) == ()


@pytest.mark.parametrize(("data_lines", "workers"), [(-1, 1), (10, 0), (10, -2)])
def test_plan_chunks_rejects_invalid_arguments(data_lines: int, workers: int) -> None:
    with pytest.raises(ChunkError):
        plan_chunks(data_lines=data_lines, workers=workers)


def test_scan_summary_handles_a_missing_trailing_newline(tmp_path: Path) -> None:
    assert scan_summary(_write(tmp_path / "with-newline.csv", "h\n1\n2\n"))[0] == 3
    assert scan_summary(_write(tmp_path / "without-newline.csv", "h\n1\n2"))[0] == 3
    assert scan_summary(_write(tmp_path / "empty.csv", ""))[0] == 0


def test_scan_file_detects_only_multiline_quoted_records(tmp_path: Path) -> None:
    plain = scan_file(_write(tmp_path / "plain.csv", "h\n1\n2\n3\n"), 2)
    quoted_line = scan_file(_write(tmp_path / "quoted.csv", 'h\n1\n"2"\n3\n'), 2)
    multiline = scan_file(_write(tmp_path / "multiline.csv", 'h\n1\n"2\n3"\n4\n'), 2)

    assert plain.total_lines == 4
    assert plain.data_lines == 3
    assert plain.has_multiline_records is False
    assert quoted_line.has_multiline_records is False
    assert multiline.has_multiline_records is True


def test_scan_file_offsets_point_to_the_start_of_each_chunk(tmp_path: Path) -> None:
    path = _write(tmp_path / "offsets.csv", "header\nrow1\nrow2\n\nrow4\n")

    scan = scan_file(path, 2)

    assert [(chunk.start_line, chunk.end_line) for chunk in scan.chunks] == [
        (2, 4),
        (4, 6),
    ]

    content = path.read_bytes()
    assert content[scan.chunks[0].start_offset :].startswith(b"row1\nrow2\n")
    assert content[scan.chunks[1].start_offset :].startswith(b"\nrow4\n")


def test_scan_file_never_creates_more_chunks_than_lines(tmp_path: Path) -> None:
    scan = scan_file(_write(tmp_path / "tiny.csv", "h\n1\n2\n"), 8)

    assert scan.workers == 2
    assert len(scan.chunks) == 2


def test_scan_file_handles_a_header_only_file(tmp_path: Path) -> None:
    scan = scan_file(_write(tmp_path / "header.csv", "h\n"), 4)

    assert scan.data_lines == 0
    assert scan.workers == 1
    assert scan.chunks == ()


@pytest.mark.parametrize(
    ("requested", "data_lines", "cpu_count", "expected"),
    [
        (None, 0, 64, 1),
        (None, MIN_LINES_FOR_AUTO_PARALLEL - 1, 64, 1),
        (None, MIN_LINES_FOR_AUTO_PARALLEL, 4, 4),
        (None, 10_000_000, 64, MAX_WORKERS),
        (None, 10_000_000, 2, 2),
        (4, 100, 64, 4),
        (16, 10, 64, 10),
        (2, 0, 64, 1),
    ],
)
def test_resolve_workers(
    requested: int | None,
    data_lines: int,
    cpu_count: int,
    expected: int,
) -> None:
    assert resolve_workers(requested, data_lines, cpu_count) == expected


@pytest.mark.parametrize("requested", [0, -1])
def test_resolve_workers_rejects_invalid_values(requested: int) -> None:
    with pytest.raises(ChunkError):
        resolve_workers(requested, data_lines=100, cpu_count=8)


def test_merge_reject_logs_preserves_line_order(tmp_path: Path) -> None:
    first = _write(
        tmp_path / "rejects.part-0",
        json.dumps({"line": 2, "reason": "a"}) + "\n",
    )
    second = _write(
        tmp_path / "rejects.part-1",
        json.dumps({"line": 9, "reason": "b"}) + "\n",
    )
    final = tmp_path / "nested" / "rejects.jsonl"

    merge_reject_logs([first, second], final)

    lines = [json.loads(line) for line in final.read_text(encoding="utf-8").splitlines()]
    assert lines == [
        {"line": 2, "reason": "a"},
        {"line": 9, "reason": "b"},
    ]
