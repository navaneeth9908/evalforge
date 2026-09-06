from __future__ import annotations

import pytest


def test_tool_trace_contracts_reject_mismatched_request_and_result_ids() -> None:
    from pydantic import ValidationError

    from evalforge.contracts import AgentToolTrace, ToolCall, ToolCallRequest, ToolCallResult

    with pytest.raises(ValidationError, match="call IDs must match"):
        AgentToolTrace(
            schema_version=1,
            calls=(
                ToolCall(
                    request=ToolCallRequest(
                        call_id="call-1",
                        tool="search",
                        arguments={"query": "evals"},
                    ),
                    result=ToolCallResult(call_id="call-2", result={"hits": 1}),
                ),
            ),
        )


def test_multi_tool_trace_matches_arguments_results_and_redacts_evidence() -> None:
    from evalforge.contracts import (
        AgentToolTrace,
        ExpectedToolCall,
        ToolCall,
        ToolCallRequest,
        ToolCallResult,
        ToolTraceExpectation,
    )
    from evalforge.tool_traces import evaluate_tool_trace

    expectation = ToolTraceExpectation(
        schema_version=1,
        allowed_tools=("search", "lookup"),
        expected_calls=(
            ExpectedToolCall(
                tool="search",
                arguments={"query": "private customer question"},
                result={"document_ids": ["doc-secret"]},
            ),
            ExpectedToolCall(
                tool="lookup",
                arguments={"document_id": "doc-secret"},
                result={"answer": "private answer"},
            ),
        ),
    )
    trace = AgentToolTrace(
        schema_version=1,
        calls=(
            ToolCall(
                request=ToolCallRequest(
                    call_id="call-1",
                    tool="search",
                    arguments={"query": "private customer question"},
                ),
                result=ToolCallResult(call_id="call-1", result={"document_ids": ["doc-secret"]}),
            ),
            ToolCall(
                request=ToolCallRequest(
                    call_id="call-2", tool="lookup", arguments={"document_id": "doc-secret"}
                ),
                result=ToolCallResult(call_id="call-2", result={"answer": "private answer"}),
            ),
        ),
    )

    report = evaluate_tool_trace(expectation, trace)

    assert report.release_ready is True
    assert report.metrics.model_dump() == {
        "expected_call_count": 2,
        "actual_call_count": 2,
        "tool_match_count": 2,
        "argument_match_count": 2,
        "result_match_count": 2,
        "complete_match_count": 2,
        "precision": 1.0,
        "recall": 1.0,
    }
    assert report.findings == ()
    serialized = report.model_dump_json()
    assert "private customer question" not in serialized
    assert "doc-secret" not in serialized
    assert "private answer" not in serialized
    assert all(len(call.argument_sha256) == 64 for call in report.calls)
    assert all(len(call.result_sha256) == 64 for call in report.calls)


def test_trace_reports_missing_extra_repeated_and_disallowed_calls() -> None:
    from evalforge.contracts import (
        AgentToolTrace,
        ExpectedToolCall,
        ToolCall,
        ToolCallRequest,
        ToolCallResult,
        ToolTraceExpectation,
    )
    from evalforge.tool_traces import evaluate_tool_trace

    expectation = ToolTraceExpectation(
        schema_version=1,
        allowed_tools=("search", "write"),
        expected_calls=(
            ExpectedToolCall(tool="search", arguments={"query": "safe"}, result=["one"]),
            ExpectedToolCall(tool="write", arguments={"path": "report.txt"}, result="ok"),
        ),
    )
    trace = AgentToolTrace(
        schema_version=1,
        calls=tuple(
            ToolCall(
                request=ToolCallRequest(call_id=f"call-{index}", tool=tool, arguments=arguments),
                result=ToolCallResult(call_id=f"call-{index}", result=result),
            )
            for index, (tool, arguments, result) in enumerate(
                (
                    ("search", {"query": "safe"}, ["one"]),
                    ("search", {"query": "again"}, ["two"]),
                    ("shell", {"command": "ignored"}, "ignored"),
                )
            )
        ),
    )

    report = evaluate_tool_trace(expectation, trace)

    assert report.release_ready is False
    assert report.metrics.model_dump() == {
        "expected_call_count": 2,
        "actual_call_count": 3,
        "tool_match_count": 1,
        "argument_match_count": 1,
        "result_match_count": 1,
        "complete_match_count": 1,
        "precision": 1 / 3,
        "recall": 0.5,
    }
    assert [finding.model_dump(exclude_none=True) for finding in report.findings] == [
        {"code": "repeated_call", "tool": "search", "actual_index": 1},
        {"code": "extra_call", "tool": "search", "actual_index": 1},
        {"code": "disallowed_call", "tool": "shell", "actual_index": 2},
        {"code": "extra_call", "tool": "shell", "actual_index": 2},
        {"code": "missing_call", "tool": "write", "expected_index": 1},
    ]


def test_trace_mismatches_expose_hashes_without_raw_values() -> None:
    from evalforge.contracts import (
        AgentToolTrace,
        ExpectedToolCall,
        ToolCall,
        ToolCallRequest,
        ToolCallResult,
        ToolTraceExpectation,
    )
    from evalforge.tool_traces import evaluate_tool_trace

    expectation = ToolTraceExpectation(
        schema_version=1,
        allowed_tools=("lookup",),
        expected_calls=(
            ExpectedToolCall(
                tool="lookup",
                arguments={"account": "expected-secret"},
                result={"balance": "expected-private"},
            ),
        ),
    )
    trace = AgentToolTrace(
        schema_version=1,
        calls=(
            ToolCall(
                request=ToolCallRequest(
                    call_id="lookup-1",
                    tool="lookup",
                    arguments={"account": "actual-secret"},
                ),
                result=ToolCallResult(call_id="lookup-1", result={"balance": "actual-private"}),
            ),
        ),
    )

    report = evaluate_tool_trace(expectation, trace)

    assert [finding.code for finding in report.findings] == [
        "argument_mismatch",
        "result_mismatch",
    ]
    assert all(finding.expected_sha256 for finding in report.findings)
    assert all(finding.actual_sha256 for finding in report.findings)
    serialized = report.model_dump_json()
    for secret in ("expected-secret", "expected-private", "actual-secret", "actual-private"):
        assert secret not in serialized
    assert report.metrics.complete_match_count == 0
    assert report.release_ready is False


