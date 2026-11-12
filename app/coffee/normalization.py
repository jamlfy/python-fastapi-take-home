"""Pure parsing, normalization and validation for coffee health rows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

from app.coffee.constants import (
    BOOLEAN_VALUES,
    COUNTRY_OVERRIDES,
    COUNTRY_RE,
    DECIMAL_RANGES,
    EXPECTED_HEADER,
    GENDER_VALUES,
    HEALTH_ISSUES_VALUES,
    INTEGER_RANGES,
    MAX_DECAF_CAFFEINE_MG,
    NUMBER_RE,
    OCCUPATION_VALUES,
    SLEEP_QUALITY_VALUES,
    STRESS_LEVEL_VALUES,
)
from app.models import CoffeeRecord


class RowError(ValueError):
    """Raised for one invalid CSV row; the loader keeps going."""


def normalize_categorical(
    raw: str,
    field: str,
    values: Mapping[str, str],
) -> tuple[str, tuple[str, ...]]:
    cleaned = raw.strip()
    if not cleaned:
        raise RowError(f"{field}: empty value")

    canonical = values.get(cleaned.upper())
    if canonical is None:
        raise RowError(f"{field}: invalid value ({raw!r})")

    fixes = () if raw == canonical else (f"{field}: normalized",)
    return canonical, fixes


def normalize_country(raw: str) -> tuple[str, tuple[str, ...]]:
    cleaned = " ".join(raw.split())
    if not cleaned:
        raise RowError("Country: empty value")
    if COUNTRY_RE.fullmatch(cleaned) is None:
        raise RowError(f"Country: invalid value ({raw!r})")

    override = COUNTRY_OVERRIDES.get(cleaned.upper())
    canonical = (
        override
        if override is not None
        else " ".join(part.capitalize() for part in cleaned.split())
    )
    fixes = () if raw == canonical else ("Country: normalized",)
    return canonical, fixes


def parse_numeric_token(raw: str, field: str) -> tuple[Decimal, tuple[str, ...]]:
    match = NUMBER_RE.fullmatch(raw)
    if match is None:
        raise RowError(f"{field}: not a number ({raw!r})")

    token = match.group(1)
    value = Decimal(token.replace(",", "."))
    fixes: list[str] = []
    if raw != raw.strip():
        fixes.append(f"{field}: whitespace")
    if "," in token:
        fixes.append(f"{field}: decimal comma")
    if match.group(2) is not None:
        fixes.append(f"{field}: unit suffix")
    return value, tuple(fixes)


def parse_decimal(raw: str, field: str) -> tuple[Decimal, tuple[str, ...]]:
    value, fixes = parse_numeric_token(raw, field)
    low, high = DECIMAL_RANGES[field]
    if not low <= value <= high:
        raise RowError(f"{field}: out of range ({value} not in [{low}, {high}])")
    return value, fixes


def parse_integer(raw: str, field: str) -> tuple[int, tuple[str, ...]]:
    value, fixes = parse_numeric_token(raw, field)
    if value != value.to_integral_value():
        raise RowError(f"{field}: expected an integer ({raw!r})")

    integer = int(value)
    low, high = INTEGER_RANGES[field]
    if not low <= integer <= high:
        raise RowError(f"{field}: out of range ({integer} not in [{low}, {high}])")
    return integer, fixes


def parse_boolean(raw: str, field: str) -> tuple[bool, tuple[str, ...]]:
    cleaned = raw.strip()
    key = cleaned.lower()
    if key not in BOOLEAN_VALUES:
        raise RowError(f"{field}: invalid boolean ({raw!r})")

    fixes: list[str] = []
    if raw != raw.strip():
        fixes.append(f"{field}: whitespace")
    if cleaned not in {"0", "1"}:
        fixes.append(f"{field}: boolean synonym")
    return BOOLEAN_VALUES[key], tuple(fixes)


def normalize_row(fields: Sequence[str]) -> tuple[CoffeeRecord, tuple[str, ...]]:
    """Validate and normalize one row. Raise RowError if it must be rejected."""
    if len(fields) != len(EXPECTED_HEADER):
        raise RowError(f"structure: expected {len(EXPECTED_HEADER)} columns, got {len(fields)}")

    fixes: list[str] = []

    record_id, value_fixes = parse_integer(fields[0], "ID")
    fixes.extend(value_fixes)
    age, value_fixes = parse_integer(fields[1], "Age")
    fixes.extend(value_fixes)

    gender, value_fixes = normalize_categorical(fields[2], "Gender", GENDER_VALUES)
    fixes.extend(value_fixes)
    country, value_fixes = normalize_country(fields[3])
    fixes.extend(value_fixes)

    coffee_intake, value_fixes = parse_decimal(fields[4], "Coffee_Intake")
    fixes.extend(value_fixes)
    caffeine_mg, value_fixes = parse_decimal(fields[5], "Caffeine_mg")
    fixes.extend(value_fixes)
    sleep_hours, value_fixes = parse_decimal(fields[6], "Sleep_Hours")
    fixes.extend(value_fixes)

    sleep_quality, value_fixes = normalize_categorical(
        fields[7], "Sleep_Quality", SLEEP_QUALITY_VALUES
    )
    fixes.extend(value_fixes)

    bmi, value_fixes = parse_decimal(fields[8], "BMI")
    fixes.extend(value_fixes)
    heart_rate, value_fixes = parse_integer(fields[9], "Heart_Rate")
    fixes.extend(value_fixes)

    stress_level, value_fixes = normalize_categorical(
        fields[10], "Stress_Level", STRESS_LEVEL_VALUES
    )
    fixes.extend(value_fixes)

    physical_activity_hours, value_fixes = parse_decimal(fields[11], "Physical_Activity_Hours")
    fixes.extend(value_fixes)

    health_issues, value_fixes = normalize_categorical(
        fields[12], "Health_Issues", HEALTH_ISSUES_VALUES
    )
    fixes.extend(value_fixes)
    occupation, value_fixes = normalize_categorical(fields[13], "Occupation", OCCUPATION_VALUES)
    fixes.extend(value_fixes)

    smoking, value_fixes = parse_boolean(fields[14], "Smoking")
    fixes.extend(value_fixes)
    alcohol_consumption, value_fixes = parse_boolean(fields[15], "Alcohol_Consumption")
    fixes.extend(value_fixes)

    if coffee_intake == 0 and caffeine_mg > MAX_DECAF_CAFFEINE_MG:
        raise RowError(
            f"logical: Coffee_Intake=0 but Caffeine_mg={caffeine_mg} (>{MAX_DECAF_CAFFEINE_MG})"
        )
    if coffee_intake > 0 and caffeine_mg == 0:
        raise RowError("logical: Coffee_Intake>0 but Caffeine_mg=0")

    record = CoffeeRecord(
        id=record_id,
        age=age,
        gender=gender,
        country=country,
        coffee_intake=coffee_intake,
        caffeine_mg=caffeine_mg,
        sleep_hours=sleep_hours,
        sleep_quality=sleep_quality,
        bmi=bmi,
        heart_rate=heart_rate,
        stress_level=stress_level,
        physical_activity_hours=physical_activity_hours,
        health_issues=health_issues,
        occupation=occupation,
        smoking=smoking,
        alcohol_consumption=alcohol_consumption,
    )
    return record, tuple(fixes)
