from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.coffee.schemas import CoffeeFilters, CoffeeListQuery


def test_filters_normalize_case_and_whitespace() -> None:
    query = CoffeeListQuery(
        gender=[" female ", "MALE"],
        country=[" south korea ", "USA"],
        sleep_quality=["good"],
        stress_level=["HIGH"],
        health_issues=[" mild "],
        occupation=["office"],
    )

    assert query.gender == ["Female", "Male"]
    assert query.country == ["South Korea", "USA"]
    assert query.sleep_quality == ["Good"]
    assert query.stress_level == ["High"]
    assert query.health_issues == ["Mild"]
    assert query.occupation == ["Office"]


def test_repeated_values_are_deduplicated_after_normalization() -> None:
    query = CoffeeListQuery(gender=["male", "MALE", " male "])

    assert query.gender == ["Male"]


def test_default_pagination() -> None:
    query = CoffeeListQuery()

    assert query.limit == 50
    assert query.after_id is None


def test_pagination_values_are_validated() -> None:
    with pytest.raises(ValidationError):
        CoffeeListQuery(limit=0)
    with pytest.raises(ValidationError):
        CoffeeListQuery(limit=201)
    with pytest.raises(ValidationError):
        CoffeeListQuery(after_id=0)


def test_invalid_categorical_value_is_rejected() -> None:
    with pytest.raises(ValidationError, match="gender"):
        CoffeeFilters(gender=["alien"])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("min_age", 17),
        ("max_age", 101),
        ("min_coffee_intake", Decimal("-0.1")),
        ("max_coffee_intake", Decimal("10.1")),
        ("min_bmi", Decimal("9.9")),
        ("max_bmi", Decimal("60.1")),
        ("min_sleep_hours", Decimal("-1")),
        ("max_sleep_hours", Decimal("17")),
    ],
)
def test_values_outside_database_ranges_are_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        CoffeeFilters.model_validate({field: value})


@pytest.mark.parametrize(
    ("minimum", "maximum"),
    [
        ("min_age", "max_age"),
        ("min_coffee_intake", "max_coffee_intake"),
        ("min_bmi", "max_bmi"),
        ("min_sleep_hours", "max_sleep_hours"),
    ],
)
def test_minimum_greater_than_maximum_is_rejected(minimum: str, maximum: str) -> None:
    values: dict[str, object] = {minimum: 5, maximum: 2}

    with pytest.raises(ValidationError, match=minimum):
        CoffeeFilters.model_validate(values)
