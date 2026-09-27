from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient


def test_server_factory_uses_absolute_database_path_from_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evalforge.server import create_app_from_environment

    database_path = tmp_path / "state" / "evalforge.db"
    monkeypatch.setenv("EVALFORGE_DATABASE_PATH", str(database_path))

    with TestClient(create_app_from_environment()) as client:
        health = client.get("/api/v1/health")
        dashboard = client.get("/docs")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert dashboard.status_code == 200
    assert database_path.is_file()


@pytest.mark.parametrize("configured_path", ["", "relative/evalforge.db"])
def test_server_factory_rejects_ambiguous_database_paths(
    configured_path: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evalforge.server import create_app_from_environment

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EVALFORGE_DATABASE_PATH", configured_path)

    with pytest.raises(RuntimeError, match="absolute path"):
        create_app_from_environment()

    assert not (tmp_path / "relative" / "evalforge.db").exists()


class _SmokeHandler(BaseHTTPRequestHandler):
    requests: ClassVar[list[tuple[str, str, object | None]]] = []

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _respond(self, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload).encode()
        self.send_response(200 if self.command == "GET" else 201)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        self.requests.append(("GET", self.path, None))
        self._respond({"api_version": "v1", "status": "ok", "storage_schema_version": 1})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        self.requests.append(("POST", self.path, payload))
        if self.path == "/api/v1/suites":
            self._respond({"suite_id": "a" * 64})
            return
        self._respond(
            {
                "run_id": "b" * 64,
                "report": {"run_id": "b" * 64, "release_ready": True},
            }
        )


class _WrongStatusSmokeHandler(_SmokeHandler):
    def _respond(self, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class _RedirectSmokeHandler(BaseHTTPRequestHandler):
    target_origin: ClassVar[str]

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _redirect(self) -> None:
        self.send_response(302)
        self.send_header("Location", f"{self.target_origin}{self.path}")
        self.end_headers()

    do_GET = _redirect
    do_POST = _redirect


def test_deployment_smoke_exercises_health_and_deterministic_evaluation() -> None:
    from evalforge.deployment_smoke import run_smoke

    _SmokeHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SmokeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = run_smoke(f"http://127.0.0.1:{server.server_port}", timeout_seconds=2)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result == {
        "api_version": "v1",
        "release_ready": True,
        "run_id": "b" * 64,
        "storage_schema_version": 1,
    }
    assert [request[:2] for request in _SmokeHandler.requests] == [
        ("GET", "/api/v1/health"),
        ("POST", "/api/v1/suites"),
        ("POST", "/api/v1/evaluations"),
    ]
    suite_payload = _SmokeHandler.requests[1][2]
    evaluation_payload = _SmokeHandler.requests[2][2]
    assert isinstance(suite_payload, dict)
    assert suite_payload["name"] == "container-smoke"
    assert isinstance(evaluation_payload, dict)
    assert evaluation_payload == {"suite_id": "a" * 64, "candidate_outputs": {"ready": "ok"}}


def test_deployment_health_probe_avoids_mutating_persistent_state() -> None:
    from evalforge.deployment_smoke import run_smoke

    _SmokeHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SmokeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = run_smoke(
            f"http://127.0.0.1:{server.server_port}", timeout_seconds=2, health_only=True
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result == {"api_version": "v1", "status": "ok", "storage_schema_version": 1}
    assert _SmokeHandler.requests == [("GET", "/api/v1/health", None)]


def test_deployment_smoke_rejects_success_payloads_with_wrong_creation_status() -> None:
    from evalforge.deployment_smoke import run_smoke

    server = ThreadingHTTPServer(("127.0.0.1", 0), _WrongStatusSmokeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(RuntimeError, match="unexpected HTTP status"):
            run_smoke(f"http://127.0.0.1:{server.server_port}", timeout_seconds=2)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_deployment_smoke_does_not_follow_cross_origin_redirects() -> None:
    from evalforge.deployment_smoke import run_smoke

    _SmokeHandler.requests = []
    target = ThreadingHTTPServer(("127.0.0.1", 0), _SmokeHandler)
    target_thread = threading.Thread(target=target.serve_forever, daemon=True)
    target_thread.start()
    _RedirectSmokeHandler.target_origin = f"http://127.0.0.1:{target.server_port}"
    source = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectSmokeHandler)
    source_thread = threading.Thread(target=source.serve_forever, daemon=True)
    source_thread.start()
    try:
        with pytest.raises(RuntimeError):
            run_smoke(f"http://127.0.0.1:{source.server_port}", timeout_seconds=2)
    finally:
        source.shutdown()
        source.server_close()
        source_thread.join(timeout=2)
        target.shutdown()
        target.server_close()
        target_thread.join(timeout=2)

    assert _SmokeHandler.requests == []


def test_deployment_smoke_rejects_malformed_ports_before_network_access() -> None:
    from evalforge.deployment_smoke import _base_url

    with pytest.raises(RuntimeError, match="valid port"):
        _base_url("http://127.0.0.1:not-a-port")


def test_dockerfile_is_pinned_multistage_non_root_and_health_checked() -> None:
    root = Path(__file__).parents[1]
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    pinned_base = (
        "python:3.11.15-slim-bookworm@"
        "sha256:d29f48a31a8b408ed19272ca1e7b10ebae13b240a27e862d3d4217c528e2e0c3"
    )

    from_lines = [line for line in dockerfile.splitlines() if line.startswith("FROM ")]
    assert from_lines == [f"FROM {pinned_base} AS builder", f"FROM {pinned_base} AS runtime"]
    assert "COPY ." not in dockerfile
    assert "UV_PROJECT_ENVIRONMENT=/opt/evalforge" in dockerfile
    assert "COPY --from=builder --chown=10001:10001 /opt/evalforge /opt/evalforge" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert '"evalforge-smoke", "--health-only"' in dockerfile
    assert '"uvicorn", "--factory", "evalforge.server:create_app_from_environment"' in dockerfile
    assert 'evalforge-smoke = "evalforge.deployment_smoke:main"' in pyproject
    assert '"uvicorn>=' in pyproject


def test_compose_example_confines_writes_privileges_and_resources() -> None:
    compose = (Path(__file__).parents[1] / "compose.yaml").read_text(encoding="utf-8")

    assert '"127.0.0.1:8000:8000"' in compose
    assert "read_only: true" in compose
    assert "evalforge-data:/var/lib/evalforge" in compose
    assert "/tmp:size=16m,mode=1777,noexec,nosuid,nodev" in compose
    assert "no-new-privileges:true" in compose
    assert "cap_drop:" in compose and "- ALL" in compose
    assert "pids_limit: 128" in compose
    assert "cpus: 1.0" in compose
    assert "mem_limit: 512m" in compose
    assert "API_KEY" not in compose


def test_container_build_context_and_ci_runtime_smoke_are_release_gates() -> None:
    root = Path(__file__).parents[1]
    dockerignore = (root / ".dockerignore").read_text(encoding="utf-8").splitlines()
    workflow = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert {".git", ".venv", ".env", ".env.*", "reports", "artifacts", "*.db", "*.sqlite*"} <= set(
        dockerignore
    )
    assert "docker build --tag evalforge:ci ." in workflow
    assert "docker run --detach" in workflow
    assert "evalforge-smoke --base-url http://127.0.0.1:8000" in workflow
    assert "docker inspect" in workflow
