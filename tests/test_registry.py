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
        tenant_id="__local__",
        suite_name="registry-smoke",
        suite=suite,
        candidate=candidate,
        report=report,
    )
    loaded = registry.get_run(tenant_id="__local__", run_id=created.run_id)
    listed = registry.list_runs(tenant_id="__local__")

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
        tenant_id="__local__",
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


@pytest.mark.parametrize(
    "statement",
    (
        """
        INSERT OR REPLACE INTO candidates(id, digest, payload_json, created_at)
        SELECT id, digest, '{}', 'tampered' FROM candidates LIMIT 1
        """,
        """
        INSERT OR REPLACE INTO runs(
            run_id, suite_id, candidate_id, dataset_manifest_digest, created_at
        )
        SELECT run_id, suite_id, candidate_id, dataset_manifest_digest, 'tampered'
        FROM runs LIMIT 1
        """,
        """
        INSERT OR REPLACE INTO reports(
            id, run_id, digest, payload_json, release_ready, created_at
        )
        SELECT id, run_id, digest, '{}', 0, 'tampered' FROM reports LIMIT 1
        """,
    ),
)
def test_run_evidence_rejects_insert_or_replace_tampering(tmp_path: Path, statement: str) -> None:
    import sqlite3

    from evalforge.registry import SQLiteRunRegistry

    suite, candidate, report = _evaluation_payloads()
    registry = SQLiteRunRegistry(tmp_path / "history.db")
    registry.create_run(
        tenant_id="__local__",
        suite_name="registry-smoke",
        suite=suite,
        candidate=candidate,
        report=report,
    )

    with (
        sqlite3.connect(registry.path) as connection,
        pytest.raises(sqlite3.IntegrityError, match="immutable"),
    ):
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(statement)


def test_registry_applies_migrations_and_safe_connection_defaults(tmp_path: Path) -> None:
    import sqlite3

    from evalforge.registry import SQLiteRunRegistry

    registry = SQLiteRunRegistry(tmp_path / "history.db")

    diagnostics = registry.diagnostics()
    assert diagnostics.schema_version == 3
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
    assert {
        "schema_migrations",
        "suites",
        "tenant_suites",
        "tenant_runs",
        "candidates",
        "runs",
        "reports",
    } <= tables


