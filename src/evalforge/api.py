"""Versioned HTTP API for deterministic EvalForge services."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from evalforge.contracts import (
    CandidateOutput,
    EvaluationReport,
    EvaluationSuite,
    OutputText,
    Sha256Digest,
)
from evalforge.registry import RunSummary, SQLiteRunRegistry, StoredRun
from evalforge.service import EvalForgeService, SuiteNotFoundError


class HealthResponse(BaseModel):
    """Versioned readiness state for the local API and registry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    api_version: Literal["v1"] = "v1"
    status: Literal["ok"] = "ok"
    storage_schema_version: int


class ErrorDetail(BaseModel):
    """Stable error identifier and redaction-safe public message."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str


class ErrorResponse(BaseModel):
    """Common error envelope for API failures."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    error: ErrorDetail


class APIError(Exception):
    """Internal control-flow exception containing only public-safe fields."""

    def __init__(self, *, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class SuiteResponse(BaseModel):
    """Stored suite metadata and the validated versioned contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    suite_id: Sha256Digest
    created_at: str
    suite: EvaluationSuite


class SuitePage(BaseModel):
    """Bounded page of stored evaluation suites."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[SuiteResponse, ...]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0, le=10_000)
    total: int = Field(ge=0)


class EvaluationRequest(BaseModel):
    """Bounded candidate evidence evaluated against one stored suite."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    suite_id: Sha256Digest
    candidate_outputs: dict[str, OutputText | CandidateOutput] = Field(
        min_length=1,
        max_length=10_000,
    )


class EvaluationReportResponse(EvaluationReport):
    """Core report wrapped with reproducibility identifiers."""

    suite_name: str = Field(min_length=1, max_length=512)
    suite_sha256: Sha256Digest
    candidate_sha256: Sha256Digest
    dataset_manifest_sha256: Sha256Digest | None
    evaluation_semantics_version: str
    canonicalization_version: str
    run_id: Sha256Digest


class EvaluationResponse(BaseModel):
    """Persisted evaluation result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: Sha256Digest
    report: EvaluationReportResponse


class RunSummaryResponse(BaseModel):
    """Compact content-addressed run history row."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: Sha256Digest
    suite_sha256: Sha256Digest
    suite_name: str
    candidate_sha256: Sha256Digest
    report_sha256: Sha256Digest
    dataset_manifest_sha256: Sha256Digest | None
    release_ready: bool
    created_at: str


class RunPage(BaseModel):
    """Bounded page of immutable evaluation runs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[RunSummaryResponse, ...]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0, le=10_000)
    total: int = Field(ge=0)


class RunResponse(RunSummaryResponse):
    """Complete stored inputs and report for one run."""

    suite: EvaluationSuite
    candidate: dict[str, OutputText | CandidateOutput]
    report: EvaluationReportResponse


def _run_summary_response(summary: RunSummary) -> RunSummaryResponse:
    return RunSummaryResponse(
        run_id=summary.run_id,
        suite_sha256=summary.suite_sha256,
        suite_name=summary.suite_name,
        candidate_sha256=summary.candidate_sha256,
        report_sha256=summary.report_sha256,
        dataset_manifest_sha256=summary.dataset_manifest_sha256,
        release_ready=summary.release_ready,
        created_at=summary.created_at,
    )


def _run_response(stored: StoredRun) -> RunResponse:
    summary = _run_summary_response(stored.summary())
    return RunResponse.model_validate(
        {
            **summary.model_dump(),
            "suite": stored.suite,
            "candidate": stored.candidate,
            "report": stored.report,
        }
    )


