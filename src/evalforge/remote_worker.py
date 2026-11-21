"""Bounded HTTP-backed execution for tenant-scoped evaluation jobs."""

from __future__ import annotations

import http.client
import json
import socket
import ssl
from collections.abc import Callable
from dataclasses import dataclass, field
from math import isfinite
from queue import Empty, Queue
from threading import Lock, Thread, Timer
from time import monotonic
from typing import Literal, TypeAlias, TypeVar, cast
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request
from weakref import ReferenceType, ref

from pydantic import BaseModel, SecretStr

from evalforge.api import (
    ClaimedEvaluationJobResponse,
    EvaluationJobResponse,
    WorkerClaimRequest,
    WorkerClaimResponse,
    WorkerCompletionRequest,
    WorkerFailureRequest,
    WorkerRetryRequest,
)
from evalforge.provenance import JsonValue, canonical_json_sha256
from evalforge.service import _evaluation_report_payload

MAX_WORKER_RESPONSE_BYTES = 1024 * 1024


@dataclass(frozen=True)
class WorkerHTTPResponse:
    """Bounded response returned by the injectable worker HTTP transport."""

    status: int
    body: bytes


WorkerTransport = Callable[..., WorkerHTTPResponse]
_ResponseModel = TypeVar("_ResponseModel", bound=BaseModel)


@dataclass(frozen=True)
class _RemoteWorkerConfig:
    base_url: str
    access_token: SecretStr
    worker_id: str
    lease_seconds: int
    retry_backoff_seconds: int
    timeout_seconds: float
    transport: WorkerTransport


_WORKER_CONFIGS: dict[int, tuple[ReferenceType[object], _RemoteWorkerConfig]] = {}
_WORKER_CONFIGS_LOCK = Lock()


class RemoteWorkerError(RuntimeError):
    """A redaction-safe remote worker configuration, transport, or protocol failure."""


class _DuplicateResponseKeyError(ValueError):
    pass


def _unique_response_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateResponseKeyError
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> object:
    raise ValueError("non-finite JSON number")


def _remove_worker_config(key: int, ended_reference: ReferenceType[object]) -> None:
    with _WORKER_CONFIGS_LOCK:
        current = _WORKER_CONFIGS.get(key)
        if current is not None and current[0] is ended_reference:
            _WORKER_CONFIGS.pop(key, None)


def _install_worker_config(worker: object, config: _RemoteWorkerConfig) -> bool:
    try:
        key = id(worker)

        def remove_config(ended_reference: ReferenceType[object]) -> None:
            _remove_worker_config(key, ended_reference)

        worker_reference = ref(worker, remove_config)
        with _WORKER_CONFIGS_LOCK:
            _WORKER_CONFIGS[key] = (worker_reference, config)
    except BaseException:
        return False
    return True


def _get_worker_config(worker: object) -> _RemoteWorkerConfig | None:
    with _WORKER_CONFIGS_LOCK:
        current = _WORKER_CONFIGS.get(id(worker))
        if current is None or current[0]() is not worker:
            return None
        return current[1]


def _discard_worker_config(worker: object) -> None:
    key = id(worker)
    with _WORKER_CONFIGS_LOCK:
        current = _WORKER_CONFIGS.get(key)
        if current is not None and current[0]() is worker:
            _WORKER_CONFIGS.pop(key, None)


