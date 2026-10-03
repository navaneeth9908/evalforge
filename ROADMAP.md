# EvalForge roadmap

EvalForge is a deterministic, vendor-neutral evaluation and release-gating toolkit for LLM and agent systems. This roadmap separates the verified `v0.1.0` release contract from later control-plane ambitions. Checked items have implementation and automated evidence; unchecked items are not release claims.

## Product principles

- Deterministic offline evaluation is the default.
- Networked generation is an explicit adapter boundary.
- Missing or ambiguous evidence fails closed.
- Content-addressed provenance binds decisions without treating hashes as confidentiality.
- Reports are automation-friendly and privacy-conscious.
- Human judgment is supported, not disguised as mathematical certainty.

## v0.1.0 release scope

### M0 — contracts and CI

- [x] Typed, strict, versioned suite and policy contracts
- [x] Locked `uv` environment, branch-covered tests, Ruff, strict Mypy, and package build
- [x] Immutable GitHub Action pins with least-privilege workflow permissions
- [x] JUnit and SARIF release evidence

### M1 — deterministic offline evaluation

- [x] Exact, contains, and bounded regex metrics
- [x] Case, metric, suite, severity, category, and tag policy dimensions
- [x] Weighted pass rates and fail-closed release decisions
- [x] Canonical input digests, deterministic run IDs, and dataset lineage

### M2 — regression and reliability

- [x] Baseline-versus-candidate comparison gates
- [x] Weighted slices and model/ablation summaries
- [x] Integer-safe latency and cost budgets
- [x] Repeated-run variance and flaky-case evidence

### M3 — agentic and safety evaluation

- [x] Typed tool-call matching with redacted value digests
- [x] Ordered trajectory and termination checks
- [x] Citation and lexical grounding evidence
- [x] Sensitive-data leakage scanning with redacted findings
- [x] Structured rubric judging, calibration, and portable human review

### M4 — local operations and release hardening

- [x] Transactional SQLite suite/run registry
- [x] Versioned FastAPI suite, evaluation, run, report, and health routes
- [x] Basic read-only local run dashboard
- [x] Non-root container, loopback Compose default, and deployment smoke
- [x] Credential-free end-to-end demo spanning fake generation, metrics, persistence, API, dashboard, JUnit, and SARIF
- [x] Threat model, contributor workflow, extension guide, changelog, acceptance matrix, and automated link checks

The exact command and test mapping are in [docs/acceptance.md](docs/acceptance.md).

## Post-v0.1.0 roadmap

These are intentionally open and are not prerequisites that the `v0.1.0` release pretends to satisfy.

### M5 — richer evaluation catalog

- [x] Semantic-similarity and embedding metrics with governed model/version provenance
- [x] Task-specific code evaluator over governed precomputed harness evidence
- [x] Retrieval evaluator
- [ ] Structured-output evaluator beyond current contracts
- [ ] Dataset mutation and adversarial test-case generation
- [ ] Cross-dataset scorecards and long-horizon trend analysis

### M6 — production control plane

- [ ] Authentication, authorization, and tenant isolation
- [ ] Full analyst dashboard for run comparison, regressions, slices, evidence, and trend drill-down
- [ ] Hosted API workers, queues, retries, and distributed execution
- [ ] Postgres/object-storage adapters and retention controls
- [ ] OpenTelemetry traces and service-level operational metrics
- [ ] Signed release artifacts, SBOM publication, and provenance attestations

### M7 — governance and team workflows

- [ ] Role-based policy approvals and change history
- [ ] Reviewer identity federation and adjudication UI
- [ ] Organization-level reusable policy packs
- [ ] Scheduled production sampling with explicit privacy and consent controls

## Release completion policy

A green aggregate score alone does not close a roadmap item. Completion requires implementation, adversarial tests, public-contract documentation, and successful release gates on the exact commit. Environment-dependent checks such as a local Docker runtime are reported separately when unavailable. Release tags are created only after `main` is pushed, attribution is verified, and GitHub Actions is green.
