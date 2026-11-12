from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_summary_groups_by_gender_using_the_same_filters(
    client: TestClient,
    seeded: None,
) -> None:
    response = client.get(
        "/coffee/summary",
        params=[("country", "Testland"), ("group_by", "gender")],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["group_by"] == "gender"
    groups = {group["key"]: group for group in body["groups"]}

    assert groups["Female"]["count"] == 2
    assert groups["Female"]["avg_sleep_hours"] == pytest.approx(6.5)
    assert groups["Male"]["count"] == 1
    assert groups["Male"]["avg_sleep_hours"] == pytest.approx(7.5)


def test_summary_groups_by_country(client: TestClient, seeded: None) -> None:
    response = client.get(
        "/coffee/summary",
        params=[
            ("country", "Testland"),
            ("country", "Testonia"),
            ("group_by", "country"),
        ],
    )

    assert response.status_code == 200
    groups = {group["key"]: group for group in response.json()["groups"]}
    assert groups["Testland"]["count"] == 3
    assert groups["Testonia"]["count"] == 1


def test_summary_rejects_an_unknown_group_by(
    client: TestClient,
    seeded: None,
) -> None:
    response = client.get("/coffee/summary", params={"group_by": "id"})

    assert response.status_code == 422
