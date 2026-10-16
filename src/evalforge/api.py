"""Versioned HTTP API for deterministic EvalForge services."""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Query, Request, Security, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from evalforge.auth import (
    TRUSTED_LOCAL_TENANT_ID,
    AccessCredential,
    AuthenticatedPrincipal,
    AuthenticationError,
    BearerAuthenticator,
)
from evalforge.contracts import (
    CandidateOutput,
    EvaluationSuite,
    OutputText,
    PersistedEvaluationReport,
    Sha256Digest,
)
from evalforge.registry import RunSummary, SQLiteRunRegistry, StoredRun
from evalforge.service import (
    EvalForgeService,
    IncompatibleRunComparisonError,
    RunComparisonNotFoundError,
    StoredRunComparison,
    SuiteNotFoundError,
)


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

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers


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


class EvaluationReportResponse(PersistedEvaluationReport):
    """Core report wrapped with reproducibility identifiers."""


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


def _dashboard_html(runs: tuple[RunSummary, ...]) -> str:
    def comparison_link(index: int, run: RunSummary) -> str:
        previous = next(
            (
                older
                for older in runs[index + 1 :]
                if older.suite_sha256 == run.suite_sha256
                and older.dataset_manifest_sha256 == run.dataset_manifest_sha256
            ),
            None,
        )
        if previous is None:
            return ""
        return (
            '<br><a href="/dashboard/compare?baseline_run_id='
            f'{previous.run_id}&amp;candidate_run_id={run.run_id}">compare with previous</a>'
        )

    rows = "".join(
        (
            "<tr>"
            f'<td><span class="status {("pass" if run.release_ready else "fail")}">'
            f"{'PASS' if run.release_ready else 'FAIL'}</span></td>"
            f"<td>{escape(run.suite_name)}</td>"
            f"<td><code>{run.run_id}</code></td>"
            f"<td>{escape(run.created_at)}</td>"
            f'<td><a href="/api/v1/reports/{run.run_id}">report</a>'
            f"{comparison_link(index, run)}</td>"
            "</tr>"
        )
        for index, run in enumerate(runs)
    )
    if not rows:
        rows = '<tr><td colspan="5">No persisted runs yet.</td></tr>'
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>EvalForge run dashboard</title>
  <style>
    body {{ font: 16px/1.5 system-ui, sans-serif; margin: 2rem; color: #172033; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border-bottom: 1px solid #d8deea; padding: .65rem; text-align: left; }}
    code {{ font-size: .8rem; overflow-wrap: anywhere; }}
    .status {{ font-weight: 700; }} .pass {{ color: #087a3e; }} .fail {{ color: #b42318; }}
  </style>
</head>
<body>
  <h1>EvalForge run dashboard</h1>
  <p>Latest persisted release-gate outcomes. Candidate text is not rendered.</p>
  <table>
    <thead>
      <tr><th>Gate</th><th>Suite</th><th>Run ID</th><th>Created</th><th>Evidence</th></tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
</body>
</html>
"""


def _percent(value: float) -> str:
    return f"{value * 100:.2f}%"


def _signed_percent(value: float) -> str:
    return f"{value * 100:+.2f}%"


def _comparison_dashboard_html(comparison: StoredRunComparison) -> str:
    scores = comparison.scores
    metric_rows = "".join(
        (
            "<tr>"
            f"<td>{escape(delta.metric)}</td>"
            f"<td>{_percent(delta.baseline_score)}</td>"
            f"<td>{_percent(delta.candidate_score)}</td>"
            f"<td>{_signed_percent(delta.absolute_delta)}</td>"
            f'<td class="{escape(delta.status)}">{escape(delta.status)}</td>'
            "</tr>"
        )
        for delta in scores.metric_deltas
    )
    case_rows = "".join(
        (
            "<tr>"
            f"<td>{escape(delta.case_id)}</td>"
            f"<td>{escape(delta.metric)}</td>"
            f"<td>{_percent(delta.baseline_score)}</td>"
            f"<td>{_percent(delta.candidate_score)}</td>"
            f"<td>{_signed_percent(delta.absolute_delta)}</td>"
            f'<td class="{escape(delta.status)}">{escape(delta.status)}</td>'
            "</tr>"
        )
        for delta in scores.case_deltas[:100]
    )
    category_rows = "".join(
        (
            "<tr>"
            f"<td>{escape(delta.name)}</td>"
            f"<td>{_percent(delta.baseline_score)}</td>"
            f"<td>{_percent(delta.candidate_score)}</td>"
            f"<td>{_signed_percent(delta.absolute_delta)}</td>"
            f'<td class="{escape(delta.status)}">{escape(delta.status)}</td>'
            "</tr>"
        )
        for delta in scores.category_deltas[:100]
    )
    tag_rows = "".join(
        (
            "<tr>"
            f"<td>{escape(delta.name)}</td>"
            f"<td>{_percent(delta.baseline_score)}</td>"
            f"<td>{_percent(delta.candidate_score)}</td>"
            f"<td>{_signed_percent(delta.absolute_delta)}</td>"
            f'<td class="{escape(delta.status)}">{escape(delta.status)}</td>'
            "</tr>"
        )
        for delta in scores.tag_deltas[:100]
    )
    case_note = (
        f"Showing 100 of {len(scores.case_deltas)} cases."
        if len(scores.case_deltas) > 100
        else f"Showing all {len(scores.case_deltas)} cases."
    )
    slice_note = (
        "Category and tag tables show at most 100 rows each."
        if len(scores.category_deltas) > 100 or len(scores.tag_deltas) > 100
        else "All category and tag slices are shown."
    )
    suite_description = (
        "<p>Descriptive candidate-minus-baseline analytics for "
        f"<strong>{escape(comparison.suite_name)}</strong>. "
        "Candidate and expected text are not rendered.</p>"
    )
    baseline_summary = (
        f"<section><h2>Baseline</h2><code>{escape(comparison.baseline.run_id)}</code>"
        f"<p>Weighted pass rate: {_percent(comparison.baseline_weighted_pass_rate)}</p>"
        f'<p><a href="/api/v1/reports/{escape(comparison.baseline.run_id)}">'
        "Report</a></p></section>"
    )
    candidate_summary = (
        f"<section><h2>Candidate</h2><code>{escape(comparison.candidate.run_id)}</code>"
        f"<p>Weighted pass rate: {_percent(comparison.candidate_weighted_pass_rate)}</p>"
        f'<p><a href="/api/v1/reports/{escape(comparison.candidate.run_id)}">'
        "Report</a></p></section>"
    )
    delta_status = (
        "regression"
        if scores.weighted_pass_rate_absolute_delta < 0
        else "improvement"
        if scores.weighted_pass_rate_absolute_delta > 0
        else "unchanged"
    )
    delta_summary = (
        f'<section><h2>Delta</h2><p class="{delta_status}">Weighted pass rate: '
        f"{_signed_percent(scores.weighted_pass_rate_absolute_delta)}</p>"
        f"<p>{scores.ablation_summary.improved_cases} improved, "
        f"{scores.ablation_summary.regressed_cases} regressed, "
        f"{scores.ablation_summary.unchanged_cases} unchanged</p></section>"
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>EvalForge run comparison</title>
  <style>
    body {{ font: 16px/1.5 system-ui, sans-serif; margin: 2rem; color: #172033; }}
    table {{ border-collapse: collapse; width: 100%; margin-bottom: 2rem; }}
    th, td {{ border-bottom: 1px solid #d8deea; padding: .65rem; text-align: left; }}
    code {{ font-size: .8rem; overflow-wrap: anywhere; }}
    .summary {{ display: flex; gap: 2rem; flex-wrap: wrap; }}
    .summary section {{ border: 1px solid #d8deea; border-radius: .5rem; padding: 1rem; }}
    .improvement {{ color: #087a3e; }} .regression {{ color: #b42318; }}
    .unchanged {{ color: #475467; }}
  </style>
</head>
<body>
  <p><a href="/dashboard">Back to runs</a></p>
  <h1>EvalForge run comparison</h1>
  {suite_description}
  <div class="summary">
    {baseline_summary}
    {candidate_summary}
    {delta_summary}
  </div>
  <h2>Metric evidence</h2>
  <table><thead><tr><th>Metric</th><th>Baseline</th><th>Candidate</th><th>Delta</th><th>Status</th></tr></thead><tbody>{metric_rows}</tbody></table>
  <h2>Case evidence</h2><p>{escape(case_note)}</p>
  <table><thead><tr><th>Case</th><th>Metric</th><th>Baseline</th><th>Candidate</th><th>Delta</th><th>Status</th></tr></thead><tbody>{case_rows}</tbody></table>
  <h2>Category slices</h2><p>{escape(slice_note)}</p>
  <table><thead><tr><th>Slice</th><th>Baseline</th><th>Candidate</th><th>Delta</th><th>Status</th></tr></thead><tbody>{category_rows}</tbody></table>
  <h2>Tag slices</h2>
  <table><thead><tr><th>Slice</th><th>Baseline</th><th>Candidate</th><th>Delta</th><th>Status</th></tr></thead><tbody>{tag_rows}</tbody></table>
</body>
</html>
"""


def _secure_dashboard_response(content: str) -> HTMLResponse:
    response = HTMLResponse(content)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'none'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def create_app(
    database_path: Path,
    *,
    access_credentials: tuple[AccessCredential, ...] | None = None,
    access_authenticator: BearerAuthenticator | None = None,
) -> FastAPI:
    """Create an isolated API with optional fail-closed bearer access control."""

    registry = SQLiteRunRegistry(database_path)
    service = EvalForgeService(registry)
    authenticator = access_authenticator
    if authenticator is None and access_credentials is not None:
        authenticator = BearerAuthenticator(access_credentials)
    bearer_scheme = HTTPBearer(auto_error=False)
    bearer_dependency = Security(bearer_scheme)
    app = FastAPI(title="EvalForge API", version="1.0.0")
    app.state.service = service

    def bearer_authenticate(
        credentials: HTTPAuthorizationCredentials | None = bearer_dependency,
    ) -> AuthenticatedPrincipal:
        if authenticator is None:  # pragma: no cover - only selected in authenticated mode
            raise RuntimeError("bearer authenticator is not configured")
        principal: AuthenticatedPrincipal | None = None
        if credentials is not None:
            scheme = credentials.scheme
            token = credentials.credentials
            del credentials
            try:
                principal = authenticator.authenticate(f"{scheme} {token}")
            except AuthenticationError:
                pass
            finally:
                del scheme
                del token
        if principal is None:
            raise APIError(
                status_code=401,
                code="authentication_required",
                message="authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return principal

    def trusted_local_authenticate() -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(tenant_id=TRUSTED_LOCAL_TENANT_ID, role="admin")

    authenticate = trusted_local_authenticate if authenticator is None else bearer_authenticate

    authenticate_dependency = Depends(authenticate)

    def require_read(
        principal: AuthenticatedPrincipal = authenticate_dependency,
    ) -> AuthenticatedPrincipal:
        return principal

    def require_write(
        principal: AuthenticatedPrincipal = authenticate_dependency,
    ) -> AuthenticatedPrincipal:
        if principal.role == "reader":
            raise APIError(
                status_code=403,
                code="permission_denied",
                message="permission denied",
            )
        return principal

    read_dependency = Depends(require_read)
    write_dependency = Depends(require_write)

    @app.exception_handler(APIError)
    async def handle_api_error(_request: Request, exc: APIError) -> JSONResponse:
        payload = ErrorResponse(error=ErrorDetail(code=exc.code, message=exc.message))
        return JSONResponse(
            status_code=exc.status_code,
            content=payload.model_dump(mode="json"),
            headers=exc.headers,
        )

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

    @app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
    def dashboard(
        principal: AuthenticatedPrincipal = read_dependency,
    ) -> HTMLResponse:
        return _secure_dashboard_response(
            _dashboard_html(service.list_runs(tenant_id=principal.tenant_id, limit=100, offset=0))
        )

    @app.get("/dashboard/compare", response_class=HTMLResponse, include_in_schema=False)
    def compare_dashboard(
        baseline_run_id: Annotated[Sha256Digest, Query()],
        candidate_run_id: Annotated[Sha256Digest, Query()],
        principal: AuthenticatedPrincipal = read_dependency,
    ) -> HTMLResponse:
        try:
            comparison = service.compare_runs(
                tenant_id=principal.tenant_id,
                baseline_run_id=baseline_run_id,
                candidate_run_id=candidate_run_id,
            )
        except RunComparisonNotFoundError as exc:
            raise APIError(
                status_code=404,
                code="run_comparison_not_found",
                message="run comparison was not found",
            ) from exc
        except IncompatibleRunComparisonError as exc:
            raise APIError(
                status_code=409,
                code="runs_not_comparable",
                message="evaluation runs are not comparable",
            ) from exc
        return _secure_dashboard_response(_comparison_dashboard_html(comparison))

    @app.post(
        "/api/v1/suites",
        response_model=SuiteResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["suites"],
    )
    def create_suite(
        suite: EvaluationSuite,
        principal: AuthenticatedPrincipal = write_dependency,
    ) -> SuiteResponse:
        stored = service.create_suite(suite, tenant_id=principal.tenant_id)
        return SuiteResponse(
            suite_id=stored.suite_id,
            created_at=stored.created_at,
            suite=EvaluationSuite.model_validate(stored.suite),
        )

    @app.get("/api/v1/suites", response_model=SuitePage, tags=["suites"])
    def list_suites(
        principal: AuthenticatedPrincipal = read_dependency,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
    ) -> SuitePage:
        items = tuple(
            SuiteResponse(
                suite_id=stored.suite_id,
                created_at=stored.created_at,
                suite=EvaluationSuite.model_validate(stored.suite),
            )
            for stored in service.list_suites(
                tenant_id=principal.tenant_id,
                limit=limit,
                offset=offset,
            )
        )
        return SuitePage(
            items=items,
            limit=limit,
            offset=offset,
            total=service.count_suites(tenant_id=principal.tenant_id),
        )

    @app.post(
        "/api/v1/evaluations",
        response_model=EvaluationResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["evaluations"],
    )
    def create_evaluation(
        request: EvaluationRequest,
        principal: AuthenticatedPrincipal = write_dependency,
    ) -> EvaluationResponse:
        try:
            stored = service.evaluate(
                tenant_id=principal.tenant_id,
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
        principal: AuthenticatedPrincipal = read_dependency,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
    ) -> RunPage:
        return RunPage(
            items=tuple(
                _run_summary_response(summary)
                for summary in service.list_runs(
                    tenant_id=principal.tenant_id,
                    limit=limit,
                    offset=offset,
                )
            ),
            limit=limit,
            offset=offset,
            total=registry.count_runs(tenant_id=principal.tenant_id),
        )

    @app.get("/api/v1/runs/{run_id}", response_model=RunResponse, tags=["runs"])
    def get_run(
        run_id: Sha256Digest,
        principal: AuthenticatedPrincipal = read_dependency,
    ) -> RunResponse:
        stored = service.get_run(tenant_id=principal.tenant_id, run_id=run_id)
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
    def get_report(
        run_id: Sha256Digest,
        principal: AuthenticatedPrincipal = read_dependency,
    ) -> EvaluationReportResponse:
        stored = service.get_run(tenant_id=principal.tenant_id, run_id=run_id)
        if stored is None:
            raise APIError(
                status_code=404,
                code="report_not_found",
                message="evaluation report was not found",
            )
        return EvaluationReportResponse.model_validate(stored.report)

    return app