def create_app(database_path: Path) -> FastAPI:
    """Create an isolated EvalForge API backed by local SQLite storage."""

    registry = SQLiteRunRegistry(database_path)
    service = EvalForgeService(registry)
    app = FastAPI(title="EvalForge API", version="1.0.0")
    app.state.service = service

    @app.exception_handler(APIError)
    async def handle_api_error(_request: Request, exc: APIError) -> JSONResponse:
        payload = ErrorResponse(error=ErrorDetail(code=exc.code, message=exc.message))
        return JSONResponse(status_code=exc.status_code, content=payload.model_dump(mode="json"))

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _request: Request, _exc: RequestValidationError
    ) -> JSONResponse:
        payload = ErrorResponse(
            error=ErrorDetail(code="validation_error", message="request validation failed")
        )
        return JSONResponse(status_code=422, content=payload.model_dump(mode="json"))

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_request: Request, _exc: Exception) -> JSONResponse:
        payload = ErrorResponse(
            error=ErrorDetail(code="internal_error", message="internal service error")
        )
        return JSONResponse(status_code=500, content=payload.model_dump(mode="json"))

    @app.get("/api/v1/health", response_model=HealthResponse, tags=["health"])
    def health() -> HealthResponse:
        return HealthResponse(storage_schema_version=registry.diagnostics().schema_version)

    @app.post(
        "/api/v1/suites",
        response_model=SuiteResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["suites"],
    )
    def create_suite(suite: EvaluationSuite) -> SuiteResponse:
        stored = service.create_suite(suite)
        return SuiteResponse(
            suite_id=stored.suite_id,
            created_at=stored.created_at,
            suite=EvaluationSuite.model_validate(stored.suite),
        )

    @app.get("/api/v1/suites", response_model=SuitePage, tags=["suites"])
    def list_suites(
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
    ) -> SuitePage:
        items = tuple(
            SuiteResponse(
                suite_id=stored.suite_id,
                created_at=stored.created_at,
                suite=EvaluationSuite.model_validate(stored.suite),
            )
            for stored in service.list_suites(limit=limit, offset=offset)
        )
        return SuitePage(
            items=items,
            limit=limit,
            offset=offset,
            total=registry.count_suites(),
        )

    @app.post(
        "/api/v1/evaluations",
        response_model=EvaluationResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["evaluations"],
    )
    def create_evaluation(request: EvaluationRequest) -> EvaluationResponse:
        try:
            stored = service.evaluate(
                suite_id=request.suite_id,
                candidate_outputs=request.candidate_outputs,
            )
        except SuiteNotFoundError as exc:
            raise APIError(
                status_code=404,
                code="suite_not_found",
                message="evaluation suite was not found",
            ) from exc
        except ValueError as exc:
            raise APIError(
                status_code=400,
                code="invalid_evaluation",
                message="evaluation input is invalid",
            ) from exc
        return EvaluationResponse(
            run_id=stored.run_id,
            report=EvaluationReportResponse.model_validate(stored.report),
        )

    @app.get("/api/v1/runs", response_model=RunPage, tags=["runs"])
    def list_runs(
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
    ) -> RunPage:
        return RunPage(
            items=tuple(
                _run_summary_response(summary)
                for summary in service.list_runs(limit=limit, offset=offset)
            ),
            limit=limit,
            offset=offset,
            total=registry.count_runs(),
        )

    @app.get("/api/v1/runs/{run_id}", response_model=RunResponse, tags=["runs"])
    def get_run(run_id: Sha256Digest) -> RunResponse:
        stored = service.get_run(run_id)
        if stored is None:
            raise APIError(
                status_code=404,
                code="run_not_found",
                message="evaluation run was not found",
            )
        return _run_response(stored)

    @app.get(
        "/api/v1/reports/{run_id}",
        response_model=EvaluationReportResponse,
        tags=["reports"],
    )
    def get_report(run_id: Sha256Digest) -> EvaluationReportResponse:
        stored = service.get_run(run_id)
        if stored is None:
            raise APIError(
                status_code=404,
                code="report_not_found",
                message="evaluation report was not found",
            )
        return EvaluationReportResponse.model_validate(stored.report)

    return app
