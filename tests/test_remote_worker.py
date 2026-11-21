import json
import traceback
from urllib.parse import urlsplit
from urllib.request import Request


def _exception_evidence(error: BaseException) -> str:
    evidence = list(traceback.format_exception(error))
    current = error.__traceback__
    while current is not None:
        filename = current.tb_frame.f_code.co_filename.replace("\\", "/")
        if "/src/evalforge/" in filename:
            for name, value in current.tb_frame.f_locals.items():
                try:
                    rendered = repr(value)
                except BaseException:
                    rendered = f"<{type(value).__name__}:unrepresentable>"
                evidence.append(f"{name}={rendered}")
        current = current.tb_next
    return "\n".join(evidence)


def _traceback_worker_credentials(error: BaseException) -> list[str]:
    from pydantic import SecretStr

    recovered: list[str] = []
    current = error.__traceback__
    while current is not None:
        worker = current.tb_frame.f_locals.get("self")
        credential = getattr(worker, "access_token", None)
        if isinstance(credential, SecretStr):
            recovered.append(credential.get_secret_value())
        elif isinstance(credential, str):
            recovered.append(credential)
        current = current.tb_next
    return recovered


def test_remote_worker_claims_evaluates_and_completes_one_job() -> None:
    from evalforge.api import EvaluationJobResponse
    from evalforge.contracts import EvaluationSuite
    from evalforge.provenance import canonical_json_sha256
    from evalforge.remote_worker import RemoteEvaluationWorker, WorkerHTTPResponse

    suite = EvaluationSuite.model_validate(
        {
            "schema_version": 1,
            "name": "remote-worker-smoke",
            "minimum_pass_rate": 1.0,
            "cases": [
                {
                    "case_id": "policy",
                    "prompt": "State the synthetic policy.",
                    "expected_output": "Allowed",
                }
            ],
        }
    ).model_dump(mode="json", exclude_none=True)
    suite_id = canonical_json_sha256(suite)
    job_id = "a" * 64
    lease_token = "T" * 43
    access_token = "remote-worker-admin-token-000000000001"
    requests: list[tuple[str, dict[str, object], str | None]] = []

    def transport(
        *,
        request: Request,
        connect_timeout: float,
        read_timeout: float,
        max_response_bytes: int,
    ) -> WorkerHTTPResponse:
        assert connect_timeout == read_timeout == 5
        assert max_response_bytes == 1024 * 1024
        assert request.data is not None
        path = urlsplit(request.full_url).path
        payload = json.loads(request.data)
        requests.append((path, payload, request.get_header("Authorization")))
        if path == "/api/v1/worker-claims":
            response: object = {
                "job": {
                    "job_id": job_id,
                    "suite_id": suite_id,
                    "suite": suite,
                    "candidate": {"policy": "Allowed"},
                    "attempts": 1,
                    "max_attempts": 3,
                    "lease_token": lease_token,
                    "lease_expires_at": "2099-01-01T00:01:00.000000Z",
                }
            }
        else:
            assert path == f"/api/v1/worker-claims/{job_id}/complete"
            report = payload["report"]
            assert isinstance(report, dict)
            response = {
                "job_id": job_id,
                "status": "succeeded",
                "attempts": 1,
                "max_attempts": 3,
                "run_id": report["run_id"],
                "failure_code": None,
                "submitted_at": "2099-01-01T00:00:00.000000Z",
                "updated_at": "2099-01-01T00:00:01.000000Z",
            }
        return WorkerHTTPResponse(
            status=200,
            body=json.dumps(response, separators=(",", ":")).encode("utf-8"),
        )

    completed = RemoteEvaluationWorker(
        base_url="https://api.example.test",
        access_token=access_token,
        worker_id="remote-worker-a",
        lease_seconds=30,
        timeout_seconds=5,
        transport=transport,
    ).run_once()

    assert isinstance(completed, EvaluationJobResponse)
    assert completed.status == "succeeded"
    assert completed.run_id is not None
    assert [request[0] for request in requests] == [
        "/api/v1/worker-claims",
        f"/api/v1/worker-claims/{job_id}/complete",
    ]
    assert requests[0][1] == {"lease_seconds": 30, "worker_id": "remote-worker-a"}
    assert requests[1][1]["worker_id"] == "remote-worker-a"
    assert requests[1][1]["lease_token"] == lease_token
    assert all(request[2] == f"Bearer {access_token}" for request in requests)


