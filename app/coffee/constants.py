"""Constants and canonical values for the coffee health dataset."""

from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Final

EXPECTED_HEADER: Final[tuple[str, ...]] = (
    "ID",
    "Age",
    "Gender",
    "Country",
    "Coffee_Intake",
    "Caffeine_mg",
    "Sleep_Hours",
    "Sleep_Quality",
    "BMI",
    "Heart_Rate",
    "Stress_Level",
    "Physical_Activity_Hours",
    "Health_Issues",
    "Occupation",
    "Smoking",
    "Alcohol_Consumption",
)

COLUMNS: Final[tuple[str, ...]] = (
    "id",
    "age",
    "gender",
    "country",
    "coffee_intake",
    "caffeine_mg",
    "sleep_hours",
    "sleep_quality",
    "bmi",
    "heart_rate",
    "stress_level",
    "physical_activity_hours",
    "health_issues",
    "occupation",
    "smoking",
    "alcohol_consumption",
)

GENDER_VALUES: Final[Mapping[str, str]] = {
    "MALE": "Male",
    "FEMALE": "Female",
    "OTHER": "Other",
}
SLEEP_QUALITY_VALUES: Final[Mapping[str, str]] = {
    "POOR": "Poor",
    "FAIR": "Fair",
    "GOOD": "Good",
    "EXCELLENT": "Excellent",
}
STRESS_LEVEL_VALUES: Final[Mapping[str, str]] = {
    "LOW": "Low",
    "MEDIUM": "Medium",
    "HIGH": "High",
}
HEALTH_ISSUES_VALUES: Final[Mapping[str, str]] = {
    "NONE": "None",
    "MILD": "Mild",
    "MODERATE": "Moderate",
    "SEVERE": "Severe",
}
OCCUPATION_VALUES: Final[Mapping[str, str]] = {
    "OFFICE": "Office",
    "HEALTHCARE": "Healthcare",
    "SERVICE": "Service",
    "STUDENT": "Student",
    "OTHER": "Other",
}
COUNTRY_OVERRIDES: Final[Mapping[str, str]] = {
    "UK": "UK",
    "USA": "USA",
}

BOOLEAN_VALUES: Final[Mapping[str, bool]] = {
    "1": True,
    "true": True,
    "t": True,
    "yes": True,
    "y": True,
    "0": False,
    "false": False,
    "f": False,
    "no": False,
    "n": False,
}

NUMBER_RE: Final = re.compile(
    r"^\s*([+-]?\d+(?:[.,]\d+)?)\s*(cups?|hours?|hrs?|mg|kg|bpm|years?|yrs?)?\s*$",
    re.IGNORECASE,
)
COUNTRY_RE: Final = re.compile(r"[A-Za-z][A-Za-z .'-]*")

DECIMAL_RANGES: Final[Mapping[str, tuple[Decimal, Decimal]]] = {
    "Coffee_Intake": (Decimal("0"), Decimal("10")),
    "Caffeine_mg": (Decimal("0"), Decimal("1000")),
    "Sleep_Hours": (Decimal("0"), Decimal("16")),
    "BMI": (Decimal("10"), Decimal("60")),
    "Physical_Activity_Hours": (Decimal("0"), Decimal("40")),
}
INTEGER_RANGES: Final[Mapping[str, tuple[int, int]]] = {
    "ID": (1, 2_147_483_647),
    "Age": (18, 100),
    "Heart_Rate": (30, 220),
}

MAX_DECAF_CAFFEINE_MG: Final = Decimal("10")
