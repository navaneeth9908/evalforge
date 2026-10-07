from __future__ import annotations

import pytest


def _producer_payload() -> dict[str, object]:
    return {
        "producer_id": "offline-evaluation-exporter",
        "revision": "exporter-v3",
        "artifact_sha256": "a" * 64,
    }


def _policy_payload() -> dict[str, object]:
    from evalforge.cross_dataset_trends import producer_sha256

    return {
        "schema_version": 1,
        "approved_producer_sha256": producer_sha256(_producer_payload()),
        "datasets": [
            {
                "dataset_id": "support",
                "dataset_version": "2026-09",
                "suite_sha256": "b" * 64,
                "evaluator_semantics_version": "text-metrics-v3",
                "weight": 3.0,
                "max_drawdown": 0.2,
            },
            {
                "dataset_id": "safety",
                "dataset_version": "2026-09",
                "suite_sha256": "c" * 64,
                "evaluator_semantics_version": "policy-v2",
                "weight": 1.0,
                "max_drawdown": 0.1,
            },
        ],
        "max_overall_drawdown": 0.15,
    }


def _observations_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "producer": _producer_payload(),
        "checkpoints": [
            {
                "observed_at": "2026-01-01T00:00:00Z",
                "datasets": [
                    {
                        "dataset_id": "support",
                        "dataset_version": "2026-09",
                        "suite_sha256": "b" * 64,
                        "evaluator_semantics_version": "text-metrics-v3",
                        "score": 0.6,
                        "release_ready": True,
                    },
                    {
                        "dataset_id": "safety",
                        "dataset_version": "2026-09",
                        "suite_sha256": "c" * 64,
                        "evaluator_semantics_version": "policy-v2",
                        "score": 0.8,
                        "release_ready": True,
                    },
                ],
            },
            {
                "observed_at": "2026-02-01T00:00:00Z",
                "datasets": [
                    {
                        "dataset_id": "support",
                        "dataset_version": "2026-09",
                        "suite_sha256": "b" * 64,
                        "evaluator_semantics_version": "text-metrics-v3",
                        "score": 0.9,
                        "release_ready": True,
                    },
                    {
                        "dataset_id": "safety",
                        "dataset_version": "2026-09",
                        "suite_sha256": "c" * 64,
                        "evaluator_semantics_version": "policy-v2",
                        "score": 0.7,
                        "release_ready": True,
                    },
                ],
            },
            {
                "observed_at": "2026-03-01T00:00:00Z",
                "datasets": [
                    {
                        "dataset_id": "support",
                        "dataset_version": "2026-09",
                        "suite_sha256": "b" * 64,
                        "evaluator_semantics_version": "text-metrics-v3",
                        "score": 0.75,
                        "release_ready": True,
                    },
                    {
                        "dataset_id": "safety",
                        "dataset_version": "2026-09",
                        "suite_sha256": "c" * 64,
                        "evaluator_semantics_version": "policy-v2",
                        "score": 0.75,
                        "release_ready": True,
                    },
                ],
            },
        ],
    }


def test_analyze_cross_dataset_trends_preserves_order_and_full_history_statistics() -> None:
    from evalforge.cross_dataset_trends import (
        CrossDatasetObservations,
        CrossDatasetPolicy,
        analyze_cross_dataset_trends,
    )

    observations = CrossDatasetObservations.model_validate(_observations_payload())
    policy = CrossDatasetPolicy.model_validate(_policy_payload())

    report = analyze_cross_dataset_trends(observations, policy)

    assert [dataset.dataset_id for dataset in report.datasets] == ["support", "safety"]
    assert [checkpoint.overall_score for checkpoint in report.checkpoints] == pytest.approx(
        [0.65, 0.85, 0.75]
    )
    support, safety = report.datasets
    assert (
        support.first_score,
        support.latest_score,
        support.peak_score,
        support.max_drawdown,
    ) == pytest.approx((0.6, 0.75, 0.9, 0.15))
    assert (
        safety.first_score,
        safety.latest_score,
        safety.peak_score,
        safety.max_drawdown,
    ) == pytest.approx((0.8, 0.75, 0.8, 0.1))
    assert (
        report.overall.first_score,
        report.overall.latest_score,
        report.overall.peak_score,
        report.overall.max_drawdown,
    ) == pytest.approx((0.65, 0.75, 0.85, 0.1))
    assert report.gate_failures == ()
    assert report.release_ready


