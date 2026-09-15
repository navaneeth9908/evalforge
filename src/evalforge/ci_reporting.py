"""Deterministic, redaction-safe CI report exporters."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from xml.etree import ElementTree

from evalforge.contracts import (
    EvaluationReport,
    ResourceGateFailure,
    SensitiveDataReport,
    Severity,
)


def _xml_text(value: str) -> str:
    """Replace code points XML 1.0 cannot represent."""
    return "".join(
        character
        if (
            character in "\t\n\r"
            or "\u0020" <= character <= "\ud7ff"
            or "\ue000" <= character <= "\ufffd"
            or "\U00010000" <= character <= "\U0010ffff"
        )
        else "\ufffd"
        for character in value
    )


def _number(value: int | float) -> str:
    return str(value) if type(value) is int else format(value, ".15g")


def _add_failure(testcase: ElementTree.Element, *, message: str, detail: str) -> None:
    failure = ElementTree.SubElement(testcase, "failure", {"message": message})
    failure.text = detail


def _resource_metrics(
    report: EvaluationReport,
) -> Iterable[tuple[str, int, ResourceGateFailure | None]]:
    if report.performance is None:
        return ()
    summary = report.performance
    values: list[tuple[str, int]] = [
        ("total_latency_ms", summary.total_latency_ms),
        ("average_latency_ms", summary.average_latency_ms),
        ("total_cost_micro_usd", summary.total_cost_micro_usd),
        ("average_cost_micro_usd", summary.average_cost_micro_usd),
    ]
    if summary.latency_percentile is not None:
        values.append(
            (
                f"p{summary.latency_percentile.percentile}_latency_ms",
                summary.latency_percentile.observed_latency_ms,
            )
        )
    if summary.cost_percentile is not None:
        values.append(
            (
                f"p{summary.cost_percentile.percentile}_cost_micro_usd",
                summary.cost_percentile.observed_cost_micro_usd,
            )
        )
    failures: dict[str, ResourceGateFailure] = {
        failure.metric: failure for failure in report.resource_gate_failures
    }
    aliases = {
        "total_latency_ms": "total_latency_ms",
        "average_latency_ms": "average_latency_ms",
        "total_cost_micro_usd": "total_cost_micro_usd",
        "average_cost_micro_usd": "average_cost_micro_usd",
        **(
            {f"p{summary.latency_percentile.percentile}_latency_ms": "percentile_latency_ms"}
            if summary.latency_percentile is not None
            else {}
        ),
        **(
            {f"p{summary.cost_percentile.percentile}_cost_micro_usd": "percentile_cost_micro_usd"}
            if summary.cost_percentile is not None
            else {}
        ),
    }
    return tuple((name, value, failures.get(aliases[name])) for name, value in values)


def evaluation_report_to_junit(report: EvaluationReport, *, suite_name: str) -> str:
    """Render stable JUnit XML without expected or actual output content."""
    entries: list[tuple[ElementTree.Element, bool]] = []
    suite = ElementTree.Element("testsuite", {"name": f"evalforge.{_xml_text(suite_name)}"})

    for result in report.results:
        testcase = ElementTree.SubElement(
            suite,
            "testcase",
            {
                "classname": f"evalforge.metric.{result.metric}",
                "name": f"case:{_xml_text(result.case_id)}",
            },
        )
        failed = not result.passed
        if failed:
            _add_failure(
                testcase,
                message=f"case {_xml_text(result.case_id)} failed {result.metric} threshold",
                detail=(
                    f"case_id={_xml_text(result.case_id)}; metric={result.metric}; "
                    f"score={_number(result.score)}; threshold={_number(result.threshold)}; "
                    f"severity={result.severity}; category={result.category}; content=redacted"
                ),
            )
        entries.append((testcase, failed))

    weighted = ElementTree.SubElement(
        suite,
        "testcase",
        {"classname": "evalforge.gate", "name": "metric:weighted_pass_rate"},
    )
    pass_rate_failure = next(
        (
            failure
            for failure in report.gate_failures
            if failure.code == "minimum_pass_rate_not_met"
        ),
        None,
    )
    if pass_rate_failure is not None:
        _add_failure(
            weighted,
            message="weighted pass rate gate failed",
            detail=(
                "metric=weighted_pass_rate; "
                f"observed={_number(pass_rate_failure.observed)}; "
                f"required={_number(pass_rate_failure.required)}"
            ),
        )
    entries.append((weighted, pass_rate_failure is not None))

    for name, observed, resource_failure in _resource_metrics(report):
        testcase = ElementTree.SubElement(
            suite,
            "testcase",
            {"classname": "evalforge.resource", "name": f"metric:{name}"},
        )
        if resource_failure is not None:
            percentile = (
                f"; percentile={resource_failure.percentile}"
                if resource_failure.percentile is not None
                else ""
            )
            _add_failure(
                testcase,
                message=f"resource gate {name} failed",
                detail=(
                    f"metric={name}; observed={observed}; required={resource_failure.required}"
                    f"{percentile}"
                ),
            )
        entries.append((testcase, resource_failure is not None))

    test_count = len(entries)
    failure_count = sum(failed for _, failed in entries)
    suite.attrib.update({"tests": str(test_count), "failures": str(failure_count)})
    root = ElementTree.Element(
        "testsuites",
        {"name": "evalforge", "tests": str(test_count), "failures": str(failure_count)},
    )
    root.append(suite)
    ElementTree.indent(root, space="  ")
    xml_bytes = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
    if not isinstance(xml_bytes, bytes):  # pragma: no cover - fixed by the explicit encoding
        raise TypeError("XML serializer returned text instead of bytes")
    return f"{xml_bytes.decode()}\n"


_SARIF_LEVEL: dict[Severity, str] = {
    "low": "note",
    "medium": "warning",
    "high": "error",
    "critical": "error",
}
_SEVERITY_ORDER: dict[Severity, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _sarif_rule_id(category: str) -> str:
    return f"evalforge.sensitive-data.{category.replace('_', '-')}"


def sensitive_data_report_to_sarif(report: SensitiveDataReport) -> str:
    """Render a SARIF 2.1.0 log containing only redacted safety evidence."""
    categories = sorted({finding.category for finding in report.findings})
    category_level = {
        category: max(
            (finding.severity for finding in report.findings if finding.category == category),
            key=_SEVERITY_ORDER.__getitem__,
        )
        for category in categories
    }
    rule_indexes = {category: index for index, category in enumerate(categories)}
    rules = [
        {
            "id": _sarif_rule_id(category),
            "name": category,
            "shortDescription": {"text": f"Sensitive-data finding: {category.replace('_', ' ')}"},
            "defaultConfiguration": {"level": _SARIF_LEVEL[category_level[category]]},
        }
        for category in categories
    ]
    results = []
    for finding_index, finding in enumerate(report.findings):
        fingerprint_source = f"{finding.category}\0{finding.output_index}\0{finding_index}".encode()
        results.append(
            {
                "ruleId": _sarif_rule_id(finding.category),
                "ruleIndex": rule_indexes[finding.category],
                "level": _SARIF_LEVEL[finding.severity],
                "message": {
                    "text": (
                        "EvalForge detected redacted "
                        f"{finding.category.replace('_', ' ')} evidence in candidate output "
                        f"{finding.output_index}; raw evidence is omitted."
                    )
                },
                "partialFingerprints": {
                    "evalforgeFinding/v1": hashlib.sha256(fingerprint_source).hexdigest()
                },
                "properties": {
                    "outputIndex": finding.output_index,
                    "severity": finding.severity,
                    "evidence": "redacted",
                },
            }
        )
    payload = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "EvalForge",
                        "informationUri": "https://github.com/navaneeth9908/evalforge",
                        "semanticVersion": "0.1.0",
                        "rules": rules,
                    }
                },
                "results": results,
                "properties": {
                    "releaseReady": report.release_ready,
                    "totalFindings": report.total_findings,
                },
            }
        ],
    }
    return f"{json.dumps(payload, indent=2)}\n"
