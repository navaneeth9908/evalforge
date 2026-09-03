from __future__ import annotations

import unicodedata

import pytest


def test_canonical_json_sha256_is_key_order_independent() -> None:
    from evalforge.provenance import canonical_json_sha256

    left = {"b": 2, "a": 1}
    right = {"a": 1, "b": 2}

    assert canonical_json_sha256(left) == canonical_json_sha256(right)
    assert canonical_json_sha256(left) == (
        "43258cff783fe7036d8a43033f830adfc60ec037382473548ac742b888292777"
    )


def test_canonical_json_treats_negative_zero_as_semantic_zero() -> None:
    from evalforge.provenance import canonical_json_sha256

    assert canonical_json_sha256({"threshold": -0.0}) == canonical_json_sha256({"threshold": 0.0})


def test_canonical_json_rejects_lone_unicode_surrogates() -> None:
    from evalforge.provenance import canonical_json_sha256

    with pytest.raises(ValueError, match="valid Unicode"):
        canonical_json_sha256({"output": "\ud800"})


def test_run_id_binds_evaluation_and_canonicalization_semantics() -> None:
    from evalforge.provenance import (
        CANONICALIZATION_VERSION,
        EVALUATION_SEMANTICS_VERSION,
        RUN_ID_SCHEMA_VERSION,
        canonical_json_sha256,
        deterministic_run_id,
    )

    expected = canonical_json_sha256(
        {
            "candidate_sha256": "b" * 64,
            "canonicalization_version": CANONICALIZATION_VERSION,
            "dataset_manifest_sha256": "m" * 64,
            "evaluation_semantics_version": EVALUATION_SEMANTICS_VERSION,
            "run_id_schema_version": RUN_ID_SCHEMA_VERSION,
            "suite_sha256": "s" * 64,
        }
    )

    assert (
        deterministic_run_id(
            suite_sha256="s" * 64,
            candidate_sha256="b" * 64,
            dataset_manifest_sha256="m" * 64,
        )
        == expected
    )


def test_evaluation_semantics_identifies_the_runtime_unicode_database() -> None:
    from evalforge.provenance import EVALUATION_SEMANTICS_VERSION

    assert (
        f"normalized-exact-v1-unicode-{unicodedata.unidata_version}"
    ) == EVALUATION_SEMANTICS_VERSION


def test_run_id_changes_when_any_input_digest_changes() -> None:
    from evalforge.provenance import deterministic_run_id

    base = deterministic_run_id(
        suite_sha256="s" * 64,
        candidate_sha256="c" * 64,
        dataset_manifest_sha256="m" * 64,
    )
    variants = {
        deterministic_run_id(
            suite_sha256="x" * 64,
            candidate_sha256="c" * 64,
            dataset_manifest_sha256="m" * 64,
        ),
        deterministic_run_id(
            suite_sha256="s" * 64,
            candidate_sha256="x" * 64,
            dataset_manifest_sha256="m" * 64,
        ),
        deterministic_run_id(
            suite_sha256="s" * 64,
            candidate_sha256="c" * 64,
            dataset_manifest_sha256="x" * 64,
        ),
        deterministic_run_id(
            suite_sha256="s" * 64,
            candidate_sha256="c" * 64,
            dataset_manifest_sha256=None,
        ),
    }

    assert base not in variants
    assert len(variants) == 4