def test_trend_artifact_is_deterministic_and_binds_producer_inputs_policy_and_report() -> None:
    from evalforge.cross_dataset_trends import (
        CrossDatasetObservations,
        CrossDatasetPolicy,
        build_cross_dataset_trend_artifact,
    )

    observations = CrossDatasetObservations.model_validate(_observations_payload())
    policy = CrossDatasetPolicy.model_validate(_policy_payload())

    first = build_cross_dataset_trend_artifact(observations, policy)
    second = build_cross_dataset_trend_artifact(observations, policy)

    assert first.model_dump_json() == second.model_dump_json()
    assert first.producer == observations.producer
    assert first.policy == policy
    assert len(first.producer_sha256) == 64
    assert len(first.observations_sha256) == 64
    assert len(first.policy_sha256) == 64
    assert len(first.report_sha256) == 64
    assert len(first.trend_analysis_id) == 64
    assert first.canonicalization_version == "evalforge-json-v1"
    assert first.trend_semantics_version == "cross-dataset-scorecard-v1"
    serialized = first.model_dump_json()
    assert "prompt" not in serialized
    assert "output" not in serialized
    assert "lineage" not in serialized


def test_analysis_rejects_unapproved_or_drifted_checkpoint_identity() -> None:
    from evalforge.cross_dataset_trends import (
        CrossDatasetObservations,
        CrossDatasetPolicy,
        analyze_cross_dataset_trends,
    )

    policy = CrossDatasetPolicy.model_validate(_policy_payload())
    unapproved_payload = _observations_payload()
    unapproved_payload["producer"] = {
        **_producer_payload(),
        "revision": "unapproved-exporter-v4",
    }
    drifted_payload = _observations_payload()
    checkpoints = drifted_payload["checkpoints"]
    assert isinstance(checkpoints, list)
    latest = checkpoints[-1]
    assert isinstance(latest, dict)
    datasets = latest["datasets"]
    assert isinstance(datasets, list)
    datasets.reverse()

    with pytest.raises(ValueError, match="producer is not approved"):
        analyze_cross_dataset_trends(
            CrossDatasetObservations.model_validate(unapproved_payload), policy
        )
    with pytest.raises(ValueError, match="coverage and order"):
        analyze_cross_dataset_trends(
            CrossDatasetObservations.model_validate(drifted_payload), policy
        )

    version_drift_payload = _observations_payload()
    version_checkpoints = version_drift_payload["checkpoints"]
    assert isinstance(version_checkpoints, list)
    version_latest = version_checkpoints[-1]
    assert isinstance(version_latest, dict)
    version_datasets = version_latest["datasets"]
    assert isinstance(version_datasets, list)
    version_datasets[0]["evaluator_semantics_version"] = "text-metrics-v4"
    with pytest.raises(ValueError, match="coverage and order"):
        analyze_cross_dataset_trends(
            CrossDatasetObservations.model_validate(version_drift_payload), policy
        )


def test_observations_require_strictly_increasing_canonical_utc_timestamps() -> None:
    from pydantic import ValidationError

    from evalforge.cross_dataset_trends import CrossDatasetObservations

    for invalid_timestamp in (
        "2026-01-01T00:00:00+00:00",
        "2026-01-01T00:00:00.000000Z",
        "2026-02-30T00:00:00Z",
    ):
        payload = _observations_payload()
        checkpoints = payload["checkpoints"]
        assert isinstance(checkpoints, list)
        checkpoints[0]["observed_at"] = invalid_timestamp
        with pytest.raises(ValidationError):
            CrossDatasetObservations.model_validate(payload)

    payload = _observations_payload()
    checkpoints = payload["checkpoints"]
    assert isinstance(checkpoints, list)
    checkpoints[1]["observed_at"] = checkpoints[0]["observed_at"]
    with pytest.raises(ValidationError, match="strictly increasing"):
        CrossDatasetObservations.model_validate(payload)


