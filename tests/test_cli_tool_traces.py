from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner


def test_tool_trace_cli_writes_deterministic_redacted_report(tmp_path: Path) -> None:
    from evalforge.cli import app

    expectation = {
        "schema_version": 1,
        "allowed_tools": ["search", "lookup"],
        "expected_calls": [
            {
                "tool": "search",
                "arguments": {"query": "sensitive-query"},
                "result": {"ids": ["private-id"]},
            },
            {
                "tool": "lookup",
                "arguments": {"id": "private-id"},
                "result": {"answer": "sensitive-answer"},
            },
        ],
    }
    trace = {
        "schema_version": 1,
        "calls": [
            {
                "request": {
                    "call_id": "call-1",
                    "tool": "search",
                    "arguments": {"query": "sensitive-query"},
                },
                "result": {"call_id": "call-1", "result": {"ids": ["private-id"]}},
            },
            {
                "request": {
                    "call_id": "call-2",
                    "tool": "lookup",
                    "arguments": {"id": "private-id"},
                },
                "result": {
                    "call_id": "call-2",
                    "result": {"answer": "sensitive-answer"},
                },
            },
        ],
    }
    expectation_path = tmp_path / "expectation.json"
    trace_path = tmp_path / "trace.json"
    expectation_path.write_text(json.dumps(expectation), encoding="utf-8")
    trace_path.write_text(json.dumps(trace), encoding="utf-8")
    report_paths = (tmp_path / "report-a.json", tmp_path / "report-b.json")

    results = [
        CliRunner().invoke(
            app,
            [
                "tool-trace",
                str(expectation_path),
                str(trace_path),
                "--report-path",
                str(report_path),
            ],
        )
        for report_path in report_paths
    ]

    assert [result.exit_code for result in results] == [0, 0]
    assert "Tool-trace gate: PASS" in results[0].output
    assert report_paths[0].read_bytes() == report_paths[1].read_bytes()
    payload = json.loads(report_paths[0].read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["metrics"]["complete_match_count"] == 2
    assert len(payload["expectation_sha256"]) == 64
    assert len(payload["trace_sha256"]) == 64
    assert len(payload["tool_trace_id"]) == 64
    assert payload["tool_trace_semantics_version"] == "2"
    serialized = report_paths[0].read_text(encoding="utf-8")
    for secret in ("sensitive-query", "private-id", "sensitive-answer"):
        assert secret not in serialized


def test_tool_trace_cli_invalid_input_writes_no_report(tmp_path: Path) -> None:
    from evalforge.cli import app

    expectation_path = tmp_path / "expectation.json"
    trace_path = tmp_path / "trace.json"
    report_path = tmp_path / "report.json"
    expectation_path.write_text(
        '{"schema_version":1,"schema_version":1,"allowed_tools":["search"],'
        '"expected_calls":[{"tool":"search","arguments":{},"result":null}]}',
        encoding="utf-8",
    )
    trace_path.write_text('{"schema_version":1,"calls":[]}', encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["tool-trace", str(expectation_path), str(trace_path), "--report-path", str(report_path)],
    )

    assert result.exit_code == 2
    assert "invalid or ambiguous" in result.output
    assert not report_path.exists()


def test_tool_trace_cli_failed_gate_writes_redacted_report_and_exits_one(
    tmp_path: Path,
) -> None:
    from evalforge.cli import app

    expectation = {
        "schema_version": 1,
        "allowed_tools": ["search"],
        "expected_calls": [
            {"tool": "search", "arguments": {"query": "safe"}, "result": {"hits": 1}}
        ],
    }
    trace = {
        "schema_version": 1,
        "calls": [
            {
                "request": {
                    "call_id": "call-1",
                    "tool": "shell",
                    "arguments": {"command": "private-command"},
                },
                "result": {"call_id": "call-1", "result": "private-result"},
            }
        ],
    }
    expectation_path = tmp_path / "expectation.json"
    trace_path = tmp_path / "trace.json"
    report_path = tmp_path / "report.json"
    expectation_path.write_text(json.dumps(expectation), encoding="utf-8")
    trace_path.write_text(json.dumps(trace), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["tool-trace", str(expectation_path), str(trace_path), "--report-path", str(report_path)],
    )

    assert result.exit_code == 1
    assert "Tool-trace gate: FAIL" in result.output
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["release_ready"] is False
    assert [finding["code"] for finding in payload["findings"]] == [
        "disallowed_call",
        "extra_call",
        "missing_call",
    ]
    serialized = report_path.read_text(encoding="utf-8")
    assert "private-command" not in serialized
    assert "private-result" not in serialized
