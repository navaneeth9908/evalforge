from __future__ import annotations

import math

import pytest
from pydantic import ValidationError


def test_comparison_policy_is_strict_versioned_and_bounded() -> None:
    from evalforge.contracts import ComparisonPolicy

    policy = ComparisonPolicy(
        schema_version=1,
        max_absolute_regression=0.1,
        max_relative_regression=0.2,
    )

    assert policy.model_dump() == {
        "schema_version": 1,
        "max_absolute_regression": 0.1,
        "max_relative_regression": 0.2,
    }
    for invalid_version in (True, 1.0, "1", 2):
        with pytest.raises(ValidationError):
            ComparisonPolicy(
                schema_version=invalid_version,  # type: ignore[arg-type]
                max_absolute_regression=0.0,
                max_relative_regression=0.0,
            )
    for invalid_budget in (True, "0.1", -0.01, 1.01, math.nan, math.inf, -math.inf):
        with pytest.raises(ValidationError):
            ComparisonPolicy(
                schema_version=1,
                max_absolute_regression=invalid_budget,  # type: ignore[arg-type]
                max_relative_regression=0.0,
            )
        with pytest.raises(ValidationError):
            ComparisonPolicy(
                schema_version=1,
                max_absolute_regression=0.0,
                max_relative_regression=invalid_budget,  # type: ignore[arg-type]
            )