def test_latest_dataset_gates_and_dataset_and_overall_drawdowns_all_block() -> None:
    from evalforge.cross_dataset_trends import (
        CrossDatasetObservations,
        CrossDatasetPolicy,
        analyze_cross_dataset_trends,
    )

    observations_payload = _observations_payload()
    checkpoints = observations_payload["checkpoints"]
    assert isinstance(checkpoints, list)
    checkpoints[:] = [checkpoints[0], checkpoints[-1]]
    checkpoints[0]["datasets"][0]["score"] = 0.9
    checkpoints[0]["datasets"][1]["score"] = 1.0
    checkpoints[0]["datasets"][1]["release_ready"] = False
    checkpoints[1]["datasets"][0]["score"] = 0.6
    checkpoints[1]["datasets"][0]["release_ready"] = False
    checkpoints[1]["datasets"][1]["score"] = 0.7
    policy_payload = _policy_payload()
    policy_datasets = policy_payload["datasets"]
    assert isinstance(policy_datasets, list)
    policy_datasets[0]["max_drawdown"] = 0.2
    policy_datasets[1]["max_drawdown"] = 0.1
    policy_payload["max_overall_drawdown"] = 0.15

    report = analyze_cross_dataset_trends(
        CrossDatasetObservations.model_validate(observations_payload),
        CrossDatasetPolicy.model_validate(policy_payload),
    )

    assert [
        (failure.scope, failure.dataset_id, failure.reason) for failure in report.gate_failures
    ] == [
        ("dataset", "support", "latest_underlying_gate"),
        ("dataset", "support", "max_drawdown"),
        ("dataset", "safety", "max_drawdown"),
        ("overall", None, "max_drawdown"),
    ]
    assert report.datasets[0].max_drawdown == pytest.approx(0.3)
    assert report.datasets[1].max_drawdown == pytest.approx(0.3)
    assert report.overall.max_drawdown == pytest.approx(0.3)
    assert not report.release_ready


def test_trend_report_rejects_coercive_schema_version() -> None:
    from pydantic import ValidationError

    from evalforge.cross_dataset_trends import (
        CrossDatasetObservations,
        CrossDatasetPolicy,
        CrossDatasetTrendArtifact,
        build_cross_dataset_trend_artifact,
    )

    artifact = build_cross_dataset_trend_artifact(
        CrossDatasetObservations.model_validate(_observations_payload()),
        CrossDatasetPolicy.model_validate(_policy_payload()),
    )
    payload = artifact.model_dump(mode="json")
    payload["report"]["schema_version"] = True

    with pytest.raises(ValidationError, match="schema_version must be an integer"):
        CrossDatasetTrendArtifact.model_validate(payload)


def test_weighted_score_and_drawdown_equality_do_not_fail_from_binary_rounding() -> None:
    from evalforge.cross_dataset_trends import (
        CrossDatasetObservations,
        CrossDatasetPolicy,
        analyze_cross_dataset_trends,
    )

    policy_payload = _policy_payload()
    policy_payload["max_overall_drawdown"] = 0.1
    report = analyze_cross_dataset_trends(
        CrossDatasetObservations.model_validate(_observations_payload()),
        CrossDatasetPolicy.model_validate(policy_payload),
    )

    assert [checkpoint.overall_score for checkpoint in report.checkpoints] == [
        0.65,
        0.85,
        0.75,
    ]
    assert report.overall.max_drawdown == 0.1
    assert report.release_ready