def test_remote_worker_records_invalid_evaluation_without_publishing_a_report() -> None:
    from evalforge.contracts import EvaluationSuite
    from evalforge.provenance import canonical_json_sha256
    from evalforge.remote_worker import RemoteEvaluationWorker, WorkerHTTPResponse

    suite = EvaluationSuite.model_validate(
        {
            "schema_version": 1,
            "name": "remote-worker-invalid",
            "minimum_pass_rate": 1.0,
            "cases": [
                {
                    "case_id": "policy",
                    "prompt": "State the synthetic policy.",
                    "expected_output": "Allowed",
                }
            ],
        }
    ).model_dump(mode="json", exclude_none=True)
    suite_id = canonical_json_sha256(suite)
    job_id = "b" * 64
    lease_token = "U" * 43
    requests: list[tuple[str, dict[str, object]]] = []

    def transport(**kwargs: object) -> WorkerHTTPResponse:
        request = kwargs["request"]
        assert isinstance(request, Request) and request.data is not None
        path = urlsplit(request.full_url).path
        payload = json.loads(request.data)
        requests.append((path, payload))
        if path == "/api/v1/worker-claims":
            response: object = {
                "job": {
                    "job_id": job_id,
                    "suite_id": suite_id,
                    "suite": suite,
                    "candidate": {"unexpected": "output"},
                    "attempts": 1,
                    "max_attempts": 3,
                    "lease_token": lease_token,
                    "lease_expires_at": "2099-01-01T00:01:00.000000Z",
                }
            }
        else:
            assert path == f"/api/v1/worker-claims/{job_id}/fail"
            response = {
                "job_id": job_id,
                "status": "failed",
                "attempts": 1,
                "max_attempts": 3,
                "run_id": None,
                "failure_code": "invalid_evaluation",
                "submitted_at": "2099-01-01T00:00:00.000000Z",
                "updated_at": "2099-01-01T00:00:01.000000Z",
            }
        return WorkerHTTPResponse(status=200, body=json.dumps(response).encode())

    failed = RemoteEvaluationWorker(
        base_url="https://api.example.test",
        access_token="remote-worker-admin-token-000000000001",
        worker_id="remote-worker-a",
        transport=transport,
    ).run_once()

    assert failed is not None
    assert failed.status == "failed"
    assert failed.failure_code == "invalid_evaluation"
    assert requests[-1] == (
        f"/api/v1/worker-claims/{job_id}/fail",
        {
            "failure_code": "invalid_evaluation",
            "lease_token": lease_token,
            "worker_id": "remote-worker-a",
        },
    )


def test_remote_worker_schedules_transient_failure_with_exponential_backoff(
    monkeypatch: object,
) -> None:
    from evalforge import remote_worker as remote_worker_module
    from evalforge.contracts import EvaluationSuite
    from evalforge.provenance import canonical_json_sha256
    from evalforge.remote_worker import RemoteEvaluationWorker, WorkerHTTPResponse

    suite = EvaluationSuite.model_validate(
        {
            "schema_version": 1,
            "name": "remote-worker-retry",
            "minimum_pass_rate": 1.0,
            "cases": [
                {
                    "case_id": "policy",
                    "prompt": "State the synthetic policy.",
                    "expected_output": "Allowed",
                }
            ],
        }
    ).model_dump(mode="json", exclude_none=True)
    suite_id = canonical_json_sha256(suite)
    job_id = "c" * 64
    lease_token = "V" * 43
    requests: list[tuple[str, dict[str, object]]] = []

    def raise_transient(**_kwargs: object) -> object:
        raise RuntimeError("provider password=never-persist")

    monkeypatch.setattr(remote_worker_module, "_evaluation_report_payload", raise_transient)  # type: ignore[attr-defined]

    def transport(**kwargs: object) -> WorkerHTTPResponse:
        request = kwargs["request"]
        assert isinstance(request, Request) and request.data is not None
        path = urlsplit(request.full_url).path
        payload = json.loads(request.data)
        requests.append((path, payload))
        if path == "/api/v1/worker-claims":
            response: object = {
                "job": {
                    "job_id": job_id,
                    "suite_id": suite_id,
                    "suite": suite,
                    "candidate": {"policy": "Allowed"},
                    "attempts": 3,
                    "max_attempts": 5,
                    "lease_token": lease_token,
                    "lease_expires_at": "2099-01-01T00:01:00.000000Z",
                }
            }
        else:
            assert path == f"/api/v1/worker-claims/{job_id}/retry"
            response = {
                "job_id": job_id,
                "status": "queued",
                "attempts": 3,
                "max_attempts": 5,
                "run_id": None,
                "failure_code": None,
                "submitted_at": "2099-01-01T00:00:00.000000Z",
                "updated_at": "2099-01-01T00:00:01.000000Z",
            }
        return WorkerHTTPResponse(status=200, body=json.dumps(response).encode())

    retrying = RemoteEvaluationWorker(
        base_url="https://api.example.test",
        access_token="remote-worker-admin-token-000000000001",
        worker_id="remote-worker-a",
        retry_backoff_seconds=15,
        transport=transport,
    ).run_once()

    assert retrying is not None and retrying.status == "queued"
    assert requests[-1] == (
        f"/api/v1/worker-claims/{job_id}/retry",
        {
            "backoff_seconds": 60,
            "lease_token": lease_token,
            "worker_id": "remote-worker-a",
        },
    )