@pytest.mark.parametrize(
    ("expected_value", "actual_value"),
    (
        (True, 1),
        (False, 0),
        (1, 1.0),
    ),
)
def test_tool_trace_matching_is_json_type_strict(
    expected_value: object, actual_value: object
) -> None:
    from evalforge.contracts import (
        AgentToolTrace,
        ExpectedToolCall,
        ToolCall,
        ToolCallRequest,
        ToolCallResult,
        ToolTraceExpectation,
    )
    from evalforge.tool_traces import evaluate_tool_trace

    expectation = ToolTraceExpectation(
        schema_version=1,
        allowed_tools=("inspect",),
        expected_calls=(
            ExpectedToolCall(
                tool="inspect",
                arguments={"value": expected_value},
                result={"value": expected_value},
            ),
        ),
    )
    trace = AgentToolTrace(
        schema_version=1,
        calls=(
            ToolCall(
                request=ToolCallRequest(
                    call_id="inspect-1",
                    tool="inspect",
                    arguments={"value": actual_value},
                ),
                result=ToolCallResult(
                    call_id="inspect-1",
                    result={"value": actual_value},
                ),
            ),
        ),
    )

    report = evaluate_tool_trace(expectation, trace)

    assert report.release_ready is False
    assert [finding.code for finding in report.findings] == [
        "argument_mismatch",
        "result_mismatch",
    ]
    assert report.calls[0].argument_sha256 != report.findings[0].expected_sha256
    assert report.calls[0].result_sha256 != report.findings[1].expected_sha256


def test_same_tool_calls_match_without_imposing_trajectory_order() -> None:
    from evalforge.contracts import (
        AgentToolTrace,
        ExpectedToolCall,
        ToolCall,
        ToolCallRequest,
        ToolCallResult,
        ToolTraceExpectation,
    )
    from evalforge.tool_traces import evaluate_tool_trace

    expectation = ToolTraceExpectation(
        schema_version=1,
        allowed_tools=("lookup",),
        expected_calls=(
            ExpectedToolCall(tool="lookup", arguments={"id": 1}, result={"name": "one"}),
            ExpectedToolCall(tool="lookup", arguments={"id": 2}, result={"name": "two"}),
        ),
    )
    trace = AgentToolTrace(
        schema_version=1,
        calls=(
            ToolCall(
                request=ToolCallRequest(call_id="lookup-2", tool="lookup", arguments={"id": 2}),
                result=ToolCallResult(call_id="lookup-2", result={"name": "two"}),
            ),
            ToolCall(
                request=ToolCallRequest(call_id="lookup-1", tool="lookup", arguments={"id": 1}),
                result=ToolCallResult(call_id="lookup-1", result={"name": "one"}),
            ),
        ),
    )

    report = evaluate_tool_trace(expectation, trace)

    assert report.release_ready is True
    assert report.findings == ()
    assert report.metrics.complete_match_count == 2
    assert [call.expected_index for call in report.calls] == [1, 0]


def test_tool_trace_contracts_parse_json_shapes_and_fail_closed() -> None:
    import math

    import pytest
    from pydantic import ValidationError

    from evalforge.contracts import AgentToolTrace, ToolTraceExpectation

    expectation_payload = {
        "schema_version": 1,
        "allowed_tools": ["search"],
        "expected_calls": [{"tool": "search", "arguments": {"query": "safe"}, "result": ["one"]}],
    }
    trace_payload = {
        "schema_version": 1,
        "calls": [
            {
                "request": {
                    "call_id": "call-1",
                    "tool": "search",
                    "arguments": {"query": "safe"},
                },
                "result": {"call_id": "call-1", "result": ["one"]},
            }
        ],
    }

    assert (
        ToolTraceExpectation.model_validate(expectation_payload).expected_calls[0].tool == "search"
    )
    assert AgentToolTrace.model_validate(trace_payload).calls[0].request.call_id == "call-1"

    too_deep: object = None
    for _ in range(66):
        too_deep = [too_deep]
    invalid_expectations = (
        {**expectation_payload, "schema_version": True},
        {**expectation_payload, "allowed_tools": ["search", "search"]},
        {**expectation_payload, "allowed_tools": ["lookup"]},
        {**expectation_payload, "unexpected": "field"},
        {
            **expectation_payload,
            "expected_calls": [
                {"tool": "search", "arguments": {"score": math.nan}, "result": None}
            ],
        },
        {
            **expectation_payload,
            "expected_calls": [{"tool": "search", "arguments": {}, "result": too_deep}],
        },
    )
    for payload in invalid_expectations:
        with pytest.raises(ValidationError):
            ToolTraceExpectation.model_validate(payload)

    duplicate_id_trace = {
        **trace_payload,
        "calls": [trace_payload["calls"][0], trace_payload["calls"][0]],
    }
    with pytest.raises(ValidationError, match="call IDs must be unique"):
        AgentToolTrace.model_validate(duplicate_id_trace)
