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
- Deterministic secret and PII detectors for email, phone, US SSN, payment-card, API-key, and private-key patterns
- Digest allowlists, category controls, severity overrides, and configurable blocking severities
- Redacted leakage findings for candidate outputs and nested evaluation-report values
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
- Bounded JSON decoding with duplicate-key, Unicode, size, depth, and string limits
- Nonzero CLI exit status when a release gate fails
- Friendly validation for malformed candidate-output JSON
- Fully offline example workflow

The broader platform roadmap—including OpenTelemetry, APIs, dashboards, and richer CI reports—is tracked in [ROADMAP.md](ROADMAP.md). Planned features are not presented as implemented.

## Quick start

Prerequisites: Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/navaneeth9908/evalforge.git
cd evalforge
uv sync --group dev
uv run evalforge evaluate examples/suite.json examples/outputs.json \
  --dataset-manifest examples/dataset-manifest.json \
  --report-path reports/example.json

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

uv run evalforge scan-leakage examples/leakage-outputs.json \
  --policy examples/leakage-policy.json \
  --report-path reports/leakage.json

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

Sensitive-data scanning uses fixed deterministic detectors for email addresses, North American phone numbers, structurally valid US Social Security numbers, Luhn-valid payment-card numbers, labeled or common-prefixed API keys, and PEM private-key material. `scan-leakage` scans candidate output text; `scan-report-leakage` recursively scans string values in an evaluation report. A strict `schema_version: 1` policy selects enabled categories, overrides category severities, and chooses which severities block release. False positives can be suppressed without storing plaintext in policy by listing exact lowercase SHA-256 digests in `allowlisted_value_sha256`; the digest must be computed from the detector's exact matched value. Findings contain only a zero-based string index, category, and severity—never the matched value, output ID, JSON key, or source text. See [`examples/leakage-policy.json`](examples/leakage-policy.json) and [`examples/leakage-outputs.json`](examples/leakage-outputs.json).

These detectors are intentionally conservative pattern checks, not proof of identity or secret validity. Phone and email syntax can match public or fictional values, only US SSN structure is recognized, API-key formats evolve, and encoded or obfuscated values may be missed. Digest allowlists can be brute-forced for low-entropy values and must be reviewed as security configuration. Keep source inputs and generated scan reports in controlled artifact storage even though findings are redacted.

An optional dataset manifest binds a stable dataset ID and version to the suite's canonical SHA-256 digest. Its required lineage records the source, source revision, creator, license, and at least one transformation. Unknown fields, non-integer or unsupported schema versions, malformed identifiers or digests, empty lineage values, and suite-digest mismatches fail closed. The minimum pass rate must be a JSON number rather than a boolean or numeric string. Each JSON input is limited to 1 MiB, 64 levels of nesting, 100,000 decoded nodes, 65,536 characters per string, and 256 characters per numeric literal. See [`examples/dataset-manifest.json`](examples/dataset-manifest.json) for synthetic data safe to publish.

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

