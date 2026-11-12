from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_list_filters_by_country_case_insensitively(
    client: TestClient,
    seeded: None,
) -> None:
    response = client.get("/coffee", params={"country": "testland", "limit": 10})

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [910_001, 910_003, 910_004]
    assert body["has_more"] is False
    assert body["next_cursor"] is None


def test_list_combines_categorical_and_numeric_filters(
    client: TestClient,
    seeded: None,
) -> None:
    response = client.get(
        "/coffee",
        params=[
            ("country", "Testland"),
            ("gender", "female"),
            ("min_age", 30),
        ],
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [910_003]


def test_list_filters_by_numeric_range_and_boolean(
    client: TestClient,
    seeded: None,
) -> None:
    by_coffee = client.get(
        "/coffee",
        params=[
            ("country", "Testland"),
            ("min_coffee_intake", "1"),
            ("max_coffee_intake", "5"),
        ],
    )
    assert [item["id"] for item in by_coffee.json()["items"]] == [910_001, 910_003]

    by_alcohol = client.get(
        "/coffee",
        params=[("country", "Testland"), ("alcohol_consumption", "true")],
    )
    assert [item["id"] for item in by_alcohol.json()["items"]] == [910_003]


def test_list_uses_keyset_pagination(
    client: TestClient,
    seeded: None,
) -> None:
    first = client.get(
        "/coffee",
        params=[("country", "Testland"), ("limit", 2)],
    ).json()

    assert [item["id"] for item in first["items"]] == [910_001, 910_003]
    assert first["has_more"] is True
    assert first["next_cursor"] == 910_003

    second = client.get(
        "/coffee",
        params=[
            ("country", "Testland"),
            ("limit", 2),
            ("after_id", first["next_cursor"]),
        ],
    ).json()

    assert [item["id"] for item in second["items"]] == [910_004]
    assert second["has_more"] is False
    assert second["next_cursor"] is None


def test_list_returns_an_empty_page_when_nothing_matches(
    client: TestClient,
    seeded: None,
) -> None:
    response = client.get("/coffee", params={"country": "Nowhere"})

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["has_more"] is False
    assert body["next_cursor"] is None


def test_sql_injection_payloads_are_not_executed(
    client: TestClient,
    seeded: None,
) -> None:
    # This payload passes country validation, so it reaches the query as a value.
    # If it were concatenated into SQL it would match every row; as a parameter
    # it is just a country name that does not exist.
    response = client.get(
        "/coffee",
        params={"country": "Testland' OR true --", "limit": 10},
    )
    assert response.status_code == 200
    assert response.json()["items"] == []

    # Pagination and grouping are typed / allowlisted, so SQL never reaches them.
    assert client.get("/coffee", params={"after_id": "1 OR 1=1"}).status_code == 422
    assert client.get("/coffee", params={"limit": "1; drop table coffee_health"}).status_code == 422
    assert (
        client.get(
            "/coffee/summary",
            params={"group_by": "id; drop table coffee_health;--"},
        ).status_code
        == 422
    )

    # The table and the seeded rows survived all of the above.
    response = client.get("/coffee", params={"country": "Testland"})
    assert [item["id"] for item in response.json()["items"]] == [910_001, 910_003, 910_004]


def test_list_returns_full_records_with_booleans(
    client: TestClient,
    seeded: None,
) -> None:
    response = client.get("/coffee", params={"country": "Testonia", "limit": 1})

    item = response.json()["items"][0]
    assert item["coffee_intake"] == 3.0
    assert item["smoking"] is True
    assert item["alcohol_consumption"] is False
    assert set(item) == {
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
    }


@pytest.mark.parametrize(
    "params",
    [
        [("min_age", 50), ("max_age", 40)],
        [("min_coffee_intake", "8"), ("max_coffee_intake", "2")],
        [("min_bmi", "40"), ("max_bmi", "20")],
        [("min_sleep_hours", "8"), ("max_sleep_hours", "4")],
        [("min_age", 17)],
        [("gender", "alien")],
        [("limit", 0)],
        [("limit", 201)],
        [("after_id", 0)],
    ],
)
def test_invalid_query_parameters_return_422(
    client: TestClient,
    seeded: None,
    params: list[tuple[str, str | int | float | bool | None]],
) -> None:
    response = client.get("/coffee", params=params)

    assert response.status_code == 422


def test_min_age_error_names_the_invalid_field(
    client: TestClient,
    seeded: None,
) -> None:
    response = client.get("/coffee", params=[("min_age", 50), ("max_age", 40)])

    assert response.status_code == 422
    assert "min_age" in response.text
