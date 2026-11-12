from __future__ import annotations

from decimal import Decimal

from app.coffee.loader import to_db_row
from app.models import CoffeeRecord


def test_to_db_row_keeps_column_order() -> None:
    record = CoffeeRecord(
        id=42,
        age=30,
        gender="Female",
        country="South Korea",
        coffee_intake=Decimal("2.5"),
        caffeine_mg=Decimal("237.5"),
        sleep_hours=Decimal("7.0"),
        sleep_quality="Good",
        bmi=Decimal("22.0"),
        heart_rate=65,
        stress_level="Low",
        physical_activity_hours=Decimal("5.5"),
        health_issues="None",
        occupation="Office",
        smoking=False,
        alcohol_consumption=True,
    )

    assert to_db_row(record) == (
        42,
        30,
        "Female",
        "South Korea",
        Decimal("2.5"),
        Decimal("237.5"),
        Decimal("7.0"),
        "Good",
        Decimal("22.0"),
        65,
        "Low",
        Decimal("5.5"),
        "None",
        "Office",
        False,
        True,
    )