def test_remote_worker_requires_tls_except_for_exact_loopback_hosts() -> None:
    import pytest

    from evalforge.remote_worker import RemoteEvaluationWorker

    token = "remote-worker-admin-token-000000000001"
    RemoteEvaluationWorker(
        base_url="http://127.0.0.1:8000",
        access_token=token,
        worker_id="remote-worker-a",
    )
    RemoteEvaluationWorker(
        base_url="http://localhost:8000",
        access_token=token,
        worker_id="remote-worker-a",
    )

    with pytest.raises(ValueError, match="remote worker configuration is invalid"):
        RemoteEvaluationWorker(
            base_url="http://api.example.test",
            access_token=token,
            worker_id="remote-worker-a",
        )


def test_remote_worker_rejects_origins_with_empty_query_or_fragment_delimiters() -> None:
    import pytest

    from evalforge.remote_worker import RemoteEvaluationWorker

    for base_url in ("https://api.example.test?", "https://api.example.test#"):
        with pytest.raises(ValueError, match="remote worker configuration is invalid"):
            RemoteEvaluationWorker(
                base_url=base_url,
                access_token="remote-worker-admin-token-000000000001",
                worker_id="remote-worker-a",
            )


def test_remote_worker_command_consumes_environment_token_and_reports_bounded_status(
    monkeypatch: object, capsys: object
) -> None:
    import os
    from pathlib import Path

    from evalforge.api import EvaluationJobResponse
    from evalforge.remote_worker import RemoteEvaluationWorker
    from evalforge.remote_worker_service import main

    pyproject = (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert 'evalforge-remote-worker = "evalforge.remote_worker_service:main"' in pyproject
    token = "remote-worker-admin-token-000000000001"
    monkeypatch.setenv("EVALFORGE_WORKER_API_URL", "https://api.example.test")  # type: ignore[attr-defined]
    monkeypatch.setenv("EVALFORGE_WORKER_ACCESS_TOKEN", token)  # type: ignore[attr-defined]
    completed = EvaluationJobResponse(
        job_id="d" * 64,
        status="succeeded",
        attempts=1,
        max_attempts=3,
        run_id="e" * 64,
        failure_code=None,
        submitted_at="2099-01-01T00:00:00.000000Z",
        updated_at="2099-01-01T00:00:01.000000Z",
    )
    monkeypatch.setattr(RemoteEvaluationWorker, "run_once", lambda _worker: completed)  # type: ignore[attr-defined]

    exit_code = main(["--worker-id", "remote-worker-a"])

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert exit_code == 0
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "attempts": 1,
        "job_id": "d" * 64,
        "processed": True,
        "status": "succeeded",
    }
    assert "EVALFORGE_WORKER_ACCESS_TOKEN" not in os.environ
    assert token not in captured.out


def test_remote_worker_rejects_ambiguous_or_unbounded_claim_responses() -> None:
    import pytest

    from evalforge.remote_worker import (
        MAX_WORKER_RESPONSE_BYTES,
        RemoteEvaluationWorker,
        RemoteWorkerError,
        WorkerHTTPResponse,
    )

    token = "remote-worker-admin-token-000000000001"
    responses = (
        WorkerHTTPResponse(status=200, body=b'{"job":null,"job":null}'),
        WorkerHTTPResponse(status=200, body=b'{"job":null,"unexpected":true}'),
        WorkerHTTPResponse(status=503, body=b'{"secret":"remote-worker-response-secret"}'),
        WorkerHTTPResponse(status=200, body="remote-worker-body-secret"),  # type: ignore[arg-type]
        WorkerHTTPResponse(status=200, body=b"[" * 2_000 + b"]" * 2_000),
        WorkerHTTPResponse(status=200, body=b"x" * (MAX_WORKER_RESPONSE_BYTES + 1)),
    )
    for response in responses:
        worker = RemoteEvaluationWorker(
            base_url="https://api.example.test",
            access_token=token,
            worker_id="remote-worker-a",
            transport=lambda response=response, **_kwargs: response,
        )
        with pytest.raises(
            RemoteWorkerError,
            match=r"^remote worker API request failed$",
        ) as rejected:
            worker.run_once()
        assert rejected.value.__cause__ is None
        assert rejected.value.__context__ is None
        evidence = _exception_evidence(rejected.value)
        assert token not in evidence
        assert "remote-worker-response-secret" not in evidence
        assert "remote-worker-body-secret" not in evidence