@pytest.mark.parametrize("weight", [5e-324, 0.000999, 1.0001, 1000.001])
def test_policy_rejects_weights_outside_fixed_point_contract(weight: float) -> None:
    from pydantic import ValidationError

    from evalforge.cross_dataset_trends import CrossDatasetPolicy

    payload = _policy_payload()
    datasets = payload["datasets"]
    assert isinstance(datasets, list)
    datasets[0]["weight"] = weight

    with pytest.raises(ValidationError, match="weight"):
        CrossDatasetPolicy.model_validate(payload)


def test_scores_and_drawdown_budgets_reject_excess_decimal_places() -> None:
    from pydantic import ValidationError

    from evalforge.cross_dataset_trends import CrossDatasetObservations, CrossDatasetPolicy

    observations_payload = _observations_payload()
    observations_payload["checkpoints"][0]["datasets"][0]["score"] = 0.1234567
    with pytest.raises(ValidationError, match="six decimal places"):
        CrossDatasetObservations.model_validate(observations_payload)

    policy_payload = _policy_payload()
    policy_payload["max_overall_drawdown"] = 0.1234567
    with pytest.raises(ValidationError, match="six decimal places"):
        CrossDatasetPolicy.model_validate(policy_payload)


def test_smallest_valid_weighted_drawdown_above_budget_cannot_round_to_pass() -> None:
    from evalforge.cross_dataset_trends import (
        CrossDatasetObservations,
        CrossDatasetPolicy,
        analyze_cross_dataset_trends,
    )

    observations_payload = _observations_payload()
    checkpoints = observations_payload["checkpoints"]
    assert isinstance(checkpoints, list)
    checkpoints[:] = [checkpoints[0], checkpoints[-1]]
    checkpoints[0]["datasets"][0]["score"] = 0.9
    checkpoints[0]["datasets"][1]["score"] = 0.9
    checkpoints[1]["datasets"][0]["score"] = 0.8
    checkpoints[1]["datasets"][1]["score"] = 0.799999

    policy_payload = _policy_payload()
    policy_payload["max_overall_drawdown"] = 0.1
    datasets = policy_payload["datasets"]
    assert isinstance(datasets, list)
    datasets[0]["weight"] = 1000.0
    datasets[0]["max_drawdown"] = 0.2
    datasets[1]["weight"] = 0.001
    datasets[1]["max_drawdown"] = 0.2

    report = analyze_cross_dataset_trends(
        CrossDatasetObservations.model_validate(observations_payload),
        CrossDatasetPolicy.model_validate(policy_payload),
    )

    assert report.overall.max_drawdown > 0.1
    assert report.gate_failures[-1].scope == "overall"
    assert not report.release_ready


def test_trend_decisions_ignore_ambient_decimal_precision() -> None:
    from decimal import localcontext

    from evalforge.cross_dataset_trends import (
        CrossDatasetObservations,
        CrossDatasetPolicy,
        analyze_cross_dataset_trends,
    )

    observations_payload = _observations_payload()
    checkpoints = observations_payload["checkpoints"]
    assert isinstance(checkpoints, list)
    checkpoints[:] = [checkpoints[0], checkpoints[-1]]
    for dataset in checkpoints[0]["datasets"]:
        dataset["score"] = 0.99
    for dataset in checkpoints[1]["datasets"]:
        dataset["score"] = 0.88

    policy_payload = _policy_payload()
    policy_payload["max_overall_drawdown"] = 0.1
    for dataset in policy_payload["datasets"]:
        dataset["max_drawdown"] = 0.1

    with localcontext() as context:
        context.prec = 1
        report = analyze_cross_dataset_trends(
            CrossDatasetObservations.model_validate(observations_payload),
            CrossDatasetPolicy.model_validate(policy_payload),
        )

    assert [checkpoint.overall_score for checkpoint in report.checkpoints] == [0.99, 0.88]
    assert report.overall.max_drawdown == 0.11
    assert not report.release_ready


