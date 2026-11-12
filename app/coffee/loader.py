"""Public interface for streaming coffee CSV imports."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.coffee.stream import load as load
from app.models import CoffeeRecord


class LoaderError(RuntimeError):
    """Raised when the file or database cannot be processed safely."""


@dataclass(frozen=True, slots=True)
class LoadSummary:
    rows_read: int
    blank_lines: int
    loaded: int
    corrected: int
    rejected: int
    exact_duplicates: int
    conflicts: int
    already_loaded: int
    reject_reasons: Mapping[str, int]

    @property
    def accounted_for(self) -> int:
        return (
            self.loaded
            + self.rejected
            + self.exact_duplicates
            + self.conflicts
            + self.already_loaded
        )


def to_db_row(record: CoffeeRecord) -> tuple[object, ...]:
    """Return record values in destination column order."""
    return (
        record.id,
        record.age,
        record.gender,
        record.country,
        record.coffee_intake,
        record.caffeine_mg,
        record.sleep_hours,
        record.sleep_quality,
        record.bmi,
        record.heart_rate,
        record.stress_level,
        record.physical_activity_hours,
        record.health_issues,
        record.occupation,
        record.smoking,
        record.alcohol_consumption,
    )
