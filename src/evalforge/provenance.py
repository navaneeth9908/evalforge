"""Content-addressed provenance for deterministic evaluation inputs."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import TypeAlias

JsonScalar: TypeAlias = bool | int | float | str | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

CANONICALIZATION_VERSION = "evalforge-json-v1"
EVALUATION_SEMANTICS_VERSION = f"normalized-exact-v1-unicode-{unicodedata.unidata_version}"
RUN_ID_SCHEMA_VERSION = 1


def _normalize_canonical_value(value: JsonValue) -> JsonValue:
    if isinstance(value, float) and value == 0.0:
        return 0.0
    if isinstance(value, list):
        return [_normalize_canonical_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_canonical_value(item) for key, item in value.items()}
    return value


def canonical_json_bytes(value: JsonValue) -> bytes:
    """Serialize a JSON value deterministically for content addressing."""
    try:
        return json.dumps(
            _normalize_canonical_value(value),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("JSON strings must contain valid Unicode scalar values") from exc


def canonical_json_sha256(value: JsonValue) -> str:
    """Return the lowercase SHA-256 digest of canonical JSON bytes."""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def deterministic_run_id(
    *,
    suite_sha256: str,
    candidate_sha256: str,
    dataset_manifest_sha256: str | None,
) -> str:
    """Identify an evaluation from its inputs and versioned evaluation semantics."""
    return canonical_json_sha256(
        {
            "candidate_sha256": candidate_sha256,
            "canonicalization_version": CANONICALIZATION_VERSION,
            "dataset_manifest_sha256": dataset_manifest_sha256,
            "evaluation_semantics_version": EVALUATION_SEMANTICS_VERSION,
            "run_id_schema_version": RUN_ID_SCHEMA_VERSION,
            "suite_sha256": suite_sha256,
        }
    )