def test_artifact_rejects_rehashed_internally_contradictory_report() -> None:
    from copy import deepcopy

    from pydantic import ValidationError

    from evalforge.cross_dataset_trends import (
        CrossDatasetObservations,
        CrossDatasetPolicy,
        CrossDatasetTrendArtifact,
        build_cross_dataset_trend_artifact,
    )
    from evalforge.provenance import canonical_json_sha256

    artifact = build_cross_dataset_trend_artifact(
        CrossDatasetObservations.model_validate(_observations_payload()),
        CrossDatasetPolicy.model_validate(_policy_payload()),
    )

    for mutation in ("checkpoint_count", "latest_gate"):
        payload = deepcopy(artifact.model_dump(mode="json"))
        if mutation == "checkpoint_count":
            payload["report"]["checkpoint_count"] = 999
        else:
            payload["report"]["datasets"][0]["latest_release_ready"] = False
        payload["report_sha256"] = canonical_json_sha256(payload["report"])
        payload["trend_analysis_id"] = canonical_json_sha256(
            {
                "canonicalization_version": payload["canonicalization_version"],
                "observations_sha256": payload["observations_sha256"],
                "policy_sha256": payload["policy_sha256"],
                "producer_sha256": payload["producer_sha256"],
                "report_sha256": payload["report_sha256"],
                "trend_analysis_id_schema_version": 1,
                "trend_semantics_version": payload["trend_semantics_version"],
            }
        )

        with pytest.raises(ValidationError, match="trend report"):
            CrossDatasetTrendArtifact.model_validate(payload)


def test_trend_report_rederives_each_weighted_checkpoint_score() -> None:
    from pydantic import ValidationError

    from evalforge.cross_dataset_trends import (
        CrossDatasetObservations,
        CrossDatasetPolicy,
        CrossDatasetTrendReport,
        build_cross_dataset_trend_artifact,
    )

    artifact = build_cross_dataset_trend_artifact(
        CrossDatasetObservations.model_validate(_observations_payload()),
        CrossDatasetPolicy.model_validate(_policy_payload()),
    )
    report = artifact.report.model_dump(mode="json")
    report["checkpoints"][0]["overall_score"] = 0.66
    report["overall"]["first_score"] = 0.66

    with pytest.raises(ValidationError, match="weighted score"):
        CrossDatasetTrendReport.model_validate(report)


def test_artifact_embeds_policy_and_rejects_rehashed_policy_control_tampering() -> None:
    from copy import deepcopy

    from pydantic import ValidationError

    from evalforge.cross_dataset_trends import (
        CrossDatasetObservations,
        CrossDatasetPolicy,
        CrossDatasetTrendArtifact,
        build_cross_dataset_trend_artifact,
    )
    from evalforge.provenance import canonical_json_sha256

    policy = CrossDatasetPolicy.model_validate(_policy_payload())
    artifact = build_cross_dataset_trend_artifact(
        CrossDatasetObservations.model_validate(_observations_payload()), policy
    )
    payload = artifact.model_dump(mode="json")

    assert payload["policy"] == policy.model_dump(mode="json")
    digest_mismatch = deepcopy(payload)
    digest_mismatch["policy_sha256"] = "d" * 64
    with pytest.raises(ValidationError, match="policy_sha256"):
        CrossDatasetTrendArtifact.model_validate(digest_mismatch)

    payload["policy"]["datasets"][0]["weight"] = 4.0
    payload["policy_sha256"] = canonical_json_sha256(payload["policy"])
    payload["trend_analysis_id"] = canonical_json_sha256(
        {
            "canonicalization_version": payload["canonicalization_version"],
            "observations_sha256": payload["observations_sha256"],
            "policy_sha256": payload["policy_sha256"],
            "producer_sha256": payload["producer_sha256"],
            "report_sha256": payload["report_sha256"],
            "trend_analysis_id_schema_version": 1,
            "trend_semantics_version": payload["trend_semantics_version"],
        }
    )

    with pytest.raises(ValidationError, match="policy controls"):
        CrossDatasetTrendArtifact.model_validate(payload)


