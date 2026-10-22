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
        "storage_schema_version": 3,
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


def test_dashboard_summarizes_persisted_runs_without_exposing_candidate_text(
    tmp_path: Path,
) -> None:
    from evalforge.api import create_app

    suite_payload = _suite('dashboard-</td><script>alert("x")</script>')
    with TestClient(create_app(tmp_path / "api.db")) as client:
        suite = client.post("/api/v1/suites", json=suite_payload).json()
        evaluation = client.post(
            "/api/v1/evaluations",
            json={
                "suite_id": suite["suite_id"],
                "candidate_outputs": {"policy": "private-candidate-value"},
            },
        ).json()
        dashboard = client.get("/dashboard")

    assert dashboard.status_code == 200
    assert dashboard.headers["content-type"].startswith("text/html")
    assert "EvalForge run dashboard" in dashboard.text
    assert (
        "dashboard-&lt;/td&gt;&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in dashboard.text
    )
    assert "</td><script>" not in dashboard.text
    assert evaluation["run_id"] in dashboard.text
    assert f"/api/v1/reports/{evaluation['run_id']}" in dashboard.text
    assert "FAIL" in dashboard.text
    assert "private-candidate-value" not in dashboard.text
    assert dashboard.headers["cache-control"] == "no-store"
    assert dashboard.headers["content-security-policy"] == (
        "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'none'"
    )
    assert dashboard.headers["x-content-type-options"] == "nosniff"
    assert dashboard.headers["referrer-policy"] == "no-referrer"


def test_dashboard_compares_persisted_runs_without_rendering_private_outputs(
    tmp_path: Path,
) -> None:
    from evalforge.api import create_app

    suite_payload = {
        "schema_version": 1,
        "name": 'comparison-</td><script>alert("x")</script>',
        "minimum_pass_rate": 0.0,
        "cases": [
            {
                "case_id": "critical-policy",
                "prompt": "State the private policy.",
                "expected_output": "Allowed",
                "weight": 3,
                "category": "policy",
                "tags": ["critical-path"],
            },
            {
                "case_id": "supporting-context",
                "prompt": "Include the private context.",
                "expected_output": "context",
                "metric": "contains",
                "category": "quality",
                "tags": ["context"],
            },
        ],
    }
    with TestClient(create_app(tmp_path / "api.db")) as client:
        suite = client.post("/api/v1/suites", json=suite_payload).json()
        baseline = client.post(
            "/api/v1/evaluations",
            json={
                "suite_id": suite["suite_id"],
                "candidate_outputs": {
                    "critical-policy": "Allowed",
                    "supporting-context": "missing baseline-private-value",
                },
            },
        ).json()
        candidate = client.post(
            "/api/v1/evaluations",
            json={
                "suite_id": suite["suite_id"],
                "candidate_outputs": {
                    "critical-policy": "Denied candidate-private-value",
                    "supporting-context": "found context candidate-private-value",
                },
            },
        ).json()
        run_dashboard = client.get("/dashboard")
        dashboard = client.get(
            "/dashboard/compare",
            params={
                "baseline_run_id": baseline["run_id"],
                "candidate_run_id": candidate["run_id"],
            },
        )

    assert dashboard.status_code == 200
    assert run_dashboard.status_code == 200
    assert "/dashboard/compare?baseline_run_id=" in run_dashboard.text
    assert f"baseline_run_id={baseline['run_id']}" in run_dashboard.text
    assert f"candidate_run_id={candidate['run_id']}" in run_dashboard.text
    assert "EvalForge run comparison" in dashboard.text
    assert baseline["run_id"] in dashboard.text
    assert candidate["run_id"] in dashboard.text
    assert "Weighted pass rate" in dashboard.text
    assert "critical-policy" in dashboard.text
    assert (
        f"/dashboard/cases?run_id={candidate['run_id']}&amp;case_id=critical-policy"
        in dashboard.text
    )
    assert "regression" in dashboard.text
    assert "Category slices" in dashboard.text
    assert "critical-path" in dashboard.text
    assert (
        "comparison-&lt;/td&gt;&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in dashboard.text
    )
    assert "</td><script>" not in dashboard.text
    for private_value in (
        "baseline-private-value",
        "candidate-private-value",
        "State the private policy",
        "Include the private context",
    ):
        assert private_value not in dashboard.text
    assert dashboard.headers["cache-control"] == "no-store"
    assert dashboard.headers["content-security-policy"] == (
        "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'none'"
    )
    assert dashboard.headers["x-content-type-options"] == "nosniff"
    assert dashboard.headers["referrer-policy"] == "no-referrer"


