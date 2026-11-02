"""Deployable one-shot command for durable evaluation jobs."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from evalforge.registry import SQLiteRunRegistry
from evalforge.worker import EvaluationWorker


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Process one durable EvalForge evaluation job.")
    parser.add_argument("--database-path", type=Path, required=True)
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--lease-seconds", type=int, default=60)
    parser.add_argument("--retry-backoff-seconds", type=int, default=5)
    parser.add_argument("--once", action="store_true", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Claim and execute at most one job from an explicitly configured registry."""

    parser = _parser()
    arguments = parser.parse_args(argv)
    if not arguments.database_path.is_absolute():
        parser.error("--database-path must be a non-empty absolute path")
    try:
        worker = EvaluationWorker(
            SQLiteRunRegistry(arguments.database_path),
            worker_id=arguments.worker_id,
            lease_seconds=arguments.lease_seconds,
            retry_backoff_seconds=arguments.retry_backoff_seconds,
        )
        completed = worker.run_once()
    except Exception:
        print(
            json.dumps(
                {"error": "worker_execution_failed", "processed": False},
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1
    if completed is None:
        print(json.dumps({"processed": False}, sort_keys=True, separators=(",", ":")))
    else:
        print(
            json.dumps(
                {
                    "attempts": completed.attempts,
                    "job_id": completed.job_id,
                    "processed": True,
                    "status": completed.status,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    return 0
