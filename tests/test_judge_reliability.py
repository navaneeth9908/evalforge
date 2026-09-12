from __future__ import annotations

import json

import pytest


def _rubric() -> object:
    from evalforge.contracts import Rubric

    return Rubric.model_validate(
        {
            "schema_version": 1,
            "rubric_id": "reliability",
            "name": "Judge reliability",
            "minimum_weighted_score": 0,
            "dimensions": [
                {
                    "schema_version": 1,
                    "dimension_id": "quality",
                    "description": "Score output quality.",
                    "weight": 1,
                    "minimum_score": 0,
                }
            ],
        }
    )


def test_repeated_blinded_calibration_observations_measure_biases() -> None:
    from evalforge.contracts import Rubric
    from evalforge.judge_reliability import (
        CalibrationCase,
        CalibrationDataset,
        JudgeReliabilityPolicy,
        measure_judge_reliability,
    )
    from evalforge.judging import JudgeRequest

    dataset = CalibrationDataset(
        schema_version=1,
        dataset_id="synthetic-calibration",
        cases=(
            CalibrationCase(
                schema_version=1,
                case_id="concise-case",
                prompt="Answer the synthetic question.",
                candidate_output="Concise answer.",
                synthetic_label=0.7,
                verbosity_pair_id="answer-pair",
                verbosity="concise",
            ),
            CalibrationCase(
                schema_version=1,
                case_id="verbose-case",
                prompt="Answer the synthetic question.",
                candidate_output="Verbose answer with additional harmless wording.",
                synthetic_label=0.7,
                verbosity_pair_id="answer-pair",
                verbosity="verbose",
            ),
        ),
    )
    requests: list[JudgeRequest] = []

    class DeterministicFakeJudge:
        def judge(self, request: JudgeRequest) -> str:
            requests.append(request)
            payload = json.loads(request.user_prompt.splitlines()[1])
            score = 0.6 if payload["candidate_output"] == "Concise answer." else 0.8
            return json.dumps(
                {
                    "schema_version": 1,
                    "dimensions": [
                        {
                            "schema_version": 1,
                            "dimension_id": "quality",
                            "score": score,
                            "rationale": "Deterministic synthetic score.",
                        }
                    ],
                    "summary": "Deterministic fake observation.",
                }
            )

    rubric = _rubric()
    assert isinstance(rubric, Rubric)
    report = measure_judge_reliability(
        rubric,
        dataset,
        DeterministicFakeJudge(),
        JudgeReliabilityPolicy(
            repetitions=2,
            agreement_tolerance=0.05,
            minimum_agreement=1.0,
            maximum_drift=0.0,
        ),
    )

    assert report.observation_count == 4
    assert report.agreement == 1.0
    assert report.drift == 0.0
    assert report.maximum_position_delta == 0.0
    assert report.verbosity_bias == pytest.approx(0.2)
    assert report.calibration_mean_absolute_error == pytest.approx(0.1)
    assert report.gate_failures == ()
    assert report.release_ready is True
    assert [(item.case_id, item.repetition, item.position) for item in report.observations] == [
        ("concise-case", 1, 1),
        ("verbose-case", 1, 2),
        ("verbose-case", 2, 1),
        ("concise-case", 2, 2),
    ]
    assert len({item.blind_id for item in report.observations}) == 4

    assert len(requests) == 4
    for request in requests:
        assert "concise-case" not in request.user_prompt
        assert "verbose-case" not in request.user_prompt
        assert "answer-pair" not in request.user_prompt
        assert "synthetic_label" not in request.user_prompt
        assert "0.7" not in request.user_prompt


