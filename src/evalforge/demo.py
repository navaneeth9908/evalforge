"""Credential-free synthetic acceptance demo for the complete local product path."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, ClassVar

from fastapi import FastAPI
from starlette.types import Message, Scope

from evalforge.api import EvaluationReportResponse, create_app
from evalforge.ci_reporting import evaluation_report_to_junit, sensitive_data_report_to_sarif
from evalforge.contracts import CandidateOutput, SensitiveDataPolicy
from evalforge.leakage import scan_sensitive_outputs
from evalforge.providers import GenerationRequest, GenerationResult, TokenUsage

_SUITE: dict[str, object] = {
    "schema_version": 1,
    "name": "synthetic-release-acceptance",
    "release_policy": {
        "minimum_pass_rate": 1.0,
        "default_case_threshold": 1.0,
        "resource_budgets": {
            "max_total_latency_ms": 24,
            "max_average_latency_ms": 12,
            "max_total_cost_micro_usd": 0,
            "max_average_cost_micro_usd": 0,
        },
    },
    "cases": [
        {
            "case_id": "approval",
            "prompt": "Return the synthetic approval token.",
            "expected_output": "approved",
            "metric": "exact",
            "severity": "critical",
            "category": "release",
        },
        {
            "case_id": "readiness",
            "prompt": "Describe the synthetic release status.",
            "expected_output": "ready",
            "metric": "contains",
            "severity": "high",
            "category": "release",
        },
    ],
}


class SyntheticFakeAdapter:
    """Deterministic provider fake used only by the credential-free acceptance demo."""

    model = "evalforge-synthetic-fake"
    _outputs: ClassVar[dict[str, str]] = {
        "Return the synthetic approval token.": "approved",
        "Describe the synthetic release status.": "synthetic private candidate is ready",
    }

    def generate(self, request: GenerationRequest) -> GenerationResult:
        """Return one declared synthetic response with deterministic usage evidence."""
        try:
            output = self._outputs[request.prompt]
        except KeyError:
            raise ValueError("synthetic fake received an unknown prompt") from None
        prompt_tokens = len(request.prompt.split())
        completion_tokens = len(output.split())
        return GenerationResult(
            text=output,
            model=self.model,
            usage=TokenUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
        )


async def _asgi_request(
    app: FastAPI,
    method: str,
    path: str,
    payload: dict[str, object] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    body = b"" if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    request_sent = False
    messages: list[Message] = []

    async def receive() -> Message:
        nonlocal request_sent
        if request_sent:
            return {"type": "http.disconnect"}
        request_sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: Message) -> None:
        messages.append(message)

    headers = [(b"accept", b"application/json")]
    if payload is not None:
        headers.append((b"content-type", b"application/json"))
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 1),
        "server": ("evalforge-demo", 80),
        "state": {},
    }
    await app(scope, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    response_body = b"".join(
        message.get("body", b"") for message in messages if message["type"] == "http.response.body"
    )
    response_headers = {
        key.decode("latin-1"): value.decode("latin-1") for key, value in start["headers"]
    }
    return int(start["status"]), response_headers, response_body


def _json_request(
    app: FastAPI,
    method: str,
    path: str,
    payload: dict[str, object] | None = None,
    *,
    expected_status: int,
) -> dict[str, Any]:
    status, _headers, body = asyncio.run(_asgi_request(app, method, path, payload))
    if status != expected_status:
        raise RuntimeError(f"synthetic demo API request failed with HTTP {status}")
    decoded = json.loads(body)
    if not isinstance(decoded, dict):
        raise RuntimeError("synthetic demo API response was not an object")
    return decoded


def run_synthetic_demo(output_directory: Path) -> dict[str, object]:
    """Exercise fake generation, metrics, persistence, API, dashboard, and CI exports."""
    output_directory.mkdir(parents=True, exist_ok=True)
    app = create_app(output_directory / "evalforge.db")
    adapter = SyntheticFakeAdapter()

    cases = _SUITE["cases"]
    assert isinstance(cases, list)
    candidate_outputs: dict[str, CandidateOutput] = {}
    total_tokens = 0
    for raw_case in cases:
        assert isinstance(raw_case, dict)
        case_id = raw_case["case_id"]
        prompt = raw_case["prompt"]
        assert isinstance(case_id, str) and isinstance(prompt, str)
        generated = adapter.generate(GenerationRequest(prompt=prompt))
        total_tokens += generated.usage.total_tokens
        candidate_outputs[case_id] = CandidateOutput(
            output=generated.text,
            latency_ms=12,
            cost_micro_usd=0,
        )

    suite = _json_request(app, "POST", "/api/v1/suites", _SUITE, expected_status=201)
    suite_id = suite.get("suite_id")
    if not isinstance(suite_id, str):
        raise RuntimeError("synthetic demo API did not return a suite ID")
    evaluation_payload: dict[str, object] = {
        "suite_id": suite_id,
        "candidate_outputs": {
            case_id: output.model_dump(mode="json") for case_id, output in candidate_outputs.items()
        },
    }
    evaluation = _json_request(
        app,
        "POST",
        "/api/v1/evaluations",
        evaluation_payload,
        expected_status=201,
    )
    report_payload = evaluation.get("report")
    if not isinstance(report_payload, dict):
        raise RuntimeError("synthetic demo API did not return a report")
    report = EvaluationReportResponse.model_validate(report_payload)
    if not report.release_ready:
        raise RuntimeError("synthetic demo release gate failed")

    run_page = _json_request(app, "GET", "/api/v1/runs", expected_status=200)
    status, dashboard_headers, dashboard_body = asyncio.run(_asgi_request(app, "GET", "/dashboard"))
    if status != 200 or not dashboard_headers.get("content-type", "").startswith("text/html"):
        raise RuntimeError("synthetic demo dashboard request failed")

    (output_directory / "evaluation.json").write_text(
        f"{json.dumps(report.model_dump(mode='json'), indent=2)}\n",
        encoding="utf-8",
    )
    (output_directory / "evaluation.junit.xml").write_text(
        evaluation_report_to_junit(report, suite_name=report.suite_name),
        encoding="utf-8",
    )
    scan = scan_sensitive_outputs(
        {case_id: output.output for case_id, output in candidate_outputs.items()},
        SensitiveDataPolicy(schema_version=1),
    )
    (output_directory / "safety.sarif.json").write_text(
        sensitive_data_report_to_sarif(scan), encoding="utf-8"
    )
    (output_directory / "dashboard.html").write_bytes(dashboard_body)

    health = _json_request(app, "GET", "/api/v1/health", expected_status=200)
    run_id = evaluation.get("run_id")
    if not isinstance(run_id, str):
        raise RuntimeError("synthetic demo API did not return a run ID")
    return {
        "schema_version": 1,
        "provider": adapter.model,
        "provider_token_usage": total_tokens,
        "api_version": health["api_version"],
        "run_id": run_id,
        "release_ready": report.release_ready,
        "persisted_runs": run_page["total"],
        "artifacts": [
            "dashboard.html",
            "evaluation.json",
            "evaluation.junit.xml",
            "safety.sarif.json",
        ],
    }
