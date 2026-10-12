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
