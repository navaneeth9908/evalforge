# EvalForge extension guide

EvalForge keeps provider I/O, deterministic evaluation, persistence, and presentation at separate boundaries. New integrations should preserve that separation.

## Provider adapters

Implement the `CandidateAdapter` protocol from `evalforge.providers`:

```python
from evalforge.providers import GenerationRequest, GenerationResult


class CandidateAdapter:
    def generate(self, request: GenerationRequest) -> GenerationResult: ...
```

An adapter must:

- take a validated `GenerationRequest` and return a validated `GenerationResult`;
- use an end-to-end deadline rather than an unbounded socket timeout;
- bound response bytes before parsing;
- normalize transport failures to a secret-safe `ProviderError`;
- never include API keys, authorization headers, complete prompts, or response bodies in errors;
- report integer token usage when the upstream service provides it;
- use dependency injection or a fake transport in tests so CI remains credential-free.

Provider output is untrusted. It must pass through the same output-size limits, leakage scan, and evaluation contracts as fixture-based output. The synthetic reference fake lives in `evalforge.demo` and is deliberately network-free.

## Metrics and release gates

Metrics belong in the deterministic engine boundary, not in an HTTP adapter or dashboard. A new metric requires:

1. a contract field with finite ranges and a bounded input representation;
2. a semantics-version decision when results for an existing input could change;
3. focused RED→GREEN tests for pass, fail, and boundary behavior;
4. aggregation and fail-closed release-policy tests;
5. serialization, CLI, and CI-export coverage; and
6. documentation of what the score does and does not prove.

Never treat missing evidence as a pass. Resource measurements use strict non-negative JSON-safe integers. Aggregate overflow, partial evidence, NaN, infinity, and ambiguous duplicate keys must be rejected before a report is written.

## Versioned JSON contracts

All persisted or exchanged suite, policy, trace, and report formats carry an integer `schema_version`. When extending one:

- prefer optional fields with safe defaults only when absence is unambiguous;
- reject unknown fields at trust boundaries;
- reject duplicate mapping keys before ordinary JSON/YAML decoding can discard them;
- update canonicalization or semantics versions when content-addressed identity would otherwise hide a behavior change;
- add old-version read tests and new-version round-trip tests; and
- document compatibility in [CHANGELOG.md](../CHANGELOG.md).

Do not place secrets or raw sensitive findings in content-addressed identifiers or public evidence. Digests establish identity, not confidentiality.

## Persistence adapters

`RunRegistry` stores canonical suite and report JSON behind `EvalForgeService`. A replacement store should preserve:

- suite digest identity and idempotent registration;
- deterministic run IDs;
- atomic writes;
- bounded pagination;
- startup schema-version checks;
- secret-safe errors; and
- the rule that summary endpoints do not expose candidate text.

Write contract tests against temporary storage before wiring a new database into the API.

## API and dashboard extensions

Treat every request as untrusted and every rendered field as attacker-controlled. Extend Pydantic request/response models first, keep validation errors generic, HTML-escape rendered values, and avoid returning raw candidate outputs from list or dashboard views. EvalForge v0.1.0 has no authentication or tenant isolation; do not expose it to an untrusted network without an authenticated reverse proxy and deployment-specific controls.

## Extension verification checklist

- A focused test failed for the intended missing behavior before implementation.
- Focused and full tests pass without warnings.
- Ruff formatting/lint and strict Mypy pass.
- Public contracts and examples are updated.
- Security and privacy effects are documented.
- CI exports and persisted round trips remain valid.
- The wheel-installed `evalforge` command exercises the extension where applicable.