def _build_worker_config(
    *,
    worker: object,
    base_url: object,
    access_token: object,
    worker_id: object,
    lease_seconds: object,
    retry_backoff_seconds: object,
    timeout_seconds: object,
    transport: object,
) -> _RemoteWorkerConfig | None:
    try:
        if type(worker) is not RemoteEvaluationWorker:
            return None
        if type(access_token) is SecretStr:
            token = access_token.get_secret_value()
        elif type(access_token) is str:
            token = access_token
        else:
            return None
        if not (
            32 <= len(token) <= 512 and all(0x21 <= ord(character) <= 0x7E for character in token)
        ):
            return None
        if (
            type(base_url) is not str
            or type(worker_id) is not str
            or type(lease_seconds) is not int
            or type(retry_backoff_seconds) is not int
            or not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
        ):
            return None
        numeric_timeout = float(timeout_seconds)
        parsed = urlsplit(base_url)
        _ = parsed.port
        WorkerClaimRequest(worker_id=worker_id, lease_seconds=lease_seconds)
        WorkerRetryRequest(
            worker_id=worker_id,
            lease_token="A" * 43,
            backoff_seconds=retry_backoff_seconds,
        )
        loopback_host = parsed.hostname is not None and parsed.hostname.lower() in {
            "127.0.0.1",
            "::1",
            "localhost",
        }
        valid_url = (
            (parsed.scheme == "https" or (parsed.scheme == "http" and loopback_host))
            and parsed.hostname is not None
            and parsed.username is None
            and parsed.password is None
            and parsed.path in {"", "/"}
            and "?" not in base_url
            and "#" not in base_url
            and not parsed.query
            and not parsed.fragment
        )
        valid_timeout = isfinite(numeric_timeout) and 0 < numeric_timeout <= 120
        if not (valid_url and valid_timeout and callable(transport)):
            return None
        return _RemoteWorkerConfig(
            base_url=base_url.rstrip("/"),
            access_token=SecretStr(token),
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            retry_backoff_seconds=retry_backoff_seconds,
            timeout_seconds=numeric_timeout,
            transport=transport,
        )
    except BaseException:
        return None


def _activate_worker(worker: object, config: _RemoteWorkerConfig) -> bool:
    try:
        object.__setattr__(worker, "base_url", config.base_url)
        object.__setattr__(worker, "access_token", SecretStr(""))
        object.__setattr__(worker, "worker_id", config.worker_id)
        object.__setattr__(worker, "lease_seconds", config.lease_seconds)
        object.__setattr__(worker, "retry_backoff_seconds", config.retry_backoff_seconds)
        object.__setattr__(worker, "timeout_seconds", config.timeout_seconds)
        object.__setattr__(worker, "transport", config.transport)
        if not _install_worker_config(worker, config):
            raise ValueError
    except BaseException:
        _discard_worker_config(worker)
        return False
    return True


def _validate_job_outcome(
    response: EvaluationJobResponse,
    *,
    claimed: ClaimedEvaluationJobResponse,
    operation: Literal["complete", "fail", "retry"],
    expected_run_id: str | None,
) -> EvaluationJobResponse:
    common_valid = (
        response.job_id == claimed.job_id
        and response.attempts == claimed.attempts
        and response.max_attempts == claimed.max_attempts
    )
    if operation == "complete":
        outcome_valid = (
            response.status == "succeeded"
            and expected_run_id is not None
            and response.run_id == expected_run_id
            and response.failure_code is None
        )
    elif operation == "fail":
        outcome_valid = (
            response.status == "failed"
            and response.run_id is None
            and response.failure_code == "invalid_evaluation"
        )
    elif claimed.attempts >= claimed.max_attempts:
        outcome_valid = (
            response.status == "failed"
            and response.run_id is None
            and response.failure_code == "attempt_limit_exhausted"
        )
    else:
        outcome_valid = (
            response.status == "queued"
            and response.run_id is None
            and response.failure_code is None
        )
    if not (common_valid and outcome_valid):
        raise RemoteWorkerError("remote worker API request failed")
    return response


_SocketAddress: TypeAlias = tuple[str, int] | tuple[str, int, int, int]
_ResolvedAddress: TypeAlias = tuple[int, int, int, _SocketAddress]


def _resolve_socket_addresses(host: str, port: int, timeout: float) -> list[_ResolvedAddress]:
    result_queue: Queue[object] = Queue(maxsize=1)

    def resolve() -> None:
        try:
            result_queue.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except BaseException as error:
            result_queue.put(error)

    resolver = Thread(target=resolve, name="evalforge-worker-dns", daemon=True)
    resolver.start()
    try:
        result = result_queue.get(timeout=timeout)
    except Empty:
        raise TimeoutError from None
    if isinstance(result, BaseException):
        raise result from None
    addresses = cast(
        list[tuple[int, int, int, str, _SocketAddress]],
        result,
    )
    if not addresses:
        raise OSError("remote worker API address resolution failed")
    return [
        (family, socket_type, protocol, address)
        for family, socket_type, protocol, _canonical_name, address in addresses
    ]


