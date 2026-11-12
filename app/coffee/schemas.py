"""Pydantic models for the coffee health API.

Filter validation reuses the canonical values and normalization rules from
``app.coffee.normalization`` so the API accepts the same spellings as the
loader (for example ``male``, `` south korea `` or ``GOOD``).
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from app.coffee.constants import (
    DECIMAL_RANGES,
    GENDER_VALUES,
    HEALTH_ISSUES_VALUES,
    INTEGER_RANGES,
    OCCUPATION_VALUES,
    SLEEP_QUALITY_VALUES,
    STRESS_LEVEL_VALUES,
)
from app.coffee.normalization import normalize_categorical, normalize_country

AGE_MIN: Final = INTEGER_RANGES["Age"][0]
AGE_MAX: Final = INTEGER_RANGES["Age"][1]
COFFEE_MIN: Final = DECIMAL_RANGES["Coffee_Intake"][0]
COFFEE_MAX: Final = DECIMAL_RANGES["Coffee_Intake"][1]
BMI_MIN: Final = DECIMAL_RANGES["BMI"][0]
BMI_MAX: Final = DECIMAL_RANGES["BMI"][1]
SLEEP_MIN: Final = DECIMAL_RANGES["Sleep_Hours"][0]
SLEEP_MAX: Final = DECIMAL_RANGES["Sleep_Hours"][1]

DEFAULT_PAGE_SIZE: Final = 50
MAX_PAGE_SIZE: Final = 200

SummaryGroupBy = Literal[
    "country",
    "gender",
    "sleep_quality",
    "stress_level",
    "coffee_intake",
    "occupation",
]

CATEGORICAL_FILTER_VALUES: Final[Mapping[str, Mapping[str, str] | None]] = {
    "country": None,
    "gender": GENDER_VALUES,
    "sleep_quality": SLEEP_QUALITY_VALUES,
    "stress_level": STRESS_LEVEL_VALUES,
    "health_issues": HEALTH_ISSUES_VALUES,
    "occupation": OCCUPATION_VALUES,
}


class CoffeeFilters(BaseModel):
    """Filters shared by the list and summary endpoints.

    Every categorical filter accepts repeated values and matches any of them.
    Numeric filters are inclusive ranges. Values are validated against the same
    ranges enforced by the database and the loader.
    """

    country: list[str] | None = Field(
        default=None,
        description="Country name (repeatable). Case and surrounding spaces are ignored.",
    )
    gender: list[str] | None = Field(
        default=None,
        description="Male, Female or Other (repeatable).",
    )
    sleep_quality: list[str] | None = Field(
        default=None,
        description="Poor, Fair, Good or Excellent (repeatable).",
    )
    stress_level: list[str] | None = Field(
        default=None,
        description="Low, Medium or High (repeatable).",
    )
    health_issues: list[str] | None = Field(
        default=None,
        description="None, Mild, Moderate or Severe (repeatable).",
    )
    occupation: list[str] | None = Field(
        default=None,
        description="Office, Healthcare, Service, Student or Other (repeatable).",
    )
    smoking: bool | None = Field(default=None, description="Smoking status.")
    alcohol_consumption: bool | None = Field(
        default=None,
        description="Alcohol consumption status.",
    )

    min_age: int | None = Field(default=None, ge=AGE_MIN, le=AGE_MAX)
    max_age: int | None = Field(default=None, ge=AGE_MIN, le=AGE_MAX)
    min_coffee_intake: Decimal | None = Field(default=None, ge=COFFEE_MIN, le=COFFEE_MAX)
    max_coffee_intake: Decimal | None = Field(default=None, ge=COFFEE_MIN, le=COFFEE_MAX)
    min_bmi: Decimal | None = Field(default=None, ge=BMI_MIN, le=BMI_MAX)
    max_bmi: Decimal | None = Field(default=None, ge=BMI_MIN, le=BMI_MAX)
    min_sleep_hours: Decimal | None = Field(default=None, ge=SLEEP_MIN, le=SLEEP_MAX)
    max_sleep_hours: Decimal | None = Field(default=None, ge=SLEEP_MIN, le=SLEEP_MAX)

    @field_validator(*CATEGORICAL_FILTER_VALUES, mode="after")
    @classmethod
    def _normalize_categorical_filter(
        cls,
        values: list[str] | None,
        info: ValidationInfo,
    ) -> list[str] | None:
        if not values:
            return None

        field_name = info.field_name
        assert field_name is not None
        mapping = CATEGORICAL_FILTER_VALUES[field_name]
        if mapping is None:
            return sorted({normalize_country(value)[0] for value in values})
        return sorted({normalize_categorical(value, field_name, mapping)[0] for value in values})

    @model_validator(mode="after")
    def _validate_ranges(self) -> Self:
        for minimum_name, maximum_name in (
            ("min_age", "max_age"),
            ("min_coffee_intake", "max_coffee_intake"),
            ("min_bmi", "max_bmi"),
            ("min_sleep_hours", "max_sleep_hours"),
        ):
            minimum = getattr(self, minimum_name)
            maximum = getattr(self, maximum_name)
            if minimum is not None and maximum is not None and minimum > maximum:
                raise ValueError(
                    f"{minimum_name} ({minimum}) must be less than or equal to "
                    f"{maximum_name} ({maximum})"
                )
        return self


class CoffeeListQuery(CoffeeFilters):
    """Filters plus keyset pagination for ``GET /coffee``."""

    after_id: int | None = Field(
        default=None,
        ge=1,
        description="Return records with an id greater than this cursor.",
    )
    limit: int = Field(
        default=DEFAULT_PAGE_SIZE,
        ge=1,
        le=MAX_PAGE_SIZE,
        description="Maximum number of records in this page.",
    )


class CoffeeRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    age: int
    gender: str
    country: str
    coffee_intake: float
    caffeine_mg: float
    sleep_hours: float
    sleep_quality: str
    bmi: float
    heart_rate: int
    stress_level: str
    physical_activity_hours: float
    health_issues: str
    occupation: str
    smoking: bool
    alcohol_consumption: bool


class CoffeePage(BaseModel):
    items: list[CoffeeRecordOut]
    limit: int
    has_more: bool
    next_cursor: int | None = Field(
        default=None,
        description="Pass this value as after_id to fetch the next page.",
    )


class SummaryGroup(BaseModel):
    key: str
    count: int
    avg_sleep_hours: float
    avg_coffee_intake: float
    avg_caffeine_mg: float


class CoffeeSummary(BaseModel):
    group_by: SummaryGroupBy
    groups: list[SummaryGroup]
