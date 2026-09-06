"""Deterministic matching and redaction-safe evidence for agent tool traces."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Hashable, Sequence
from hashlib import sha256
from typing import cast

from evalforge.contracts import (
    AgentToolTrace,
    ToolTraceCallEvidence,
    ToolTraceExpectation,
    ToolTraceFinding,
    ToolTraceMetrics,
    ToolTraceReport,
)
from evalforge.provenance import JsonValue, canonical_json_bytes

TOOL_TRACE_SEMANTICS_VERSION = "2"

Signature = Hashable


def _canonical_bytes(value: object) -> bytes:
    return canonical_json_bytes(cast(JsonValue, value))


def _digest(value: bytes) -> str:
    return sha256(value).hexdigest()


def _pair_unmatched(
    expected_indices: Sequence[int],
    actual_indices: Sequence[int],
    expected_signatures: Sequence[Signature],
    actual_signatures: Sequence[Signature],
    pairings: dict[int, int],
    matched_expected: set[int],
) -> None:
    """Pair equal signatures in stable actual and expected index order."""
    available: dict[Signature, deque[int]] = defaultdict(deque)
    for expected_index in expected_indices:
        if expected_index not in matched_expected:
            available[expected_signatures[expected_index]].append(expected_index)

    for actual_index in actual_indices:
        if actual_index in pairings:
            continue
        candidates = available.get(actual_signatures[actual_index])
        if candidates:
            expected_index = candidates.popleft()
            pairings[actual_index] = expected_index
            matched_expected.add(expected_index)


def evaluate_tool_trace(
    expectation: ToolTraceExpectation, trace: AgentToolTrace
) -> ToolTraceReport:
    """Match a trace as per-tool multisets and return redaction-safe gate evidence."""
    expected_by_tool: dict[str, list[int]] = defaultdict(list)
    for index, expected in enumerate(expectation.expected_calls):
        expected_by_tool[expected.tool].append(index)

    actual_by_tool: dict[str, list[int]] = defaultdict(list)
    for index, call in enumerate(trace.calls):
        actual_by_tool[call.request.tool].append(index)

    expected_argument_bytes = [
        _canonical_bytes(expected.arguments) for expected in expectation.expected_calls
    ]
    expected_result_bytes = [
        _canonical_bytes(expected.result) for expected in expectation.expected_calls
    ]
    actual_argument_bytes = [_canonical_bytes(call.request.arguments) for call in trace.calls]
    actual_result_bytes = [_canonical_bytes(call.result.result) for call in trace.calls]

    expected_complete_signatures: list[Signature] = list(
        zip(expected_argument_bytes, expected_result_bytes, strict=True)
    )
    actual_complete_signatures: list[Signature] = list(
        zip(actual_argument_bytes, actual_result_bytes, strict=True)
    )

    pairings: dict[int, int] = {}
    matched_expected: set[int] = set()
    for tool in sorted(set(expected_by_tool) | set(actual_by_tool)):
        expected_indices = expected_by_tool.get(tool, ())
        actual_indices = actual_by_tool.get(tool, ())

        # Exact exchanges win first. Remaining calls prefer argument matches,
        # then result matches, then stable index pairing for mismatch evidence.
        _pair_unmatched(
            expected_indices,
            actual_indices,
            expected_complete_signatures,
            actual_complete_signatures,
            pairings,
            matched_expected,
        )
        _pair_unmatched(
            expected_indices,
            actual_indices,
            expected_argument_bytes,
            actual_argument_bytes,
            pairings,
            matched_expected,
        )
        _pair_unmatched(
            expected_indices,
            actual_indices,
            expected_result_bytes,
            actual_result_bytes,
            pairings,
            matched_expected,
        )

        remaining_expected = [index for index in expected_indices if index not in matched_expected]
        remaining_actual = [index for index in actual_indices if index not in pairings]
        for remaining_actual_index, remaining_expected_index in zip(
            remaining_actual, remaining_expected, strict=False
        ):
            pairings[remaining_actual_index] = remaining_expected_index
            matched_expected.add(remaining_expected_index)

    findings: list[ToolTraceFinding] = []
    call_evidence: list[ToolTraceCallEvidence] = []
    tool_matches = 0
    argument_matches = 0
    result_matches = 0
    complete_matches = 0

    for actual_index, call in enumerate(trace.calls):
        tool = call.request.tool
        expected_indices = expected_by_tool.get(tool, ())
        expected_index = pairings.get(actual_index)

        if expected_index is None:
            if expected_indices:
                findings.append(
                    ToolTraceFinding(code="repeated_call", tool=tool, actual_index=actual_index)
                )
            if tool not in expectation.allowed_tools:
                findings.append(
                    ToolTraceFinding(code="disallowed_call", tool=tool, actual_index=actual_index)
                )
            findings.append(
                ToolTraceFinding(code="extra_call", tool=tool, actual_index=actual_index)
            )

        tool_matched = expected_index is not None
        arguments_matched = False
        result_matched = False
        if expected_index is not None:
            tool_matches += 1
            arguments_matched = (
                expected_argument_bytes[expected_index] == actual_argument_bytes[actual_index]
            )
            result_matched = (
                expected_result_bytes[expected_index] == actual_result_bytes[actual_index]
            )
            argument_matches += int(arguments_matched)
            result_matches += int(result_matched)
            complete_matches += int(arguments_matched and result_matched)

            if not arguments_matched:
                findings.append(
                    ToolTraceFinding(
                        code="argument_mismatch",
                        tool=tool,
                        expected_index=expected_index,
                        actual_index=actual_index,
                        expected_sha256=_digest(expected_argument_bytes[expected_index]),
                        actual_sha256=_digest(actual_argument_bytes[actual_index]),
                    )
                )
            if not result_matched:
                findings.append(
                    ToolTraceFinding(
                        code="result_mismatch",
                        tool=tool,
                        expected_index=expected_index,
                        actual_index=actual_index,
                        expected_sha256=_digest(expected_result_bytes[expected_index]),
                        actual_sha256=_digest(actual_result_bytes[actual_index]),
                    )
                )

        call_evidence.append(
            ToolTraceCallEvidence(
                call_index=actual_index,
                call_id=call.request.call_id,
                tool=tool,
                expected_index=expected_index,
                argument_sha256=_digest(actual_argument_bytes[actual_index]),
                result_sha256=_digest(actual_result_bytes[actual_index]),
                tool_matched=tool_matched,
                arguments_matched=arguments_matched,
                result_matched=result_matched,
            )
        )

    for expected_index, expected in enumerate(expectation.expected_calls):
        if expected_index not in matched_expected:
            findings.append(
                ToolTraceFinding(
                    code="missing_call",
                    tool=expected.tool,
                    expected_index=expected_index,
                )
            )

    expected_count = len(expectation.expected_calls)
    actual_count = len(trace.calls)
    metrics = ToolTraceMetrics(
        expected_call_count=expected_count,
        actual_call_count=actual_count,
        tool_match_count=tool_matches,
        argument_match_count=argument_matches,
        result_match_count=result_matches,
        complete_match_count=complete_matches,
        precision=complete_matches / actual_count if actual_count else 0.0,
        recall=complete_matches / expected_count,
    )
    return ToolTraceReport(
        metrics=metrics,
        calls=tuple(call_evidence),
        findings=tuple(findings),
        release_ready=not findings,
    )