def test_dashboard_case_detail_renders_bounded_evidence_without_private_text(
    tmp_path: Path,
) -> None:
    from evalforge.api import create_app

    private_case_id = 'case</td><script>alert("case")</script>'
    suite_payload = {
        "schema_version": 1,
        "name": 'detail-</strong><script>alert("suite")</script>',
        "minimum_pass_rate": 1.0,
        "cases": [
            {
                "case_id": private_case_id,
                "prompt": "private prompt must not render",
                "expected_output": "private expected must not render",
                "threshold": 0.75,
                "weight": 2,
                "severity": "critical",
                "category": "policy",
                "tags": ["customer-impact", "regression"],
            }
        ],
    }
    with TestClient(create_app(tmp_path / "api.db")) as client:
        suite = client.post("/api/v1/suites", json=suite_payload).json()
        evaluation = client.post(
            "/api/v1/evaluations",
            json={
                "suite_id": suite["suite_id"],
                "candidate_outputs": {
                    private_case_id: {
                        "output": "private candidate must not render",
                        "latency_ms": 123,
                        "cost_micro_usd": 456,
                    }
                },
            },
        ).json()
        response = client.get(
            "/dashboard/cases",
            params={"run_id": evaluation["run_id"], "case_id": private_case_id},
        )

    assert response.status_code == 200
    assert "EvalForge case evidence" in response.text
    assert evaluation["run_id"] in response.text
    assert (
        "detail-&lt;/strong&gt;&lt;script&gt;alert(&quot;suite&quot;)&lt;/script&gt;"
        in response.text
    )
    assert "case&lt;/td&gt;&lt;script&gt;alert(&quot;case&quot;)&lt;/script&gt;" in response.text
    assert "FAIL" in response.text
    assert "0.00%" in response.text
    assert "75.00%" in response.text
    assert "case" in response.text
    assert "critical" in response.text
    assert "policy" in response.text
    assert "customer-impact" in response.text
    assert "regression" in response.text
    assert "123 ms" in response.text
    assert "456 micro-USD" in response.text
    for private_value in (
        "private prompt must not render",
        "private expected must not render",
        "private candidate must not render",
    ):
        assert private_value not in response.text
    assert "</td><script>" not in response.text
    assert "</strong><script>" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-security-policy"].startswith("default-src 'none'")


def test_dashboard_case_detail_treats_absent_case_as_missing_evidence(tmp_path: Path) -> None:
    from evalforge.api import create_app

    with TestClient(create_app(tmp_path / "api.db")) as client:
        suite = client.post("/api/v1/suites", json=_suite("case-detail-missing")).json()
        run_id = client.post(
            "/api/v1/evaluations",
            json={"suite_id": suite["suite_id"], "candidate_outputs": {"policy": "Allowed"}},
        ).json()["run_id"]
        response = client.get(
            "/dashboard/cases",
            params={"run_id": run_id, "case_id": "absent-private-case"},
        )

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "case_evidence_not_found", "message": "case evidence was not found"}
    }
    assert "case-detail-missing" not in response.text


def test_dashboard_case_detail_fails_closed_when_stored_evidence_is_corrupt(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    from dataclasses import replace

    from evalforge.api import create_app

    app = create_app(tmp_path / "api.db")
    with TestClient(app, raise_server_exceptions=False) as client:
        suite = client.post("/api/v1/suites", json=_suite("case-detail-corrupt")).json()
        run_id = client.post(
            "/api/v1/evaluations",
            json={"suite_id": suite["suite_id"], "candidate_outputs": {"policy": "Allowed"}},
        ).json()["run_id"]
        stored = app.state.service.registry.get_run(tenant_id="__local__", run_id=run_id)
        assert stored is not None
        corrupt = replace(stored, candidate={"policy": "private-corrupt-case-value"})
        monkeypatch.setattr(  # type: ignore[attr-defined]
            app.state.service.registry,
            "get_run",
            lambda **_kwargs: corrupt,
        )

        response = client.get(
            "/dashboard/cases",
            params={"run_id": run_id, "case_id": "policy"},
        )

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "internal service error"}
    }
    assert "private-corrupt-case-value" not in response.text


