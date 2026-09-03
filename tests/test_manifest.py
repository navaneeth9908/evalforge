from __future__ import annotations

import pytest
from pydantic import ValidationError


def test_dataset_manifest_accepts_versioned_lineage_and_rejects_ambiguous_metadata() -> None:
    from evalforge.contracts import DatasetManifest

    manifest = DatasetManifest.model_validate(
        {
            "schema_version": 1,
            "dataset_id": "support-policy",
            "dataset_version": "2026-09-02",
            "suite_sha256": "a" * 64,
            "lineage": {
                "source": "synthetic://evalforge/support-policy",
                "source_revision": "v1",
                "created_by": "EvalForge maintainers",
                "license": "CC0-1.0",
                "transformations": ["Hand-authored synthetic examples"],
            },
        }
    )

    assert manifest.dataset_id == "support-policy"
    assert manifest.lineage.transformations == ("Hand-authored synthetic examples",)

    invalid_manifests = [
        manifest.model_dump() | {"schema_version": 2},
        manifest.model_dump() | {"schema_version": 1.0},
        manifest.model_dump() | {"schema_version": True},
        manifest.model_dump() | {"suite_sha256": "A" * 64},
        manifest.model_dump() | {"unexpected": True},
        manifest.model_dump()
        | {"lineage": manifest.lineage.model_dump() | {"transformations": []}},
    ]
    for invalid in invalid_manifests:
        with pytest.raises(ValidationError):
            DatasetManifest.model_validate(invalid)


@pytest.mark.parametrize("schema_version", [True, 1.0, "1"])
def test_evaluation_suite_requires_an_integer_schema_version(schema_version: object) -> None:
    from evalforge.contracts import EvaluationSuite

    with pytest.raises(ValidationError, match="schema_version must be an integer"):
        EvaluationSuite.model_validate(
            {
                "schema_version": schema_version,
                "name": "strict-version",
                "minimum_pass_rate": 1.0,
                "cases": [
                    {
                        "case_id": "policy",
                        "prompt": "State the policy",
                        "expected_output": "Allowed",
                    }
                ],
            }
        )


@pytest.mark.parametrize("minimum_pass_rate", [True, "1.0"])
def test_evaluation_suite_rejects_coerced_release_thresholds(
    minimum_pass_rate: object,
) -> None:
    from evalforge.contracts import EvaluationSuite

    with pytest.raises(ValidationError, match="minimum_pass_rate must be a JSON number"):
        EvaluationSuite.model_validate(
            {
                "schema_version": 1,
                "name": "strict-threshold",
                "minimum_pass_rate": minimum_pass_rate,
                "cases": [
                    {
                        "case_id": "policy",
                        "prompt": "State the policy",
                        "expected_output": "Allowed",
                    }
                ],
            }
        )
