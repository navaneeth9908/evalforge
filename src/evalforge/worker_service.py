"""Deployable one-shot and supervised commands for durable evaluation jobs."""

from __future__ import annotations

import argparse
import json
import signal
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from evalforge.registry import SQLiteRunRegistry, StoredEvaluationJob
from evalforge.worker import EvaluationWorker


@dataclass(frozen=True)
class WorkerServiceResult:
    """Bounded operational summary returned when a worker service stops."""

    processed_jobs: int
    stop_reason: str


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Process durable EvalForge evaluation jobs.")
    parser.add_argument("--database-path", type=Path, required=True)
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--lease-seconds", type=int, default=60)
    parser.add_argument("--retry-backoff-seconds", type=int, default=5)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--serve", action="store_true")
    parser.add_argument("--poll-interval-seconds", type=int)
    parser.add_argument("--max-idle-polls", type=int)
    return parser


def _job_status(completed: StoredEvaluationJob) -> dict[str, int | str | bool]:
    return {
        "attempts": completed.attempts,
        "job_id": completed.job_id,
        "processed": True,
        "status": completed.status,
    }


def _print_json(payload: dict[str, int | str | bool]) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")), flush=True)


@contextmanager
def _termination_event() -> Iterator[Event]:
    stop_event = Event()
    previous_handlers: dict[signal.Signals, signal._HANDLER] = {}

    def request_stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    stop_signals = [signal.SIGINT, signal.SIGTERM]
    if sys.platform == "win32":
        stop_signals.append(signal.SIGBREAK)
    for stop_signal in stop_signals:
        previous_handlers[stop_signal] = signal.signal(stop_signal, request_stop)
    try:
        yield stop_event
    finally:
        for stop_signal, previous_handler in previous_handlers.items():
            signal.signal(stop_signal, previous_handler)


def run_service(
    worker: EvaluationWorker,
    *,
    stop_event: Event,
    poll_interval_seconds: int,
    max_idle_polls: int | None = None,
) -> WorkerServiceResult:
    """Process ready jobs until termination or an optional bounded idle limit."""

    processed_jobs = 0
    idle_polls = 0
    while not stop_event.is_set():
        completed = worker.run_once()
        if completed is not None:
            processed_jobs += 1
            idle_polls = 0
            _print_json(_job_status(completed))
            continue
        idle_polls += 1
        if max_idle_polls is not None and idle_polls >= max_idle_polls:
            return WorkerServiceResult(processed_jobs, "idle_limit")
        stop_event.wait(poll_interval_seconds)
    return WorkerServiceResult(processed_jobs, "signal")


def main(argv: Sequence[str] | None = None) -> int:
    """Run one worker claim or a supervised queue-draining service."""

    parser = _parser()
    arguments = parser.parse_args(argv)
    if not arguments.database_path.is_absolute():
        parser.error("--database-path must be a non-empty absolute path")
    if arguments.once and (
        arguments.poll_interval_seconds is not None or arguments.max_idle_polls is not None
    ):
        parser.error("poll controls require --serve")
    poll_interval_seconds = (
        1 if arguments.poll_interval_seconds is None else arguments.poll_interval_seconds
    )
    if arguments.serve and not 1 <= poll_interval_seconds <= 60:
        parser.error("--poll-interval-seconds must be from 1 through 60")
    if arguments.max_idle_polls is not None and not 1 <= arguments.max_idle_polls <= 1_000_000:
        parser.error("--max-idle-polls must be from 1 through 1000000")
    try:
        worker = EvaluationWorker(
            SQLiteRunRegistry(arguments.database_path),
            worker_id=arguments.worker_id,
            lease_seconds=arguments.lease_seconds,
            retry_backoff_seconds=arguments.retry_backoff_seconds,
        )
        if arguments.once:
            completed = worker.run_once()
            if completed is None:
                _print_json({"processed": False})
            else:
                _print_json(_job_status(completed))
        else:
            with _termination_event() as stop_event:
                _print_json({"service_started": True})
                result = run_service(
                    worker,
                    stop_event=stop_event,
                    poll_interval_seconds=poll_interval_seconds,
                    max_idle_polls=arguments.max_idle_polls,
                )
            _print_json(
                {
                    "processed_jobs": result.processed_jobs,
                    "service_stopped": result.stop_reason,
                }
            )
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
