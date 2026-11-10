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


def test_evaluation_jobs_are_idempotent_tenant_scoped_and_do_not_run_inline(
    tmp_path: Path,
) -> None:
    from evalforge.api import create_app
    from evalforge.auth import AccessCredential

    alpha_token = "tenant-alpha-editor-token-0000000001"
    beta_token = "tenant-beta-reader-token-00000000001"
    app = create_app(
        tmp_path / "tenant-jobs.db",
        access_credentials=(
            AccessCredential(token=alpha_token, tenant_id="tenant-alpha", role="editor"),
            AccessCredential(token=beta_token, tenant_id="tenant-beta", role="reader"),
        ),
    )
    key_headers = {
        **_authorization(alpha_token),
        "Idempotency-Key": "evaluation-job-test-0001",
    }
    with TestClient(app) as client:
        suite_id = client.post(
            "/api/v1/suites",
            headers=_authorization(alpha_token),
            json=_suite("queued-alpha-suite"),
        ).json()["suite_id"]
        payload = {"suite_id": suite_id, "candidate_outputs": {"policy": "Allowed"}}
        created = client.post("/api/v1/evaluation-jobs", headers=key_headers, json=payload)
        replayed = client.post("/api/v1/evaluation-jobs", headers=key_headers, json=payload)
        conflict = client.post(
            "/api/v1/evaluation-jobs",
            headers=key_headers,
            json={"suite_id": suite_id, "candidate_outputs": {"policy": "Denied"}},
        )
        job_id = created.json().get("job_id", "0" * 64)
        visible = client.get(
            f"/api/v1/evaluation-jobs/{job_id}",
            headers=_authorization(alpha_token),
        )
        foreign = client.get(
            f"/api/v1/evaluation-jobs/{job_id}",
            headers=_authorization(beta_token),
        )
        absent = client.get(
            f"/api/v1/evaluation-jobs/{'0' * 64}",
            headers=_authorization(beta_token),
        )
        forbidden = client.post(
            "/api/v1/evaluation-jobs",
            headers={
                **_authorization(beta_token),
                "Idempotency-Key": "evaluation-job-test-0002",
            },
            json=payload,
        )
        runs = client.get("/api/v1/runs", headers=_authorization(alpha_token))

    assert created.status_code == replayed.status_code == 202
    assert replayed.json() == created.json()
    assert created.json() == {
        "job_id": job_id,
        "status": "queued",
        "attempts": 0,
        "max_attempts": 3,
        "run_id": None,
        "failure_code": None,
        "submitted_at": created.json()["submitted_at"],
        "updated_at": created.json()["updated_at"],
    }
    assert visible.status_code == 200
    assert visible.json() == created.json()
    assert conflict.status_code == 409
    assert conflict.json() == {
        "error": {
            "code": "idempotency_conflict",
            "message": "idempotency key is already bound to another evaluation request",
        }
    }
    expected_missing = {
        "error": {
            "code": "evaluation_job_not_found",
            "message": "evaluation job was not found",
        }
    }
    assert foreign.status_code == absent.status_code == 404
    assert foreign.json() == absent.json() == expected_missing
    assert forbidden.status_code == 403
    assert runs.json()["total"] == 0


