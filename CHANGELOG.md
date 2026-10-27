# Changelog

All notable changes to EvalForge are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Opt-in bearer authentication with strict bounded environment configuration, redaction-safe failures, tenant-bound principals, and reader/editor/admin API authorization.
- Immutable tenant-to-suite memberships with tenant-filtered registration, listing, pagination, totals, and evaluation lookup.
- Immutable tenant-to-run memberships with tenant-filtered run pages, report retrieval, and dashboard rows.
- Tenant-scoped persisted-run comparisons with candidate-minus-baseline metric, case, category, tag, and ablation evidence.
- Tenant-scoped recorded unique-run trends with validated weighted pass rates, deterministic chronology, and adjacent pairwise evidence links.
- Filtered metric, category, and tag trends with weighted score projections, bounded selectors, and fail-closed unknown or ambiguous filter handling.
- Tenant-scoped dedicated case evidence with threshold provenance, dimensions, optional resource measurements, and private-text omission.
- Durable tenant-scoped asynchronous evaluation submission with hashed idempotency keys, queued status polling, and no inline run creation.
- Atomic oldest-first worker claims with expiring leases, capability-token digest storage, expiry recovery, and terminal three-attempt exhaustion.

### Security

- Protected suite, evaluation, asynchronous-job, run, report, and dashboard routes when access control is configured; health remains unauthenticated. Pairwise comparisons, filtered trends, and case detail require tenant-visible anchors, revalidate stored evidence, render bounded redacted projections, reject ambiguous trend filters, and treat foreign run or job identifiers like absent identifiers. Job idempotency keys and worker capability tokens are stored only as SHA-256 digests; lease-state triggers reject partial running states. Legacy suites and runs migrate only to trusted-local ownership.

## [0.2.0] - 2026-10-08

### Added

- Governed precomputed-embedding evaluation with cosine similarity, Euclidean distance, immutable model provenance approval, content-redacted vector digests, and deterministic CLI evidence.
- Task-specific code evaluation over governed precomputed harness outcomes with exact case accounting, content-addressed candidate identity, and no candidate code execution.
- Ranked retrieval evaluation with governed retriever/corpus/index provenance, exact precision@k, recall@k, and MRR@k gates, redacted case evidence, and deterministic CLI artifacts.
- Governed structured-output evaluation with an approved bounded Draft 2020-12 schema catalog, exact rational pass-rate gates, value-redacted diagnostics, and deterministic atomic CLI artifacts.
- Deterministic dataset mutation with source-bound adversarial plans, reproducible prompt transformations, content-addressed campaign artifacts, and tamper-resistant generation evidence.
- Governed cross-dataset scorecards and long-horizon trend analysis with exact ordered dataset identity, weighted scores, complete-history drawdowns, and atomic content-addressed CLI artifacts.

### Security

- Raised the development pytest floor to a release without the current `PYSEC-2026-1845` advisory.

## [0.1.0] - 2026-09-27

### Added

- Versioned evaluation suites with exact, contains, regex, rubric, grounding, tool-call, trajectory, leakage, latency, cost, and stability evidence, plus schema-constrained rubric-judge responses.
- Deterministic content-addressed provenance, dataset lineage, candidate/baseline comparisons, weighted slices, and fail-closed release policies.
- Typed OpenAI-compatible provider adapter with bounded requests and secret-safe failures.
- Human-review and judge-reliability workflows for structured rubric evaluation.
- SQLite-backed suite/run registry and versioned FastAPI endpoints for suites, evaluations, run history, and reports.
- Read-only local run dashboard that omits candidate text and escapes persisted metadata.
- JUnit and SARIF exports plus GitHub Actions examples for pull-request release gates.
- Non-root container image, loopback-only Compose example, deployment health smoke, and credential-free synthetic end-to-end demo.
- Threat model, contribution workflow, extension guide, release acceptance matrix, and automated Markdown link checks.

### Security

- Bounded and duplicate-key-safe JSON parsing, strict versioned contracts, finite numeric validation, bounded regular expressions, and fail-closed resource aggregation.
- Sensitive-data findings and public summaries avoid copying matched values or candidate output.
- Provider credentials are environment-only and transport failures are normalized to redacted errors.

[Unreleased]: https://github.com/navaneeth9908/evalforge/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/navaneeth9908/evalforge/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/navaneeth9908/evalforge/releases/tag/v0.1.0
