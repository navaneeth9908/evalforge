"""Deterministic HTTP health and evaluation smoke probe for deployments."""

from __future__ import annotations

import argparse
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

_MAX_RESPONSE_BYTES = 65_536


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


_OPENER = build_opener(_NoRedirectHandler())
_SMOKE_SUITE: dict[str, object] = {
    "schema_version": 1,
    "name": "container-smoke",
    "minimum_pass_rate": 1.0,
    "cases": [
        {
            "case_id": "ready",
            "prompt": "Return the synthetic readiness token.",
            "expected_output": "ok",
        }
    ],
}


def _request_json(
    method: str,
    url: str,
    *,
    payload: dict[str, object] | None,
    timeout_seconds: float,
    expected_status: int,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    request = Request(
        url,
        data=body,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with _OPENER.open(request, timeout=timeout_seconds) as response:
            if response.status != expected_status:
                raise RuntimeError(
                    f"deployment smoke received unexpected HTTP status {response.status}"
                )
            if response.geturl() != url:
                raise RuntimeError("deployment smoke response URL did not match the request")
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError("deployment smoke request failed") from exc
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise RuntimeError("deployment smoke response exceeded 65536 bytes")
    try:
        decoded = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("deployment smoke response was not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError("deployment smoke response must be a JSON object")
    return decoded


def _base_url(value: str) -> str:
    parsed = urlsplit(value)
    try:
        _ = parsed.port
    except ValueError:
        raise RuntimeError("base URL must include a valid port") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise RuntimeError("base URL must be an HTTP(S) origin without credentials")
    return value.rstrip("/")


def _require_digest(value: object, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise RuntimeError(f"deployment smoke {label} was invalid")
    return value


def run_smoke(
    base_url: str,
    *,
    timeout_seconds: float = 5.0,
    health_only: bool = False,
) -> dict[str, object]:
    """Verify readiness and, unless disabled, one deterministic evaluation round trip."""
    if not 0 < timeout_seconds <= 30:
        raise RuntimeError("timeout must be greater than zero and at most 30 seconds")
    origin = _base_url(base_url)
    health = _request_json(
        "GET",
        f"{origin}/api/v1/health",
        payload=None,
        timeout_seconds=timeout_seconds,
        expected_status=200,
    )
    storage_version = health.get("storage_schema_version")
    if (
        health.get("api_version") != "v1"
        or health.get("status") != "ok"
        or type(storage_version) is not int
        or storage_version < 1
    ):
        raise RuntimeError("deployment health response was invalid")
    result: dict[str, object] = {
        "api_version": "v1",
        "storage_schema_version": storage_version,
    }
    if health_only:
        result["status"] = "ok"
        return result

    suite = _request_json(
        "POST",
        f"{origin}/api/v1/suites",
        payload=_SMOKE_SUITE,
        timeout_seconds=timeout_seconds,
        expected_status=201,
    )
    suite_id = _require_digest(suite.get("suite_id"), label="suite ID")
    evaluation = _request_json(
        "POST",
        f"{origin}/api/v1/evaluations",
        payload={"suite_id": suite_id, "candidate_outputs": {"ready": "ok"}},
        timeout_seconds=timeout_seconds,
        expected_status=201,
    )
    run_id = _require_digest(evaluation.get("run_id"), label="run ID")
    report = evaluation.get("report")
    if (
        not isinstance(report, dict)
        or report.get("run_id") != run_id
        or report.get("release_ready") is not True
    ):
        raise RuntimeError("deployment evaluation response was invalid")
    result.update({"release_ready": True, "run_id": run_id})
    return result


def main() -> None:
    """Run the bounded deployment smoke probe and emit stable JSON evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    parser.add_argument("--health-only", action="store_true")
    arguments = parser.parse_args()
    result = run_smoke(
        arguments.base_url,
        timeout_seconds=arguments.timeout_seconds,
        health_only=arguments.health_only,
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":  # pragma: no cover - exercised through the console script
    main()
