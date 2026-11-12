from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_get_one_record_returns_the_full_record(client: TestClient, seeded: None) -> None:
    response = client.get("/coffee/910002")

    assert response.status_code == 200
    item = response.json()
    assert item["id"] == 910_002
    assert item["country"] == "Testonia"
    assert item["gender"] == "Male"
    assert item["smoking"] is True
    assert item["alcohol_consumption"] is False


def test_get_one_record_matches_the_list_item(client: TestClient, seeded: None) -> None:
    one = client.get("/coffee/910001").json()
    listed = client.get(
        "/coffee",
        params=[("country", "Testland"), ("limit", 1)],
    ).json()["items"][0]

    assert one == listed


def test_get_one_record_returns_404_when_missing(client: TestClient, seeded: None) -> None:
    response = client.get("/coffee/123456789")

    assert response.status_code == 404
    assert "123456789" in response.text


@pytest.mark.parametrize("record_id", ["0", "-1", "not-a-number"])
def test_get_one_record_rejects_invalid_ids(
    client: TestClient,
    seeded: None,
    record_id: str,
) -> None:
    response = client.get(f"/coffee/{record_id}")

    assert response.status_code == 422