@pytest.mark.parametrize("mutation", ["noncanonical", "not_increasing"])
def test_serialized_report_requires_canonical_strictly_increasing_utc_timestamps(
    mutation: str,
) -> None:
    from pydantic import ValidationError

    from evalforge.cross_dataset_trends import (
        CrossDatasetObservations,
        CrossDatasetPolicy,
        CrossDatasetTrendReport,
        build_cross_dataset_trend_artifact,
    )

    artifact = build_cross_dataset_trend_artifact(
        CrossDatasetObservations.model_validate(_observations_payload()),
        CrossDatasetPolicy.model_validate(_policy_payload()),
    )
    report = artifact.report.model_dump(mode="json")
    if mutation == "noncanonical":
        report["checkpoints"][0]["observed_at"] = "2026-01-01T00:00:00+00:00"
    else:
        report["checkpoints"][1]["observed_at"] = report["checkpoints"][0]["observed_at"]

    with pytest.raises(ValidationError, match=r"observed_at|timestamp"):
        CrossDatasetTrendReport.model_validate(report)


@pytest.mark.parametrize("mutation", ["checkpoint_integer", "summary_string"])
def test_serialized_report_rejects_coercive_boolean_evidence(mutation: str) -> None:
    from pydantic import ValidationError

    from evalforge.cross_dataset_trends import (
        CrossDatasetObservations,
        CrossDatasetPolicy,
        CrossDatasetTrendReport,
        build_cross_dataset_trend_artifact,
    )

    artifact = build_cross_dataset_trend_artifact(
        CrossDatasetObservations.model_validate(_observations_payload()),
        CrossDatasetPolicy.model_validate(_policy_payload()),
    )
    report = artifact.report.model_dump(mode="json")
    if mutation == "checkpoint_integer":
        report["checkpoints"][-1]["dataset_release_ready"][0] = 1
    else:
        report["datasets"][0]["latest_release_ready"] = "true"

    with pytest.raises(ValidationError, match="bool"):
        CrossDatasetTrendReport.model_validate(report)


def test_artifact_rejects_stale_observations_digest_with_coherent_rehashed_report() -> None:
    from copy import deepcopy

    from pydantic import ValidationError

    from evalforge.cross_dataset_trends import (
        CrossDatasetObservations,
        CrossDatasetPolicy,
        CrossDatasetTrendArtifact,
        build_cross_dataset_trend_artifact,
    )
    from evalforge.provenance import canonical_json_sha256

    policy = CrossDatasetPolicy.model_validate(_policy_payload())
    original = build_cross_dataset_trend_artifact(
        CrossDatasetObservations.model_validate(_observations_payload()), policy
    )
    alternate_payload = _observations_payload()
    alternate_checkpoints = alternate_payload["checkpoints"]
    assert isinstance(alternate_checkpoints, list)
    alternate_checkpoints[-1]["datasets"][0]["score"] = 0.7
    alternate = build_cross_dataset_trend_artifact(
        CrossDatasetObservations.model_validate(alternate_payload), policy
    )
    payload = deepcopy(alternate.model_dump(mode="json"))
    payload["observations_sha256"] = original.observations_sha256
    payload["trend_analysis_id"] = canonical_json_sha256(
        {
            "canonicalization_version": payload["canonicalization_version"],
            "observations_sha256": payload["observations_sha256"],
            "policy_sha256": payload["policy_sha256"],
            "producer_sha256": payload["producer_sha256"],
            "report_sha256": payload["report_sha256"],
            "trend_analysis_id_schema_version": 1,
            "trend_semantics_version": payload["trend_semantics_version"],
        }
    )

    with pytest.raises(ValidationError, match="observations_sha256"):
        CrossDatasetTrendArtifact.model_validate(payload)
