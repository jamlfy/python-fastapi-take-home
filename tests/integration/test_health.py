from fastapi.testclient import TestClient


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_coffee_router_is_mounted(client: TestClient) -> None:
    response = client.get("/coffee", params={"limit": 1})
    assert response.status_code == 200
    assert set(response.json()) == {"items", "limit", "has_more", "next_cursor"}