def _stdlib_transport(
    *,
    request: Request,
    connect_timeout: float,
    read_timeout: float,
    max_response_bytes: int,
) -> WorkerHTTPResponse:
    deadline = monotonic() + min(connect_timeout, read_timeout)

    def remaining_seconds() -> float:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError
        return remaining

    parsed = urlsplit(request.full_url)
    host = parsed.hostname
    assert host is not None
    timeout = min(connect_timeout, read_timeout)
    if parsed.scheme == "https":
        secure_connection = http.client.HTTPSConnection(host, parsed.port, timeout=timeout)
        tls_context = ssl.create_default_context()
        tls_context.set_alpn_protocols(["http/1.1"])
        connection: http.client.HTTPConnection = secure_connection
    else:
        secure_connection = None
        tls_context = None
        connection = http.client.HTTPConnection(host, parsed.port, timeout=timeout)
    path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    deadline_timer = Timer(remaining_seconds(), connection.close)
    deadline_timer.daemon = True
    deadline_timer.start()
    try:
        raw_socket: socket.socket | None = None
        if connection.sock is None:
            port = parsed.port if parsed.port is not None else (443 if secure_connection else 80)
            addresses = _resolve_socket_addresses(
                host,
                port,
                remaining_seconds(),
            )
            for family, socket_type, protocol, address in addresses:
                candidate_socket: socket.socket | None = None
                try:
                    candidate_socket = socket.socket(family, socket_type, protocol)
                    connection.sock = candidate_socket
                    candidate_socket.settimeout(min(connect_timeout, remaining_seconds()))
                    candidate_socket.connect(address)
                    if secure_connection is not None and tls_context is not None:
                        candidate_socket.settimeout(min(connect_timeout, remaining_seconds()))
                        candidate_socket = tls_context.wrap_socket(
                            candidate_socket,
                            server_hostname=host,
                        )
                        connection.sock = candidate_socket
                    remaining_seconds()
                except OSError:
                    if candidate_socket is not None:
                        candidate_socket.close()
                    connection.sock = None
                    remaining_seconds()
                    continue
                raw_socket = candidate_socket
                break
            if raw_socket is None:
                raise OSError("remote worker API connection failed") from None
        connection.request(
            request.get_method(),
            path,
            body=request.data,
            headers=dict(request.header_items()),
        )
        if connection.sock is not None:
            connection.sock.settimeout(min(read_timeout, remaining_seconds()))
        response = connection.getresponse()
        remaining_seconds()
        body = bytearray()
        while len(body) <= max_response_bytes:
            if connection.sock is not None:
                connection.sock.settimeout(min(read_timeout, remaining_seconds()))
            chunk = response.read1(min(64 * 1024, max_response_bytes + 1 - len(body)))
            remaining_seconds()
            if not chunk:
                break
            body.extend(chunk)
        return WorkerHTTPResponse(
            status=response.status,
            body=bytes(body),
        )
    finally:
        deadline_timer.cancel()
        connection.close()


