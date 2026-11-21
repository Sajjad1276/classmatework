from fastapi.testclient import TestClient

from app.main import create_app


def test_liveness_reports_process_status() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_request_id_is_returned_to_caller() -> None:
    supplied_id = "trace-test-001"
    with TestClient(create_app()) as client:
        response = client.get(
            "/api/v1/health/live",
            headers={"X-Request-ID": supplied_id},
        )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == supplied_id


def test_invalid_request_id_is_replaced() -> None:
    with TestClient(create_app()) as client:
        response = client.get(
            "/api/v1/health/live",
            headers={"X-Request-ID": "bad request id with spaces"},
        )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] != "bad request id with spaces"
    assert len(response.headers["X-Request-ID"]) == 36
