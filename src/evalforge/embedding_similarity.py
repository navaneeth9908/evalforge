"""Deterministic semantic similarity over governed precomputed embeddings."""

from __future__ import annotations

import math
from fractions import Fraction
from typing import cast

from evalforge.contracts import (
    EmbeddingEvaluation,
    EmbeddingGateFailure,
    EmbeddingModelProvenance,
    EmbeddingPolicy,
    EmbeddingSimilarityReport,
    EmbeddingSimilarityResult,
)
from evalforge.provenance import JsonValue, canonical_json_sha256

EMBEDDING_SEMANTICS_VERSION = "embedding-cosine-l2-v1-scaled-hypot-fsum-exact-decision-aligned"


def model_provenance_sha256(model: EmbeddingModelProvenance) -> str:
    """Digest the complete normalized model identity used for policy approval."""
    return canonical_json_sha256(cast(JsonValue, model.model_dump(mode="json")))


def _vector_sha256(vector: tuple[float, ...]) -> str:
    return canonical_json_sha256(cast(JsonValue, list(vector)))


def _unit_vector(vector: tuple[float, ...]) -> tuple[float, ...]:
    scale = max(abs(coordinate) for coordinate in vector)
    scaled = tuple(coordinate / scale for coordinate in vector)
    norm = math.hypot(*scaled)
    return tuple(coordinate / norm for coordinate in scaled)


def _exact_collinear_direction(
    reference: tuple[float, ...], candidate: tuple[float, ...]
) -> float | None:
    if reference == candidate:
        return 1.0
    if all(
        reference_coordinate == -candidate_coordinate
        for reference_coordinate, candidate_coordinate in zip(reference, candidate, strict=True)
    ):
        return -1.0

    ratio: Fraction | None = None
    for reference_coordinate, candidate_coordinate in zip(reference, candidate, strict=True):
        if reference_coordinate == 0.0 or candidate_coordinate == 0.0:
            if reference_coordinate != 0.0 or candidate_coordinate != 0.0:
                return None
            continue
        coordinate_ratio = Fraction.from_float(candidate_coordinate) / Fraction.from_float(
            reference_coordinate
        )
        if ratio is None:
            ratio = coordinate_ratio
        elif coordinate_ratio != ratio:
            return None

    assert ratio is not None
    return 1.0 if ratio > 0 else -1.0


def _meets_cosine_threshold(
    reference: tuple[float, ...], candidate: tuple[float, ...], threshold: float
) -> bool:
    dot_product = Fraction()
    reference_squared_norm = Fraction()
    candidate_squared_norm = Fraction()
    for reference_coordinate, candidate_coordinate in zip(reference, candidate, strict=True):
        exact_reference = Fraction.from_float(reference_coordinate)
        exact_candidate = Fraction.from_float(candidate_coordinate)
        dot_product += exact_reference * exact_candidate
        reference_squared_norm += exact_reference * exact_reference
        candidate_squared_norm += exact_candidate * exact_candidate

    exact_threshold = Fraction.from_float(threshold)
    if exact_threshold >= 0:
        return dot_product >= 0 and dot_product * dot_product >= (
            exact_threshold * exact_threshold * reference_squared_norm * candidate_squared_norm
        )
    return dot_product >= 0 or dot_product * dot_product <= (
        exact_threshold * exact_threshold * reference_squared_norm * candidate_squared_norm
    )


def evaluate_embedding_similarity(
    evaluation: EmbeddingEvaluation, policy: EmbeddingPolicy
) -> EmbeddingSimilarityReport:
    """Score embedding pairs and require the exact governed model provenance."""
    provenance_sha256 = model_provenance_sha256(evaluation.model)
    if provenance_sha256 != policy.approved_model_sha256:
        raise ValueError("embedding model provenance is not approved by policy")

    results: list[EmbeddingSimilarityResult] = []
    for case in evaluation.cases:
        reference_unit = _unit_vector(case.reference_embedding)
        candidate_unit = _unit_vector(case.candidate_embedding)
        possible_endpoint = reference_unit == candidate_unit or all(
            reference == -candidate
            for reference, candidate in zip(reference_unit, candidate_unit, strict=True)
        )
        exact_endpoint = (
            _exact_collinear_direction(case.reference_embedding, case.candidate_embedding)
            if possible_endpoint
            else None
        )
        if exact_endpoint is not None:
            cosine_similarity = exact_endpoint
        else:
            cosine_similarity = math.fsum(
                reference * candidate
                for reference, candidate in zip(reference_unit, candidate_unit, strict=True)
            )
            cosine_similarity = max(
                math.nextafter(-1.0, 0.0),
                min(math.nextafter(1.0, 0.0), cosine_similarity),
            )
        passed = _meets_cosine_threshold(
            case.reference_embedding,
            case.candidate_embedding,
            policy.minimum_cosine_similarity,
        )
        if passed and cosine_similarity < policy.minimum_cosine_similarity:
            cosine_similarity = policy.minimum_cosine_similarity
        elif not passed and cosine_similarity >= policy.minimum_cosine_similarity:
            assert policy.minimum_cosine_similarity > -1.0
            cosine_similarity = math.nextafter(policy.minimum_cosine_similarity, -math.inf)
        euclidean_distance = math.dist(case.reference_embedding, case.candidate_embedding)
        results.append(
            EmbeddingSimilarityResult(
                case_id=case.case_id,
                cosine_similarity=cosine_similarity,
                euclidean_distance=euclidean_distance,
                passed=passed,
                reference_embedding_sha256=_vector_sha256(case.reference_embedding),
                candidate_embedding_sha256=_vector_sha256(case.candidate_embedding),
            )
        )

    passed_cases = sum(result.passed for result in results)
    pass_rate = passed_cases / len(results)
    gate_failures = (
        (
            EmbeddingGateFailure(
                code="minimum_pass_rate_not_met",
                observed=pass_rate,
                required=policy.minimum_pass_rate,
            ),
        )
        if pass_rate < policy.minimum_pass_rate
        else ()
    )
    return EmbeddingSimilarityReport(
        model=evaluation.model,
        model_provenance_sha256=provenance_sha256,
        minimum_cosine_similarity=policy.minimum_cosine_similarity,
        minimum_pass_rate=policy.minimum_pass_rate,
        total_cases=len(results),
        passed_cases=passed_cases,
        pass_rate=pass_rate,
        results=tuple(results),
        gate_failures=gate_failures,
        release_ready=not gate_failures,
    )