sensitive_data_ready = no finding severity appears in blocking_severities
```

The report is written for both passing and failing evaluations. Report schema version `5` retains the unweighted count-based pass rate for diagnostics, weighted totals, the weighted release rate, case quality dimensions, deterministic category/tag slices, and machine-readable quality-gate failures. It adds optional per-case performance evidence, deterministic latency/cost aggregates, and ordered `resource_gate_failures` with observed and required integer values. Reports also record metric evidence plus each effective threshold and precedence source, canonical suite and candidate SHA-256 digests, an optional manifest digest and minimal dataset summary, explicit canonicalization and evaluation-semantics versions, and a deterministic run ID derived from that versioned preimage. Digests are computed from each successfully parsed and validated raw JSON value before typed-model normalization, so an independently computed canonical digest matches the report. Canonicalization sorts keys, uses compact UTF-8 JSON, rejects non-finite numbers and lone surrogates, and normalizes negative zero to zero. Evaluation semantics version `3` binds resource-gate behavior and the runtime Unicode database version used by whitespace trimming and case folding. A failed gate exits with status `1`, making the command suitable for CI. Invalid command input exits with a usage error and does not write a misleading report.

Comparison report schema version `3` embeds schema-version-5 evaluation reports and accepts the same plain or structured candidate-output values as `evaluate`. It uses case weights for its overall pass-rate delta, per-metric means, regression budgets, and baseline/candidate matrix, while each nested evaluation independently enforces configured resource budgets. It also includes ordered per-case deltas, budget-failure evidence, and deterministic ablation counts. Canonical hashes bind the suite, baseline outputs, candidate outputs, and comparison policy; `comparison_id` binds those hashes, the serialized normalized variant labels, canonicalization and evaluator semantics, and comparison-ID schema version `3`. The comparison gate requires the candidate's release gate and every weighted regression budget to pass. A blocked comparison is still written for diagnosis and exits with status `1`.

Stability report schema version `1` preserves input run order, each run's weighted pass rate and suite-gate verdict, aggregate mean/minimum/maximum and population variance, plus suite-ordered per-case pass/fail counts and flaky flags. One run is valid and has zero population variance. Limits are inclusive and failures are ordered as variance then flaky-case rate. The final stability verdict also requires every observed run to pass the suite's existing quality/resource gate. Canonical digests bind the suite, repeated observations, and stability policy; `stability_id` additionally binds canonicalization and evaluator semantics. Identical validated inputs serialize byte-for-byte identically. Invalid observations do not produce a report, a failed gate still produces evidence and exits `1`, and a passing gate exits `0`.

Tool-trace report schema version `1` preserves observed call order and emits tool names, call IDs, expected/actual indices, Boolean component matches, canonical argument/result digests, deterministic counts, precision, recall, and ordered findings. The report also binds the raw validated expectation and trace digests, canonicalization version, tool-matching semantics version, and a deterministic `tool_trace_id`. Identical inputs serialize byte-for-byte identically. A finding writes diagnostic evidence and exits `1`; malformed input writes no report and exits with a usage error.

Trajectory report schema version `1` emits deterministic transition and violation counts, sequence and termination scores, ordered findings, and a release verdict. The CLI binds canonical policy and trajectory hashes, canonicalization and trajectory-semantics versions, and a deterministic `trajectory_id`. A finding writes diagnostic evidence and exits `1`; a valid trajectory exits `0`; malformed input writes no report and exits with a usage error.

Grounding report schema version `1` emits document-ID-only retrieval evidence, claim/document citation IDs, Boolean validity/support evidence, deterministic metrics, and ordered invalid-reference, unsupported-citation, and uncited-claim findings. The CLI binds a canonical input digest, canonicalization and grounding-semantics versions (including the runtime Unicode database used for lexical tokenization), and a deterministic `grounding_id`; raw answer, claim, document text, and answer spans are excluded. Identical inputs under the same reported grounding-semantics version serialize byte-for-byte identically. Malformed inputs write no report, a failed gate writes evidence and exits `1`, and a passing gate exits `0`.

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

```bash
uv sync --group dev
uv run --group dev ruff format --check .
uv run --group dev ruff check .
uv run --group dev mypy
uv run --group dev pytest --cov=evalforge --cov-branch --cov-report=term-missing -q
uv build
```

CI runs formatting, lint, strict type checking, branch-covered tests, and package builds from the committed lockfile. Third-party GitHub Actions are pinned to immutable commit SHAs.

## Repository layout

```text
src/evalforge/       typed contracts, deterministic engine, and CLI
tests/               behavior and CLI integration tests
examples/            synthetic offline suite, outputs, manifests, and policies
docs/                architecture and design boundaries
.github/workflows/   locked continuous-integration checks
```

## Security and privacy

The example data is synthetic. Do not commit production prompts, model outputs, customer records, API keys, or generated reports. Reports can contain sensitive prompts and responses and belong in controlled artifact storage. See [SECURITY.md](SECURITY.md) for trust boundaries and responsible disclosure.

## License

[MIT](LICENSE)
