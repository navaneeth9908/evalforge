from __future__ import annotations

import pytest


def _exception_traceback_references(error: BaseException, secret: str) -> bool:
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        traceback = current.__traceback__
        while traceback is not None:
            frame = traceback.tb_frame
            if str(frame.f_globals.get("__name__", "")).startswith("evalforge") and secret in repr(
                frame.f_locals
            ):
                return True
            traceback = traceback.tb_next
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return False


def test_bearer_authenticator_returns_tenant_principal_without_retaining_plaintext_token() -> None:
    from evalforge.auth import AccessCredential, BearerAuthenticator

    token = "tenant-alpha-production-token-000001"
    authenticator = BearerAuthenticator(
        (
            AccessCredential(
                token=token,
                tenant_id="tenant-alpha",
                role="editor",
            ),
        )
    )

    principal = authenticator.authenticate(f"Bearer {token}")

    assert principal.tenant_id == "tenant-alpha"
    assert principal.role == "editor"
    assert token not in repr(authenticator)
    assert token not in str(authenticator)


@pytest.mark.parametrize("scheme", ("Bearer", "bearer", "BEARER"))
def test_bearer_authenticator_accepts_case_insensitive_authentication_scheme(scheme: str) -> None:
    from evalforge.auth import AccessCredential, BearerAuthenticator

    token = "tenant-alpha-production-token-000001"
    authenticator = BearerAuthenticator(
        (AccessCredential(token=token, tenant_id="tenant-alpha", role="reader"),)
    )

    assert authenticator.authenticate(f"{scheme} {token}").tenant_id == "tenant-alpha"


def test_bearer_authenticator_rejection_traceback_does_not_retain_supplied_token() -> None:
    from evalforge.auth import AccessCredential, AuthenticationError, BearerAuthenticator

    rejected_token = "attacker-supplied-production-token-0001"
    authenticator = BearerAuthenticator(
        (
            AccessCredential(
                token="tenant-alpha-production-token-000001",
                tenant_id="tenant-alpha",
                role="reader",
            ),
        )
    )

    with pytest.raises(AuthenticationError) as error:
        authenticator.authenticate(f"Bearer {rejected_token}")

    assert not _exception_traceback_references(error.value, rejected_token)


def test_api_authentication_error_chain_does_not_retain_rejected_token(tmp_path: object) -> None:
    from pathlib import Path

    from fastapi.routing import APIRoute
    from fastapi.security import HTTPAuthorizationCredentials

    from evalforge.api import APIError, create_app
    from evalforge.auth import AccessCredential

    rejected_token = "attacker-supplied-production-token-0001"
    app = create_app(
        Path(str(tmp_path)) / "authenticated.db",
        access_credentials=(
            AccessCredential(
                token="tenant-alpha-production-token-000001",
                tenant_id="tenant-alpha",
                role="reader",
            ),
        ),
    )
    route = next(
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path == "/api/v1/suites" and "GET" in route.methods
    )
    authenticate = route.dependant.dependencies[0].dependencies[0].call

    with pytest.raises(APIError) as error:
        authenticate(HTTPAuthorizationCredentials(scheme="Bearer", credentials=rejected_token))

    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert not _exception_traceback_references(error.value, rejected_token)


@pytest.mark.parametrize(
    "authorization",
    (
        None,
        "",
        "Basic dGVzdDp0ZXN0",
        "Bearer",
        "Bearer wrong-production-token-0000000000",
    ),
)
def test_bearer_authenticator_rejects_missing_malformed_or_unknown_credentials(
    authorization: str | None,
) -> None:
    from evalforge.auth import AccessCredential, AuthenticationError, BearerAuthenticator

    authenticator = BearerAuthenticator(
        (
            AccessCredential(
                token="tenant-alpha-production-token-000001",
                tenant_id="tenant-alpha",
                role="reader",
            ),
        )
    )

    with pytest.raises(AuthenticationError, match="invalid bearer credential"):
        authenticator.authenticate(authorization)


def test_bearer_authenticator_rejects_duplicate_tokens_and_invalid_identity_fields() -> None:
    from pydantic import ValidationError

    from evalforge.auth import AccessCredential, BearerAuthenticator

    token = "shared-production-token-000000000001"
    with pytest.raises(ValueError, match="unique"):
        BearerAuthenticator(
            (
                AccessCredential(token=token, tenant_id="tenant-alpha", role="reader"),
                AccessCredential(token=token, tenant_id="tenant-beta", role="editor"),
            )
        )

    with pytest.raises(ValidationError):
        AccessCredential(token="short", tenant_id="Tenant Alpha", role="owner")

    for token in (
        " leading-space-production-token-000001",
        "trailing-space-production-token-000001 ",
        "embedded\twhitespace-production-token-000001",
    ):
        with pytest.raises(ValidationError):
            AccessCredential(token=token, tenant_id="tenant-alpha", role="reader")