def test_minimum_agreement_and_maximum_drift_block_release() -> None:
    from evalforge.contracts import Rubric
    from evalforge.judge_reliability import (
        CalibrationCase,
        CalibrationDataset,
        JudgeReliabilityPolicy,
        measure_judge_reliability,
    )
    from evalforge.judging import JudgeRequest

    dataset = CalibrationDataset(
        schema_version=1,
        dataset_id="drifting-calibration",
        cases=(
            CalibrationCase(
                schema_version=1,
                case_id="case-a",
                prompt="Synthetic A.",
                candidate_output="Answer A.",
                synthetic_label=0.8,
            ),
            CalibrationCase(
                schema_version=1,
                case_id="case-b",
                prompt="Synthetic B.",
                candidate_output="Answer B.",
                synthetic_label=0.7,
            ),
        ),
    )
    scores = iter((0.9, 0.8, 0.6, 0.7))

    class DriftingFakeJudge:
        def judge(self, request: JudgeRequest) -> str:
            return json.dumps(
                {
                    "schema_version": 1,
                    "dimensions": [
                        {
                            "schema_version": 1,
                            "dimension_id": "quality",
                            "score": next(scores),
                            "rationale": "Synthetic drift.",
                        }
                    ],
                    "summary": "Synthetic drift observation.",
                }
            )

    rubric = _rubric()
    assert isinstance(rubric, Rubric)
    report = measure_judge_reliability(
        rubric,
        dataset,
        DriftingFakeJudge(),
        JudgeReliabilityPolicy(
            repetitions=2,
            agreement_tolerance=0.1,
            minimum_agreement=0.5,
            maximum_drift=0.1,
        ),
    )

    assert report.agreement == 0.0
    assert report.drift == pytest.approx(0.2)
    assert report.maximum_position_delta == pytest.approx(0.2)
    assert report.calibration_mean_absolute_error == pytest.approx(0.0)
    assert report.verbosity_bias is None
    assert [failure.metric for failure in report.gate_failures] == ["agreement", "drift"]
    assert report.gate_failures[0].observed == 0.0
    assert report.gate_failures[0].required == 0.5
    assert report.gate_failures[1].observed == pytest.approx(0.2)
    assert report.gate_failures[1].required == 0.1
    assert report.release_ready is False


@pytest.mark.parametrize(
    "cases",
    [
        (
            {
                "schema_version": 1,
                "case_id": "only-concise",
                "prompt": "Synthetic prompt.",
                "candidate_output": "Short.",
                "synthetic_label": 0.5,
                "verbosity_pair_id": "pair",
                "verbosity": "concise",
            },
            {
                "schema_version": 1,
                "case_id": "unpaired",
                "prompt": "Another prompt.",
                "candidate_output": "Other.",
                "synthetic_label": 0.5,
            },
        ),
        (
            {
                "schema_version": 1,
                "case_id": "concise-a",
                "prompt": "Synthetic prompt.",
                "candidate_output": "Short A.",
                "synthetic_label": 0.5,
                "verbosity_pair_id": "pair",
                "verbosity": "concise",
            },
            {
                "schema_version": 1,
                "case_id": "concise-b",
                "prompt": "Synthetic prompt.",
                "candidate_output": "Short B.",
                "synthetic_label": 0.5,
                "verbosity_pair_id": "pair",
                "verbosity": "concise",
            },
        ),
        (
            {
                "schema_version": 1,
                "case_id": "concise",
                "prompt": "Synthetic prompt.",
                "candidate_output": "Short.",
                "synthetic_label": 0.4,
                "verbosity_pair_id": "pair",
                "verbosity": "concise",
            },
            {
                "schema_version": 1,
                "case_id": "verbose",
                "prompt": "Synthetic prompt.",
                "candidate_output": "Longer wording.",
                "synthetic_label": 0.6,
                "verbosity_pair_id": "pair",
                "verbosity": "verbose",
            },
        ),
    ],
)
def test_calibration_dataset_rejects_invalid_verbosity_harnesses(
    cases: tuple[dict[str, object], ...],
) -> None:
    from pydantic import ValidationError

    from evalforge.judge_reliability import CalibrationDataset

    with pytest.raises(ValidationError, match="verbosity pairs"):
        CalibrationDataset.model_validate(
            {
                "schema_version": 1,
                "dataset_id": "bad-verbosity-pair",
                "cases": cases,
            }
        )