def test_v1_registry_migration_assigns_legacy_suites_only_to_trusted_local(
    tmp_path: Path,
) -> None:
    import sqlite3

    from evalforge.auth import TRUSTED_LOCAL_TENANT_ID
    from evalforge.provenance import canonical_json_bytes, canonical_json_sha256
    from evalforge.registry import SQLiteRunRegistry

    path = tmp_path / "legacy.db"
    SQLiteRunRegistry(path)
    suite, _candidate, _report = _evaluation_payloads()
    suite_id = canonical_json_sha256(suite)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP INDEX tenant_runs_page_idx")
        connection.execute("DROP TABLE tenant_runs")
        connection.execute("DELETE FROM schema_migrations WHERE version = 3")
        connection.execute("DROP TRIGGER suites_immutable_insert")
        connection.execute("DROP TRIGGER candidates_immutable_insert")
        connection.execute("DROP TRIGGER runs_immutable_insert")
        connection.execute("DROP TRIGGER reports_immutable_insert")
        connection.execute("DROP TRIGGER tenant_suites_immutable_update")
        connection.execute("DROP TRIGGER tenant_suites_immutable_delete")
        connection.execute("DROP INDEX tenant_suites_page_idx")
        connection.execute("DROP TABLE tenant_suites")
        connection.execute("DELETE FROM schema_migrations WHERE version = 2")
        connection.execute(
            """
            INSERT INTO suites(digest, name, payload_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                suite_id,
                "registry-smoke",
                canonical_json_bytes(suite).decode("utf-8"),
                "2026-01-01T00:00:00.000000Z",
            ),
        )

    migrated = SQLiteRunRegistry(path)

    assert migrated.diagnostics().schema_version == 3
    assert migrated.get_suite(tenant_id=TRUSTED_LOCAL_TENANT_ID, suite_id=suite_id) is not None
    assert migrated.get_suite(tenant_id="tenant-alpha", suite_id=suite_id) is None
    assert migrated.count_suites(tenant_id=TRUSTED_LOCAL_TENANT_ID) == 1
    assert SQLiteRunRegistry(path).diagnostics().schema_version == 3


def test_v2_registry_migration_assigns_legacy_runs_only_to_trusted_local(
    tmp_path: Path,
) -> None:
    import sqlite3

    from evalforge.auth import TRUSTED_LOCAL_TENANT_ID
    from evalforge.registry import SQLiteRunRegistry

    path = tmp_path / "legacy-runs.db"
    registry = SQLiteRunRegistry(path)
    suite, candidate, report = _evaluation_payloads()
    created = registry.create_run(
        tenant_id=TRUSTED_LOCAL_TENANT_ID,
        suite_name="registry-smoke",
        suite=suite,
        candidate=candidate,
        report=report,
    )
    with sqlite3.connect(path) as connection:
        connection.execute("DROP INDEX tenant_runs_page_idx")
        connection.execute("DROP TABLE tenant_runs")
        connection.execute("DELETE FROM schema_migrations WHERE version = 3")

    migrated = SQLiteRunRegistry(path)

    assert migrated.diagnostics().schema_version == 3
    assert (
        migrated.get_run(
            tenant_id=TRUSTED_LOCAL_TENANT_ID,
            run_id=created.run_id,
        )
        is not None
    )
    assert migrated.get_run(tenant_id="tenant-alpha", run_id=created.run_id) is None
    assert migrated.count_runs(tenant_id=TRUSTED_LOCAL_TENANT_ID) == 1


def test_identical_suite_content_has_independent_tenant_memberships(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evalforge import registry as registry_module
    from evalforge.registry import SQLiteRunRegistry

    suite, _candidate, _report = _evaluation_payloads()
    registry = SQLiteRunRegistry(tmp_path / "memberships.db")
    timestamps = iter(
        (
            "2026-01-01T00:00:00.000001Z",
            "2026-01-01T00:00:00.000002Z",
        )
    )
    monkeypatch.setattr(registry_module, "_utc_now", lambda: next(timestamps))

    alpha = registry.create_suite(tenant_id="tenant-alpha", name="registry-smoke", suite=suite)
    beta = registry.create_suite(tenant_id="tenant-beta", name="registry-smoke", suite=suite)

    assert alpha.suite_id == beta.suite_id
    assert alpha.created_at == "2026-01-01T00:00:00.000001Z"
    assert beta.created_at == "2026-01-01T00:00:00.000002Z"
    assert registry.list_suites(tenant_id="tenant-alpha") == (alpha,)
    assert registry.list_suites(tenant_id="tenant-beta") == (beta,)


@pytest.mark.parametrize(
    "tenant_id",
    ("", "Tenant-Alpha", "tenant alpha", "-tenant-alpha", "tenant-alpha-", "a" * 65),
)
def test_registry_rejects_invalid_tenant_id_before_persistence(
    tmp_path: Path, tenant_id: str
) -> None:
    from evalforge.registry import SQLiteRunRegistry

    suite, _candidate, _report = _evaluation_payloads()
    registry = SQLiteRunRegistry(tmp_path / "invalid-tenant.db")

    with pytest.raises(ValueError, match="tenant ID is invalid"):
        registry.create_suite(tenant_id=tenant_id, name="registry-smoke", suite=suite)

    assert registry.count_suites(tenant_id="tenant-alpha") == 0


def test_registry_rejects_tenant_id_subclasses_before_sql(tmp_path: Path) -> None:
    from evalforge.registry import SQLiteRunRegistry

    class TenantId(str):
        pass

    suite, _candidate, _report = _evaluation_payloads()
    registry = SQLiteRunRegistry(tmp_path / "invalid-tenant-subclass.db")

    with pytest.raises(ValueError, match="tenant ID is invalid"):
        registry.create_suite(
            tenant_id=TenantId("tenant-alpha"),
            name="registry-smoke",
            suite=suite,
        )

    assert registry.count_suites(tenant_id="tenant-alpha") == 0


@pytest.mark.parametrize(
    "statement",
    (
        "UPDATE tenant_suites SET created_at = 'tampered'",
        "DELETE FROM tenant_suites",
        """
        INSERT OR REPLACE INTO tenant_suites(tenant_id, suite_id, created_at)
        SELECT tenant_id, suite_id, 'tampered' FROM tenant_suites LIMIT 1
        """,
    ),
)
def test_tenant_suite_membership_is_database_immutable(tmp_path: Path, statement: str) -> None:
    import sqlite3

    from evalforge.registry import SQLiteRunRegistry

    suite, _candidate, _report = _evaluation_payloads()
    registry = SQLiteRunRegistry(tmp_path / "memberships.db")
    registry.create_suite(tenant_id="tenant-alpha", name="registry-smoke", suite=suite)

    with (
        sqlite3.connect(registry.path) as connection,
        pytest.raises(sqlite3.IntegrityError, match="immutable"),
    ):
        connection.execute(statement)


@pytest.mark.parametrize(
    "statement",
    (
        "UPDATE tenant_runs SET created_at = 'tampered'",
        "DELETE FROM tenant_runs",
        """
        INSERT OR REPLACE INTO tenant_runs(tenant_id, run_id, created_at)
        SELECT tenant_id, run_id, 'tampered' FROM tenant_runs LIMIT 1
        """,
    ),
)
def test_tenant_run_membership_is_database_immutable(tmp_path: Path, statement: str) -> None:
    import sqlite3

    from evalforge.registry import SQLiteRunRegistry

    suite, candidate, report = _evaluation_payloads()
    registry = SQLiteRunRegistry(tmp_path / "run-memberships.db")
    registry.create_run(
        tenant_id="tenant-alpha",
        suite_name="registry-smoke",
        suite=suite,
        candidate=candidate,
        report=report,
    )

    with (
        sqlite3.connect(registry.path) as connection,
        pytest.raises(sqlite3.IntegrityError, match="immutable"),
    ):
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(statement)


def test_rejected_cross_tenant_run_collision_does_not_grant_membership(tmp_path: Path) -> None:
    from evalforge.registry import SQLiteRunRegistry

    suite, candidate, report = _evaluation_payloads()
    registry = SQLiteRunRegistry(tmp_path / "run-collision.db")
    created = registry.create_run(
        tenant_id="tenant-alpha",
        suite_name="registry-smoke",
        suite=suite,
        candidate=candidate,
        report=report,
    )
    altered_report = {**report, "release_ready": False}

    with pytest.raises(ValueError, match="different immutable evidence"):
        registry.create_run(
            tenant_id="tenant-beta",
            suite_name="registry-smoke",
            suite=suite,
            candidate=candidate,
            report=altered_report,
        )

    assert registry.count_suites(tenant_id="tenant-beta") == 0
    assert registry.count_runs(tenant_id="tenant-beta") == 0
    assert registry.get_run(tenant_id="tenant-beta", run_id=created.run_id) is None


def test_suite_evidence_cannot_be_replaced_and_retarget_tenant_membership(
    tmp_path: Path,
) -> None:
    import sqlite3

    from evalforge.provenance import canonical_json_bytes, canonical_json_sha256
    from evalforge.registry import SQLiteRunRegistry

    suite, _candidate, _report = _evaluation_payloads()
    replacement = {**suite, "name": "replacement-suite"}
    registry = SQLiteRunRegistry(tmp_path / "memberships.db")
    created = registry.create_suite(
        tenant_id="tenant-alpha",
        name="registry-smoke",
        suite=suite,
    )

    with (
        sqlite3.connect(registry.path) as connection,
        pytest.raises(sqlite3.IntegrityError, match="immutable"),
    ):
        suite_row_id = connection.execute(
            "SELECT id FROM suites WHERE digest = ?", (created.suite_id,)
        ).fetchone()[0]
        connection.execute(
            """
            INSERT OR REPLACE INTO suites(id, digest, name, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                suite_row_id,
                canonical_json_sha256(replacement),
                "replacement-suite",
                canonical_json_bytes(replacement).decode("utf-8"),
                "2026-01-02T00:00:00.000000Z",
            ),
        )


def test_create_run_rejects_a_run_id_not_derived_from_its_evidence(tmp_path: Path) -> None:
    from evalforge.registry import SQLiteRunRegistry

    suite, candidate, report = _evaluation_payloads()
    report["run_id"] = "0" * 64
    registry = SQLiteRunRegistry(tmp_path / "history.db")

    with pytest.raises(ValueError, match="run ID does not match"):
        registry.create_run(
            tenant_id="__local__",
            suite_name="registry-smoke",
            suite=suite,
            candidate=candidate,
            report=report,
        )
    assert registry.list_runs(tenant_id="__local__") == ()


def test_create_run_rejects_suite_name_metadata_mismatch(tmp_path: Path) -> None:
    from evalforge.registry import SQLiteRunRegistry

    suite, candidate, report = _evaluation_payloads()
    registry = SQLiteRunRegistry(tmp_path / "history.db")

    with pytest.raises(ValueError, match="suite name"):
        registry.create_run(
            tenant_id="__local__",
            suite_name="different-suite",
            suite=suite,
            candidate=candidate,
            report=report,
        )
