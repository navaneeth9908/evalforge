# EvalForge

[![CI](https://github.com/navaneeth9908/evalforge/actions/workflows/ci.yml/badge.svg)](https://github.com/navaneeth9908/evalforge/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Production-grade evaluation evidence and release gates for LLM and AI-agent systems.

EvalForge turns versioned test cases and candidate outputs into an auditable report and a machine-readable release decision. The initial implementation is deliberately deterministic and offline: it requires no provider credentials, makes no network calls, and fails closed when evaluation evidence is missing or ambiguous.

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

The broader platform roadmap—including multi-candidate matrices, agent traces, RAG and safety metrics, model judges, OpenTelemetry, APIs, dashboards, and CI reports—is tracked in [ROADMAP.md](ROADMAP.md). Planned features are not presented as implemented.

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
```

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
```

The report is written for both passing and failing evaluations. Report schema version `5` retains the unweighted count-based pass rate for diagnostics, weighted totals, the weighted release rate, case quality dimensions, deterministic category/tag slices, and machine-readable quality-gate failures. It adds optional per-case performance evidence, deterministic latency/cost aggregates, and ordered `resource_gate_failures` with observed and required integer values. Reports also record metric evidence plus each effective threshold and precedence source, canonical suite and candidate SHA-256 digests, an optional manifest digest and minimal dataset summary, explicit canonicalization and evaluation-semantics versions, and a deterministic run ID derived from that versioned preimage. Digests are computed from each successfully parsed and validated raw JSON value before typed-model normalization, so an independently computed canonical digest matches the report. Canonicalization sorts keys, uses compact UTF-8 JSON, rejects non-finite numbers and lone surrogates, and normalizes negative zero to zero. Evaluation semantics version `3` binds resource-gate behavior and the runtime Unicode database version used by whitespace trimming and case folding. A failed gate exits with status `1`, making the command suitable for CI. Invalid command input exits with a usage error and does not write a misleading report.

Comparison report schema version `3` embeds schema-version-5 evaluation reports and accepts the same plain or structured candidate-output values as `evaluate`. It uses case weights for its overall pass-rate delta, per-metric means, regression budgets, and baseline/candidate matrix, while each nested evaluation independently enforces configured resource budgets. It also includes ordered per-case deltas, budget-failure evidence, and deterministic ablation counts. Canonical hashes bind the suite, baseline outputs, candidate outputs, and comparison policy; `comparison_id` binds those hashes, the serialized normalized variant labels, canonicalization and evaluator semantics, and comparison-ID schema version `3`. The comparison gate requires the candidate's release gate and every weighted regression budget to pass. A blocked comparison is still written for diagnosis and exits with status `1`.

Stability report schema version `1` preserves input run order, each run's weighted pass rate and suite-gate verdict, aggregate mean/minimum/maximum and population variance, plus suite-ordered per-case pass/fail counts and flaky flags. One run is valid and has zero population variance. Limits are inclusive and failures are ordered as variance then flaky-case rate. The final stability verdict also requires every observed run to pass the suite's existing quality/resource gate. Canonical digests bind the suite, repeated observations, and stability policy; `stability_id` additionally binds canonicalization and evaluator semantics. Identical validated inputs serialize byte-for-byte identically. Invalid observations do not produce a report, a failed gate still produces evidence and exits `1`, and a passing gate exits `0`.

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
