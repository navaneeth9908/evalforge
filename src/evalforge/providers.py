"""Provider-neutral generation contracts and a bounded OpenAI-compatible adapter."""

from __future__ import annotations

import http.client
import json
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from math import isfinite
from typing import Protocol, runtime_checkable
from urllib.error import URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request

MAX_PROVIDER_RESPONSE_BYTES = 1024 * 1024
MAX_PROVIDER_TEXT_CHARS = 65_536
MAX_SAFE_INTEGER = 9_007_199_254_740_991


@dataclass(frozen=True)
class AttemptEvidence:
    """Secret-free evidence for one provider attempt."""

    attempt: int
    outcome: str
    elapsed_seconds: float
    retry_delay_seconds: float | None = None


class ProviderError(RuntimeError):
    """A sanitized provider transport or protocol failure."""

    def __init__(self, message: str, *, attempts: tuple[AttemptEvidence, ...] = ()) -> None:
        super().__init__(message)
        self.attempts = attempts


@dataclass(frozen=True)
class GenerationRequest:
    """A provider-neutral single-prompt generation request."""

    prompt: str


@dataclass(frozen=True)
class TokenUsage:
    """Token counts reported by a model provider."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class GenerationResult:
    """Normalized text, usage, and secret-free attempt evidence."""

    text: str
    model: str
    usage: TokenUsage
    attempts: tuple[AttemptEvidence, ...] = ()


@runtime_checkable
class CandidateAdapter(Protocol):
    """Provider-neutral boundary used to generate candidate output."""

    def generate(self, request: GenerationRequest) -> GenerationResult:
        """Generate one candidate result."""


@dataclass(frozen=True)
class DeadlineConfig:
    """Finite per-phase and end-to-end request deadlines."""

    connect_seconds: float = 5.0
    read_seconds: float = 30.0
    overall_seconds: float = 60.0

    def __post_init__(self) -> None:
        phase_values = (self.connect_seconds, self.read_seconds)
        invalid_phase = any(
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not isfinite(value)
            or value <= 0
            or value > 120
            for value in phase_values
        )
        invalid_overall = (
            not isinstance(self.overall_seconds, (int, float))
            or isinstance(self.overall_seconds, bool)
            or not isfinite(self.overall_seconds)
            or self.overall_seconds <= 0
            or self.overall_seconds > 600
        )
        if invalid_phase or invalid_overall:
            raise ValueError("provider deadline configuration is invalid")


@dataclass(frozen=True)
class RetryConfig:
    """Bounded exponential retry policy."""

    max_attempts: int = 3
    initial_backoff_seconds: float = 0.25
    max_backoff_seconds: float = 4.0
    jitter_ratio: float = 0.2

    def __post_init__(self) -> None:
        valid_attempts = (
            isinstance(self.max_attempts, int)
            and not isinstance(self.max_attempts, bool)
            and 1 <= self.max_attempts <= 10
        )
        numeric_values = (
            self.initial_backoff_seconds,
            self.max_backoff_seconds,
            self.jitter_ratio,
        )
        valid_numeric = all(
            isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)
            for value in numeric_values
        )
        valid_ranges = valid_numeric and (
            0 <= self.initial_backoff_seconds <= self.max_backoff_seconds <= 60
            and 0 <= self.jitter_ratio <= 1
        )
        if not (valid_attempts and valid_ranges):
            raise ValueError("provider retry configuration is invalid")


@dataclass(frozen=True)
class ProviderHTTPResponse:
    """Bounded HTTP response supplied to the protocol parser."""

    status: int
    body: bytes


class ProviderTransport(Protocol):
    """Injectable HTTP boundary used by deterministic tests."""

    def __call__(
        self,
        *,
        request: Request,
        connect_timeout: float,
        read_timeout: float,
        max_response_bytes: int,
    ) -> ProviderHTTPResponse: ...


class _DuplicateResponseKeyError(ValueError):
    """Raised when a provider response contains duplicate object keys."""


def _unique_response_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateResponseKeyError
        result[key] = value
    return result


def _strict_token_count(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= MAX_SAFE_INTEGER:
        raise ValueError
    return value


def _parse_generation_result(raw_body: bytes) -> GenerationResult:
    if len(raw_body) > MAX_PROVIDER_RESPONSE_BYTES:
        raise ValueError
    decoded = json.loads(raw_body.decode("utf-8"), object_pairs_hook=_unique_response_object)
    if not isinstance(decoded, dict):
        raise ValueError
    model = decoded["model"]
    choices = decoded["choices"]
    usage = decoded["usage"]
    if (
        not isinstance(model, str)
        or not 0 < len(model) <= 256
        or not isinstance(choices, list)
        or len(choices) != 1
        or not isinstance(choices[0], dict)
        or not isinstance(usage, dict)
    ):
        raise ValueError
    message = choices[0]["message"]
    if not isinstance(message, dict):
        raise ValueError
    content = message["content"]
    if not isinstance(content, str) or len(content) > MAX_PROVIDER_TEXT_CHARS:
        raise ValueError
    prompt_tokens = _strict_token_count(usage["prompt_tokens"])
    completion_tokens = _strict_token_count(usage["completion_tokens"])
    total_tokens = _strict_token_count(usage["total_tokens"])
    if prompt_tokens + completion_tokens != total_tokens:
        raise ValueError
    return GenerationResult(
        text=content,
        model=model,
        usage=TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        ),
    )


def _stdlib_transport(
    *,
    request: Request,
    connect_timeout: float,
    read_timeout: float,
    max_response_bytes: int,
) -> ProviderHTTPResponse:
    parsed = urlsplit(request.full_url)
    connection_type = (
        http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    )
    host = parsed.hostname
    assert host is not None
    connection = connection_type(host, parsed.port, timeout=connect_timeout)
    path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    try:
        connection.request(
            request.get_method(),
            path,
            body=request.data,
            headers=dict(request.header_items()),
        )
        if connection.sock is not None:
            connection.sock.settimeout(read_timeout)
        response = connection.getresponse()
        return ProviderHTTPResponse(
            status=response.status,
            body=response.read(max_response_bytes + 1),
        )
    finally:
        connection.close()


def _classify_status(status: int) -> tuple[str, bool]:
    if status == 429:
        return "rate_limited", True
    if status in {408, 425} or 500 <= status <= 599:
        return "retryable_http", True
    return "nonretryable_http", False


class OpenAICompatibleAdapter:
    """Synchronous adapter with bounded deadlines, retries, and response parsing."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout_seconds: float | None = None,
        deadlines: DeadlineConfig | None = None,
        retry: RetryConfig | None = None,
        transport: ProviderTransport | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        random_value: Callable[[], float] = random.random,
    ) -> None:
        try:
            parsed_url = urlsplit(base_url)
            _ = parsed_url.port
        except (TypeError, ValueError) as exc:
            raise ValueError("OpenAI-compatible adapter configuration is invalid") from exc
        valid_url = (
            parsed_url.scheme in {"http", "https"}
            and parsed_url.hostname is not None
            and parsed_url.username is None
            and parsed_url.password is None
            and not parsed_url.query
            and not parsed_url.fragment
        )
        valid_model = isinstance(model, str) and 0 < len(model.strip()) <= 256
        valid_key = api_key is None or (isinstance(api_key, str) and 0 < len(api_key) <= 4096)
        if timeout_seconds is not None:
            if deadlines is not None:
                raise ValueError("OpenAI-compatible adapter configuration is invalid")
            try:
                deadlines = DeadlineConfig(
                    connect_seconds=timeout_seconds,
                    read_seconds=timeout_seconds,
                    overall_seconds=timeout_seconds,
                )
            except ValueError:
                raise ValueError("OpenAI-compatible adapter configuration is invalid") from None
        selected_deadlines = deadlines or DeadlineConfig()
        selected_retry = retry or RetryConfig(max_attempts=1)
        valid_policy = isinstance(selected_deadlines, DeadlineConfig) and isinstance(
            selected_retry, RetryConfig
        )
        if not (valid_url and valid_model and valid_key and valid_policy):
            raise ValueError("OpenAI-compatible adapter configuration is invalid")
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._model = model
        self._api_key = api_key
        self._deadlines = selected_deadlines
        self._retry = selected_retry
        self._transport = transport or _stdlib_transport
        self._monotonic = monotonic
        self._sleep = sleep
        self._random_value = random_value

    def _retry_delay(self, attempt: int) -> float:
        random_value = self._random_value()
        if (
            not isinstance(random_value, (int, float))
            or isinstance(random_value, bool)
            or not isfinite(random_value)
            or not 0 <= random_value <= 1
        ):
            raise ProviderError("OpenAI-compatible retry randomness is invalid")
        base = min(
            self._retry.max_backoff_seconds,
            self._retry.initial_backoff_seconds * (2 ** (attempt - 1)),
        )
        jitter = 1 + self._retry.jitter_ratio * (2 * random_value - 1)
        return float(min(self._retry.max_backoff_seconds, max(0.0, base * jitter)))

    def generate(self, request: GenerationRequest) -> GenerationResult:
        """Generate text while retaining bounded, secret-free attempt evidence."""
        payload = json.dumps(
            {
                "model": self._model,
                "messages": [{"role": "user", "content": request.prompt}],
                "temperature": 0,
            }
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._api_key is not None:
            headers["Authorization"] = f"Bearer {self._api_key}"
        http_request = Request(self._url, data=payload, headers=headers, method="POST")
        started = self._monotonic()
        attempt_started = started
        evidence: list[AttemptEvidence] = []

        for attempt in range(1, self._retry.max_attempts + 1):
            remaining = self._deadlines.overall_seconds - (attempt_started - started)
            if remaining <= 0:
                raise ProviderError(
                    "OpenAI-compatible overall deadline exceeded",
                    attempts=tuple(evidence),
                )
            response: ProviderHTTPResponse | None = None
            try:
                response = self._transport(
                    request=http_request,
                    connect_timeout=min(self._deadlines.connect_seconds, remaining),
                    read_timeout=min(self._deadlines.read_seconds, remaining),
                    max_response_bytes=MAX_PROVIDER_RESPONSE_BYTES,
                )
                outcome, retryable = _classify_status(response.status)
            except TimeoutError:
                outcome, retryable = "timeout", True
            except (URLError, OSError, http.client.HTTPException):
                outcome, retryable = "transport_error", True
            except ProviderError as exc:
                if exc.attempts:
                    raise
                outcome, retryable = "transport_error", True

            ended = self._monotonic()
            elapsed = max(0.0, ended - attempt_started)
            if ended - started > self._deadlines.overall_seconds:
                evidence.append(AttemptEvidence(attempt, "overall_timeout", elapsed))
                raise ProviderError(
                    "OpenAI-compatible overall deadline exceeded",
                    attempts=tuple(evidence),
                ) from None

            if response is not None and response.status == 200:
                try:
                    result = _parse_generation_result(response.body)
                except (
                    KeyError,
                    TypeError,
                    ValueError,
                    UnicodeDecodeError,
                    json.JSONDecodeError,
                ):
                    evidence.append(AttemptEvidence(attempt, "malformed_response", elapsed))
                    raise ProviderError(
                        "OpenAI-compatible response is invalid",
                        attempts=tuple(evidence),
                    ) from None
                evidence.append(AttemptEvidence(attempt, "success", elapsed))
                return replace(result, attempts=tuple(evidence))

            if not retryable:
                evidence.append(AttemptEvidence(attempt, outcome, elapsed))
                status = response.status if response is not None else 0
                raise ProviderError(
                    f"OpenAI-compatible request failed with HTTP {status}",
                    attempts=tuple(evidence),
                ) from None
            if attempt == self._retry.max_attempts:
                evidence.append(AttemptEvidence(attempt, outcome, elapsed))
                message = (
                    "OpenAI-compatible request timed out"
                    if self._retry.max_attempts == 1 and outcome == "timeout"
                    else "OpenAI-compatible retry attempts exhausted"
                )
                raise ProviderError(message, attempts=tuple(evidence)) from None

            delay = self._retry_delay(attempt)
            evidence.append(AttemptEvidence(attempt, outcome, elapsed, delay))
            if ended - started + delay >= self._deadlines.overall_seconds:
                raise ProviderError(
                    "OpenAI-compatible overall deadline exceeded",
                    attempts=tuple(evidence),
                )
            self._sleep(delay)
            attempt_started = self._monotonic()

        raise AssertionError("bounded provider loop exited unexpectedly")


def openai_adapter_from_environment(
    environment: Mapping[str, str],
) -> OpenAICompatibleAdapter:
    """Load explicit OpenAI-compatible settings without exposing their values."""
    try:
        base_url = environment["EVALFORGE_OPENAI_BASE_URL"]
        model = environment["EVALFORGE_OPENAI_MODEL"]
        legacy_timeout = environment.get("EVALFORGE_OPENAI_TIMEOUT_SECONDS")
        api_key = environment.get("EVALFORGE_OPENAI_API_KEY") or None
        if legacy_timeout is not None:
            return OpenAICompatibleAdapter(
                base_url=base_url,
                model=model,
                api_key=api_key,
                timeout_seconds=float(legacy_timeout),
            )
        return OpenAICompatibleAdapter(
            base_url=base_url,
            model=model,
            api_key=api_key,
            deadlines=DeadlineConfig(
                connect_seconds=float(
                    environment.get("EVALFORGE_OPENAI_CONNECT_TIMEOUT_SECONDS", "5")
                ),
                read_seconds=float(environment.get("EVALFORGE_OPENAI_READ_TIMEOUT_SECONDS", "30")),
                overall_seconds=float(
                    environment.get("EVALFORGE_OPENAI_OVERALL_TIMEOUT_SECONDS", "60")
                ),
            ),
            retry=RetryConfig(
                max_attempts=int(environment.get("EVALFORGE_OPENAI_MAX_ATTEMPTS", "3")),
                initial_backoff_seconds=float(
                    environment.get("EVALFORGE_OPENAI_INITIAL_BACKOFF_SECONDS", "0.25")
                ),
                max_backoff_seconds=float(
                    environment.get("EVALFORGE_OPENAI_MAX_BACKOFF_SECONDS", "4")
                ),
                jitter_ratio=float(environment.get("EVALFORGE_OPENAI_JITTER_RATIO", "0.2")),
            ),
        )
    except (KeyError, TypeError, ValueError):
        raise ValueError("OpenAI-compatible environment configuration is invalid") from None