def test_position_harness_compares_all_forward_and_reverse_repetitions() -> None:
    from evalforge.contracts import Rubric
    from evalforge.judge_reliability import (
        CalibrationCase,
        CalibrationDataset,
        JudgeReliabilityPolicy,
        measure_judge_reliability,
    )
    from evalforge.judging import JudgeRequest

    dataset = CalibrationDataset(
        schema_version=1,
        dataset_id="position-calibration",
        cases=(
            CalibrationCase(
                schema_version=1,
                case_id="case-a",
                prompt="Synthetic A.",
                candidate_output="Answer A.",
                synthetic_label=0.6,
            ),
            CalibrationCase(
                schema_version=1,
                case_id="case-b",
                prompt="Synthetic B.",
                candidate_output="Answer B.",
                synthetic_label=0.5,
            ),
        ),
    )
    scores = iter((0.9, 0.8, 0.2, 0.3, 0.7, 0.6, 0.4, 0.5))

    class PositionBiasedFakeJudge:
        def judge(self, request: JudgeRequest) -> str:
            return json.dumps(
                {
                    "schema_version": 1,
                    "dimensions": [
                        {
                            "schema_version": 1,
                            "dimension_id": "quality",
                            "score": next(scores),
                            "rationale": "Deterministic position score.",
                        }
                    ],
                    "summary": "Deterministic position observation.",
                }
            )

    rubric = _rubric()
    assert isinstance(rubric, Rubric)
    report = measure_judge_reliability(
        rubric,
        dataset,
        PositionBiasedFakeJudge(),
        JudgeReliabilityPolicy(
            repetitions=4,
            agreement_tolerance=1.0,
            minimum_agreement=0.0,
            maximum_drift=1.0,
        ),
    )

    assert report.maximum_position_delta == pytest.approx(0.4)


def test_calibration_and_reliability_policy_reject_coercive_contracts() -> None:
    from pydantic import ValidationError

    from evalforge.judge_reliability import (
        CalibrationCase,
        CalibrationDataset,
        JudgeReliabilityPolicy,
    )

    valid_case = {
        "schema_version": 1,
        "case_id": "case-a",
        "prompt": "Synthetic prompt.",
        "candidate_output": "Synthetic output.",
        "synthetic_label": 0.5,
    }
    for invalid_case in (
        {**valid_case, "schema_version": True},
        {**valid_case, "synthetic_label": True},
        {**valid_case, "synthetic_label": "0.5"},
        {**valid_case, "verbosity_pair_id": "pair"},
    ):
        with pytest.raises(ValidationError):
            CalibrationCase.model_validate(invalid_case)

    valid_second = {**valid_case, "case_id": "case-b"}
    for invalid_dataset in (
        {
            "schema_version": True,
            "dataset_id": "calibration",
            "cases": [valid_case, valid_second],
        },
        {
            "schema_version": 1,
            "dataset_id": "calibration",
            "cases": [valid_case, valid_case],
        },
    ):
        with pytest.raises(ValidationError):
            CalibrationDataset.model_validate(invalid_dataset)

    valid_policy = {
        "repetitions": 2,
        "agreement_tolerance": 0.1,
        "minimum_agreement": 0.8,
        "maximum_drift": 0.1,
    }
    for invalid_policy in (
        {**valid_policy, "repetitions": True},
        {**valid_policy, "agreement_tolerance": True},
        {**valid_policy, "minimum_agreement": "0.8"},
        {**valid_policy, "maximum_drift": False},
        {**valid_policy, "unknown": 1},
    ):
        with pytest.raises(ValidationError):
            JudgeReliabilityPolicy.model_validate(invalid_policy)
