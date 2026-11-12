from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from typing import Final

import psycopg
import pytest

from app.config import settings

SEED_IDS: Final = (910_001, 910_002, 910_003, 910_004)
SEED_ROWS: Final[list[tuple[object, ...]]] = [
    (
        910_001,
        25,
        "Female",
        "Testland",
        Decimal("1.0"),
        Decimal("95.0"),
        Decimal("8.0"),
        "Excellent",
        Decimal("21.0"),
        60,
        "Low",
        Decimal("5.0"),
        "None",
        "Office",
        False,
        False,
    ),
    (
        910_002,
        35,
        "Male",
        "Testonia",
        Decimal("3.0"),
        Decimal("285.0"),
        Decimal("6.0"),
        "Good",
        Decimal("27.0"),
        75,
        "Medium",
        Decimal("3.0"),
        "Mild",
        "Service",
        True,
        False,
    ),
    (
        910_003,
        45,
        "Female",
        "Testland",
        Decimal("5.0"),
        Decimal("475.0"),
        Decimal("5.0"),
        "Poor",
        Decimal("32.0"),
        85,
        "High",
        Decimal("2.0"),
        "Moderate",
        "Healthcare",
        False,
        True,
    ),
    (
        910_004,
        55,
        "Male",
        "Testland",
        Decimal("0.0"),
        Decimal("0.0"),
        Decimal("7.5"),
        "Good",
        Decimal("24.0"),
        70,
        "Low",
        Decimal("4.0"),
        "None",
        "Student",
        False,
        False,
    ),
]

INSERT_SQL: Final = """
    insert into coffee_health (
      id, age, gender, country, coffee_intake, caffeine_mg, sleep_hours,
      sleep_quality, bmi, heart_rate, stress_level, physical_activity_hours,
      health_issues, occupation, smoking, alcohol_consumption
    ) values (
      %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
    )
"""


def _delete_seed_rows() -> None:
    with psycopg.connect(settings.database_url) as connection:
        connection.execute(
            "delete from coffee_health where id = any(%s)",
            (list(SEED_IDS),),
        )


@pytest.fixture
def seeded() -> Iterator[None]:
    _delete_seed_rows()
    with psycopg.connect(settings.database_url) as connection, connection.cursor() as cursor:
        cursor.executemany(INSERT_SQL, SEED_ROWS)
    try:
        yield
    finally:
        _delete_seed_rows()