def test_authenticated_api_requires_bearer_identity_and_enforces_read_write_roles(
    tmp_path: object,
) -> None:
    from pathlib import Path

    from fastapi.testclient import TestClient

    from evalforge.api import create_app
    from evalforge.auth import AccessCredential

    database_path = Path(str(tmp_path)) / "authenticated.db"
    credentials = (
        AccessCredential(
            token="tenant-alpha-reader-token-0000000001",
            tenant_id="tenant-alpha",
            role="reader",
        ),
        AccessCredential(
            token="tenant-alpha-editor-token-0000000001",
            tenant_id="tenant-alpha",
            role="editor",
        ),
    )
    with TestClient(create_app(database_path, access_credentials=credentials)) as client:
        health = client.get("/api/v1/health")
        missing = client.get("/api/v1/suites")
        unknown = client.get(
            "/api/v1/suites",
            headers={"Authorization": "Bearer unknown-production-token-0000000000"},
        )
        reader_page = client.get(
            "/api/v1/suites",
            headers={"Authorization": "Bearer tenant-alpha-reader-token-0000000001"},
        )
        lowercase_scheme = client.get(
            "/api/v1/suites",
            headers={"Authorization": "bearer tenant-alpha-reader-token-0000000001"},
        )
        forbidden = client.post(
            "/api/v1/suites",
            headers={"Authorization": "Bearer tenant-alpha-reader-token-0000000001"},
            json={
                "schema_version": 1,
                "name": "reader-cannot-write",
                "minimum_pass_rate": 1.0,
                "cases": [
                    {
                        "case_id": "policy",
                        "prompt": "State the policy.",
                        "expected_output": "Allowed",
                    }
                ],
            },
        )
        created = client.post(
            "/api/v1/suites",
            headers={"Authorization": "Bearer tenant-alpha-editor-token-0000000001"},
            json={
                "schema_version": 1,
                "name": "editor-can-write",
                "minimum_pass_rate": 1.0,
                "cases": [
                    {
                        "case_id": "policy",
                        "prompt": "State the policy.",
                        "expected_output": "Allowed",
                    }
                ],
            },
        )

    assert health.status_code == 200
    assert missing.status_code == 401
    assert unknown.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert (
        missing.json()
        == unknown.json()
        == {"error": {"code": "authentication_required", "message": "authentication required"}}
    )
    assert reader_page.status_code == 200
    assert lowercase_scheme.status_code == 200
    assert forbidden.status_code == 403
    assert forbidden.json() == {
        "error": {"code": "permission_denied", "message": "permission denied"}
    }
    assert created.status_code == 201
    assert created.json()["suite"]["name"] == "editor-can-write"


def test_authenticated_api_protects_dashboard_and_documents_bearer_security(
    tmp_path: object,
) -> None:
    from pathlib import Path

    from fastapi.testclient import TestClient

    from evalforge.api import create_app
    from evalforge.auth import AccessCredential

    app = create_app(
        Path(str(tmp_path)) / "authenticated.db",
        access_credentials=(
            AccessCredential(
                token="tenant-alpha-reader-token-0000000001",
                tenant_id="tenant-alpha",
                role="reader",
            ),
        ),
    )
    with TestClient(app) as client:
        missing = client.get("/dashboard")
        allowed = client.get(
            "/dashboard",
            headers={"Authorization": "Bearer tenant-alpha-reader-token-0000000001"},
        )
        lowercase_scheme = client.get(
            "/api/v1/suites",
            headers={"Authorization": "bearer tenant-alpha-reader-token-0000000001"},
        )
        openapi = client.get("/openapi.json").json()

    assert missing.status_code == 401
    assert allowed.status_code == 200
    assert lowercase_scheme.status_code == 200
    assert "HTTPBearer" in openapi["components"]["securitySchemes"]
    assert openapi["paths"]["/api/v1/suites"]["get"]["security"] == [{"HTTPBearer": []}]


def test_trusted_local_api_openapi_does_not_claim_bearer_security(tmp_path: object) -> None:
    from pathlib import Path

    from fastapi.testclient import TestClient

    from evalforge.api import create_app

    with TestClient(create_app(Path(str(tmp_path)) / "local.db")) as client:
        openapi = client.get("/openapi.json").json()

    assert "securitySchemes" not in openapi.get("components", {})
    assert "security" not in openapi["paths"]["/api/v1/suites"]["get"]


def test_access_credentials_json_is_strict_bounded_and_rejects_ambiguity() -> None:
    from evalforge.auth import parse_access_credentials_json

    parsed = parse_access_credentials_json(
        '[{"token":"tenant-alpha-production-token-000001",'
        '"tenant_id":"tenant-alpha","role":"admin"}]'
    )
    assert parsed is not None
    assert len(parsed) == 1
    assert parsed[0].tenant_id == "tenant-alpha"
    assert parsed[0].role == "admin"

    invalid_values = (
        "",
        "[]",
        "{}",
        '[{"token":"tenant-alpha-production-token-000001",'
        '"tenant_id":"tenant-alpha","tenant_id":"tenant-beta","role":"admin"}]',
        '[{"token":"tenant-alpha-production-token-000001",'
        '"tenant_id":"tenant-alpha","role":"admin","extra":true}]',
        "[true]",
        "[NaN]",
        "[" * 17 + "]" * 17,
    )
    for value in invalid_values:
        with pytest.raises(ValueError, match="access credential configuration is invalid"):
            parse_access_credentials_json(value)

    deeply_nested = "[" * 16_000 + "0" + "]" * 16_000
    with pytest.raises(ValueError, match="access credential configuration is invalid"):
        parse_access_credentials_json(deeply_nested)

    assert parse_access_credentials_json(None) is None


def test_access_credentials_json_rejection_traceback_does_not_retain_token() -> None:
    from evalforge.auth import parse_access_credentials_json

    rejected_token = "customer-secret-production-token-0001"
    raw = f'[{{"token":"{rejected_token}","tenant_id":"INVALID TENANT","role":"admin"}}]'

    with pytest.raises(ValueError, match="access credential configuration is invalid") as error:
        parse_access_credentials_json(raw)

    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert not _exception_traceback_references(error.value, rejected_token)