def test_remote_worker_rejects_mismatched_terminal_job_outcome(monkeypatch: object) -> None:
    import pytest

    from evalforge import remote_worker as remote_worker_module
    from evalforge.remote_worker import (
        RemoteEvaluationWorker,
        RemoteWorkerError,
        WorkerHTTPResponse,
    )

    claimed_job_id = "a" * 64

    def invalid_evaluation(**_kwargs: object) -> object:
        raise ValueError("invalid synthetic evaluation")

    monkeypatch.setattr(  # type: ignore[attr-defined]
        remote_worker_module,
        "_evaluation_report_payload",
        invalid_evaluation,
    )

    def transport(**kwargs: object) -> WorkerHTTPResponse:
        request = kwargs["request"]
        assert isinstance(request, Request)
        path = urlsplit(request.full_url).path
        if path == "/api/v1/worker-claims":
            response = {
                "job": {
                    "job_id": claimed_job_id,
                    "suite_id": "b" * 64,
                    "suite": {},
                    "candidate": {},
                    "attempts": 1,
                    "max_attempts": 3,
                    "lease_token": "T" * 43,
                    "lease_expires_at": "2099-01-01T00:01:00.000000Z",
                }
            }
        else:
            assert path == f"/api/v1/worker-claims/{claimed_job_id}/fail"
            response = {
                "job_id": "c" * 64,
                "status": "queued",
                "attempts": 1,
                "max_attempts": 3,
                "run_id": None,
                "failure_code": None,
                "submitted_at": "2099-01-01T00:00:00.000000Z",
                "updated_at": "2099-01-01T00:00:01.000000Z",
            }
        return WorkerHTTPResponse(status=200, body=json.dumps(response).encode())

    worker = RemoteEvaluationWorker(
        base_url="https://api.example.test",
        access_token="remote-worker-admin-token-000000000001",
        worker_id="remote-worker-a",
        transport=transport,
    )

    with pytest.raises(RemoteWorkerError, match=r"^remote worker API request failed$"):
        worker.run_once()


def test_stdlib_transport_interrupts_slow_response_headers_at_the_total_deadline(
    monkeypatch: object,
) -> None:
    import time
    from threading import Event

    import pytest

    from evalforge import remote_worker as remote_worker_module
    from evalforge.remote_worker import _stdlib_transport

    closed = Event()

    class FakeSocket:
        def settimeout(self, _timeout: float) -> None:
            pass

    class SlowConnection:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.sock = FakeSocket()

        def request(self, *_args: object, **_kwargs: object) -> None:
            pass

        def getresponse(self) -> object:
            closed.wait(0.5)
            raise TimeoutError

        def close(self) -> None:
            closed.set()

    monkeypatch.setattr(  # type: ignore[attr-defined]
        remote_worker_module.http.client,
        "HTTPSConnection",
        SlowConnection,
    )

    started = time.monotonic()
    with pytest.raises(TimeoutError):
        _stdlib_transport(
            request=Request("https://api.example.test/api/v1/worker-claims", method="POST"),
            connect_timeout=0.05,
            read_timeout=0.05,
            max_response_bytes=1024,
        )

    assert time.monotonic() - started < 0.2
    assert closed.is_set()


def test_stdlib_transport_starts_the_total_deadline_before_tls_handshake(
    monkeypatch: object,
) -> None:
    import socket
    import time
    from threading import Event

    import pytest

    from evalforge import remote_worker as remote_worker_module
    from evalforge.remote_worker import _stdlib_transport

    closed = Event()

    class FakeSocket:
        def __init__(self, *_args: object) -> None:
            pass

        def settimeout(self, _timeout: float) -> None:
            pass

        def connect(self, _address: object) -> None:
            pass

        def close(self) -> None:
            closed.set()

    class FakeTLSContext:
        def set_alpn_protocols(self, _protocols: list[str]) -> None:
            pass

        def wrap_socket(self, raw_socket: FakeSocket, *, server_hostname: str) -> FakeSocket:
            assert server_hostname == "api.example.test"
            if not closed.wait(0.3):
                raise TimeoutError
            raise TimeoutError

    class FakeConnection:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.sock: FakeSocket | None = None

        def close(self) -> None:
            if self.sock is not None:
                self.sock.close()

    monkeypatch.setattr(  # type: ignore[attr-defined]
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.10", 443))
        ],
    )
    monkeypatch.setattr(socket, "socket", FakeSocket)  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        remote_worker_module.http.client,
        "HTTPSConnection",
        FakeConnection,
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        remote_worker_module.ssl,
        "create_default_context",
        FakeTLSContext,
    )

    started = time.monotonic()
    with pytest.raises((OSError, TimeoutError)):
        _stdlib_transport(
            request=Request("https://api.example.test/api/v1/worker-claims", method="POST"),
            connect_timeout=0.05,
            read_timeout=0.05,
            max_response_bytes=1024,
        )

    assert time.monotonic() - started < 0.2
    assert closed.is_set()


