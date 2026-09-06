# Security Policy

## Supported version

EvalForge is under active development. Security fixes are applied to the latest commit on `main`.

## Reporting a vulnerability

Do not open a public issue for suspected vulnerabilities. Use GitHub's private vulnerability-reporting feature for this repository when available, or contact the maintainer through the address in `pyproject.toml`.

Include the affected version, reproduction steps, impact, and any suggested mitigation. Do not include real credentials, production prompts, customer records, or proprietary evaluation datasets.

## Trust boundaries

- Suite, output, repeated-observation, stability-policy, comparison-policy, and dataset-manifest files are untrusted input and must pass duplicate-key, strict schema, Unicode, finite-number, numeric-literal-length, size, nesting, node-count, and string-length validation. Release, stability, and regression thresholds reject coercive types; suite policies reject ambiguous legacy/declarative combinations.
- User-controlled regular expressions use a constrained, length-limited syntax without repetition, grouping, alternation, or optional operators so matching work remains bounded by the input limits.
- Dataset manifests are metadata, not proof that a source is trustworthy or appropriately licensed; operators must verify claimed lineage.
- Evaluation and comparison reports can contain prompts, baseline and candidate model outputs, labels, dataset identifiers, and licenses; stability reports contain run labels and aggregate/per-case outcomes. Treat all reports as potentially sensitive artifacts. Full manifest source, revision, creator, and transformation lineage is deliberately excluded from reports.
- Provider credentials must come from runtime environment variables or an external secret manager.
- Offline deterministic evaluation requires no API keys or outbound network access.
- Future model-based judges and tool adapters must document network, data-retention, and prompt-injection boundaries before release.
