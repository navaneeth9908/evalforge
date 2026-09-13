from __future__ import annotations

from pathlib import Path

import pytest


def _evaluation_payloads() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    from evalforge.provenance import canonical_json_sha256, deterministic_run_id

    suite: dict[str, object] = {
        "schema_version": 1,
        "name": "registry-smoke",
        "minimum_pass_rate": 1.0,
        "cases": [
            {
                "case_id": "policy",
                "prompt": "State the synthetic policy.",
                "expected_output": "Allowed",
            }
        ],
    }
    candidate: dict[str, object] = {"policy": "Allowed"}
    suite_sha256 = canonical_json_sha256(suite)
    candidate_sha256 = canonical_json_sha256(candidate)
    run_id = deterministic_run_id(
        suite_sha256=suite_sha256,
        candidate_sha256=candidate_sha256,
        dataset_manifest_sha256=None,
    )
    report: dict[str, object] = {
        "schema_version": 5,
        "run_id": run_id,
        "suite_name": "registry-smoke",
        "suite_sha256": suite_sha256,
        "candidate_sha256": candidate_sha256,
        "dataset_manifest_sha256": None,
        "release_ready": True,
        "passed_cases": 1,
        "total_cases": 1,
    }
    return suite, candidate, report


def test_create_get_and_list_run_history_transactionally(tmp_path: Path) -> None:
    from evalforge.registry import SQLiteRunRegistry

    suite, candidate, report = _evaluation_payloads()
    registry = SQLiteRunRegistry(tmp_path / "history.db")

    created = registry.create_run(
        suite_name="registry-smoke",
        suite=suite,
        candidate=candidate,
        report=report,
    )
    loaded = registry.get_run(created.run_id)
    listed = registry.list_runs()

    assert loaded == created
    assert loaded is not None
    assert loaded.suite == suite
    assert loaded.candidate == candidate
    assert loaded.report == report
    assert listed == (created.summary(),)


@pytest.mark.parametrize(
    "statement",
    ("UPDATE runs SET created_at = 'tampered'", "DELETE FROM runs"),
)
def test_run_evidence_is_database_immutable(tmp_path: Path, statement: str) -> None:
    import sqlite3

    from evalforge.registry import SQLiteRunRegistry

    suite, candidate, report = _evaluation_payloads()
    registry = SQLiteRunRegistry(tmp_path / "history.db")
    registry.create_run(
        suite_name="registry-smoke",
        suite=suite,
        candidate=candidate,
        report=report,
    )

    with (
        sqlite3.connect(registry.path) as connection,
        pytest.raises(sqlite3.IntegrityError, match="immutable"),
    ):
        connection.execute(statement)


def test_registry_applies_migrations_and_safe_connection_defaults(tmp_path: Path) -> None:
    import sqlite3

    from evalforge.registry import SQLiteRunRegistry

    registry = SQLiteRunRegistry(tmp_path / "history.db")

    diagnostics = registry.diagnostics()
    assert diagnostics.schema_version == 1
    assert diagnostics.foreign_keys is True
    assert diagnostics.journal_mode == "wal"
    assert diagnostics.busy_timeout_ms == 5_000
    assert diagnostics.synchronous == 1

    with sqlite3.connect(registry.path) as connection:
        run_foreign_keys = connection.execute("PRAGMA foreign_key_list(runs)").fetchall()
        report_foreign_keys = connection.execute("PRAGMA foreign_key_list(reports)").fetchall()
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = ?", ("table",)
            )
        }
    assert {row[2] for row in run_foreign_keys} == {"suites", "candidates"}
    assert {row[2] for row in report_foreign_keys} == {"runs"}
    assert {"schema_migrations", "suites", "candidates", "runs", "reports"} <= tables


def test_create_run_rejects_a_run_id_not_derived_from_its_evidence(tmp_path: Path) -> None:
    from evalforge.registry import SQLiteRunRegistry

    suite, candidate, report = _evaluation_payloads()
    report["run_id"] = "0" * 64
    registry = SQLiteRunRegistry(tmp_path / "history.db")

    with pytest.raises(ValueError, match="run ID does not match"):
        registry.create_run(
            suite_name="registry-smoke",
            suite=suite,
            candidate=candidate,
            report=report,
        )
    assert registry.list_runs() == ()


def test_create_run_rejects_suite_name_metadata_mismatch(tmp_path: Path) -> None:
    from evalforge.registry import SQLiteRunRegistry

    suite, candidate, report = _evaluation_payloads()
    registry = SQLiteRunRegistry(tmp_path / "history.db")

    with pytest.raises(ValueError, match="suite name"):
        registry.create_run(
            suite_name="different-suite",
            suite=suite,
            candidate=candidate,
            report=report,
        )
