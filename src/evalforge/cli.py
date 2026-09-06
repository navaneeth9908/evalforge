"""Command-line entry points for deterministic evaluation runs."""

from __future__ import annotations

import json
from math import isfinite
from pathlib import Path
from typing import Annotated, cast

import typer
from pydantic import ValidationError

from evalforge.comparison import compare_evaluations
from evalforge.contracts import (
    CandidateOutput,
    ComparisonPolicy,
    DatasetManifest,
    EvaluationSuite,
)
from evalforge.engine import evaluate_suite
from evalforge.provenance import (
    CANONICALIZATION_VERSION,
    EVALUATION_SEMANTICS_VERSION,
    JsonValue,
    canonical_json_sha256,
    deterministic_run_id,
)

MAX_JSON_BYTES = 1024 * 1024
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 100_000
MAX_JSON_STRING_CHARS = 65_536
MAX_JSON_NUMBER_CHARS = 256

app = typer.Typer(
    no_args_is_help=True, help="Evaluate AI-system outputs and enforce release gates."
)


class _DuplicateJsonKeyError(ValueError):
    """Raised when JSON object pairs contain an ambiguous duplicate key."""


class _UnsafeJsonError(ValueError):
    """Raised when JSON exceeds deterministic resource or Unicode limits."""


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError(key)
        result[key] = value
    return result


def _bounded_json_integer(raw_value: str) -> int:
    if len(raw_value) > MAX_JSON_NUMBER_CHARS:
        raise _UnsafeJsonError("JSON number exceeds length limit")
    return int(raw_value)


def _bounded_json_float(raw_value: str) -> float:
    if len(raw_value) > MAX_JSON_NUMBER_CHARS:
        raise _UnsafeJsonError("JSON number exceeds length limit")
    value = float(raw_value)
    if not isfinite(value):
        raise _UnsafeJsonError("JSON number must be finite")
    return value


def _reject_json_constant(raw_value: str) -> None:
    raise _UnsafeJsonError(f"JSON constant {raw_value!r} is not permitted")


def _validate_json_safety(value: object) -> None:
    stack: list[tuple[object, int]] = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            raise _UnsafeJsonError("JSON exceeds structural limits")
        if isinstance(item, str):
            if len(item) > MAX_JSON_STRING_CHARS:
                raise _UnsafeJsonError("JSON string exceeds length limit")
            try:
                item.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise _UnsafeJsonError("JSON contains an invalid Unicode scalar value") from exc
        elif isinstance(item, dict):
            for key, child in item.items():
                stack.append((key, depth + 1))
                stack.append((child, depth + 1))
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)


def _read_json(path: Path, *, label: str) -> object:
    try:
        with path.open("rb") as source:
            raw = source.read(MAX_JSON_BYTES + 1)
        if len(raw) > MAX_JSON_BYTES:
            raise _UnsafeJsonError("JSON file exceeds byte limit")
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
            parse_float=_bounded_json_float,
            parse_int=_bounded_json_integer,
        )
        _validate_json_safety(value)
        return value
    except (
        OSError,
        RecursionError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateJsonKeyError,
        _UnsafeJsonError,
    ) as exc:
        messages = {
            "outputs": "outputs file is not valid JSON or is ambiguous",
            "suite": "suite file is invalid or ambiguous",
            "manifest": "dataset manifest is invalid or ambiguous",
            "comparison_policy": "comparison policy is invalid or ambiguous",
        }
        message = messages[label]
        raise typer.BadParameter(message) from exc


def _parse_candidate_outputs(raw_outputs: object) -> dict[str, str | CandidateOutput]:
    message = "outputs must be a JSON object mapping case IDs to strings or evidence objects"
    if not isinstance(raw_outputs, dict) or not all(isinstance(key, str) for key in raw_outputs):
        raise typer.BadParameter(message)
    try:
        return {
            key: value if isinstance(value, str) else CandidateOutput.model_validate(value)
            for key, value in raw_outputs.items()
        }
    except ValidationError as exc:
        raise typer.BadParameter(message) from exc


@app.callback()
def main() -> None:
    """Run EvalForge evaluation workflows."""