def test_stdlib_transport_bounds_dns_resolution_with_the_total_deadline(
    monkeypatch: object,
) -> None:
    import socket
    import time

    import pytest

    from evalforge.remote_worker import _stdlib_transport

    def slow_resolution(*_args: object, **_kwargs: object) -> list[object]:
        time.sleep(0.5)
        return []

    monkeypatch.setattr(socket, "getaddrinfo", slow_resolution)  # type: ignore[attr-defined]

    started = time.monotonic()
    with pytest.raises(TimeoutError):
        _stdlib_transport(
            request=Request("https://api.example.test/api/v1/worker-claims", method="POST"),
            connect_timeout=0.05,
            read_timeout=0.05,
            max_response_bytes=1024,
        )

    assert time.monotonic() - started < 0.2


def test_stdlib_transport_tries_each_resolved_address_within_the_total_deadline(
    monkeypatch: object,
) -> None:
    import socket

    from evalforge import remote_worker as remote_worker_module
    from evalforge.remote_worker import WorkerHTTPResponse, _stdlib_transport

    failure_mode = "constructor"
    created_families: list[object] = []
    attempted: list[object] = []
    sockets: list[object] = []

    class FakeSocket:
        def __init__(self, family: object, *_args: object) -> None:
            self.closed = False
            created_families.append(family)
            if family == socket.AF_INET6 and failure_mode == "constructor":
                raise OSError("synthetic unsupported IPv6 socket family")
            sockets.append(self)

        def settimeout(self, _timeout: float) -> None:
            pass

        def connect(self, address: object) -> None:
            attempted.append(address)
            if isinstance(address, tuple) and address[0] == "::1" and failure_mode == "connect":
                raise OSError("synthetic IPv6 refusal")

        def close(self) -> None:
            self.closed = True

    class FakeResponse:
        status = 200

        def __init__(self) -> None:
            self._body = b'{"job":null}'

        def read1(self, _amount: int) -> bytes:
            body, self._body = self._body, b""
            return body

    class FakeConnection:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.sock: FakeSocket | None = None

        def request(self, *_args: object, **_kwargs: object) -> None:
            assert self.sock is sockets[-1]

        def getresponse(self) -> FakeResponse:
            return FakeResponse()

        def close(self) -> None:
            if self.sock is not None:
                self.sock.close()

    monkeypatch.setattr(  # type: ignore[attr-defined]
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 8000, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 8000)),
        ],
    )
    monkeypatch.setattr(socket, "socket", FakeSocket)  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        remote_worker_module.http.client,
        "HTTPConnection",
        FakeConnection,
    )

    constructor_fallback = _stdlib_transport(
        request=Request("http://localhost:8000/api/v1/worker-claims", method="POST"),
        connect_timeout=1,
        read_timeout=1,
        max_response_bytes=1024,
    )

    assert constructor_fallback == WorkerHTTPResponse(status=200, body=b'{"job":null}')
    assert created_families == [socket.AF_INET6, socket.AF_INET]
    assert attempted == [("127.0.0.1", 8000)]

    failure_mode = "connect"
    created_families.clear()
    attempted.clear()
    sockets.clear()
    connect_fallback = _stdlib_transport(
        request=Request("http://localhost:8000/api/v1/worker-claims", method="POST"),
        connect_timeout=1,
        read_timeout=1,
        max_response_bytes=1024,
    )

    assert connect_fallback == WorkerHTTPResponse(status=200, body=b'{"job":null}')
    assert attempted == [("::1", 8000, 0, 0), ("127.0.0.1", 8000)]
    assert isinstance(sockets[0], FakeSocket) and sockets[0].closed is True


def test_stdlib_transport_enforces_one_total_request_deadline(monkeypatch: object) -> None:
    import pytest

    from evalforge import remote_worker as remote_worker_module
    from evalforge.remote_worker import _stdlib_transport

    class FakeSocket:
        def settimeout(self, _timeout: float) -> None:
            pass

    class FakeResponse:
        status = 200

        def read(self, _amount: int) -> bytes:
            return b'{"job":null}'

        def read1(self, _amount: int) -> bytes:
            return b'{"job":null}'

    class FakeConnection:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.sock = FakeSocket()

        def request(self, *_args: object, **_kwargs: object) -> None:
            pass

        def getresponse(self) -> FakeResponse:
            return FakeResponse()

        def close(self) -> None:
            pass

    clock = iter((0.0, 0.05, 0.15))
    monkeypatch.setattr(remote_worker_module, "monotonic", lambda: next(clock), raising=False)  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        remote_worker_module.http.client,
        "HTTPSConnection",
        FakeConnection,
    )

    with pytest.raises(TimeoutError):
        _stdlib_transport(
            request=Request("https://api.example.test/api/v1/worker-claims", method="POST"),
            connect_timeout=0.1,
            read_timeout=0.1,
            max_response_bytes=1024,
        )


