from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner


def _write_valid_suite(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "bounded-input-smoke",
                "minimum_pass_rate": 1.0,
                "cases": [
                    {
                        "case_id": "policy",
                        "prompt": "State the policy",
                        "expected_output": "Allowed",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("target", "expected_message"),
    [
        ("suite", "suite file is invalid or ambiguous"),
        ("outputs", "outputs file is not valid JSON or is ambiguous"),
    ],
)
def test_evaluate_rejects_lone_unicode_surrogates_without_a_traceback(
    tmp_path: Path,
    target: str,
    expected_message: str,
) -> None:
    from evalforge.cli import app

    suite_path = tmp_path / "suite.json"
    outputs_path = tmp_path / "outputs.json"
    report_path = tmp_path / "report.json"
    _write_valid_suite(suite_path)
    outputs_path.write_text('{"policy":"Allowed"}', encoding="utf-8")
    if target == "suite":
        suite_path.write_text(
            suite_path.read_text(encoding="utf-8").replace('"bounded-input-smoke"', '"\\ud800"'),
            encoding="utf-8",
        )
    else:
        outputs_path.write_text('{"policy":"\\ud800"}', encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "evaluate",
            str(suite_path),
            str(outputs_path),
            "--report-path",
            str(report_path),
        ],
    )

    assert result.exit_code == 2
    assert expected_message in result.output
    assert "Traceback" not in result.output
    assert not report_path.exists()


@pytest.mark.parametrize("unsafe_shape", ["oversized", "deeply-nested", "long-string"])
def test_evaluate_bounds_untrusted_json_work(
    tmp_path: Path,
    unsafe_shape: str,
) -> None:
    from evalforge import cli

    suite_path = tmp_path / "suite.json"
    outputs_path = tmp_path / "outputs.json"
    report_path = tmp_path / "report.json"
    _write_valid_suite(suite_path)
    if unsafe_shape == "oversized":
        outputs_path.write_text(
            json.dumps({"policy": "A" * (1024 * 1024)}),
            encoding="utf-8",
        )
    elif unsafe_shape == "long-string":
        outputs_path.write_text(
            json.dumps({"policy": "A" * (cli.MAX_JSON_STRING_CHARS + 1)}),
            encoding="utf-8",
        )
    else:
        nested: object = "Allowed"
        for _ in range(70):
            nested = [nested]
        outputs_path.write_text(json.dumps({"policy": nested}), encoding="utf-8")

    result = CliRunner().invoke(
        cli.app,
        [
            "evaluate",
            str(suite_path),
            str(outputs_path),
            "--report-path",
            str(report_path),
        ],
    )

    assert result.exit_code == 2
    assert "outputs file is not valid JSON or is ambiguous" in result.output
    assert "Traceback" not in result.output
    assert not report_path.exists()


@pytest.mark.parametrize(
    "numeric_literal",
    [
        pytest.param("9" * 5_000, id="oversized-integer"),
        pytest.param("0." + "1" * 300, id="oversized-float"),
        pytest.param("1e999", id="non-finite-float"),
    ],
)
def test_evaluate_rejects_unsafe_numeric_literals_without_a_traceback(
    tmp_path: Path,
    numeric_literal: str,
) -> None:
    from evalforge.cli import app

    suite_path = tmp_path / "suite.json"
    outputs_path = tmp_path / "outputs.json"
    report_path = tmp_path / "report.json"
    suite_path.write_text(
        "".join(
            [
                '{"schema_version":1,"name":"unsafe-number",',
                '"minimum_pass_rate":',
                numeric_literal,
                ',"cases":[{"case_id":"policy","prompt":"State the policy",',
                '"expected_output":"Allowed"}]}',
            ]
        ),
        encoding="utf-8",
    )
    outputs_path.write_text('{"policy":"Allowed"}', encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "evaluate",
            str(suite_path),
            str(outputs_path),
            "--report-path",
            str(report_path),
        ],
    )

    assert result.exit_code == 2
    assert "suite file is invalid or ambiguous" in result.output
    assert "Traceback" not in result.output
    assert not report_path.exists()


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_evaluate_rejects_non_finite_json_constants(
    tmp_path: Path,
    constant: str,
) -> None:
    from evalforge.cli import app

    suite_path = tmp_path / "suite.json"
    outputs_path = tmp_path / "outputs.json"
    report_path = tmp_path / "report.json"
    _write_valid_suite(suite_path)
    outputs_path.write_text(f'{{"policy":{constant}}}', encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "evaluate",
            str(suite_path),
            str(outputs_path),
            "--report-path",
            str(report_path),
        ],
    )

    assert result.exit_code == 2
    assert "outputs file is not valid JSON or is ambiguous" in result.output
    assert "Traceback" not in result.output
    assert not report_path.exists()


@pytest.mark.parametrize(
    ("target", "expected_message"),
    [
        ("suite", "suite file is invalid or ambiguous"),
        ("manifest", "dataset manifest is invalid or ambiguous"),
    ],
)
def test_evaluate_rejects_duplicate_keys_in_suite_and_manifest(
    tmp_path: Path,
    target: str,
    expected_message: str,
) -> None:
    from evalforge.cli import app
    from evalforge.provenance import canonical_json_sha256

    suite_path = tmp_path / "suite.json"
    outputs_path = tmp_path / "outputs.json"
    manifest_path = tmp_path / "manifest.json"
    report_path = tmp_path / "report.json"
    _write_valid_suite(suite_path)
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    outputs_path.write_text('{"policy":"Allowed"}', encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "dataset_id": "duplicate-key-smoke",
        "dataset_version": "v1",
        "suite_sha256": canonical_json_sha256(suite),
        "lineage": {
            "source": "synthetic://evalforge/duplicate-key-smoke",
            "source_revision": "v1",
            "created_by": "EvalForge maintainers",
            "license": "CC0-1.0",
            "transformations": ["Hand-authored synthetic examples"],
        },
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    arguments = ["evaluate", str(suite_path), str(outputs_path)]
    if target == "suite":
        suite_path.write_text(
            suite_path.read_text(encoding="utf-8").replace(
                '"name": "bounded-input-smoke"',
                '"name": "shadowed", "name": "bounded-input-smoke"',
            ),
            encoding="utf-8",
        )
    else:
        manifest_path.write_text(
            manifest_path.read_text(encoding="utf-8").replace(
                '"license": "CC0-1.0"',
                '"license": "Proprietary", "license": "CC0-1.0"',
            ),
            encoding="utf-8",
        )
        arguments.extend(["--dataset-manifest", str(manifest_path)])
    arguments.extend(["--report-path", str(report_path)])

    result = CliRunner().invoke(app, arguments)

    assert result.exit_code == 2
    assert expected_message in result.output
    assert "Traceback" not in result.output
    assert not report_path.exists()
