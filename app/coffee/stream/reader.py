"""Read CSV records once while preserving their original text."""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path
from typing import TextIO

from app.coffee.constants import EXPECTED_HEADER

CsvRecord = tuple[int, tuple[str, ...], str]


class RawCsvLines:
    """Feed csv.reader while retaining only the current raw record."""

    def __init__(self, handle: TextIO) -> None:
        self.handle = handle
        self.lines: list[str] = []

    def __iter__(self) -> RawCsvLines:
        return self

    def __next__(self) -> str:
        line = self.handle.readline()
        if not line:
            raise StopIteration
        self.lines.append(line)
        return line

    def take_record(self) -> str:
        raw = "".join(self.lines)
        self.lines.clear()
        return raw


def read_records(path: Path) -> Iterator[tuple[int, CsvRecord]]:
    """Yield sequence number, physical line and raw CSV for each record."""
    from app.coffee.loader import LoaderError

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        captured = RawCsvLines(handle)
        reader = csv.reader(captured)
        try:
            header = tuple(next(reader))
        except StopIteration as exc:
            raise LoaderError(f"{path} is empty") from exc
        captured.take_record()
        if header != EXPECTED_HEADER:
            raise LoaderError(
                f"unexpected CSV header: expected {EXPECTED_HEADER!r}, got {header!r}"
            )
        for sequence, fields in enumerate(reader, start=1):
            yield sequence, (reader.line_num, tuple(fields), captured.take_record())