def test_admin_worker_claim_is_tenant_scoped_and_editor_cannot_consume_jobs(
    tmp_path: Path,
) -> None:
    from evalforge.api import create_app
    from evalforge.auth import AccessCredential
    from evalforge.contracts import EvaluationSuite

    alpha_admin_token = "tenant-alpha-admin-token-00000000001"
    alpha_editor_token = "tenant-alpha-editor-token-0000000001"
    beta_admin_token = "tenant-beta-admin-token-000000000001"
    app = create_app(
        tmp_path / "tenant-worker-claims.db",
        access_credentials=(
            AccessCredential(token=alpha_admin_token, tenant_id="tenant-alpha", role="admin"),
            AccessCredential(token=alpha_editor_token, tenant_id="tenant-alpha", role="editor"),
            AccessCredential(token=beta_admin_token, tenant_id="tenant-beta", role="admin"),
        ),
    )

    with TestClient(app) as client:
        alpha_suite_id = client.post(
            "/api/v1/suites",
            headers=_authorization(alpha_editor_token),
            json=_suite("alpha-private-worker-suite"),
        ).json()["suite_id"]
        beta_suite_id = client.post(
            "/api/v1/suites",
            headers=_authorization(beta_admin_token),
            json=_suite("beta-private-worker-suite"),
        ).json()["suite_id"]
        alpha_job = client.post(
            "/api/v1/evaluation-jobs",
            headers={
                **_authorization(alpha_editor_token),
                "Idempotency-Key": "alpha-remote-worker-job-0001",
            },
            json={"suite_id": alpha_suite_id, "candidate_outputs": {"policy": "alpha-private"}},
        ).json()
        beta_job = client.post(
            "/api/v1/evaluation-jobs",
            headers={
                **_authorization(beta_admin_token),
                "Idempotency-Key": "beta-remote-worker-job-00001",
            },
            json={"suite_id": beta_suite_id, "candidate_outputs": {"policy": "beta-private"}},
        ).json()

        forbidden = client.post(
            "/api/v1/worker-claims",
            headers=_authorization(alpha_editor_token),
            json={"worker_id": "remote-worker-a", "lease_seconds": 30},
        )
        claimed = client.post(
            "/api/v1/worker-claims",
            headers=_authorization(alpha_admin_token),
            json={"worker_id": "remote-worker-a", "lease_seconds": 30},
        )
        empty = client.post(
            "/api/v1/worker-claims",
            headers=_authorization(alpha_admin_token),
            json={"worker_id": "remote-worker-b", "lease_seconds": 30},
        )
        beta_visible = client.get(
            f"/api/v1/evaluation-jobs/{beta_job['job_id']}",
            headers=_authorization(beta_admin_token),
        )

    assert forbidden.status_code == 403
    assert claimed.status_code == 200
    assert claimed.json() == {
        "job": {
            "job_id": alpha_job["job_id"],
            "suite_id": alpha_suite_id,
            "suite": EvaluationSuite.model_validate(
                _suite("alpha-private-worker-suite")
            ).model_dump(mode="json", exclude_none=True),
            "candidate": {"policy": "alpha-private"},
            "attempts": 1,
            "max_attempts": 3,
            "lease_token": claimed.json()["job"]["lease_token"],
            "lease_expires_at": claimed.json()["job"]["lease_expires_at"],
        }
    }
    assert len(claimed.json()["job"]["lease_token"]) == 43
    assert empty.status_code == 200
    assert empty.json() == {"job": None}
    assert beta_visible.status_code == 200
    assert beta_visible.json()["status"] == "queued"
    assert beta_visible.json()["attempts"] == 0
    assert "beta-private" not in claimed.text


def test_admin_can_renew_http_worker_lease_with_rotated_capability(tmp_path: Path) -> None:
    from evalforge.api import create_app

    with TestClient(create_app(tmp_path / "worker-renewal.db")) as client:
        suite_id = client.post(
            "/api/v1/suites",
            json=_suite("remote-worker-renewal"),
        ).json()["suite_id"]
        client.post(
            "/api/v1/evaluation-jobs",
            headers={"Idempotency-Key": "remote-worker-renewal-0001"},
            json={"suite_id": suite_id, "candidate_outputs": {"policy": "Allowed"}},
        )
        claimed = client.post(
            "/api/v1/worker-claims",
            json={"worker_id": "remote-worker-a", "lease_seconds": 30},
        ).json()["job"]

        renewed = client.post(
            f"/api/v1/worker-claims/{claimed['job_id']}/renew",
            json={
                "worker_id": "remote-worker-a",
                "lease_token": claimed["lease_token"],
                "lease_seconds": 60,
            },
        )

    assert renewed.status_code == 200
    assert renewed.json()["job_id"] == claimed["job_id"]
    assert renewed.json()["attempts"] == 1
    assert renewed.json()["lease_token"] != claimed["lease_token"]
    assert renewed.json()["lease_expires_at"] > claimed["lease_expires_at"]


