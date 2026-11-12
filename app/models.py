"""Domain models shared by the loader and the API."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class CoffeeRecord:
    id: int
    age: int
    gender: str
    country: str
    coffee_intake: Decimal
    caffeine_mg: Decimal
    sleep_hours: Decimal
    sleep_quality: str
    bmi: Decimal
    heart_rate: int
    stress_level: str
    physical_activity_hours: Decimal
    health_issues: str
    occupation: str
    smoking: bool
    alcohol_consumption: bool