@app.command("evaluate")
def evaluate_command(
    suite_path: Path,
    outputs_path: Path,
    report_path: Annotated[Path, typer.Option()] = Path("reports/evaluation.json"),
    dataset_manifest: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """Evaluate candidate outputs against a versioned deterministic suite."""
    raw_suite = _read_json(suite_path, label="suite")
    try:
        suite = EvaluationSuite.model_validate(raw_suite)
    except ValidationError as exc:
        raise typer.BadParameter("suite file is invalid or ambiguous") from exc

    raw_outputs = _read_json(outputs_path, label="outputs")
    candidate_outputs = _parse_candidate_outputs(raw_outputs)

    suite_payload = cast(JsonValue, raw_suite)
    candidate_payload = cast(JsonValue, raw_outputs)
    suite_sha256 = canonical_json_sha256(suite_payload)
    candidate_sha256 = canonical_json_sha256(candidate_payload)

    dataset_summary: dict[str, str] | None = None
    manifest_sha256: str | None = None
    if dataset_manifest is not None:
        raw_manifest = _read_json(dataset_manifest, label="manifest")
        try:
            manifest = DatasetManifest.model_validate(raw_manifest)
        except ValidationError as exc:
            raise typer.BadParameter("dataset manifest is invalid or ambiguous") from exc
        if manifest.suite_sha256 != suite_sha256:
            raise typer.BadParameter("dataset manifest suite digest does not match suite content")
        manifest_payload = cast(JsonValue, raw_manifest)
        manifest_sha256 = canonical_json_sha256(manifest_payload)
        dataset_summary = {
            "dataset_id": manifest.dataset_id,
            "dataset_version": manifest.dataset_version,
            "license": manifest.lineage.license,
        }

    try:
        report = evaluate_suite(
            suite.cases,
            candidate_outputs,
            policy=suite.resolved_policy,
        )
    except ValueError as exc:
        raise typer.BadParameter("evaluation input is invalid or ambiguous") from exc
    payload = {
        "suite_name": suite.name,
        **report.model_dump(mode="json"),
        "schema_version": 5,
        "suite_sha256": suite_sha256,
        "candidate_sha256": candidate_sha256,
        "dataset_manifest_sha256": manifest_sha256,
        "dataset": dataset_summary,
        "evaluation_semantics_version": EVALUATION_SEMANTICS_VERSION,
        "canonicalization_version": CANONICALIZATION_VERSION,
        "run_id": deterministic_run_id(
            suite_sha256=suite_sha256,
            candidate_sha256=candidate_sha256,
            dataset_manifest_sha256=manifest_sha256,
        ),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(f"{json.dumps(payload, indent=2)}\n", encoding="utf-8")

    typer.echo(f"Evaluation report: {report_path}")
    typer.echo(f"Weighted pass rate: {report.weighted_pass_rate:.2%}")
    typer.echo(f"Release gate: {'PASS' if report.release_ready else 'FAIL'}")
    if not report.release_ready:
        raise typer.Exit(code=1)


@app.command("compare")
def compare_command(
    suite_path: Path,
    baseline_outputs_path: Path,
    candidate_outputs_path: Path,
    comparison_policy_path: Annotated[Path, typer.Option("--comparison-policy")],
    report_path: Annotated[Path, typer.Option()] = Path("reports/comparison.json"),
    baseline_label: Annotated[str, typer.Option()] = "baseline",
    candidate_label: Annotated[str, typer.Option()] = "candidate",
) -> None:
    """Compare candidate outputs with a baseline under versioned regression budgets."""
    raw_suite = _read_json(suite_path, label="suite")
    raw_baseline = _read_json(baseline_outputs_path, label="outputs")
    raw_candidate = _read_json(candidate_outputs_path, label="outputs")
    raw_policy = _read_json(comparison_policy_path, label="comparison_policy")
    try:
        suite = EvaluationSuite.model_validate(raw_suite)
        comparison_policy = ComparisonPolicy.model_validate(raw_policy)
    except ValidationError as exc:
        raise typer.BadParameter("suite or comparison policy is invalid or ambiguous") from exc

    baseline_outputs = _parse_candidate_outputs(raw_baseline)
    candidate_outputs = _parse_candidate_outputs(raw_candidate)

    try:
        report = compare_evaluations(
            suite.cases,
            baseline_outputs,
            candidate_outputs,
            evaluation_policy=suite.resolved_policy,
            comparison_policy=comparison_policy,
            baseline_label=baseline_label,
            candidate_label=candidate_label,
        )
    except ValueError as exc:
        raise typer.BadParameter("comparison input is invalid or ambiguous") from exc

    suite_sha256 = canonical_json_sha256(cast(JsonValue, raw_suite))
    baseline_sha256 = canonical_json_sha256(cast(JsonValue, raw_baseline))
    candidate_sha256 = canonical_json_sha256(cast(JsonValue, raw_candidate))
    comparison_policy_sha256 = canonical_json_sha256(cast(JsonValue, raw_policy))
    comparison_id = canonical_json_sha256(
        {
            "schema_version": 3,
            "canonicalization_version": CANONICALIZATION_VERSION,
            "suite_sha256": suite_sha256,
            "baseline_sha256": baseline_sha256,
            "candidate_sha256": candidate_sha256,
            "comparison_policy_sha256": comparison_policy_sha256,
            "baseline_label": report.baseline_label,
            "candidate_label": report.candidate_label,
            "evaluation_semantics_version": EVALUATION_SEMANTICS_VERSION,
        }
    )
    payload = {
        **report.model_dump(mode="json"),
        "suite_name": suite.name,
        "suite_sha256": suite_sha256,
        "baseline_sha256": baseline_sha256,
        "candidate_sha256": candidate_sha256,
        "comparison_policy_sha256": comparison_policy_sha256,
        "comparison_policy": comparison_policy.model_dump(mode="json"),
        "evaluation_semantics_version": EVALUATION_SEMANTICS_VERSION,
        "canonicalization_version": CANONICALIZATION_VERSION,
        "comparison_id": comparison_id,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(f"{json.dumps(payload, indent=2)}\n", encoding="utf-8")

    typer.echo(f"Comparison report: {report_path}")
    typer.echo(f"Weighted pass-rate delta: {report.weighted_pass_rate_absolute_delta:+.2%}")
    typer.echo(f"Comparison gate: {'PASS' if report.release_ready else 'FAIL'}")
    if not report.release_ready:
        raise typer.Exit(code=1)
