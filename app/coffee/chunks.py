"""Line counting and chunk planning for the parallel CSV loader.

The parallel loader splits the file by physical line ranges. That is exact for
this dataset, which has no quoted fields and therefore one record per line.

Scanning is done in binary blocks and only a few offsets are kept, so memory
does not grow with the number of lines. The first pass counts lines and detects
quotes; a short second pass records the byte offset where each requested chunk
starts. Workers then ``seek`` straight to their range instead of re-reading and
discarding the file prefix.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

READ_BLOCK_SIZE: Final = 1 << 20
MIN_LINES_FOR_AUTO_PARALLEL: Final = 50_000
MAX_WORKERS: Final = 8


class ChunkError(ValueError):
    """Raised when the file cannot be counted or split safely."""


@dataclass(frozen=True, slots=True)
class LineRange:
    """A contiguous range of physical data lines, without byte offsets."""

    index: int
    start_line: int
    end_line: int

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line


@dataclass(frozen=True, slots=True)
class LineChunk(LineRange):
    """A line range plus the byte offset where its first line starts."""

    start_offset: int


@dataclass(frozen=True, slots=True)
class FileScan:
    total_lines: int
    data_lines: int
    has_multiline_records: bool
    workers: int
    chunks: tuple[LineChunk, ...]


def scan_summary(csv_path: Path) -> tuple[int, bool]:
    """Return (physical line count, any newline inside a quoted field).

    Quotes on a single line are harmless for line-based splitting; a newline
    inside a quoted field is not, because one record would span chunks.
    """
    lines = 0
    last_byte = b""
    in_quotes = False
    multiline = False

    with csv_path.open("rb") as handle:
        while block := handle.read(READ_BLOCK_SIZE):
            position = 0
            while (newline := block.find(b"\n", position)) != -1:
                # RFC 4180: a quote toggles the state; the ``""`` escape is two
                # quotes and therefore cancels out.
                in_quotes ^= bool(block.count(b'"', position, newline) % 2)
                if in_quotes:
                    multiline = True
                lines += 1
                position = newline + 1

            in_quotes ^= bool(block.count(b'"', position) % 2)
            last_byte = block[-1:]

    # A file without a trailing newline still has one last physical line.
    if last_byte and last_byte != b"\n":
        lines += 1

    return lines, multiline


def resolve_workers(requested: int | None, data_lines: int, cpu_count: int) -> int:
    """Turn the CLI value into a worker count.

    ``None`` means "auto": stay sequential below the threshold and otherwise
    use one worker per CPU, capped to avoid opening too many connections.
    """
    if data_lines <= 0:
        return 1

    if requested is None:
        if data_lines < MIN_LINES_FOR_AUTO_PARALLEL:
            return 1
        return max(1, min(cpu_count, MAX_WORKERS, data_lines))

    if requested < 1:
        raise ChunkError(f"workers must be >= 1, got {requested}")
    return min(requested, data_lines)


def plan_chunks(data_lines: int, workers: int) -> tuple[LineRange, ...]:
    """Split ``data_lines`` into contiguous ranges, one per worker.

    Line 1 is the header, so data starts at physical line 2. Every line is
    covered exactly once and the remainder is spread over the first chunks.
    """
    if data_lines < 0:
        raise ChunkError(f"data_lines must be >= 0, got {data_lines}")
    if workers < 1:
        raise ChunkError(f"workers must be >= 1, got {workers}")
    if data_lines == 0:
        return ()

    effective = min(workers, data_lines)
    base, extra = divmod(data_lines, effective)
    chunks: list[LineRange] = []
    start = 2

    for index in range(effective):
        count = base + (1 if index < extra else 0)
        chunks.append(LineRange(index=index, start_line=start, end_line=start + count))
        start += count

    return tuple(chunks)


def _locate_offsets(csv_path: Path, target_lines: Sequence[int]) -> dict[int, int]:
    """Return the byte offset where each target physical line starts."""
    wanted = set(target_lines)
    if not wanted:
        return {}

    last = max(wanted)
    offsets: dict[int, int] = {}
    if 1 in wanted:
        offsets[1] = 0

    line = 1
    block_start = 0

    with csv_path.open("rb") as handle:
        while line < last:
            block = handle.read(READ_BLOCK_SIZE)
            if not block:
                break

            position = 0
            while line < last:
                newline = block.find(b"\n", position)
                if newline == -1:
                    break
                line += 1
                if line in wanted:
                    offsets[line] = block_start + newline + 1
                position = newline + 1

            block_start += len(block)

    missing = wanted - offsets.keys()
    if missing:
        raise ChunkError(f"could not locate line(s) {sorted(missing)} in {csv_path}")
    return offsets


def scan_file(
    csv_path: Path,
    workers: int | None = None,
    *,
    cpu_count: int | None = None,
) -> FileScan:
    """Count the file and plan one chunk per worker."""
    total_lines, has_multiline_records = scan_summary(csv_path)
    data_lines = max(total_lines - 1, 0)
    resolved = resolve_workers(workers, data_lines, cpu_count or os.cpu_count() or 1)
    ranges = plan_chunks(data_lines, resolved)
    offsets = _locate_offsets(csv_path, [line_range.start_line for line_range in ranges])

    chunks = tuple(
        LineChunk(
            index=line_range.index,
            start_line=line_range.start_line,
            end_line=line_range.end_line,
            start_offset=offsets[line_range.start_line],
        )
        for line_range in ranges
    )

    return FileScan(
        total_lines=total_lines,
        data_lines=data_lines,
        has_multiline_records=has_multiline_records,
        workers=resolved,
        chunks=chunks,
    )


def merge_reject_logs(parts: Sequence[Path], final: Path) -> None:
    """Concatenate reject logs written by workers, preserving line order."""
    final.parent.mkdir(parents=True, exist_ok=True)
    with final.open("w", encoding="utf-8") as destination:
        for part in parts:
            with part.open("r", encoding="utf-8") as source:
                shutil.copyfileobj(source, destination)