def test_http_worker_lease_renewal_rejects_non_advancing_duration_without_mutation(
    tmp_path: Path,
) -> None:
    from evalforge.api import create_app

    app = create_app(tmp_path / "worker-renewal-duration.db")
    with TestClient(app, raise_server_exceptions=False) as client:
        suite_id = client.post(
            "/api/v1/suites",
            json=_suite("remote-worker-renewal-duration"),
        ).json()["suite_id"]
        client.post(
            "/api/v1/evaluation-jobs",
            headers={"Idempotency-Key": "remote-worker-renewal-duration-0001"},
            json={"suite_id": suite_id, "candidate_outputs": {"policy": "Allowed"}},
        )
        claimed = client.post(
            "/api/v1/worker-claims",
            json={"worker_id": "remote-worker-a", "lease_seconds": 30},
        ).json()["job"]
        rejected = client.post(
            f"/api/v1/worker-claims/{claimed['job_id']}/renew",
            json={
                "worker_id": "remote-worker-a",
                "lease_token": claimed["lease_token"],
                "lease_seconds": 1,
            },
        )
        renewed = client.post(
            f"/api/v1/worker-claims/{claimed['job_id']}/renew",
            json={
                "worker_id": "remote-worker-a",
                "lease_token": claimed["lease_token"],
                "lease_seconds": 60,
            },
        )

    assert rejected.status_code == 400
    assert rejected.json() == {
        "error": {
            "code": "invalid_lease_extension",
            "message": "lease duration must extend the active lease",
        }
    }
    assert renewed.status_code == 200


def test_http_worker_lease_renewal_enforces_tenant_admin_non_disclosure(tmp_path: Path) -> None:
    from evalforge.api import create_app
    from evalforge.auth import AccessCredential

    alpha_admin_token = "renew-alpha-admin-token-000000000001"
    alpha_editor_token = "renew-alpha-editor-token-00000000001"
    beta_admin_token = "renew-beta-admin-token-0000000000001"
    app = create_app(
        tmp_path / "tenant-worker-renewal.db",
        access_credentials=(
            AccessCredential(token=alpha_admin_token, tenant_id="tenant-alpha", role="admin"),
            AccessCredential(token=alpha_editor_token, tenant_id="tenant-alpha", role="editor"),
            AccessCredential(token=beta_admin_token, tenant_id="tenant-beta", role="admin"),
        ),
    )

    with TestClient(app) as client:
        suite_id = client.post(
            "/api/v1/suites",
            headers=_authorization(alpha_admin_token),
            json=_suite("tenant-worker-renewal"),
        ).json()["suite_id"]
        client.post(
            "/api/v1/evaluation-jobs",
            headers={
                **_authorization(alpha_admin_token),
                "Idempotency-Key": "tenant-worker-renewal-0001",
            },
            json={"suite_id": suite_id, "candidate_outputs": {"policy": "Allowed"}},
        )
        claimed = client.post(
            "/api/v1/worker-claims",
            headers=_authorization(alpha_admin_token),
            json={"worker_id": "remote-worker-a", "lease_seconds": 30},
        ).json()["job"]
        renewal_payload = {
            "worker_id": "remote-worker-a",
            "lease_token": claimed["lease_token"],
            "lease_seconds": 60,
        }

        foreign = client.post(
            f"/api/v1/worker-claims/{claimed['job_id']}/renew",
            headers=_authorization(beta_admin_token),
            json=renewal_payload,
        )
        absent = client.post(
            f"/api/v1/worker-claims/{'f' * 64}/renew",
            headers=_authorization(beta_admin_token),
            json=renewal_payload,
        )
        forbidden = client.post(
            f"/api/v1/worker-claims/{claimed['job_id']}/renew",
            headers=_authorization(alpha_editor_token),
            json=renewal_payload,
        )
        renewed = client.post(
            f"/api/v1/worker-claims/{claimed['job_id']}/renew",
            headers=_authorization(alpha_admin_token),
            json=renewal_payload,
        )

    expected_missing = {
        "error": {
            "code": "evaluation_job_lease_not_found",
            "message": "evaluation job lease was not found",
        }
    }
    assert foreign.status_code == absent.status_code == 404
    assert foreign.json() == absent.json() == expected_missing
    assert forbidden.status_code == 403
    assert renewed.status_code == 200


def test_worker_claim_rejects_coercive_lease_durations(tmp_path: Path) -> None:
    from evalforge.api import create_app

    with TestClient(create_app(tmp_path / "strict-worker-claims.db")) as client:
        responses = [
            client.post(
                "/api/v1/worker-claims",
                json={"worker_id": "strict-worker", "lease_seconds": value},
            )
            for value in (True, 1.0, "1")
        ]

    expected = {"error": {"code": "validation_error", "message": "request validation failed"}}
    assert all(response.status_code == 422 for response in responses)
    assert all(response.json() == expected for response in responses)


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
