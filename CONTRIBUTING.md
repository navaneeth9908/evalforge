# Contributing to EvalForge

EvalForge treats evaluation policy, evidence formats, and release decisions as security-sensitive code. Small, reviewable changes with executable evidence are preferred over broad rewrites.

## Development setup

Requirements:

- Python 3.11 or newer
- [`uv`](https://docs.astral.sh/uv/)
- Git
- Docker Desktop or another Docker engine only for container verification

Create the locked development environment:

```bash
uv sync --frozen --group dev
uv run evalforge --help
```

No provider credentials are required for the tests or synthetic demo. Real provider credentials must stay in environment variables and must never be committed.

## Test-driven changes

Behavior changes use a RED–GREEN–REFACTOR cycle:

1. **RED:** add the smallest test that expresses the desired public behavior and run it to observe the expected failure.
2. **GREEN:** implement only enough production code to pass that test.
3. **REFACTOR:** improve the design while keeping the focused and full suites green.

Bug fixes require a regression test that fails before the repair. Documentation-only changes should run the documentation contract tests.

## Required local checks

Run from the repository root:

```bash
uv lock --check
uv run --frozen --group dev ruff format --check .
uv run --frozen --group dev ruff check .
uv run --frozen --group dev mypy
uv run --frozen --group dev pytest --cov=evalforge --cov-branch --cov-report=term-missing --cov-fail-under=85
uv build
uv run --frozen --group dev evalforge demo --output-directory reports/demo
```

The demo is credential-free and writes ignored artifacts. Container checks are documented in the [deployment guide](docs/deployment.md).

## Pull requests

- Keep each pull request focused on one coherent behavior or hardening change.
- Explain the threat model impact for API, provider, parsing, persistence, or report changes.
- Include the failing and passing test commands in the description.
- Update schemas, examples, extension guidance, and the changelog when a public contract changes.
- Do not weaken finite input limits, fail-closed release gates, redaction, or deterministic identifiers without explicit rationale and adversarial tests.
- Use plain imperative commit subjects such as `add provider timeout coverage`.

Maintainers review staged changes for secrets, unsafe shell or SQL construction, path traversal, unbounded input, dependency changes, and generated artifacts before publication.

## Extending EvalForge

Read the [extension guide](docs/extensions.md) before adding providers, metrics, schemas, or persistence adapters. Public JSON changes require an explicit `schema_version`, compatibility tests, and a changelog entry.

## Reporting security issues

Do not open a public issue for a suspected vulnerability. Follow [SECURITY.md](SECURITY.md).
