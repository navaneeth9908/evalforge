from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def _suite(name: str = "tenant-suite") -> dict[str, object]:
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


def _authorization(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_suite_page_contains_only_authenticated_tenant_memberships(tmp_path: Path) -> None:
    from evalforge.api import create_app
    from evalforge.auth import AccessCredential

    alpha_token = "tenant-alpha-editor-token-0000000001"
    beta_token = "tenant-beta-reader-token-00000000001"
    app = create_app(
        tmp_path / "tenant-isolation.db",
        access_credentials=(
            AccessCredential(token=alpha_token, tenant_id="tenant-alpha", role="editor"),
            AccessCredential(token=beta_token, tenant_id="tenant-beta", role="reader"),
        ),
    )

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/suites",
            headers=_authorization(alpha_token),
            json=_suite(),
        )
        alpha_page = client.get(
            "/api/v1/suites",
            headers=_authorization(alpha_token),
        )
        beta_page = client.get(
            "/api/v1/suites",
            headers=_authorization(beta_token),
        )

    assert created.status_code == 201
    assert alpha_page.status_code == 200
    assert alpha_page.json()["total"] == 1
    assert [item["suite_id"] for item in alpha_page.json()["items"]] == [created.json()["suite_id"]]
    assert beta_page.status_code == 200
    assert beta_page.json()["total"] == 0
    assert beta_page.json()["items"] == []


def test_evaluation_treats_foreign_and_absent_suite_ids_identically(tmp_path: Path) -> None:
    from evalforge.api import create_app
    from evalforge.auth import AccessCredential

    alpha_token = "tenant-alpha-editor-token-0000000001"
    beta_token = "tenant-beta-editor-token-00000000001"
    app = create_app(
        tmp_path / "tenant-isolation.db",
        access_credentials=(
            AccessCredential(token=alpha_token, tenant_id="tenant-alpha", role="editor"),
            AccessCredential(token=beta_token, tenant_id="tenant-beta", role="editor"),
        ),
    )

    with TestClient(app) as client:
        suite_id = client.post(
            "/api/v1/suites",
            headers=_authorization(alpha_token),
            json=_suite("private-alpha-suite"),
        ).json()["suite_id"]
        foreign = client.post(
            "/api/v1/evaluations",
            headers=_authorization(beta_token),
            json={"suite_id": suite_id, "candidate_outputs": {"policy": "Allowed"}},
        )
        absent = client.post(
            "/api/v1/evaluations",
            headers=_authorization(beta_token),
            json={"suite_id": "0" * 64, "candidate_outputs": {"policy": "Allowed"}},
        )

    expected = {
        "error": {
            "code": "suite_not_found",
            "message": "evaluation suite was not found",
        }
    }
    assert foreign.status_code == absent.status_code == 404
    assert foreign.json() == absent.json() == expected


