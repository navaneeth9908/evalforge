"""Installed one-shot command for authenticated remote evaluation workers."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence

from evalforge.api import EvaluationJobResponse
from evalforge.remote_worker import RemoteEvaluationWorker


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Process one EvalForge job through an authenticated worker API."
    )
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--lease-seconds", type=int, default=60)
    parser.add_argument("--retry-backoff-seconds", type=int, default=5)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    return parser


def _print_json(payload: dict[str, int | str | bool]) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")), flush=True)


def _job_status(completed: EvaluationJobResponse) -> dict[str, int | str | bool]:
    return {
        "attempts": completed.attempts,
        "job_id": completed.job_id,
        "processed": True,
        "status": completed.status,
    }


def _worker_from_environment(arguments: argparse.Namespace) -> RemoteEvaluationWorker:
    access_token = os.environ.pop("EVALFORGE_WORKER_ACCESS_TOKEN", None)
    try:
        base_url = os.environ["EVALFORGE_WORKER_API_URL"]
        if access_token is None:
            raise ValueError
        return RemoteEvaluationWorker(
            base_url=base_url,
            access_token=access_token,
            worker_id=arguments.worker_id,
            lease_seconds=arguments.lease_seconds,
            retry_backoff_seconds=arguments.retry_backoff_seconds,
            timeout_seconds=arguments.timeout_seconds,
        )
    finally:
        del access_token


def main(argv: Sequence[str] | None = None) -> int:
    """Consume secret environment configuration and process at most one remote job."""

    arguments = _parser().parse_args(argv)
    try:
        completed = _worker_from_environment(arguments).run_once()
        if completed is None:
            _print_json({"processed": False})
        else:
            _print_json(_job_status(completed))
    except BaseException:
        print(
            json.dumps(
                {"error": "remote_worker_execution_failed", "processed": False},
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
