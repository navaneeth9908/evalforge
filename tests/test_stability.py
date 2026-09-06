from __future__ import annotations


def test_repeated_runs_report_population_variance_and_flaky_cases() -> None:
    from evalforge.contracts import (
        EvaluationCase,
        ObservationRun,
        ReleasePolicy,
        RepeatedObservations,
        StabilityPolicy,
    )
    from evalforge.stability import analyze_stability

    cases = (
        EvaluationCase(case_id="stable", prompt="Stable", expected_output="yes"),
        EvaluationCase(case_id="flaky", prompt="Flaky", expected_output="yes"),
    )
    observations = RepeatedObservations(
        schema_version=1,
        runs=(
            ObservationRun(run_id="run-1", outputs={"stable": "yes", "flaky": "yes"}),
            ObservationRun(run_id="run-2", outputs={"stable": "yes", "flaky": "no"}),
        ),
    )

    report = analyze_stability(
        cases,
        observations,
        evaluation_policy=ReleasePolicy(minimum_pass_rate=0.0),
        stability_policy=StabilityPolicy(
            schema_version=1,
            max_weighted_pass_rate_variance=0.05,
            max_flaky_case_rate=0.25,
        ),
    )

    assert report.schema_version == 1
    assert report.run_count == 2
    assert report.weighted_pass_rate.model_dump() == {
        "mean": 0.75,
        "minimum": 0.5,
        "maximum": 1.0,
        "population_variance": 0.0625,
    }
    assert [case.model_dump() for case in report.cases] == [
        {
            "case_id": "stable",
            "pass_count": 2,
            "fail_count": 0,
            "flaky": False,
        },
        {
            "case_id": "flaky",
            "pass_count": 1,
            "fail_count": 1,
            "flaky": True,
        },
    ]
    assert report.flaky_case_count == 1
    assert report.flaky_case_rate == 0.5
    assert [failure.model_dump() for failure in report.gate_failures] == [
        {
            "metric": "weighted_pass_rate_population_variance",
            "observed": 0.0625,
            "required": 0.05,
        },
        {"metric": "flaky_case_rate", "observed": 0.5, "required": 0.25},
    ]
    assert report.release_ready is False


def test_one_run_is_stable_with_zero_population_variance() -> None:
    from evalforge.contracts import (
        EvaluationCase,
        ObservationRun,
        ReleasePolicy,
        RepeatedObservations,
        StabilityPolicy,
    )
    from evalforge.stability import analyze_stability

    report = analyze_stability(
        (EvaluationCase(case_id="only", prompt="Only", expected_output="yes"),),
        RepeatedObservations(
            schema_version=1,
            runs=(ObservationRun(run_id="run-1", outputs={"only": "yes"}),),
        ),
        evaluation_policy=ReleasePolicy(minimum_pass_rate=1.0),
        stability_policy=StabilityPolicy(
            schema_version=1,
            max_weighted_pass_rate_variance=0.0,
            max_flaky_case_rate=0.0,
        ),
    )

    assert report.weighted_pass_rate.model_dump() == {
        "mean": 1.0,
        "minimum": 1.0,
        "maximum": 1.0,
        "population_variance": 0.0,
    }
    assert report.flaky_case_rate == 0.0
    assert report.gate_failures == ()
    assert report.release_ready is True


def test_stability_requires_each_observation_to_pass_the_suite_gate() -> None:
    from evalforge.contracts import (
        EvaluationCase,
        ObservationRun,
        ReleasePolicy,
        RepeatedObservations,
        StabilityPolicy,
    )
    from evalforge.stability import analyze_stability

    report = analyze_stability(
        (EvaluationCase(case_id="only", prompt="Only", expected_output="yes"),),
        RepeatedObservations(
            schema_version=1,
            runs=(
                ObservationRun(run_id="run-1", outputs={"only": "no"}),
                ObservationRun(run_id="run-2", outputs={"only": "no"}),
            ),
        ),
        evaluation_policy=ReleasePolicy(minimum_pass_rate=1.0),
        stability_policy=StabilityPolicy(
            schema_version=1,
            max_weighted_pass_rate_variance=0.0,
            max_flaky_case_rate=0.0,
        ),
    )

    assert [run.model_dump() for run in report.runs] == [
        {"run_id": "run-1", "weighted_pass_rate": 0.0, "release_ready": False},
        {"run_id": "run-2", "weighted_pass_rate": 0.0, "release_ready": False},
    ]
    assert report.failed_run_count == 2
    assert report.gate_failures == ()
    assert report.release_ready is False


def test_invalid_observation_samples_and_stability_limits_fail_closed() -> None:
    import math

    import pytest
    from pydantic import ValidationError

    from evalforge.contracts import (
        EvaluationCase,
        ObservationRun,
        ReleasePolicy,
        RepeatedObservations,
        StabilityPolicy,
    )
    from evalforge.stability import analyze_stability

    with pytest.raises(ValidationError):
        RepeatedObservations(schema_version=1, runs=())
    with pytest.raises(ValidationError, match="run IDs must be unique"):
        RepeatedObservations(
            schema_version=1,
            runs=(
                ObservationRun(run_id="same", outputs={"case": "yes"}),
                ObservationRun(run_id="same", outputs={"case": "no"}),
            ),
        )
    for invalid in (True, "0.1", -0.1, 1.1, math.nan, math.inf):
        with pytest.raises(ValidationError):
            StabilityPolicy(
                schema_version=1,
                max_weighted_pass_rate_variance=invalid,  # type: ignore[arg-type]
                max_flaky_case_rate=0.0,
            )

    with pytest.raises(ValueError, match="exactly match"):
        analyze_stability(
            (EvaluationCase(case_id="case", prompt="Case", expected_output="yes"),),
            RepeatedObservations(
                schema_version=1,
                runs=(ObservationRun(run_id="run-1", outputs={"extra": "yes"}),),
            ),
            evaluation_policy=ReleasePolicy(minimum_pass_rate=1.0),
            stability_policy=StabilityPolicy(
                schema_version=1,
                max_weighted_pass_rate_variance=0.0,
                max_flaky_case_rate=0.0,
            ),
        )