@dataclass(frozen=True, eq=False, init=False)
class RemoteEvaluationWorker:
    """Claim and complete one evaluation through the authenticated worker API."""

    base_url: str
    access_token: SecretStr = field(init=False, repr=False)
    worker_id: str
    lease_seconds: int = 60
    retry_backoff_seconds: int = 5
    timeout_seconds: float = 30.0
    transport: WorkerTransport = field(default=_stdlib_transport, repr=False)

    def __init__(
        self,
        *,
        base_url: str,
        access_token: SecretStr | str,
        worker_id: str,
        lease_seconds: int = 60,
        retry_backoff_seconds: int = 5,
        timeout_seconds: float = 30.0,
        transport: WorkerTransport = _stdlib_transport,
    ) -> None:
        config = _build_worker_config(
            worker=self,
            base_url=base_url,
            access_token=access_token,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            retry_backoff_seconds=retry_backoff_seconds,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )
        del access_token
        initialized = config is not None and _activate_worker(self, config)
        if not initialized:
            config = None
            raise ValueError("remote worker configuration is invalid") from None

    def _post(
        self,
        path: str,
        payload: BaseModel,
        response_model: type[_ResponseModel],
    ) -> _ResponseModel:
        validated_response = self._perform_post_safely(path, payload, response_model)
        if validated_response is None:
            raise RemoteWorkerError("remote worker API request failed")
        return validated_response

    def _perform_post_safely(
        self,
        path: str,
        payload: BaseModel,
        response_model: type[_ResponseModel],
    ) -> _ResponseModel | None:
        try:
            config = _get_worker_config(self)
            if config is None:
                raise ValueError
            body = json.dumps(
                payload.model_dump(mode="json"),
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            request = Request(
                f"{config.base_url}{path}",
                data=body,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {config.access_token.get_secret_value()}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            response = config.transport(
                request=request,
                connect_timeout=config.timeout_seconds,
                read_timeout=config.timeout_seconds,
                max_response_bytes=MAX_WORKER_RESPONSE_BYTES,
            )
            if (
                type(response) is not WorkerHTTPResponse
                or type(response.status) is not int
                or type(response.body) is not bytes
                or response.status != 200
                or len(response.body) > MAX_WORKER_RESPONSE_BYTES
            ):
                raise ValueError
            decoded = json.loads(
                response.body.decode("utf-8"),
                object_pairs_hook=_unique_response_object,
                parse_constant=_reject_json_constant,
            )
            validated_response = response_model.model_validate(decoded, strict=True)
        except BaseException:
            return None
        return validated_response

    def run_once(self) -> EvaluationJobResponse | None:
        """Claim and complete one available remote job, or return ``None``."""

        request_succeeded, completed = self._run_once_safely()
        if not request_succeeded:
            raise RemoteWorkerError("remote worker API request failed")
        return completed

    def _run_once_safely(self) -> tuple[bool, EvaluationJobResponse | None]:
        try:
            return True, self._run_once_impl()
        except BaseException:
            return False, None

    def _run_once_impl(self) -> EvaluationJobResponse | None:
        config = _get_worker_config(self)
        if config is None:
            raise RemoteWorkerError("remote worker API request failed")
        claim = self._post(
            "/api/v1/worker-claims",
            WorkerClaimRequest(
                worker_id=config.worker_id,
                lease_seconds=config.lease_seconds,
            ),
            WorkerClaimResponse,
        )
        if claim.job is None:
            return None
        claimed = claim.job
        if claimed.attempts > claimed.max_attempts or claimed.suite_id != canonical_json_sha256(
            cast(JsonValue, claimed.suite)
        ):
            raise RemoteWorkerError("remote worker API request failed")
        try:
            _suite_name, report = _evaluation_report_payload(
                suite_id=claimed.suite_id,
                suite_payload=cast(JsonValue, claimed.suite),
                candidate_payload=cast(JsonValue, claimed.candidate),
            )
        except ValueError:
            failure = WorkerFailureRequest(
                worker_id=config.worker_id,
                lease_token=claimed.lease_token,
                failure_code="invalid_evaluation",
            )
            return _validate_job_outcome(
                self._post(
                    f"/api/v1/worker-claims/{claimed.job_id}/fail",
                    failure,
                    EvaluationJobResponse,
                ),
                claimed=claimed,
                operation="fail",
                expected_run_id=None,
            )
        except Exception:
            retry = WorkerRetryRequest(
                worker_id=config.worker_id,
                lease_token=claimed.lease_token,
                backoff_seconds=min(
                    3_600,
                    config.retry_backoff_seconds * (1 << (claimed.attempts - 1)),
                ),
            )
            return _validate_job_outcome(
                self._post(
                    f"/api/v1/worker-claims/{claimed.job_id}/retry",
                    retry,
                    EvaluationJobResponse,
                ),
                claimed=claimed,
                operation="retry",
                expected_run_id=None,
            )
        if not isinstance(report, dict):
            raise RemoteWorkerError("remote worker evaluation report is invalid")
        completion = WorkerCompletionRequest(
            worker_id=config.worker_id,
            lease_token=claimed.lease_token,
            report=cast(dict[str, object], report),
        )
        expected_run_id = completion.report.get("run_id")
        if not isinstance(expected_run_id, str):
            raise RemoteWorkerError("remote worker evaluation report is invalid")
        return _validate_job_outcome(
            self._post(
                f"/api/v1/worker-claims/{claimed.job_id}/complete",
                completion,
                EvaluationJobResponse,
            ),
            claimed=claimed,
            operation="complete",
            expected_run_id=expected_run_id,
        )
