# EvalForge

[![CI](https://github.com/navaneeth9908/evalforge/actions/workflows/ci.yml/badge.svg)](https://github.com/navaneeth9908/evalforge/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Production-grade evaluation evidence and release gates for LLM and AI-agent systems.

EvalForge turns versioned test cases and candidate outputs into an auditable report and a machine-readable release decision. Evaluation remains deterministic and offline by default; an explicit OpenAI-compatible generation command provides bounded provider access when configured. Evaluation fails closed when evidence is missing or ambiguous.

## Why this project

AI systems need more than a few hand-checked prompts before release. Teams need reproducible datasets, explicit thresholds, case-level evidence, regression analysis, safety tests, cost and latency budgets, and CI-compatible decisions. EvalForge is being built as that vendor-neutral control plane rather than as another chatbot or provider-specific demo.

## Implemented capabilities

- Strict, versioned Pydantic contracts for evaluation suites
- Normalized exact, substring, and bounded regular-expression metrics
- Declarative suite, metric, and case score thresholds with explicit precedence
- Positive finite case weights with weighted aggregate release decisions
- Low, medium, high, and critical severity labels with critical-failure blocking by default
- Deterministic weighted category and tag slice summaries
- Integer-safe per-case latency and cost evidence with total, ceiling-average, and nearest-rank percentile budgets
- Versioned repeated-observation inputs with weighted pass-rate mean, minimum, maximum, and population variance
- Configurable variance and flaky-case gates with ordered per-run and per-case evidence
- Strict request/result tool-call traces with paired call IDs and bounded JSON values
- Allowlist enforcement plus missing, extra, repeated, argument, and result checks
- Ordered agent-trajectory policies with state continuity, termination, and loop checks
- Explainable trajectory findings and deterministic sequence/termination metrics
- Strict, versioned retrieved-document, answer-claim, and citation contracts
- Citation validity, precision/recall, context utilization, and lexical grounding metrics
- Content-redacted RAG evidence that retains document and claim IDs only
- Governed precomputed-embedding evaluation with cosine similarity and Euclidean distance
- Exact model provider, revision, artifact digest, dimensionality, and license provenance approval
- Task-specific code gates over approved precomputed harness and runtime provenance
- Exact required-case accounting with redacted pass/fail/error/timeout/skipped evidence
- Governed ranked retrieval evaluation with approved retriever, corpus, and index provenance
- Exact macro precision@k, recall@k, and MRR@k thresholds with document-ID-free reports
- Governed structured-output evaluation under a bounded Draft 2020-12 JSON Schema profile
- Exact schema-catalog approval, case accounting, rational pass-rate gates, and value-redacted evidence
- Governed deterministic prompt mutation with source-suite approval and explicit adversarial plans
- Prefix, injection-suffix, and character-deletion operators with reproducible generated suites
- Deterministic secret and PII detectors for email, phone, US SSN, payment-card, API-key, and private-key patterns
- Digest allowlists, category controls, severity overrides, and configurable blocking severities
- Redacted leakage findings for candidate outputs and nested evaluation-report values
- Deterministic JUnit case/metric reports and redacted SARIF safety findings
- Reusable least-privilege GitHub Actions release gates with immutable action pins
- Redaction-safe tool evidence using canonical value digests instead of raw arguments/results
- Deterministic exact-call precision, recall, and component match counts
- Provider-neutral generation contracts and an OpenAI-compatible adapter with independent connect, read, and overall deadlines
- Bounded exponential retry for transient failures with injectable timing/randomness and sanitized per-attempt evidence
- One-mebibyte provider response reads, bounded generated text, strict response parsing, and secret-safe failures
- Versioned rubric, dimension, judge-score, and report contracts with strict numeric thresholds
- Provider-neutral structured-judge requests carrying a dimension-specific JSON Schema
- Weighted dimension and aggregate threshold evidence with fail-closed release decisions
- Canonical JSON prompt framing that separates trusted judge instructions from untrusted task and candidate text
- Repeated, label-blinded judge calibration with agreement and score-drift release gates
- Alternating-order position probes, matched verbosity probes, and synthetic-label calibration error
- Deterministic human-review selection for low-confidence and judge-disagreement evidence
- Privacy-aware JSONL queues, portable reviewer decisions, and conflict/adjudication summaries
- Non-empty and unique case-ID validation
- Exact candidate-output accounting with no silent omissions or extras
- Finite minimum pass-rate validation
- Ordered case-level evidence containing expected and actual outputs
- Pass/fail release decisions with machine-readable aggregate failure reasons
- Effective threshold and precedence source recorded for every case
- Versioned baseline-versus-candidate comparison policies with absolute and relative budgets
- Ordered case and metric deltas, two-variant model matrices, and ablation summaries
- JSON report output with canonical suite and candidate SHA-256 digests
- Deterministic run IDs bound to content-addressed inputs and versioned evaluator semantics
- Strict, versioned dataset manifests with lineage and suite-integrity validation
- Transactional SQLite run registry with immutable, content-addressed suite and report evidence
- Versioned FastAPI endpoints for suite creation, evaluation, run history, and report retrieval
- Read-only local run dashboard with escaped metadata and no candidate-text rendering
- Credential-free synthetic end-to-end demo spanning a provider fake, metrics, persistence, API, dashboard, JUnit, and SARIF
- Non-root container image, loopback-only Compose deployment, and bounded health smoke
- Bounded JSON decoding with duplicate-key, Unicode, size, depth, and string limits
- Nonzero CLI exit status when a release gate fails
- Friendly validation for malformed candidate-output JSON
- Fully offline example workflow

The release scope and later platform work are tracked in [ROADMAP.md](ROADMAP.md). The local API and basic run dashboard are implemented; hosted multi-tenant operations, authentication, and a full regression analytics dashboard remain future work and are not presented as complete.

## Quick start

Prerequisites: Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/navaneeth9908/evalforge.git
cd evalforge
uv sync --frozen --group dev

# Run the complete credential-free product tracer bullet.
uv run evalforge demo --output-directory reports/demo

uv run evalforge evaluate examples/suite.json examples/outputs.json \
  --dataset-manifest examples/dataset-manifest.json \
  --report-path reports/example.json \
  --junit-path reports/evaluation.junit.xml

uv run evalforge compare examples/suite.json examples/baseline-outputs.json \
  examples/outputs.json --comparison-policy examples/comparison-policy.json \
  --baseline-label production --candidate-label change-42 \
  --report-path reports/comparison.json

uv run evalforge stability examples/suite.json examples/repeated-observations.json \
  --stability-policy examples/stability-policy.json \
  --report-path reports/stability.json

uv run evalforge tool-trace examples/tool-trace-expectation.json \
  examples/tool-trace.json --report-path reports/tool-trace.json

uv run evalforge trajectory examples/trajectory-policy.json \
  examples/trajectory.json --report-path reports/trajectory.json

uv run evalforge grounding examples/grounding.json \
  --report-path reports/grounding.json

uv run evalforge embedding-similarity examples/embedding-evaluation.json \
  examples/embedding-policy.json --report-path reports/embedding-similarity.json

uv run evalforge code-harness examples/code-harness-evidence.json \
  examples/code-harness-policy.json --report-path reports/code-harness.json

uv run evalforge retrieval examples/retrieval-evaluation.json \
  examples/retrieval-policy.json --report-path reports/retrieval.json

uv run evalforge structured-output examples/structured-output-evaluation.json \
  examples/structured-output-policy.json --report-path reports/structured-output.json

uv run evalforge mutate-dataset examples/suite.json examples/mutation-plan.json \
  --artifact-path reports/mutations.json

uv run evalforge scan-leakage examples/leakage-outputs.json \
  --policy examples/leakage-policy.json \
  --report-path reports/leakage.json \
  --sarif-path reports/safety.sarif.json

uv run evalforge scan-report-leakage reports/example.json \
  --policy examples/leakage-policy.json \
  --report-path reports/report-leakage.json
```

Provider generation is opt-in and reads configuration only from the environment:

```bash
EVALFORGE_OPENAI_BASE_URL=https://provider.example/v1 \
EVALFORGE_OPENAI_MODEL=candidate-model \
EVALFORGE_OPENAI_API_KEY=... \
uv run evalforge generate-openai examples/suite.json \
  --outputs-path reports/openai-outputs.json
```

Use `EVALFORGE_OPENAI_CONNECT_TIMEOUT_SECONDS` (default `5`, maximum `120`),
`EVALFORGE_OPENAI_READ_TIMEOUT_SECONDS` (default `30`, maximum `120`), and
`EVALFORGE_OPENAI_OVERALL_TIMEOUT_SECONDS` (default `60`, maximum `600`) for
independent deadlines. Retries are controlled by `EVALFORGE_OPENAI_MAX_ATTEMPTS`
(default `3`, maximum `10`), `EVALFORGE_OPENAI_INITIAL_BACKOFF_SECONDS` (default
`0.25`), `EVALFORGE_OPENAI_MAX_BACKOFF_SECONDS` (default `4`, maximum `60`), and
`EVALFORGE_OPENAI_JITTER_RATIO` (default `0.2`, range `0` to `1`). The legacy
`EVALFORGE_OPENAI_TIMEOUT_SECONDS` keeps single-attempt behavior.

Only timeouts, transport failures, HTTP `408`/`425`, rate limits, and server
errors are retried. Attempt evidence records sanitized outcomes, durations, and
retry delays without retaining prompts, credentials, response bodies, or endpoint
details. Responses are read with a 1 MiB limit and generated text is capped at
65,536 characters.

## Local evaluation API

The application factory keeps storage explicit so local services and tests can use an isolated
SQLite registry. Mount the returned ASGI app in an ASGI server of your choice:

```python
from pathlib import Path

from evalforge.api import create_app

app = create_app(Path("artifacts/evalforge.db"))
```

The versioned `/api/v1` surface provides health, suite creation/listing, synchronous evaluation,
run history, and immutable report retrieval. Pagination and request sizes are bounded. Validation,
missing-resource, and unexpected errors use redaction-safe envelopes that do not echo submitted
prompts, candidate outputs, database details, or exception traces. `/dashboard` provides a basic
read-only local run table with report links; it does not render candidate text or claim to be the
future regression analytics UI.

Run the deployable server and open `http://127.0.0.1:8000/dashboard`:

```bash
# Replace this example with an absolute writable path on your machine.
EVALFORGE_DATABASE_PATH='C:/Users/you/evalforge/artifacts/evalforge.db' \
uv run --frozen uvicorn --factory evalforge.server:create_app_from_environment \
  --host 127.0.0.1 --port 8000
```

The service has no built-in authentication or TLS and is intended for loopback/private CI use in
v0.1.0. See the [deployment guide](docs/deployment.md) and [threat model](docs/threat-model.md)
before changing the bind address.

## Structured rubric judging

Rubric judging is provider-neutral and can be exercised without credentials or network access.
An adapter receives a `JudgeRequest` with a trusted `system_prompt`, an untrusted-data
`user_prompt`, and a dimension-specific Draft 2020-12 JSON Schema. The parser independently
rejects malformed JSON, duplicate keys, unknown fields, coercive numeric types, oversized or
invalid-Unicode responses, and missing, extra, duplicated, or reordered dimension IDs.

```python
from pathlib import Path

from evalforge.contracts import Rubric, RubricEvaluation
from evalforge.judging import JudgeRequest, evaluate_with_rubric


class RecordedJudge:
    def judge(self, request: JudgeRequest) -> str:
        assert request.response_schema["title"] == "evalforge-rubric-score-v1"
        return Path("examples/judge-response.json").read_text(encoding="utf-8")


rubric = Rubric.model_validate_json(Path("examples/rubric.json").read_text(encoding="utf-8"))
evaluation = RubricEvaluation.model_validate_json(
    Path("examples/rubric-evaluation.json").read_text(encoding="utf-8")
)
report = evaluate_with_rubric(rubric, evaluation, RecordedJudge())
assert report.weighted_score == 0.825
assert report.release_ready
```

Every dimension records the judge score, configured weight, weighted contribution, minimum
score, and threshold result. The report also records total weight, weighted aggregate score,
aggregate threshold, aggregate threshold result, overall summary, and final release decision.
A release passes only when the inclusive aggregate threshold and every inclusive dimension
threshold pass.

Candidate and task text are encoded as one canonical JSON object in a dedicated user-message
boundary; trusted rubric instructions stay in the separate system message. This prevents
candidate text from changing prompt structure, but prompt separation is defense in depth—not a
proof against model manipulation. Judge scores can be biased, inconsistent, or confidently
wrong. EvalForge ships no live judge adapter, multi-judge quorum, or hosted reviewer UI.
Operators must provide an adapter that preserves role separation and
enforces the supplied response schema, keep prompts and rationale evidence in controlled
storage, and validate judge quality for their domain.

### Judge reliability and calibration

`measure_judge_reliability` runs a bounded synthetic-label `CalibrationDataset` two to ten times.
Case IDs, synthetic labels, pair metadata, repetition numbers, positions, and generated blind IDs
are withheld from every `JudgeRequest`; only the task and candidate text needed for judging cross
the existing untrusted-data boundary. Cases run in declared order on odd repetitions and reversed
order on even repetitions. This creates deterministic, offline-testable repeated and mirrored-
position observations without pretending that synthetic labels are ground truth.

For case score `s(i,r)`, `N` cases, and `R` repetitions, the report uses these formulas:

- **Agreement:** the fraction of all `N * R * (R - 1) / 2` within-case unordered score pairs for
  which `abs(s(i,r) - s(i,t)) <= agreement_tolerance`.
- **Drift:** `abs(mean_i(s(i,1)) - mean_i(s(i,R)))`, the absolute dataset-mean change from the
  first to the final repetition.
- **Maximum position delta:** the largest per-case absolute difference between its mean score in
  odd (forward-order) repetitions and its mean score in even (reverse-order) repetitions.
- **Verbosity bias:** for each validated concise/verbose pair sharing a prompt and synthetic label,
  the absolute difference between the variants' repeated-score means, averaged across pairs.
- **Calibration MAE:** `mean_i(abs(mean_r(s(i,r)) - synthetic_label(i)))`.

Agreement and drift are inclusive release gates: agreement must be at least `minimum_agreement`,
and drift must be at most `maximum_drift`. Gate failures retain observed and required values.
Position, verbosity, and calibration metrics are diagnostic evidence rather than release gates so
operators can set domain-appropriate policies around them. Reports preserve case IDs for audit;
the judge sees only opaque task/output inputs before each observation is associated with its case.

### Human review and adjudication

`select_review_queue` applies inclusive, versioned low-confidence and judge-score-disagreement
thresholds, then orders selected items deterministically by reason count, confidence,
disagreement, and case ID. Every item retains a frozen `ReviewCandidate` and a canonical SHA-256
digest of that original evidence. The item ID is content-addressed from the evidence and queue
policy, so importing decisions never replaces or edits the evidence that was actually reviewed.

`export_review_queue_jsonl` emits one versioned portable item per line. Its default `redacted`
mode excludes prompt and candidate-output text while retaining case, selection, score, policy,
and evidence-digest fields. `full` mode is an explicit opt-in for controlled reviewer systems.
Digest-only rows can still expose low-entropy values to guessing attacks, so queue files and
original evidence remain controlled artifacts rather than public reports.

`import_review_queue_jsonl` rejects duplicate keys, malformed or coercive contracts, mixed policy
digests, duplicate item/case IDs, non-contiguous ordering, invalid Unicode, and inputs over 1 MiB
or 10,000 rows. Reviewer decisions require versioned identity, source, session, and valid UTC-time
provenance plus the original evidence digest. Decision import rejects unknown or altered evidence,
duplicate decisions, repeated reviewer roles, and adjudication without conflicting reviewer
outcomes. `summarize_adjudication` reports pending work, unanimous agreement, unresolved conflict,
and adjudicator-resolved outcomes without copying prompt or output text into the summary.

Expected console result:

```text
Evaluation report: reports/example.json
Weighted pass rate: 100.00%
Release gate: PASS
Comparison report: reports/comparison.json
Weighted pass-rate delta: +50.00%
Comparison gate: PASS
Stability report: reports/stability.json
Weighted pass-rate mean: 100.00%
Weighted pass-rate population variance: 0.000000
Flaky case rate: 0.00%
Stability gate: PASS
Tool-trace report: reports/tool-trace.json
Exact-call precision: 100.00%
Exact-call recall: 100.00%
Tool-trace gate: PASS
Trajectory report: reports/trajectory.json
Sequence score: 100.00%
Termination score: 100.00%
Trajectory gate: PASS
Grounding report: reports/grounding.json
Citation validity: 100.00%
Citation precision: 100.00%
Citation recall: 100.00%
Context utilization: 50.00%
Lexical grounding: 100.00%
Grounding gate: PASS
Sensitive-data report: reports/leakage.json
Findings: 0
Sensitive-data gate: PASS
```

Generated reports are intentionally ignored by Git. Review `reports/example.json` locally for the aggregate verdict, ordered case-level evidence, canonical input digests, deterministic run ID, and a minimal dataset summary. Full manifest lineage is validated and content-addressed but is not copied into reports because source locations and creator identities can be sensitive. The `--dataset-manifest` option is optional so existing CLI invocations remain valid; suite and candidate digests and a run ID are always emitted.

## Input contract

An evaluation suite declares its schema version, name, release threshold, and cases:

```json
{
  "schema_version": 1,
  "name": "support-policy-smoke",
  "release_policy": {
    "minimum_pass_rate": 1.0,
    "default_case_threshold": 1.0,
    "metric_thresholds": {
      "contains": 1.0
    }
  },
  "cases": [
    {
      "case_id": "refund-window",
      "prompt": "How long is the refund window?",
      "expected_output": "30 days",
      "metric": "contains",
      "threshold": 1.0,
      "weight": 3.0,
      "severity": "critical",
      "category": "policy",
      "tags": ["refunds", "customer-support"]
    }
  ]
}
```

Candidate outputs are a JSON object keyed by case ID. A value may be a plain output string:

```json
{
  "refund-window": "30 DAYS"
}
```

Structured evidence may be provided for observational runs. When `resource_budgets` are configured, every value must provide the output plus non-negative integer-safe latency and cost evidence:

```json
{
  "refund-window": {
    "output": "30 DAYS",
    "latency_ms": 125,
    "cost_micro_usd": 300
  }
}
```

`metric` selects `exact` (the default), `contains`, or `regex`. Exact and substring metrics trim surrounding whitespace and perform Unicode-aware case folding. Bounded regular expressions trim surrounding whitespace and use case-insensitive matching without rewriting the pattern source; they are limited to 256 characters and deliberately reject repetition, grouping, alternation, and optional operators. This constrained syntax prevents user-controlled patterns from causing unbounded matching work. Substring expectations must remain non-empty after trimming, and exact and substring metrics do not perform semantic matching. Every suite case must have exactly one candidate output, and unregistered outputs are rejected to prevent accounting drift.

A declarative `release_policy` contains the aggregate `minimum_pass_rate`, a suite-wide `default_case_threshold`, optional `metric_thresholds`, and `blocking_severities` (default: `["critical"]`). An individual case may set `threshold`. The effective score threshold precedence is **case override → metric override → suite default**; each result records both the effective value and `threshold_source`. All thresholds must be finite JSON numbers in the closed interval `[0, 1]`; booleans and numeric strings are rejected. For backward compatibility, a suite may use the legacy top-level `minimum_pass_rate` instead of `release_policy`. Exactly one of those keys must be supplied with a non-null value; supplying both keys, either key as `null`, or neither key is invalid. Legacy cases default to exact matching with a score threshold of `1.0`.

`release_policy.resource_budgets` may set any combination of total latency, ceiling-average latency, nearest-rank latency percentile, total cost, ceiling-average cost, and nearest-rank cost percentile limits. Latency uses integer milliseconds and cost uses integer micro-US dollars; per-case values and each suite aggregate must remain in `[0, 9,007,199,254,740,991]`, with booleans, numeric strings, fractions, negative values, and non-finite values rejected. Aggregate overflow is invalid input and does not produce a report. Percentiles are strict integers from 1 through 100. Limits are inclusive: a gate fails only when observed evidence exceeds its configured maximum. Complete evidence is summarized even when no budgets are configured. If budgets are configured, missing or partial performance evidence fails closed before a report is written.

Each case also has a positive finite numeric `weight` from greater than zero through `1,000,000` (default `1.0`), a `severity` of `low`, `medium` (default), `high`, or `critical`, one lowercase `category` label (default `uncategorized`), and up to 20 unique lowercase `tags` (default `[]`). Category and tag labels are 1–64 characters and may contain letters, digits, dots, underscores, and hyphens. Zero, negative, Boolean, string, out-of-range, and non-finite weights fail validation. Reports preserve these dimensions on each result and emit category and tag summaries sorted by label, with case counts, total and passing weight, and weighted pass rate. A failed case whose severity appears in `blocking_severities` blocks release even when the aggregate threshold passes.

A comparison policy is a separate strict JSON contract with `schema_version: 1`, `max_absolute_regression`, and `max_relative_regression`. Both budgets are finite numbers in `[0, 1]`. A drop is blocked only when it exceeds a configured budget, so equality is accepted. Relative delta is `(candidate - baseline) / baseline`; it is explicitly `null` when the baseline score is zero. Overall weighted pass rate and every represented metric's weighted mean are budgeted independently.

Repeated observations use a separate `schema_version: 1` contract containing one to 1,000 uniquely named runs. Each run provides the same exact case-ID-to-output mapping accepted by `evaluate`; missing or extra case IDs in any run fail closed. A stability policy has `schema_version: 1`, `max_weighted_pass_rate_variance`, and `max_flaky_case_rate`. Both limits are finite JSON numbers in `[0, 1]`; booleans, numeric strings, unknown fields, duplicate run IDs, and empty run collections are rejected. See [`examples/repeated-observations.json`](examples/repeated-observations.json) and [`examples/stability-policy.json`](examples/stability-policy.json).

Tool-trace evaluation uses two strict `schema_version: 1` contracts. The expectation declares a unique non-empty `allowed_tools` list and one or more expected tool names, argument objects, and result JSON values. The observed trace contains zero or more complete `request`/`result` exchanges; every exchange requires a unique call ID shared by its request and result. Unknown fields, malformed names and IDs, non-integer versions, non-finite or non-JSON values, duplicate call IDs, duplicate allowlist entries, and expected tools outside the allowlist fail closed. Values are bounded to 64 levels, 100,000 nodes, and 65,536 characters per string. Matching treats calls for each tool as an unordered multiset and uses type-strict canonical JSON identity. Deterministic pairing prioritizes complete argument/result matches, then argument matches, result matches, and stable indices. Reports classify missing, extra, repeated, disallowed, argument-mismatch, and result-mismatch findings; raw expected and observed arguments/results are never copied into report evidence. Canonical SHA-256 digests support comparisons without exposing those values, but operators should still protect reports because hashes of low-entropy secrets may be guessable. See [`examples/tool-trace-expectation.json`](examples/tool-trace-expectation.json) and [`examples/tool-trace.json`](examples/tool-trace.json).

Trajectory evaluation uses a strict `schema_version: 1` policy plus an ordered agent trace. Policies declare the initial state, one or more terminal states, the exact required transition sequence, optional forbidden transitions, and whether revisiting a state is allowed. Every trace contains uniquely identified steps with `state_before`, an action label, `state_after`, and an explicit termination signal. EvalForge fails closed on sequence mismatches, missing or unexpected steps, state discontinuities, forbidden transitions, premature or missing termination, and disallowed loops. Reports preserve deterministic counts, sequence and termination scores, and ordered explainable findings without copying action payloads. See [`examples/trajectory-policy.json`](examples/trajectory-policy.json) and [`examples/trajectory.json`](examples/trajectory.json).

RAG grounding evaluation uses one strict `schema_version: 1` contract containing an answer, uniquely identified answer claims, uniquely identified retrieved documents, and unique claim-to-document citations. Every claim includes an exact half-open `answer_start`/`answer_end` span; spans must be ordered, non-overlapping, match the claim text, and cover every answer character except ASCII space, tab, carriage return, and line feed. This prevents detached claim text from inflating citation recall while leaving answer content unassessed. Citation validity measures references to known claims and documents. Citation precision is the fraction of citations whose claim tokens are all present in the cited document; citation recall is the fraction of claims with at least one such citation. Context utilization is the fraction of retrieved documents used by a valid citation. Lexical grounding is the fraction of unique case-folded answer tokens present anywhere in the retrieved context. Reports expose document and claim IDs, Boolean citation evidence, metrics, and findings, but never copy answers, claims, document content, or answer spans. See [`examples/grounding.json`](examples/grounding.json).

These grounding metrics are deliberately lexical and deterministic, not semantic entailment or factuality judgments. Tokenization uses Unicode-aware case-folded word matching, ignores order and frequency, and can miss synonyms, paraphrases, negation, numerical equivalence, and contradictory passages that share vocabulary. A citation is considered supported only by complete claim-token containment; context utilization records valid references rather than relevance. Required spans make the claim set complete, but caller-selected claim granularity still affects citation recall, so compare recall only under the same versioned claim-generation policy. The report's canonical input digest binds the hidden source text, so reports containing low-entropy inputs should still be protected against offline guessing. Use these measurements as reproducible offline signals alongside domain review or a separately governed semantic judge, not as proof that an answer is true.

Embedding similarity uses a separate strict `schema_version: 1` evaluation and policy. The evaluation records one model provider, model ID, revision identifier, expected dimensions, artifact SHA-256, license, and one or more reference/candidate vector pairs. The policy approves the canonical SHA-256 of that complete normalized model provenance and configures inclusive minimum cosine similarity and aggregate pass rate. Vectors contain 1–8,192 finite JSON numbers bounded to `[-1,000,000, 1,000,000]`, must match the governed dimensions, and must be nonzero. Boolean coordinates, non-finite numbers, unknown fields, duplicate case IDs, dimension mismatches, zero vectors, and an unapproved model fail closed before a report is written. See [`examples/embedding-evaluation.json`](examples/embedding-evaluation.json) and [`examples/embedding-policy.json`](examples/embedding-policy.json).

The evaluator computes cosine similarity in `[-1, 1]` by first scaling each vector by its maximum absolute coordinate, then applying `math.hypot` normalization and `math.fsum`; only original vectors proven exactly proportional or antiparallel retain the `1.0` and `-1.0` endpoints, while rounded non-matches cannot be promoted to either endpoint. Euclidean distance uses `math.dist`. Each inclusive cosine gate is decided with an exact rational comparison over the original binary64 coordinates. If the bounded diagnostic cosine rounds across the configured threshold, the report moves it by at most one binary64 step onto the exact decision's side, so persisted `cosine_similarity` and `passed` remain self-consistent without changing the gate. These choices preserve direction for permitted subnormal vectors and strict gate decisions under semantics version `embedding-cosine-l2-v1-scaled-hypot-fsum-exact-decision-aligned`. The cosine threshold controls each case; the minimum pass rate controls the release verdict. Reports retain public model provenance, per-case metric values, and canonical vector digests, but omit raw vectors. Digests are integrity evidence, not confidentiality: low-entropy embeddings can be guessed, and embedding models can encode bias or sensitive attributes. Generate vectors in a controlled system, verify the artifact digest independently, and do not interpret similarity as factual equivalence.

Task-specific code evaluation accepts only a strict `schema_version: 1` document of precomputed terminal test outcomes plus a separate policy. The policy approves the canonical digest of the complete harness and runtime provenance, declares the exact ordered required case IDs, names the task, and sets an inclusive minimum pass rate as JSON-safe integer `minimum_pass_rate_numerator` and `minimum_pass_rate_denominator` fields. Exact integer cross-multiplication decides the gate, avoiding binary floating-point aliases around rational thresholds. Missing, extra, duplicate, or reordered cases, invalid fractions, coercive integers, and unapproved provenance fail before artifact creation. `passed` is the only passing outcome; `error`, `timeout`, and `skipped` always block release even when the ratio threshold would otherwise pass. See [`examples/code-harness-evidence.json`](examples/code-harness-evidence.json) and [`examples/code-harness-policy.json`](examples/code-harness-policy.json).

EvalForge does not execute candidate code. It does not accept source, commands, paths, environment values, standard output, standard error, or exception messages. The evidence producer and sandbox are trusted: operators must independently ensure that the harness executed the candidate identified by `candidate_artifact_sha256` with appropriate network, filesystem, process, CPU, memory, and wall-time controls. Candidate and provenance digests bind identity but do not prove execution, authenticity, confidentiality, or sandbox strength.

Ranked retrieval evaluation accepts a strict `schema_version: 1` evaluation plus a separate policy. Retriever provenance binds the producer and revision to exact retriever, corpus, and index artifact SHA-256 digests; policy approves the canonical digest of that complete provenance and requires the exact ordered query-case set. Every case declares one or more unique relevance judgments and an ordered, duplicate-free retrieval list. The cutoff is a strict integer from 1 through 1,000. Precision@k uses `relevant_at_k / k`, recall@k uses `relevant_at_k / relevant_documents`, and reciprocal rank uses the first relevant result at or before k. Macro means are computed as exact fractions and compared to reduced numerator/denominator thresholds, so binary floating-point rounding cannot change a gate. See [`examples/retrieval-evaluation.json`](examples/retrieval-evaluation.json) and [`examples/retrieval-policy.json`](examples/retrieval-policy.json).

Retrieval reports retain case IDs, counts, ranks, diagnostic metric values, exact aggregate fractions encoded as bounded decimal strings, governed public provenance, and content-addressed input/report identity. They omit relevance and retrieved document IDs. Those IDs still contribute to the input digest, which provides integrity rather than confidentiality and can be guessed when identifiers have low entropy. Metrics measure judged ranking quality, not factual correctness, corpus completeness, retriever safety, or online latency; provenance approval does not attest that an external producer actually built the claimed index.

Structured-output evaluation accepts a strict `schema_version: 1` collection of case IDs, bounded Draft 2020-12 schemas, and candidate JSON values plus a separate policy. The policy approves the canonical digest of the exact ordered case/schema catalog, requires the same ordered case set, names the task, and sets an inclusive pass rate with a reduced JSON-safe integer fraction. The supported schema profile includes object properties and required fields, closed objects, homogeneous arrays, bounded collection/string sizes, numeric ranges, type checks, constants, enums, and uniqueness. It deliberately rejects references, dynamic references, regular-expression keywords, combinators, remote resolution, coercive bounds, duplicate case IDs, unsupported keywords, and non-JSON values. See [`examples/structured-output-evaluation.json`](examples/structured-output-evaluation.json) and [`examples/structured-output-policy.json`](examples/structured-output-policy.json).

Structured-output reports retain case IDs, schema and candidate digests, validity, a capped violation count, truncation state, validator categories, exact aggregate threshold evidence, and content-addressed input/report identity. They omit raw schemas, property paths, candidate keys, and candidate values. Digests provide integrity rather than confidentiality and may permit guessing low-entropy content. Input files remain bounded by the shared parser; schema validation uses canonical key order and stops diagnostic collection after 100 violations per case. A valid failed gate still writes evidence and exits `1`; malformed, unsafe, unapproved, or incompletely accounted input does not replace an existing report and exits `2`.

Dataset mutation accepts a strict `schema_version: 1` plan bound to the normalized source-suite digest. Each ordered mutation names one source case and applies exactly one bounded operator: literal prompt prefix, explicit prompt-injection suffix, or deletion of one Unicode code point at a declared index. Mutation IDs are unique and become part of generated case IDs; expected outputs, metrics, thresholds, weights, severities, categories, tags, and the suite release policy are preserved. Unknown cases, stale source digests, invalid operator parameters, out-of-range deletions, duplicate generated IDs, and prompts that exceed the existing suite limits fail before an artifact is written. See [`examples/mutation-plan.json`](examples/mutation-plan.json).

The deterministic artifact embeds the validated source suite, plan, and generated suite plus content-redacted per-case prompt digests. It binds those inputs, fixed mutation semantics, canonicalization version, and generated suite to a mutation campaign ID, and re-derives the generated cases when the artifact is loaded so rehashed tampering cannot pass. Because source and generated prompts are intentionally present to make the dataset usable, mutation artifacts are controlled dataset material—not public redacted reports. Operators choose all mutation text; the built-in operators generate reproducible stress cases but do not prove adversarial coverage, safety, or realism.

Sensitive-data scanning uses fixed deterministic detectors for email addresses, North American phone numbers, structurally valid US Social Security numbers, Luhn-valid payment-card numbers, labeled or common-prefixed API keys, and PEM private-key material. `scan-leakage` scans candidate output text; `scan-report-leakage` recursively scans string values in an evaluation report. A strict `schema_version: 1` policy selects enabled categories, overrides category severities, and chooses which severities block release. False positives can be suppressed without storing plaintext in policy by listing exact lowercase SHA-256 digests in `allowlisted_value_sha256`; the digest must be computed from the detector's exact matched value. Findings contain only a zero-based string index, category, and severity—never the matched value, output ID, JSON key, or source text. See [`examples/leakage-policy.json`](examples/leakage-policy.json) and [`examples/leakage-outputs.json`](examples/leakage-outputs.json).

These detectors are intentionally conservative pattern checks, not proof of identity or secret validity. Phone and email syntax can match public or fictional values, only US SSN structure is recognized, API-key formats evolve, and encoded or obfuscated values may be missed. Digest allowlists can be brute-forced for low-entropy values and must be reviewed as security configuration. Keep source inputs and generated scan reports in controlled artifact storage even though findings are redacted.

### CI-native reports

`evaluate --junit-path` writes one stable JUnit testcase for every evaluated case, the weighted pass-rate gate, and each observed latency/cost metric. Failure details contain IDs, scores, thresholds, severities, categories, and resource limits, but never expected or candidate output text. `scan-leakage --sarif-path` writes SARIF 2.1.0 rules and results with stable rule IDs and fingerprints; evidence is always the literal `redacted`. Both artifacts are written before a failed release gate returns status `1`, so CI can publish diagnostics without weakening enforcement.

The reusable [EvalForge release reports workflow](.github/workflows/evalforge-reports.yml) accepts suite, output, and leakage-policy paths. It installs only the committed lockfile, uploads JSON/JUnit/SARIF artifacts, publishes SARIF to GitHub code scanning, and fails when either evaluation or safety gates fail. Callers grant only `contents: read` and `security-events: write`; all third-party actions are pinned to verified 40-character commit SHAs. The main CI workflow dogfoods this reusable workflow on pushes to `main` with the synthetic examples.

An optional dataset manifest binds a stable dataset ID and version to the suite's canonical SHA-256 digest. Its required lineage records the source, source revision, creator, license, and at least one transformation. Unknown fields, non-integer or unsupported schema versions, malformed identifiers or digests, empty lineage values, and suite-digest mismatches fail closed. The minimum pass rate must be a JSON number rather than a boolean or numeric string. Each JSON input is limited to 1 MiB, 64 levels of nesting, 100,000 decoded nodes, 65,536 characters per string, and 256 characters per numeric literal; nonzero literals that underflow binary64 or floating-point literals that cannot round-trip through binary64 without changing their decimal value are rejected before validation. See [`examples/dataset-manifest.json`](examples/dataset-manifest.json) for synthetic data safe to publish.

## Release-gate behavior

EvalForge calculates:

```text
case_passed = metric_score >= effective_case_threshold
weighted_pass_rate = sum(weight for passed cases) / sum(weight for all cases)
release_ready = weighted_pass_rate >= minimum_pass_rate
                and no failed case has a blocking severity
                and no configured resource budget is exceeded

stability_mean = sum(run_weighted_pass_rate) / run_count
stability_population_variance = sum((rate - stability_mean) ** 2) / run_count
case_flaky = case passed in at least one run and failed in at least one run
stability_ready = every repeated run passes the suite release gate
                  and population variance <= configured maximum
                  and flaky case rate <= configured maximum

tool_trace_precision = complete_matches / observed_calls
                       (0 when there are no observed calls)
tool_trace_recall = complete_matches / expected_calls
tool_trace_ready = every expected occurrence matches its tool, arguments, and result
                   and there are no missing, extra, repeated, or disallowed calls

citation_validity = valid_claim_and_document_references / citations
citation_precision = lexically_supported_citations / citations
citation_recall = claims_with_a_supported_citation / claims
context_utilization = documents_used_by_valid_citations / retrieved_documents
lexical_grounding = unique_answer_tokens_found_in_context / unique_answer_tokens
grounding_ready = citation_validity == citation_precision == citation_recall == 1
                  and lexical_grounding == 1

embedding_case_passed = exact_cosine(binary64_reference, binary64_candidate)
                        >= minimum_cosine_similarity
embedding_ready = passed_embedding_cases / embedding_cases >= minimum_pass_rate

code_case_passed = terminal_outcome == "passed"
code_ready = passed_code_cases * minimum_pass_rate_denominator
             >= minimum_pass_rate_numerator * required_code_cases
             and no outcome is error, timeout, or skipped

retrieval_precision_at_k = mean(relevant_at_k / k)
retrieval_recall_at_k = mean(relevant_at_k / relevant_documents)
retrieval_mrr_at_k = mean(1 / first_relevant_rank_at_or_before_k, else 0)
retrieval_ready = all three exact aggregate fractions meet policy thresholds

structured_output_case_valid = candidate satisfies its approved bounded JSON Schema
structured_output_ready = valid_cases * minimum_pass_rate_denominator
                          >= minimum_pass_rate_numerator * required_cases

generated_prompt = deterministic_operator(source_prompt, mutation_parameters)
mutation_campaign_id = sha256(source_suite, plan, generated_suite, semantics)

sensitive_data_ready = no finding severity appears in blocking_severities
```

Embedding threshold decisions use exact rational comparisons over the accepted binary64 coordinates; the reported cosine is bounded binary64 diagnostic evidence and is adjusted by at most one binary64 step when necessary so it remains on the same side of the threshold as the exact decision. The report is written for both passing and failing evaluations. Report schema version `5` retains the unweighted count-based pass rate for diagnostics, weighted totals, the weighted release rate, case quality dimensions, deterministic category/tag slices, and machine-readable quality-gate failures. It adds optional per-case performance evidence, deterministic latency/cost aggregates, and ordered `resource_gate_failures` with observed and required integer values. Reports also record metric evidence plus each effective threshold and precedence source, canonical suite and candidate SHA-256 digests, an optional manifest digest and minimal dataset summary, explicit canonicalization and evaluation-semantics versions, and a deterministic run ID derived from that versioned preimage. Digests are computed from each successfully parsed and validated raw JSON value before typed-model normalization, so an independently computed canonical digest matches the report. Canonicalization sorts keys, uses compact UTF-8 JSON, rejects non-finite numbers and lone surrogates, and normalizes negative zero to zero. Evaluation semantics version `3` binds resource-gate behavior and the runtime Unicode database version used by whitespace trimming and case folding. A failed gate exits with status `1`, making the command suitable for CI. Invalid command input exits with a usage error and does not write a misleading report.

Comparison report schema version `3` embeds schema-version-5 evaluation reports and accepts the same plain or structured candidate-output values as `evaluate`. It uses case weights for its overall pass-rate delta, per-metric means, regression budgets, and baseline/candidate matrix, while each nested evaluation independently enforces configured resource budgets. It also includes ordered per-case deltas, budget-failure evidence, and deterministic ablation counts. Canonical hashes bind the suite, baseline outputs, candidate outputs, and comparison policy; `comparison_id` binds those hashes, the serialized normalized variant labels, canonicalization and evaluator semantics, and comparison-ID schema version `3`. The comparison gate requires the candidate's release gate and every weighted regression budget to pass. A blocked comparison is still written for diagnosis and exits with status `1`.

Stability report schema version `1` preserves input run order, each run's weighted pass rate and suite-gate verdict, aggregate mean/minimum/maximum and population variance, plus suite-ordered per-case pass/fail counts and flaky flags. One run is valid and has zero population variance. Limits are inclusive and failures are ordered as variance then flaky-case rate. The final stability verdict also requires every observed run to pass the suite's existing quality/resource gate. Canonical digests bind the suite, repeated observations, and stability policy; `stability_id` additionally binds canonicalization and evaluator semantics. Identical validated inputs serialize byte-for-byte identically. Invalid observations do not produce a report, a failed gate still produces evidence and exits `1`, and a passing gate exits `0`.

Tool-trace report schema version `1` preserves observed call order and emits tool names, call IDs, expected/actual indices, Boolean component matches, canonical argument/result digests, deterministic counts, precision, recall, and ordered findings. The report also binds the raw validated expectation and trace digests, canonicalization version, tool-matching semantics version, and a deterministic `tool_trace_id`. Identical inputs serialize byte-for-byte identically. A finding writes diagnostic evidence and exits `1`; malformed input writes no report and exits with a usage error.

Trajectory report schema version `1` emits deterministic transition and violation counts, sequence and termination scores, ordered findings, and a release verdict. The CLI binds canonical policy and trajectory hashes, canonicalization and trajectory-semantics versions, and a deterministic `trajectory_id`. A finding writes diagnostic evidence and exits `1`; a valid trajectory exits `0`; malformed input writes no report and exits with a usage error.

Grounding report schema version `1` emits document-ID-only retrieval evidence, claim/document citation IDs, Boolean validity/support evidence, deterministic metrics, and ordered invalid-reference, unsupported-citation, and uncited-claim findings. The CLI binds a canonical input digest, canonicalization and grounding-semantics versions (including the runtime Unicode database used for lexical tokenization), and a deterministic `grounding_id`; raw answer, claim, document text, and answer spans are excluded. Identical inputs under the same reported grounding-semantics version serialize byte-for-byte identically. Malformed inputs write no report, a failed gate writes evidence and exits `1`, and a passing gate exits `0`.

Embedding-similarity report schema version `1` emits approved model provenance, its canonical digest, cosine similarity, Euclidean distance, vector digests, thresholds, aggregate pass evidence, and ordered gate failures without raw vectors. The CLI binds canonical evaluation and policy digests plus canonicalization and embedding-semantics versions into `embedding_evaluation_id`. Malformed or unapproved inputs write no report; a failed quality gate writes evidence and exits `1`; a passing gate exits `0`.

Code-harness report schema version `1` emits the approved producer/runtime provenance, candidate artifact digest, ordered terminal outcomes, exact threshold fraction, derived diagnostic pass rate, blocking-outcome count, and machine-readable gate failures. It excludes candidate code and runtime output. A canonical digest of the complete validated report joins the canonical evidence and policy digests plus fixed code-evaluation semantics in `code_evaluation_id`, so changing either inputs or derived evidence changes the identity. Invalid, incomplete, or unapproved evidence does not replace an existing report; report writes use same-directory temporary files and atomic replacement; a valid failed gate writes evidence and exits `1`; a passing gate exits `0`.

Retrieval report schema version `1` emits approved retriever/corpus/index provenance, ordered redacted case counts and ranks, precision@k, recall@k, MRR@k, exact aggregate fractions and threshold evidence, and ordered gate failures. The CLI binds canonical evaluation, policy, and complete validated-report digests with fixed retrieval semantics in `retrieval_evaluation_id`. Invalid, incomplete, ambiguous, or unapproved evidence does not replace an existing report; writes use same-directory temporary files and atomic replacement; a valid failed gate writes evidence and exits `1`; a passing gate exits `0`.

Structured-output report schema version `1` emits approved schema-catalog identity, ordered value-redacted case evidence, bounded validator categories, exact threshold fractions, and derived gate failures. The CLI binds canonical evaluation, policy, and complete validated-report digests with `bounded-json-schema-2020-12-v1` semantics in `structured_output_evaluation_id`. Invalid, ambiguous, unsafe, unapproved, or incompletely accounted input does not replace an existing report; writes use same-directory temporary files and atomic replacement; a valid failed gate writes evidence and exits `1`; a passing gate exits `0`.

Mutation artifact schema version `1` embeds the strict source suite, mutation plan, generated suite, and redacted mutation records. Normalized source, plan, and generated-suite digests plus `deterministic-prompt-mutations-v1` semantics form `mutation_campaign_id`. Artifact validation independently recreates every generated prompt and case from the embedded source and plan. Invalid or ambiguous inputs do not replace an existing artifact; writes use same-directory temporary files and atomic replacement; successful generation exits `0`.

Sensitive-data report schema version `1` emits deterministic category/severity findings and a release verdict without matched values or caller-provided output IDs. Output and report scan commands bind canonical input and policy digests, canonicalization and Unicode-bound detector-semantics versions, and deterministic scan IDs. Invalid policies write no report; blocking findings write redacted evidence and exit `1`; clean or non-blocking findings exit `0`.

## Architecture

The current slice is intentionally small:

```text
suite JSON + baseline/candidate/repeated outputs
                 |
           schema validation
                 |
 deterministic evaluator + resource gates
                 |
 case/metric deltas + stability statistics
                 |
comparison/stability reports + exit code
```

See [docs/architecture.md](docs/architecture.md) for implemented and target boundaries.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for the frozen environment, RED–GREEN–REFACTOR workflow,
full local release gates, review requirements, and pull-request conventions. CI runs formatting,
lint, strict type checking, branch-covered tests, package builds, JUnit export, and SARIF
publication from the committed lockfile. Third-party GitHub Actions are pinned to immutable commit
SHAs.

## Project documentation

- [Architecture and implementation boundaries](docs/architecture.md)
- [Local and container deployment](docs/deployment.md)
- [Threat model and trust boundaries](docs/threat-model.md)
- [Provider, metric, schema, and persistence extensions](docs/extensions.md)
- [v0.1.0 acceptance evidence and explicit limitations](docs/acceptance.md)
- [Release history](CHANGELOG.md)
- [Security policy and private reporting](SECURITY.md)

## Repository layout

```text
src/evalforge/       typed contracts, deterministic engine, and CLI
tests/               behavior and CLI integration tests
examples/            synthetic offline suite, outputs, manifests, and policies
docs/                architecture and design boundaries
.github/workflows/   locked continuous-integration checks
```

## Security and privacy

The example data is synthetic. Do not commit production prompts, model outputs, customer records, API keys, or generated reports. Reports can contain sensitive prompts and responses and belong in controlled artifact storage. See the [threat model](docs/threat-model.md) for trust boundaries and [SECURITY.md](SECURITY.md) for responsible disclosure.

## License

[MIT](LICENSE)