def test_remote_worker_rejects_overridable_url_text() -> None:
    import pytest

    from evalforge.remote_worker import RemoteEvaluationWorker

    class MutatingURL(str):
        def rstrip(self, _characters: str | None = None) -> str:
            return "http://attacker.example"

    with pytest.raises(ValueError, match="remote worker configuration is invalid"):
        RemoteEvaluationWorker(
            base_url=MutatingURL("https://api.example.test"),
            access_token="remote-worker-admin-token-000000000001",
            worker_id="remote-worker-a",
        )


def test_remote_worker_sanitizes_arbitrary_transport_failures() -> None:
    import pytest

    from evalforge.remote_worker import (
        RemoteEvaluationWorker,
        RemoteWorkerError,
        WorkerHTTPResponse,
    )

    secret = "transport-body-secret"
    malformed = object.__new__(WorkerHTTPResponse)

    def raising_transport(**_kwargs: object) -> WorkerHTTPResponse:
        raise RuntimeError(secret)

    for transport in (
        raising_transport,
        lambda **_kwargs: malformed,
    ):
        worker = RemoteEvaluationWorker(
            base_url="https://api.example.test",
            access_token="remote-worker-admin-token-000000000001",
            worker_id="remote-worker-a",
            transport=transport,
        )
        with pytest.raises(
            RemoteWorkerError,
            match=r"^remote worker API request failed$",
        ) as rejected:
            worker.run_once()
        assert rejected.value.__cause__ is None
        assert rejected.value.__context__ is None
        assert secret not in _exception_evidence(rejected.value)


def test_remote_worker_binds_completion_to_the_submitted_run_id() -> None:
    import pytest

    from evalforge.contracts import EvaluationSuite
    from evalforge.provenance import canonical_json_sha256
    from evalforge.remote_worker import (
        RemoteEvaluationWorker,
        RemoteWorkerError,
        WorkerHTTPResponse,
    )

    suite = EvaluationSuite.model_validate(
        {
            "schema_version": 1,
            "name": "remote-worker-run-binding",
            "minimum_pass_rate": 1.0,
            "cases": [
                {
                    "case_id": "policy",
                    "prompt": "State the synthetic policy.",
                    "expected_output": "Allowed",
                }
            ],
        }
    ).model_dump(mode="json", exclude_none=True)
    suite_id = canonical_json_sha256(suite)
    job_id = "a" * 64

    def transport(**kwargs: object) -> WorkerHTTPResponse:
        request = kwargs["request"]
        assert isinstance(request, Request) and request.data is not None
        path = urlsplit(request.full_url).path
        payload = json.loads(request.data)
        if path == "/api/v1/worker-claims":
            response: object = {
                "job": {
                    "job_id": job_id,
                    "suite_id": suite_id,
                    "suite": suite,
                    "candidate": {"policy": "Allowed"},
                    "attempts": 1,
                    "max_attempts": 3,
                    "lease_token": "T" * 43,
                    "lease_expires_at": "2099-01-01T00:01:00.000000Z",
                }
            }
        else:
            assert path == f"/api/v1/worker-claims/{job_id}/complete"
            report = payload["report"]
            assert isinstance(report, dict)
            assert report["run_id"] != "f" * 64
            response = {
                "job_id": job_id,
                "status": "succeeded",
                "attempts": 1,
                "max_attempts": 3,
                "run_id": "f" * 64,
                "failure_code": None,
                "submitted_at": "2099-01-01T00:00:00.000000Z",
                "updated_at": "2099-01-01T00:00:01.000000Z",
            }
        return WorkerHTTPResponse(status=200, body=json.dumps(response).encode())

    worker = RemoteEvaluationWorker(
        base_url="https://api.example.test",
        access_token="remote-worker-admin-token-000000000001",
        worker_id="remote-worker-a",
        transport=transport,
    )

    with pytest.raises(RemoteWorkerError, match=r"^remote worker API request failed$"):
        worker.run_once()


def test_remote_worker_credentials_are_not_recoverable_from_public_tracebacks() -> None:
    import pytest

    from evalforge.remote_worker import RemoteEvaluationWorker, RemoteWorkerError

    token = "traceback-worker-token-000000000000000001"
    with pytest.raises(ValueError) as invalid_configuration:
        RemoteEvaluationWorker(
            base_url="http://api.example.test",
            access_token=token,
            worker_id="remote-worker-a",
        )
    assert token not in _traceback_worker_credentials(invalid_configuration.value)

    worker = RemoteEvaluationWorker(
        base_url="https://api.example.test",
        access_token=token,
        worker_id="remote-worker-a",
        transport=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("transport failed")),
    )
    with pytest.raises(RemoteWorkerError) as execution_failure:
        worker.run_once()
    assert token not in _traceback_worker_credentials(execution_failure.value)


