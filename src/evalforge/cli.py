"""Command-line entry points for deterministic evaluation runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from evalforge.contracts import EvaluationSuite
from evalforge.engine import evaluate_suite

app = typer.Typer(
    no_args_is_help=True, help="Evaluate AI-system outputs and enforce release gates."
)


class _DuplicateJsonKeyError(ValueError):
    """Raised when JSON object pairs contain an ambiguous duplicate key."""


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError(key)
        result[key] = value
    return result


def _read_json(path: Path, *, label: str) -> object:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_json_object,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, _DuplicateJsonKeyError) as exc:
        message = (
            "outputs file is not valid JSON or is ambiguous"
            if label == "outputs"
            else "suite file is invalid or ambiguous"
        )
        raise typer.BadParameter(message) from exc


@app.callback()
def main() -> None:
    """Run EvalForge evaluation workflows."""


@app.command("evaluate")
def evaluate_command(
    suite_path: Path,
    outputs_path: Path,
    report_path: Annotated[Path, typer.Option()] = Path("reports/evaluation.json"),
) -> None:
    """Evaluate candidate outputs against a versioned deterministic suite."""
    try:
        suite = EvaluationSuite.model_validate(_read_json(suite_path, label="suite"))
    except ValidationError as exc:
        raise typer.BadParameter("suite file is invalid or ambiguous") from exc

    raw_outputs = _read_json(outputs_path, label="outputs")
    if not isinstance(raw_outputs, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in raw_outputs.items()
    ):
        raise typer.BadParameter("outputs must be a JSON object mapping case IDs to strings")

    try:
        report = evaluate_suite(
            suite.cases,
            raw_outputs,
            minimum_pass_rate=suite.minimum_pass_rate,
        )
    except ValueError as exc:
        raise typer.BadParameter("evaluation input is invalid or ambiguous") from exc
    payload = {"suite_name": suite.name, **report.model_dump(mode="json")}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(f"{json.dumps(payload, indent=2)}\n", encoding="utf-8")

    typer.echo(f"Evaluation report: {report_path}")
    typer.echo(f"Pass rate: {report.pass_rate:.2%}")
    typer.echo(f"Release gate: {'PASS' if report.release_ready else 'FAIL'}")
    if not report.release_ready:
        raise typer.Exit(code=1)
