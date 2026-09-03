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
- Normalized exact-match scoring
- Non-empty and unique case-ID validation
- Exact candidate-output accounting with no silent omissions or extras
- Finite minimum pass-rate validation
- Ordered case-level evidence containing expected and actual outputs
- Pass/fail release decision using an explicit threshold
- JSON report output
- Nonzero CLI exit status when a release gate fails
- Friendly validation for malformed candidate-output JSON
- Fully offline example workflow

The broader platform roadmap—including regression comparisons, agent traces, RAG and safety metrics, model judges, OpenTelemetry, APIs, dashboards, and CI reports—is tracked in [ROADMAP.md](ROADMAP.md). Planned features are not presented as implemented.

## Quick start

Prerequisites: Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/navaneeth9908/evalforge.git
cd evalforge
uv sync --group dev
uv run evalforge evaluate examples/suite.json examples/outputs.json \
  --report-path reports/example.json
```

Expected console result:

```text
Evaluation report: reports/example.json
Pass rate: 100.00%
Release gate: PASS
```

Generated reports are intentionally ignored by Git. Review `reports/example.json` locally for the aggregate verdict and ordered case-level evidence.

## Input contract

An evaluation suite declares its schema version, name, release threshold, and cases:

```json
{
  "schema_version": 1,
  "name": "support-policy-smoke",
  "minimum_pass_rate": 1.0,
  "cases": [
    {
      "case_id": "refund-window",
      "prompt": "How long is the refund window?",
      "expected_output": "30 days"
    }
  ]
}
```

Candidate outputs are a JSON object keyed by case ID:

```json
{
  "refund-window": "30 DAYS"
}
```

The exact-match metric trims surrounding whitespace and performs Unicode-aware case folding. It does not perform semantic matching. Every suite case must have exactly one candidate output, and unregistered outputs are rejected to prevent accounting drift.

## Release-gate behavior

EvalForge calculates:

```text
pass_rate = passed_cases / total_cases
release_ready = pass_rate >= minimum_pass_rate
```

The report is written for both passing and failing evaluations. A failed gate exits with status `1`, making the command suitable for CI. Invalid command input exits with a usage error and does not write a misleading report.

## Architecture

The current slice is intentionally small:

```text
suite JSON + candidate outputs
            |
      schema validation
            |
 deterministic evaluator
            |
 case evidence + aggregate gate
            |
     JSON report + exit code
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
examples/            synthetic offline suite and candidate outputs
docs/                architecture and design boundaries
.github/workflows/   locked continuous-integration checks
```

## Security and privacy

The example data is synthetic. Do not commit production prompts, model outputs, customer records, API keys, or generated reports. Reports can contain sensitive prompts and responses and belong in controlled artifact storage. See [SECURITY.md](SECURITY.md) for trust boundaries and responsible disclosure.

## License

[MIT](LICENSE)