def test_dashboard_comparison_rejects_visible_runs_from_different_suites(tmp_path: Path) -> None:
    from evalforge.api import create_app

    with TestClient(create_app(tmp_path / "api.db")) as client:
        first_suite = client.post("/api/v1/suites", json=_suite("first-suite")).json()
        second_suite = client.post("/api/v1/suites", json=_suite("second-suite")).json()
        first_run = client.post(
            "/api/v1/evaluations",
            json={"suite_id": first_suite["suite_id"], "candidate_outputs": {"policy": "Allowed"}},
        ).json()
        second_run = client.post(
            "/api/v1/evaluations",
            json={
                "suite_id": second_suite["suite_id"],
                "candidate_outputs": {"policy": "Allowed"},
            },
        ).json()
        response = client.get(
            "/dashboard/compare",
            params={
                "baseline_run_id": first_run["run_id"],
                "candidate_run_id": second_run["run_id"],
            },
        )

    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "runs_not_comparable",
            "message": "evaluation runs are not comparable",
        }
    }


def test_dashboard_trend_chains_recorded_runs_into_pairwise_evidence(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    from itertools import count

    from evalforge import registry as registry_module
    from evalforge.api import create_app

    ticks = count()
    monkeypatch.setattr(  # type: ignore[attr-defined]
        registry_module,
        "_utc_now",
        lambda: f"2026-01-01T00:00:00.{next(ticks):06d}Z",
    )
    with TestClient(create_app(tmp_path / "api.db")) as client:
        suite = client.post(
            "/api/v1/suites",
            json=_suite('trend-</strong><script>alert("x")</script>'),
        ).json()
        runs = [
            client.post(
                "/api/v1/evaluations",
                json={
                    "suite_id": suite["suite_id"],
                    "candidate_outputs": {"policy": output},
                },
            ).json()["run_id"]
            for output in ("Allowed", "Denied", " allowed ")
        ]
        other_suite = client.post("/api/v1/suites", json=_suite("unrelated-suite")).json()
        unrelated_run = client.post(
            "/api/v1/evaluations",
            json={
                "suite_id": other_suite["suite_id"],
                "candidate_outputs": {"policy": "Allowed"},
            },
        ).json()["run_id"]

        response = client.get("/dashboard/trends", params={"run_id": runs[-1]})
        run_dashboard = client.get("/dashboard")

    assert response.status_code == 200
    assert "Recorded unique-run history" in response.text
    assert unrelated_run not in response.text
    assert "trend-&lt;/strong&gt;&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in response.text
    assert "</strong><script>" not in response.text
    assert (
        response.text.index(runs[0]) < response.text.index(runs[1]) < response.text.index(runs[2])
    )
    assert f"baseline_run_id={runs[0]}&amp;candidate_run_id={runs[1]}" in response.text
    assert f"baseline_run_id={runs[1]}&amp;candidate_run_id={runs[2]}" in response.text
    assert "-100.00%" in response.text
    assert "+100.00%" in response.text
    assert response.headers["cache-control"] == "no-store"
    assert "view history" in run_dashboard.text


def test_dashboard_trend_fails_closed_when_stored_history_is_corrupt(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    from dataclasses import replace

    from evalforge.api import create_app

    app = create_app(tmp_path / "api.db")
    with TestClient(app, raise_server_exceptions=False) as client:
        suite = client.post("/api/v1/suites", json=_suite("corrupt-trend")).json()
        run_id = client.post(
            "/api/v1/evaluations",
            json={
                "suite_id": suite["suite_id"],
                "candidate_outputs": {"policy": "Allowed"},
            },
        ).json()["run_id"]
        stored = app.state.service.registry.get_run(tenant_id="__local__", run_id=run_id)
        assert stored is not None
        corrupt = replace(stored, candidate={"policy": "private-corrupt-value"})
        monkeypatch.setattr(  # type: ignore[attr-defined]
            app.state.service.registry,
            "list_compatible_runs",
            lambda **_kwargs: (corrupt,),
        )

        response = client.get("/dashboard/trends", params={"run_id": run_id})

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "internal service error"}
    }
    assert "private-corrupt-value" not in response.text
