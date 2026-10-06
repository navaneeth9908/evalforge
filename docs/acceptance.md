# v0.1.0 acceptance evidence

This page maps the release contract to executable evidence. A checked item means the behavior is implemented and covered by the cited automated test or command; it does not imply completion of future roadmap scope.

## Product acceptance

| Capability | Evidence | Status |
|---|---|---|
| Versioned evaluation suite and fail-closed policy | `tests/test_engine.py`, `tests/test_release_policy.py` | Verified |
| Deterministic provider fake and typed generation result | `evalforge.demo.SyntheticFakeAdapter`, `tests/test_release_acceptance.py` | Verified |
| Exact/contains metrics and latency/cost evidence | Synthetic demo report assertions in `tests/test_release_acceptance.py` | Verified |
| Content-addressed run identity and SQLite persistence | `tests/test_provenance.py`, `tests/test_registry.py`, synthetic demo database | Verified |
| Versioned FastAPI suite/evaluation/run/report routes | `tests/test_api.py` and the demo's direct ASGI requests | Verified |
| Read-only run dashboard with escaped metadata | `tests/test_api.py::test_dashboard_summarizes_persisted_runs_without_exposing_candidate_text` | Verified |
| JUnit and SARIF CI exports | `tests/test_ci_reporting.py` and synthetic demo artifact assertions | Verified |
| Container entrypoint, health check, loopback Compose default | `tests/test_deployment.py`; local Docker smoke when an engine is available | Verified by contract tests; runtime is environment-dependent |
| Documentation links and version consistency | `tests/test_documentation.py` | Verified |
| Governed embedding similarity and provenance | `tests/test_embedding_similarity.py`, `tests/test_cli_embedding_similarity.py` | Verified |
| Governed task-specific code harness evaluation | `tests/test_code_evaluation.py`, `tests/test_cli_code_evaluation.py` | Verified |
| Governed ranked retrieval evaluation | `tests/test_retrieval_evaluation.py`, `tests/test_cli_retrieval.py` | Verified |
| Governed bounded structured-output evaluation | `tests/test_structured_output_evaluation.py`, `tests/test_cli_structured_output.py` | Verified |
| Governed deterministic dataset mutation | `tests/test_dataset_mutations.py`, `tests/test_cli_dataset_mutations.py` | Verified |

Run the complete credential-free tracer bullet:

```bash
uv run --frozen --group dev evalforge demo --output-directory reports/demo
```

It generates `evaluation.json`, `evaluation.junit.xml`, `safety.sarif.json`, `dashboard.html`, and a local SQLite database under the ignored output directory. The command returns compact JSON evidence and exits non-zero if the API path or release gate fails.

## Roadmap accounting

The release includes the deterministic core and the implemented local operations slice; it does not relabel future production capabilities as complete.

| Roadmap commitment | v0.1.0 disposition |
|---|---|
| M0 contracts and CI | Complete |
| M1 deterministic offline evaluation | Complete |
| M2 regression, weighted slices, performance, and stability | Complete |
| M3 agentic, grounding, leakage, and judge workflows | Complete |
| M4 local operations and release hardening | Complete |
| M5 governed semantic similarity and embedding metrics | Complete |
| M5 task-specific code harness evaluation | Complete |
| M5 ranked retrieval evaluation | Complete |
| M5 governed structured-output evaluation | Complete |
| M5 dataset mutation and adversarial test-case generation | Complete |
| Remaining M5 cross-dataset scorecards and trends | In progress |
| Basic local run dashboard | Complete |
| Full regression analytics dashboard | Not implemented |
| Authentication and multi-tenancy | Not implemented |
| Production hosted control plane and distributed workers | Not implemented |
| Artifact signing and SBOM publication | Not implemented |

The open items are post-v0.1.0 roadmap work. They are listed as limitations in the [threat model](threat-model.md), not hidden behind a release-readiness claim.

## Release gate

A release candidate is acceptable only when all of the following are true on the exact commit:

1. the lockfile, formatter, linter, strict type checker, full branch-coverage suite, and package build pass;
2. a clean installed-wheel `evalforge demo` smoke passes;
3. API, dashboard, persistence, and deployment smokes pass where the local environment supports them;
4. documentation/link checks pass;
5. the staged diff contains no secrets, private datasets, local databases, generated reports, or unsupported completion claims;
6. an independent staged review has no security or logic blockers;
7. `main` is pushed with the intended author attribution and GitHub Actions is green; and
8. only then may the annotated `v0.1.0` tag and GitHub release be created.

A missing Docker engine is reported separately; it is not silently represented as a passing local container runtime smoke.
