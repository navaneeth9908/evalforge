from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar

import pytest


class _MockOpenAIHandler(BaseHTTPRequestHandler):
    response_status = 200
    response_body: bytes = b"{}"
    response_delay_seconds = 0.0
    requests: ClassVar[list[dict[str, Any]]] = []

    def do_POST(self) -> None:
        length = int(self.headers["Content-Length"])
        body = self.rfile.read(length)
        type(self).requests.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "content_type": self.headers.get("Content-Type"),
                "body": json.loads(body),
            }
        )
        time.sleep(type(self).response_delay_seconds)
        self.send_response(type(self).response_status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        with suppress(BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            self.wfile.write(type(self).response_body)

    def log_message(self, format: str, *args: object) -> None:
        return


@contextmanager
def _mock_openai_server(
    payload: object | bytes,
    *,
    status: int = 200,
    delay_seconds: float = 0.0,
) -> Iterator[tuple[str, type[_MockOpenAIHandler]]]:
    handler = type("MockOpenAIHandler", (_MockOpenAIHandler,), {})
    handler.response_status = status
    handler.response_delay_seconds = delay_seconds
    handler.response_body = (
        payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    )
    handler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/v1", handler
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_provider_contract_accepts_a_deterministic_fake() -> None:
    from evalforge.providers import (
        CandidateAdapter,
        GenerationRequest,
        GenerationResult,
        TokenUsage,
    )

    class StaticAdapter:
        def generate(self, request: GenerationRequest) -> GenerationResult:
            return GenerationResult(
                text=f"fixed:{request.prompt}",
                model="deterministic-fake",
                usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            )

    adapter = StaticAdapter()

    assert isinstance(adapter, CandidateAdapter)
    assert adapter.generate(GenerationRequest(prompt="case-a")).text == "fixed:case-a"


def test_openai_adapter_posts_chat_request_and_parses_usage() -> None:
    from evalforge.providers import GenerationRequest, OpenAICompatibleAdapter

    response = {
        "id": "chatcmpl-test",
        "model": "mock-model-2026",
        "choices": [{"message": {"role": "assistant", "content": "mock answer"}}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
    }
    with _mock_openai_server(response) as (base_url, handler):
        adapter = OpenAICompatibleAdapter(
            base_url=base_url,
            model="mock-model",
            api_key="synthetic-test-key",
            timeout_seconds=2.0,
        )
        result = adapter.generate(GenerationRequest(prompt="Give a deterministic answer."))

    assert result.text == "mock answer"
    assert result.model == "mock-model-2026"
    assert result.usage.prompt_tokens == 7
    assert result.usage.completion_tokens == 3
    assert result.usage.total_tokens == 10
    assert handler.requests == [
        {
            "path": "/v1/chat/completions",
            "authorization": "Bearer synthetic-test-key",
            "content_type": "application/json",
            "body": {
                "model": "mock-model",
                "messages": [{"role": "user", "content": "Give a deterministic answer."}],
                "temperature": 0,
            },
        }
    ]


def test_openai_adapter_sanitizes_http_errors() -> None:
    from evalforge.providers import (
        GenerationRequest,
        OpenAICompatibleAdapter,
        ProviderError,
        RetryConfig,
    )

    provider_message = "rejected synthetic-secret-key at /private/customer/path"
    with _mock_openai_server({"error": {"message": provider_message}}, status=401) as (
        base_url,
        handler,
    ):
        adapter = OpenAICompatibleAdapter(
            base_url=base_url,
            model="mock-model",
            api_key="synthetic-secret-key",
            timeout_seconds=2.0,
            retry=RetryConfig(max_attempts=3),
        )
        with pytest.raises(ProviderError, match="HTTP 401") as error:
            adapter.generate(GenerationRequest(prompt="private customer prompt"))

    message = str(error.value)
    assert "synthetic-secret-key" not in message
    assert "private customer" not in message
    assert "/private/customer/path" not in message
    assert base_url not in message
    assert len(handler.requests) == 1
    assert [(item.attempt, item.outcome) for item in error.value.attempts] == [
        (1, "nonretryable_http")
    ]


def test_openai_adapter_enforces_timeout_with_sanitized_error() -> None:
    from evalforge.providers import (
        DeadlineConfig,
        GenerationRequest,
        OpenAICompatibleAdapter,
        ProviderError,
    )

    response = {
        "model": "mock-model",
        "choices": [{"message": {"content": "too late"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    with _mock_openai_server(response, delay_seconds=0.2) as (base_url, _):
        adapter = OpenAICompatibleAdapter(
            base_url=base_url,
            model="mock-model",
            api_key="synthetic-secret-key",
            deadlines=DeadlineConfig(
                connect_seconds=1.0,
                read_seconds=0.05,
                overall_seconds=1.0,
            ),
        )
        with pytest.raises(ProviderError, match="request timed out") as error:
            adapter.generate(GenerationRequest(prompt="private customer prompt"))

    message = str(error.value)
    assert "synthetic-secret-key" not in message
    assert "private customer" not in message
    assert base_url not in message


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"model": "mock", "choices": [], "usage": {}},
        {
            "model": "mock",
            "choices": [{"message": {"content": 42}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        },
        {
            "model": "mock",
            "choices": [{"message": {"content": "private response"}}],
            "usage": {"prompt_tokens": True, "completion_tokens": 1, "total_tokens": 2},
        },
        {
            "model": "mock",
            "choices": [{"message": {"content": "private response"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 3},
        },
        b'{"model":"mock","model":"ambiguous"}',
        b'{"error":"private response"',
        b"x" * (1024 * 1024 + 1),
    ],
    ids=[
        "missing-fields",
        "empty-choices",
        "non-string-content",
        "boolean-usage",
        "inconsistent-usage",
        "duplicate-key",
        "malformed-json",
        "oversized-body",
    ],
)
def test_openai_adapter_rejects_invalid_protocol_responses_without_leaking_body(
    response: object | bytes,
) -> None:
    from evalforge.providers import GenerationRequest, OpenAICompatibleAdapter, ProviderError

    with _mock_openai_server(response) as (base_url, handler):
        adapter = OpenAICompatibleAdapter(
            base_url=base_url,
            model="mock-model",
            api_key="synthetic-secret-key",
            timeout_seconds=2.0,
        )
        with pytest.raises(ProviderError, match="response is invalid") as error:
            adapter.generate(GenerationRequest(prompt="private customer prompt"))

    message = str(error.value)
    assert "synthetic-secret-key" not in message
    assert "private response" not in message
    assert "private customer" not in message
    assert base_url not in message
    assert len(handler.requests) == 1
    assert [(item.attempt, item.outcome) for item in error.value.attempts] == [
        (1, "malformed_response")
    ]


@pytest.mark.parametrize(
    "overrides",
    [
        {"base_url": "ftp://provider.example/v1"},
        {"base_url": "https://user:password@provider.example/v1"},
        {"base_url": "https://provider.example/v1?secret=query"},
        {"model": ""},
        {"timeout_seconds": 0.0},
        {"timeout_seconds": 121.0},
        {"timeout_seconds": math.inf},
        {"api_key": ""},
    ],
)
def test_openai_adapter_rejects_unbounded_or_ambiguous_configuration(
    overrides: dict[str, object],
) -> None:
    from evalforge.providers import OpenAICompatibleAdapter

    configuration: dict[str, object] = {
        "base_url": "https://provider.example/v1",
        "model": "candidate-model",
        "api_key": "synthetic-secret",
        "timeout_seconds": 30.0,
    }
    configuration.update(overrides)

    with pytest.raises(ValueError, match="adapter configuration is invalid") as error:
        OpenAICompatibleAdapter(**configuration)  # type: ignore[arg-type]

    message = str(error.value)
    assert "synthetic-secret" not in message
    assert "password" not in message
    assert "secret=query" not in message


def test_generate_openai_command_loads_environment_without_logging_key(tmp_path: Any) -> None:
    from typer.testing import CliRunner

    from evalforge.cli import app

    response = {
        "model": "mock-model-2026",
        "choices": [{"message": {"content": "mock candidate"}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
    }
    suite_path = tmp_path / "suite.json"
    outputs_path = tmp_path / "candidate-outputs.json"
    suite_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "provider-smoke",
                "minimum_pass_rate": 1.0,
                "cases": [
                    {
                        "case_id": "case-a",
                        "prompt": "private customer prompt",
                        "expected_output": "mock candidate",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with _mock_openai_server(response) as (base_url, handler):
        result = CliRunner().invoke(
            app,
            [
                "generate-openai",
                str(suite_path),
                "--outputs-path",
                str(outputs_path),
            ],
            env={
                "EVALFORGE_OPENAI_BASE_URL": base_url,
                "EVALFORGE_OPENAI_MODEL": "mock-model",
                "EVALFORGE_OPENAI_API_KEY": "synthetic-cli-secret",
                "EVALFORGE_OPENAI_TIMEOUT_SECONDS": "2",
            },
        )

    assert result.exit_code == 0, result.output
    assert json.loads(outputs_path.read_text(encoding="utf-8")) == {"case-a": "mock candidate"}
    assert "Generated candidate outputs" in result.output
    assert "synthetic-cli-secret" not in result.output
    assert "private customer prompt" not in result.output
    assert handler.requests[0]["authorization"] == "Bearer synthetic-cli-secret"


def test_openai_adapter_retries_rate_limit_with_deterministic_attempt_evidence() -> None:
    from evalforge.providers import (
        DeadlineConfig,
        GenerationRequest,
        OpenAICompatibleAdapter,
        ProviderHTTPResponse,
        RetryConfig,
    )

    successful_body = json.dumps(
        {
            "model": "mock-model-2026",
            "choices": [{"message": {"content": "recovered"}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        }
    ).encode("utf-8")
    responses = iter(
        [
            ProviderHTTPResponse(status=429, body=b'{"error":"private response"}'),
            ProviderHTTPResponse(status=200, body=successful_body),
        ]
    )
    requested_timeouts: list[tuple[float, float]] = []
    sleeps: list[float] = []

    def transport(
        *, connect_timeout: float, read_timeout: float, **_: object
    ) -> ProviderHTTPResponse:
        requested_timeouts.append((connect_timeout, read_timeout))
        return next(responses)

    ticks = iter([0.0, 0.1, 0.6, 0.8])
    adapter = OpenAICompatibleAdapter(
        base_url="https://provider.example/v1",
        model="mock-model",
        api_key="synthetic-secret-key",
        deadlines=DeadlineConfig(
            connect_seconds=2.0,
            read_seconds=3.0,
            overall_seconds=10.0,
        ),
        retry=RetryConfig(
            max_attempts=3,
            initial_backoff_seconds=0.5,
            max_backoff_seconds=2.0,
            jitter_ratio=0.0,
        ),
        transport=transport,
        monotonic=lambda: next(ticks),
        sleep=sleeps.append,
        random_value=lambda: 0.75,
    )

    result = adapter.generate(GenerationRequest(prompt="private customer prompt"))

    assert result.text == "recovered"
    assert requested_timeouts == [(2.0, 3.0), (2.0, 3.0)]
    assert sleeps == [0.5]
    assert [(item.attempt, item.outcome, item.retry_delay_seconds) for item in result.attempts] == [
        (1, "rate_limited", 0.5),
        (2, "success", None),
    ]


def test_openai_adapter_retries_timeouts_then_reports_exhaustion_without_secrets() -> None:
    from evalforge.providers import (
        DeadlineConfig,
        GenerationRequest,
        OpenAICompatibleAdapter,
        ProviderError,
        RetryConfig,
    )

    def transport(**_: object) -> object:
        raise TimeoutError("socket leaked synthetic-secret-key and private customer prompt")

    sleeps: list[float] = []
    ticks = iter([0.0, 0.1, 0.2, 0.4, 0.5, 0.9])
    adapter = OpenAICompatibleAdapter(
        base_url="https://provider.example/v1",
        model="mock-model",
        api_key="synthetic-secret-key",
        deadlines=DeadlineConfig(
            connect_seconds=2.0,
            read_seconds=3.0,
            overall_seconds=10.0,
        ),
        retry=RetryConfig(
            max_attempts=3,
            initial_backoff_seconds=0.5,
            max_backoff_seconds=2.0,
            jitter_ratio=0.0,
        ),
        transport=transport,  # type: ignore[arg-type]
        monotonic=lambda: next(ticks),
        sleep=sleeps.append,
        random_value=lambda: 0.5,
    )

    with pytest.raises(ProviderError, match="retry attempts exhausted") as captured:
        adapter.generate(GenerationRequest(prompt="private customer prompt"))

    assert sleeps == [0.5, 1.0]
    assert [
        (item.attempt, item.outcome, item.retry_delay_seconds) for item in captured.value.attempts
    ] == [
        (1, "timeout", 0.5),
        (2, "timeout", 1.0),
        (3, "timeout", None),
    ]
    error_text = str(captured.value)
    assert "synthetic-secret-key" not in error_text
    assert "private customer prompt" not in error_text


def test_openai_adapter_enforces_overall_deadline_after_transport_timeout() -> None:
    from evalforge.providers import (
        DeadlineConfig,
        GenerationRequest,
        OpenAICompatibleAdapter,
        ProviderError,
        RetryConfig,
    )

    def transport(**_: object) -> object:
        raise TimeoutError("private transport details")

    ticks = iter([0.0, 1.1])
    adapter = OpenAICompatibleAdapter(
        base_url="https://provider.example/v1",
        model="mock-model",
        deadlines=DeadlineConfig(
            connect_seconds=1.0,
            read_seconds=1.0,
            overall_seconds=1.0,
        ),
        retry=RetryConfig(max_attempts=1),
        transport=transport,  # type: ignore[arg-type]
        monotonic=lambda: next(ticks),
    )

    with pytest.raises(ProviderError, match="overall deadline exceeded") as captured:
        adapter.generate(GenerationRequest(prompt="private customer prompt"))

    assert [(item.attempt, item.outcome) for item in captured.value.attempts] == [
        (1, "overall_timeout")
    ]


@pytest.mark.parametrize(
    "configuration",
    [
        {"max_attempts": True},
        {"max_attempts": 0},
        {"max_attempts": 11},
        {"initial_backoff_seconds": "0.5"},
        {"initial_backoff_seconds": -0.1},
        {"max_backoff_seconds": 61.0},
        {"jitter_ratio": 1.1},
        {"jitter_ratio": math.nan},
    ],
)
def test_retry_configuration_rejects_unbounded_or_ambiguous_values(
    configuration: dict[str, object],
) -> None:
    from evalforge.providers import RetryConfig

    with pytest.raises(ValueError, match="retry configuration is invalid"):
        RetryConfig(**configuration)  # type: ignore[arg-type]