def test_remote_worker_rejects_claims_beyond_the_attempt_limit_before_evaluation() -> None:
    import pytest

    from evalforge.remote_worker import (
        RemoteEvaluationWorker,
        RemoteWorkerError,
        WorkerHTTPResponse,
    )

    paths: list[str] = []

    def transport(**kwargs: object) -> WorkerHTTPResponse:
        request = kwargs["request"]
        assert isinstance(request, Request)
        paths.append(urlsplit(request.full_url).path)
        response = {
            "job": {
                "job_id": "a" * 64,
                "suite_id": "b" * 64,
                "suite": {},
                "candidate": {},
                "attempts": 4,
                "max_attempts": 3,
                "lease_token": "T" * 43,
                "lease_expires_at": "2099-01-01T00:01:00.000000Z",
            }
        }
        return WorkerHTTPResponse(status=200, body=json.dumps(response).encode())

    worker = RemoteEvaluationWorker(
        base_url="https://api.example.test",
        access_token="remote-worker-admin-token-000000000001",
        worker_id="remote-worker-a",
        transport=transport,
    )

    with pytest.raises(RemoteWorkerError, match=r"^remote worker API request failed$"):
        worker.run_once()
    assert paths == ["/api/v1/worker-claims"]


def test_remote_worker_sanitizes_evaluation_base_exceptions(monkeypatch: object) -> None:
    import pytest

    from evalforge import remote_worker as remote_worker_module
    from evalforge.contracts import EvaluationSuite
    from evalforge.provenance import canonical_json_sha256
    from evalforge.remote_worker import (
        RemoteEvaluationWorker,
        RemoteWorkerError,
        WorkerHTTPResponse,
    )

    class EvaluationAbort(BaseException):
        pass

    suite = EvaluationSuite.model_validate(
        {
            "schema_version": 1,
            "name": "private-suite-name",
            "minimum_pass_rate": 1.0,
            "cases": [
                {
                    "case_id": "private-case",
                    "prompt": "private-suite-prompt",
                    "expected_output": "Allowed",
                }
            ],
        }
    ).model_dump(mode="json", exclude_none=True)
    suite_id = canonical_json_sha256(suite)
    lease_token = "L" * 43

    def abort_evaluation(**_kwargs: object) -> object:
        raise EvaluationAbort("private-evaluation-abort")

    monkeypatch.setattr(remote_worker_module, "_evaluation_report_payload", abort_evaluation)  # type: ignore[attr-defined]

    def transport(**_kwargs: object) -> WorkerHTTPResponse:
        response = {
            "job": {
                "job_id": "a" * 64,
                "suite_id": suite_id,
                "suite": suite,
                "candidate": {"private-case": "private-candidate-output"},
                "attempts": 1,
                "max_attempts": 3,
                "lease_token": lease_token,
                "lease_expires_at": "2099-01-01T00:01:00.000000Z",
            }
        }
        return WorkerHTTPResponse(status=200, body=json.dumps(response).encode())

    worker = RemoteEvaluationWorker(
        base_url="https://api.example.test",
        access_token="remote-worker-admin-token-000000000001",
        worker_id="remote-worker-a",
        transport=transport,
    )
    with pytest.raises(RemoteWorkerError) as rejected:
        worker.run_once()
    evidence = _exception_evidence(rejected.value)
    assert "private-suite-prompt" not in evidence
    assert "private-candidate-output" not in evidence
    assert lease_token not in evidence


def test_remote_worker_command_does_not_retain_token_in_public_tracebacks(
    monkeypatch: object, capsys: object
) -> None:
    import pytest

    from evalforge.remote_worker import RemoteEvaluationWorker
    from evalforge.remote_worker_service import main

    class WorkerAbort(BaseException):
        pass

    token = "command-traceback-token-00000000000000001"
    monkeypatch.setenv("EVALFORGE_WORKER_API_URL", "https://api.example.test")  # type: ignore[attr-defined]
    monkeypatch.setenv("EVALFORGE_WORKER_ACCESS_TOKEN", token)  # type: ignore[attr-defined]
    with pytest.raises(SystemExit) as invalid_arguments:
        main([])
    assert token not in _exception_evidence(invalid_arguments.value)

    monkeypatch.setenv("EVALFORGE_WORKER_ACCESS_TOKEN", token)  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        RemoteEvaluationWorker,
        "run_once",
        lambda _worker: (_ for _ in ()).throw(WorkerAbort("private-worker-abort")),
    )
    assert main(["--worker-id", "remote-worker-a"]) == 1
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert json.loads(captured.err.splitlines()[-1]) == {
        "error": "remote_worker_execution_failed",
        "processed": False,
    }
    assert token not in captured.err