def test_run_report_and_dashboard_views_are_tenant_scoped(tmp_path: Path) -> None:
    from evalforge.api import create_app
    from evalforge.auth import AccessCredential

    alpha_token = "tenant-alpha-editor-token-0000000001"
    beta_token = "tenant-beta-editor-token-00000000001"
    app = create_app(
        tmp_path / "tenant-isolation.db",
        access_credentials=(
            AccessCredential(token=alpha_token, tenant_id="tenant-alpha", role="editor"),
            AccessCredential(token=beta_token, tenant_id="tenant-beta", role="editor"),
        ),
    )

    with TestClient(app) as client:
        suite_payload = _suite("private-alpha-run")
        alpha_suite = client.post(
            "/api/v1/suites",
            headers=_authorization(alpha_token),
            json=suite_payload,
        ).json()
        alpha_run = client.post(
            "/api/v1/evaluations",
            headers=_authorization(alpha_token),
            json={
                "suite_id": alpha_suite["suite_id"],
                "candidate_outputs": {"policy": "Denied"},
            },
        ).json()
        run_id = alpha_run["run_id"]

        beta_page = client.get("/api/v1/runs", headers=_authorization(beta_token))
        beta_run = client.get(
            f"/api/v1/runs/{run_id}",
            headers=_authorization(beta_token),
        )
        beta_report = client.get(
            f"/api/v1/reports/{run_id}",
            headers=_authorization(beta_token),
        )
        absent_run = client.get(
            f"/api/v1/runs/{'0' * 64}",
            headers=_authorization(beta_token),
        )
        absent_report = client.get(
            f"/api/v1/reports/{'0' * 64}",
            headers=_authorization(beta_token),
        )
        beta_dashboard = client.get("/dashboard", headers=_authorization(beta_token))

        beta_suite = client.post(
            "/api/v1/suites",
            headers=_authorization(beta_token),
            json=suite_payload,
        ).json()
        beta_evaluation = client.post(
            "/api/v1/evaluations",
            headers=_authorization(beta_token),
            json={
                "suite_id": beta_suite["suite_id"],
                "candidate_outputs": {"policy": "Denied"},
            },
        )
        beta_visible_run = client.get(
            f"/api/v1/runs/{run_id}",
            headers=_authorization(beta_token),
        )

    assert beta_page.status_code == 200
    assert beta_page.json() == {"items": [], "limit": 50, "offset": 0, "total": 0}
    expected_run_error = {
        "error": {"code": "run_not_found", "message": "evaluation run was not found"}
    }
    assert beta_run.status_code == absent_run.status_code == 404
    assert beta_run.json() == absent_run.json() == expected_run_error
    expected_report_error = {
        "error": {"code": "report_not_found", "message": "evaluation report was not found"}
    }
    assert beta_report.status_code == absent_report.status_code == 404
    assert beta_report.json() == absent_report.json() == expected_report_error
    assert run_id not in beta_dashboard.text
    assert "private-alpha-run" not in beta_dashboard.text
    assert beta_evaluation.status_code == 201
    assert beta_evaluation.json()["run_id"] == run_id
    assert beta_visible_run.status_code == 200
    assert beta_visible_run.json()["run_id"] == run_id


def test_run_comparison_treats_foreign_and_absent_run_ids_identically(tmp_path: Path) -> None:
    from evalforge.api import create_app
    from evalforge.auth import AccessCredential

    alpha_token = "tenant-alpha-editor-token-0000000001"
    beta_token = "tenant-beta-editor-token-00000000001"
    app = create_app(
        tmp_path / "tenant-isolation.db",
        access_credentials=(
            AccessCredential(token=alpha_token, tenant_id="tenant-alpha", role="editor"),
            AccessCredential(token=beta_token, tenant_id="tenant-beta", role="editor"),
        ),
    )

    with TestClient(app) as client:
        alpha_suite = client.post(
            "/api/v1/suites",
            headers=_authorization(alpha_token),
            json=_suite("private-alpha-comparison"),
        ).json()
        alpha_run = client.post(
            "/api/v1/evaluations",
            headers=_authorization(alpha_token),
            json={"suite_id": alpha_suite["suite_id"], "candidate_outputs": {"policy": "Denied"}},
        ).json()["run_id"]
        beta_suite = client.post(
            "/api/v1/suites",
            headers=_authorization(beta_token),
            json=_suite("beta-comparison"),
        ).json()
        beta_run = client.post(
            "/api/v1/evaluations",
            headers=_authorization(beta_token),
            json={"suite_id": beta_suite["suite_id"], "candidate_outputs": {"policy": "Denied"}},
        ).json()["run_id"]
        absent = "0" * 64
        requests = (
            {"baseline_run_id": alpha_run, "candidate_run_id": beta_run},
            {"baseline_run_id": beta_run, "candidate_run_id": alpha_run},
            {"baseline_run_id": absent, "candidate_run_id": beta_run},
            {"baseline_run_id": beta_run, "candidate_run_id": absent},
        )
        responses = [
            client.get(
                "/dashboard/compare",
                headers=_authorization(beta_token),
                params=params,
            )
            for params in requests
        ]

    expected = {
        "error": {
            "code": "run_comparison_not_found",
            "message": "run comparison was not found",
        }
    }
    assert all(response.status_code == 404 for response in responses)
    assert all(response.json() == expected for response in responses)
    assert all("private-alpha-comparison" not in response.text for response in responses)


