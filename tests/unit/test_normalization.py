from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path

import pytest

from app.coffee.constants import EXPECTED_HEADER
from app.coffee.loader import iter_rows
from app.coffee.normalization import (
    RowError,
    normalize_row,
    parse_boolean,
    parse_decimal,
    parse_integer,
)
from app.models import CoffeeRecord

FIXTURE = Path(__file__).parents[1] / "fixtures" / "dirty_rows.csv"

BASE_ROW: Mapping[str, str] = {
    "ID": "1",
    "Age": "40",
    "Gender": "Male",
    "Country": "Germany",
    "Coffee_Intake": "2.0",
    "Caffeine_mg": "190.0",
    "Sleep_Hours": "7.0",
    "Sleep_Quality": "Good",
    "BMI": "22.0",
    "Heart_Rate": "65",
    "Stress_Level": "Low",
    "Physical_Activity_Hours": "5.0",
    "Health_Issues": "None",
    "Occupation": "Other",
    "Smoking": "0",
    "Alcohol_Consumption": "0",
}


def make_fields(overrides: Mapping[str, str] | None = None) -> tuple[str, ...]:
    values = dict(BASE_ROW)
    if overrides is not None:
        values.update(overrides)
    return tuple(values[column] for column in EXPECTED_HEADER)


def test_normal_clean_row_has_no_fixes() -> None:
    record, fixes = normalize_row(make_fields())

    assert isinstance(record, CoffeeRecord)
    assert fixes == ()
    assert record.id == 1
    assert record.gender == "Male"
    assert record.country == "Germany"
    assert record.smoking is False
    assert record.alcohol_consumption is False


def test_text_whitespace_and_case_are_corrected() -> None:
    record, fixes = normalize_row(
        make_fields(
            {
                "Gender": " male ",
                "Country": " south korea ",
                "Sleep_Quality": " good ",
                "Stress_Level": " LOW ",
                "Health_Issues": " none ",
                "Occupation": " office ",
            }
        )
    )

    assert record.gender == "Male"
    assert record.country == "South Korea"
    assert record.sleep_quality == "Good"
    assert record.stress_level == "Low"
    assert record.health_issues == "None"
    assert record.occupation == "Office"
    assert set(fixes) == {
        "Gender: normalized",
        "Country: normalized",
        "Sleep_Quality: normalized",
        "Stress_Level: normalized",
        "Health_Issues: normalized",
        "Occupation: normalized",
    }


def test_boolean_synonyms_are_corrected() -> None:
    record, fixes = normalize_row(make_fields({"Smoking": "YES", "Alcohol_Consumption": "no"}))

    assert record.smoking is True
    assert record.alcohol_consumption is False
    assert "Smoking: boolean synonym" in fixes
    assert "Alcohol_Consumption: boolean synonym" in fixes


def test_dirty_decimal_formats_are_corrected() -> None:
    coffee, coffee_fixes = parse_decimal("7,5", "Coffee_Intake")
    caffeine, caffeine_fixes = parse_decimal("712,5 mg", "Caffeine_mg")

    assert coffee == Decimal("7.5")
    assert caffeine == Decimal("712.5")
    assert "Coffee_Intake: decimal comma" in coffee_fixes
    assert "Caffeine_mg: decimal comma" in caffeine_fixes
    assert "Caffeine_mg: unit suffix" in caffeine_fixes


def test_unit_suffix_is_corrected() -> None:
    value, fixes = parse_decimal("3.5 cups", "Coffee_Intake")

    assert value == Decimal("3.5")
    assert "Coffee_Intake: unit suffix" in fixes


@pytest.mark.parametrize("raw", ["-1", "-0.1", "25", "seven", "", "4O"])
def test_invalid_numbers_are_rejected(raw: str) -> None:
    with pytest.raises(RowError):
        parse_decimal(raw, "Sleep_Hours")


def test_non_integer_is_rejected() -> None:
    with pytest.raises(RowError):
        parse_integer("40.5", "Age")


@pytest.mark.parametrize("raw", ["-1", "2", "None", ""])
def test_invalid_booleans_are_rejected(raw: str) -> None:
    with pytest.raises(RowError):
        parse_boolean(raw, "Smoking")


def test_boolean_zero_and_one_mean_false_and_true() -> None:
    assert parse_boolean("0", "Smoking")[0] is False
    assert parse_boolean("1", "Smoking")[0] is True


def test_logical_inconsistency_coffee_zero_with_high_caffeine_is_rejected() -> None:
    with pytest.raises(RowError, match="logical"):
        normalize_row(make_fields({"Coffee_Intake": "0", "Caffeine_mg": "400"}))


def test_small_residual_caffeine_is_allowed() -> None:
    record, _ = normalize_row(make_fields({"Coffee_Intake": "0", "Caffeine_mg": "4.0"}))

    assert record.coffee_intake == Decimal("0")
    assert record.caffeine_mg == Decimal("4.0")


def test_coffee_with_zero_caffeine_is_rejected() -> None:
    with pytest.raises(RowError, match="logical"):
        normalize_row(make_fields({"Coffee_Intake": "2", "Caffeine_mg": "0"}))


@pytest.mark.parametrize("field, value", [("Age", "17"), ("Age", "101"), ("Heart_Rate", "29")])
def test_out_of_range_is_rejected(field: str, value: str) -> None:
    with pytest.raises(RowError, match="out of range"):
        normalize_row(make_fields({field: value}))


@pytest.mark.parametrize(
    "column", ["Gender", "Sleep_Quality", "Stress_Level", "Health_Issues", "Occupation"]
)
def test_invalid_categorical_is_rejected(column: str) -> None:
    with pytest.raises(RowError):
        normalize_row(make_fields({column: "unknown"}))


def test_missing_and_extra_columns_are_rejected() -> None:
    with pytest.raises(RowError, match="structure"):
        normalize_row(make_fields()[:-1])
    with pytest.raises(RowError, match="structure"):
        normalize_row((*make_fields(), "extra"))


def test_dirty_fixture_contains_expected_valid_and_invalid_rows() -> None:
    accepted: list[int] = []
    rejected: list[tuple[int, str]] = []

    for line_number, fields in iter_rows(FIXTURE):
        if not fields:
            continue
        try:
            normalize_row(fields)
        except RowError as exc:
            rejected.append((line_number, str(exc)))
        else:
            accepted.append(line_number)

    assert len(accepted) == 8
    assert len(rejected) == 8
    assert rejected[0][0] == 9
    assert rejected[-1][0] == 17
