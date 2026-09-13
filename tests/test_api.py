from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def _suite(name: str = "api-smoke") -> dict[str, object]:
    return {
        "schema_version": 1,
        "name": name,
        "minimum_pass_rate": 1.0,
        "cases": [
            {
                "case_id": "policy",
                "prompt": "State the synthetic policy.",
                "expected_output": "Allowed",
            }
        ],
    }


def test_health_endpoint_reports_versioned_service_readiness(tmp_path: Path) -> None:
    from evalforge.api import create_app

    with TestClient(create_app(tmp_path / "api.db")) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "api_version": "v1",
        "status": "ok",
        "storage_schema_version": 1,
    }


def test_suite_endpoints_store_and_page_versioned_contracts(tmp_path: Path) -> None:
    from evalforge.api import create_app

    with TestClient(create_app(tmp_path / "api.db")) as client:
        first = client.post("/api/v1/suites", json=_suite("first-suite"))
        second = client.post("/api/v1/suites", json=_suite("second-suite"))
        page = client.get("/api/v1/suites", params={"limit": 1, "offset": 1})

    assert first.status_code == 201
    assert len(first.json()["suite_id"]) == 64
    assert first.json()["suite"]["name"] == "first-suite"
    assert second.status_code == 201
    assert page.status_code == 200
    assert page.json()["limit"] == 1
    assert page.json()["offset"] == 1
    assert page.json()["total"] == 2
    assert len(page.json()["items"]) == 1
    assert page.json()["items"][0]["suite"]["name"] in {"first-suite", "second-suite"}


def test_evaluation_endpoint_reuses_core_and_persists_deterministic_report(tmp_path: Path) -> None:
    from evalforge.api import create_app

    with TestClient(create_app(tmp_path / "api.db")) as client:
        suite_response = client.post("/api/v1/suites", json=_suite())
        response = client.post(
            "/api/v1/evaluations",
            json={
                "suite_id": suite_response.json()["suite_id"],
                "candidate_outputs": {"policy": " allowed "},
            },
        )

    assert response.status_code == 201
    body = response.json()
    assert len(body["run_id"]) == 64
    assert body["report"]["run_id"] == body["run_id"]
    assert body["report"]["suite_name"] == "api-smoke"
    assert body["report"]["release_ready"] is True
    assert body["report"]["passed_cases"] == 1
    assert body["report"]["results"][0]["case_id"] == "policy"


def test_run_and_report_endpoints_return_bounded_persisted_evidence(tmp_path: Path) -> None:
    from evalforge.api import create_app

    with TestClient(create_app(tmp_path / "api.db")) as client:
        suite = client.post("/api/v1/suites", json=_suite()).json()
        evaluation = client.post(
            "/api/v1/evaluations",
            json={
                "suite_id": suite["suite_id"],
                "candidate_outputs": {"policy": "Denied"},
            },
        ).json()
        run_id = evaluation["run_id"]
        run_page = client.get("/api/v1/runs", params={"limit": 1, "offset": 0})
        run = client.get(f"/api/v1/runs/{run_id}")
        report = client.get(f"/api/v1/reports/{run_id}")

    assert run_page.status_code == 200
    assert run_page.json()["total"] == 1
    assert run_page.json()["items"][0]["run_id"] == run_id
    assert run.status_code == 200
    assert run.json()["suite"]["name"] == "api-smoke"
    assert run.json()["candidate"] == {"policy": "Denied"}
    assert report.status_code == 200
    assert report.json()["run_id"] == run_id
    assert report.json()["release_ready"] is False


def test_invalid_evaluation_maps_to_safe_client_error_without_input_echo(tmp_path: Path) -> None:
    from evalforge.api import create_app

    with TestClient(create_app(tmp_path / "api.db")) as client:
        suite = client.post("/api/v1/suites", json=_suite()).json()
        response = client.post(
            "/api/v1/evaluations",
            json={
                "suite_id": suite["suite_id"],
                "candidate_outputs": {"unexpected": "customer-secret-value"},
            },
        )

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "invalid_evaluation",
            "message": "evaluation input is invalid",
        }
    }
    assert "customer-secret-value" not in response.text
    assert "Traceback" not in response.text


def test_unexpected_failure_maps_to_safe_server_error(tmp_path: Path, monkeypatch: object) -> None:
    from evalforge.api import create_app

    app = create_app(tmp_path / "api.db")
    service = app.state.service

    def fail_evaluation(**_kwargs: object) -> object:
        raise RuntimeError("database password=do-not-leak")

    monkeypatch.setattr(service, "evaluate", fail_evaluation)  # type: ignore[attr-defined]
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/v1/evaluations",
            json={"suite_id": "0" * 64, "candidate_outputs": {"policy": "Allowed"}},
        )

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "internal service error"}
    }
    assert "password" not in response.text
    assert "Traceback" not in response.text


def test_request_validation_error_does_not_echo_rejected_values(tmp_path: Path) -> None:
    from evalforge.api import create_app

    payload = _suite()
    payload["name"] = "customer-secret-name"
    payload["schema_version"] = "1"
    with TestClient(create_app(tmp_path / "api.db")) as client:
        response = client.post("/api/v1/suites", json=payload)

    assert response.status_code == 422
    assert response.json() == {
        "error": {"code": "validation_error", "message": "request validation failed"}
    }
    assert "customer-secret-name" not in response.text