def test_run_trend_treats_foreign_and_absent_anchor_ids_identically(tmp_path: Path) -> None:
    from evalforge.api import create_app
    from evalforge.auth import AccessCredential

    alpha_token = "tenant-alpha-editor-token-0000000001"
    beta_token = "tenant-beta-reader-token-00000000001"
    app = create_app(
        tmp_path / "tenant-trends.db",
        access_credentials=(
            AccessCredential(token=alpha_token, tenant_id="tenant-alpha", role="editor"),
            AccessCredential(token=beta_token, tenant_id="tenant-beta", role="reader"),
        ),
    )
    with TestClient(app) as client:
        alpha_suite = client.post(
            "/api/v1/suites",
            headers=_authorization(alpha_token),
            json=_suite("private-alpha-trend"),
        ).json()
        alpha_run = client.post(
            "/api/v1/evaluations",
            headers=_authorization(alpha_token),
            json={"suite_id": alpha_suite["suite_id"], "candidate_outputs": {"policy": "Denied"}},
        ).json()["run_id"]
        foreign = client.get(
            "/dashboard/trends",
            headers=_authorization(beta_token),
            params=[
                ("run_id", alpha_run),
                ("metric", "bogus"),
                ("metric", "exact"),
                ("category", "BAD"),
            ],
        )
        absent = client.get(
            "/dashboard/trends",
            headers=_authorization(beta_token),
            params={"run_id": "0" * 64, "tag": "absent"},
        )

    expected = {"error": {"code": "run_trend_not_found", "message": "run trend was not found"}}
    assert foreign.status_code == absent.status_code == 404
    assert foreign.json() == absent.json() == expected
    assert "private-alpha-trend" not in foreign.text


def test_case_evidence_treats_foreign_and_absent_run_ids_identically(tmp_path: Path) -> None:
    from evalforge.api import create_app
    from evalforge.auth import AccessCredential

    alpha_token = "tenant-alpha-editor-token-0000000001"
    beta_token = "tenant-beta-reader-token-00000000001"
    app = create_app(
        tmp_path / "tenant-case-evidence.db",
        access_credentials=(
            AccessCredential(token=alpha_token, tenant_id="tenant-alpha", role="editor"),
            AccessCredential(token=beta_token, tenant_id="tenant-beta", role="reader"),
        ),
    )
    with TestClient(app) as client:
        alpha_suite = client.post(
            "/api/v1/suites",
            headers=_authorization(alpha_token),
            json=_suite("private-alpha-case-evidence"),
        ).json()
        alpha_run = client.post(
            "/api/v1/evaluations",
            headers=_authorization(alpha_token),
            json={"suite_id": alpha_suite["suite_id"], "candidate_outputs": {"policy": "Denied"}},
        ).json()["run_id"]
        foreign = client.get(
            "/dashboard/cases",
            headers=_authorization(beta_token),
            params={"run_id": alpha_run, "case_id": "policy"},
        )
        absent = client.get(
            "/dashboard/cases",
            headers=_authorization(beta_token),
            params={"run_id": "0" * 64, "case_id": "policy"},
        )

    expected = {
        "error": {"code": "case_evidence_not_found", "message": "case evidence was not found"}
    }
    assert foreign.status_code == absent.status_code == 404
    assert foreign.json() == absent.json() == expected
    assert "private-alpha-case-evidence" not in foreign.text