def test_remote_worker_uses_the_validated_origin_after_public_field_mutation() -> None:
    from evalforge.remote_worker import RemoteEvaluationWorker, WorkerHTTPResponse

    requests: list[tuple[str, str | None]] = []

    def transport(**kwargs: object) -> WorkerHTTPResponse:
        request = kwargs["request"]
        assert isinstance(request, Request)
        requests.append((request.full_url, request.get_header("Authorization")))
        return WorkerHTTPResponse(status=200, body=b'{"job":null}')

    token = "remote-worker-admin-token-000000000001"
    worker = RemoteEvaluationWorker(
        base_url="https://api.example.test",
        access_token=token,
        worker_id="remote-worker-a",
        transport=transport,
    )
    object.__setattr__(worker, "base_url", "http://attacker.example")

    assert worker.run_once() is None
    assert requests == [("https://api.example.test/api/v1/worker-claims", f"Bearer {token}")]


def test_remote_worker_rejects_hostile_subclasses_without_retaining_credentials() -> None:
    import pytest

    from evalforge.remote_worker import RemoteEvaluationWorker

    class HostileWorker(RemoteEvaluationWorker):
        def __hash__(self) -> int:
            raise RuntimeError("hostile worker hash")

    token = "hostile-subclass-token-00000000000000001"
    with pytest.raises(ValueError, match=r"^remote worker configuration is invalid$") as rejected:
        HostileWorker(
            base_url="https://api.example.test",
            access_token=token,
            worker_id="remote-worker-a",
        )

    assert token not in _exception_evidence(rejected.value)
    assert rejected.value.__cause__ is None
    assert rejected.value.__context__ is None


def test_remote_worker_rejects_claims_with_mismatched_suite_provenance() -> None:
    import pytest

    from evalforge.contracts import EvaluationSuite
    from evalforge.remote_worker import (
        RemoteEvaluationWorker,
        RemoteWorkerError,
        WorkerHTTPResponse,
    )

    suite = EvaluationSuite.model_validate(
        {
            "schema_version": 1,
            "name": "remote-worker-provenance",
            "minimum_pass_rate": 1.0,
            "cases": [
                {
                    "case_id": "policy",
                    "prompt": "State the synthetic policy.",
                    "expected_output": "Allowed",
                }
            ],
        }
    ).model_dump(mode="json", exclude_none=True)
    paths: list[str] = []

    def transport(**kwargs: object) -> WorkerHTTPResponse:
        request = kwargs["request"]
        assert isinstance(request, Request)
        paths.append(urlsplit(request.full_url).path)
        response = {
            "job": {
                "job_id": "a" * 64,
                "suite_id": "b" * 64,
                "suite": suite,
                "candidate": {"policy": "Allowed"},
                "attempts": 1,
                "max_attempts": 3,
                "lease_token": "T" * 43,
                "lease_expires_at": "2099-01-01T00:01:00.000000Z",
            }
        }
        return WorkerHTTPResponse(status=200, body=json.dumps(response).encode())

    worker = RemoteEvaluationWorker(
        base_url="https://api.example.test",
        access_token="remote-worker-admin-token-000000000001",
        worker_id="remote-worker-a",
        transport=transport,
    )

    with pytest.raises(RemoteWorkerError, match=r"^remote worker API request failed$"):
        worker.run_once()
    assert paths == ["/api/v1/worker-claims"]


def test_stdlib_transport_stops_address_fallback_when_total_deadline_expires(
    monkeypatch: object,
) -> None:
    import socket

    import pytest

    from evalforge import remote_worker as remote_worker_module
    from evalforge.remote_worker import _stdlib_transport

    created: list[object] = []
    now = 0.0

    def clock() -> float:
        nonlocal now
        now += 0.02
        return now

    class FakeSocket:
        def __init__(self, *_args: object) -> None:
            created.append(self)

        def settimeout(self, _timeout: float) -> None:
            pass

        def connect(self, _address: object) -> None:
            raise OSError("synthetic refusal")

        def close(self) -> None:
            pass

    class FakeConnection:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.sock: FakeSocket | None = None

        def close(self) -> None:
            pass

    monkeypatch.setattr(remote_worker_module, "monotonic", clock, raising=False)  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (f"203.0.113.{index}", 443))
            for index in range(1, 11)
        ],
    )
    monkeypatch.setattr(socket, "socket", FakeSocket)  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        remote_worker_module.http.client,
        "HTTPSConnection",
        FakeConnection,
    )

    with pytest.raises(TimeoutError):
        _stdlib_transport(
            request=Request("https://api.example.test/api/v1/worker-claims", method="POST"),
            connect_timeout=0.05,
            read_timeout=0.05,
            max_response_bytes=1024,
        )

    assert len(created) == 1
