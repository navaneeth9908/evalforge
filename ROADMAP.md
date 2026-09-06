# EvalForge Roadmap

EvalForge is being built as a production-oriented evaluation and release-gating platform for LLM and AI-agent systems. Each completed capability must include tests, documentation, and reproducible evidence.

## Foundation

- [x] Versioned deterministic suite contract
- [x] Normalized exact-match evaluator
- [x] Case-level evidence and pass-rate release gate
- [x] Offline CLI with JSON reports and meaningful exit status
- [x] Dataset provenance and content-addressed fingerprints
- [x] Metric registry with exact, contains, and bounded regular-expression checks
- [x] Declarative metric and threshold policies
- [x] Baseline-versus-candidate regression analysis
- [x] Deterministic two-variant model matrix and ablation summaries

## Evaluation depth

- [x] Weighted cases and severity-aware release decisions
- [x] Latency and cost budgets
- [ ] Token budgets
- [x] Repeated-run stability and variance analysis
- [ ] Tool-call trace contract and argument validation
- [ ] Agent-trajectory ordering and state-transition checks
- [ ] Retrieval citation, grounding, and attribution metrics
- [ ] Prompt-injection and policy-adherence suites
- [ ] PII and secret-leakage detectors

## Model and human judgment

- [ ] Provider-neutral candidate adapter contract with deterministic fakes
- [ ] OpenAI-compatible candidate adapter with explicit runtime boundaries
- [ ] Structured rubric and model-based judge
- [ ] Human-review queue with portable decisions

## Platform

- [ ] SQLite run registry and reproducible run manifests
- [ ] FastAPI endpoints for suites, runs, and reports
- [ ] Bounded asynchronous execution, retries, and deadlines
- [ ] OpenTelemetry traces and evaluation metrics
- [ ] Analyst dashboard for runs, regressions, and evidence

## Delivery and governance

- [ ] GitHub Checks and JUnit release-gate output
- [ ] SARIF safety findings
- [ ] Dataset versioning and artifact-integrity verification
- [ ] Multi-candidate matrix and higher-order ablation comparisons
- [ ] Container image, health checks, and deployment guide
- [ ] Threat model, end-to-end demo, acceptance suite, and release documentation

## Definition of done

A capability is complete only when its behavior is tested, local quality gates pass, public documentation matches implementation, generated artifacts are excluded from version control, the exact staged snapshot receives independent review, and GitHub Actions passes after publication.
