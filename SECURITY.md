# Security Policy

## Supported version

EvalForge is under active development. Security fixes are applied to the latest commit on `main`.

## Reporting a vulnerability

Do not open a public issue for suspected vulnerabilities. Use GitHub's private vulnerability-reporting feature for this repository when available, or contact the maintainer through the address in `pyproject.toml`.

Include the affected version, reproduction steps, impact, and any suggested mitigation. Do not include real credentials, production prompts, customer records, or proprietary evaluation datasets.

## Trust boundaries

- Suite, output, report, sensitive-data-policy, repeated-observation, stability-policy, comparison-policy, grounding-evaluation, tool-trace, trajectory, and dataset-manifest files are untrusted input and must pass duplicate-key, strict schema, Unicode, finite-number, numeric-literal-length, size, nesting, node-count, and string-length validation. Release, stability, regression, and sensitive-data thresholds reject ambiguous or coercive controls; grounding claims require strict integer spans with complete answer coverage.
- User-controlled regular expressions use a constrained, length-limited syntax without repetition, grouping, alternation, or optional operators so matching work remains bounded by the input limits.
- Dataset manifests are metadata, not proof that a source is trustworthy or appropriately licensed; operators must verify claimed lineage.
- Evaluation and comparison reports can contain prompts, baseline and candidate model outputs, labels, dataset identifiers, and licenses; stability reports contain run labels and aggregate/per-case outcomes. Grounding reports omit answer, claim, retrieved-document content, and answer spans, but retain caller-supplied document and claim IDs plus a canonical input digest; protect identifiers and reports when they are sensitive or low entropy. Treat all reports as potentially sensitive artifacts. Full manifest source, revision, creator, and transformation lineage is deliberately excluded from reports.
- Sensitive-data findings retain only a zero-based scanned-string index, category, and severity. They never copy matched values, output IDs, JSON keys, or source text. Input and policy digests remain guessable for low-entropy data, so scan reports are not a confidentiality boundary.
- Detector categories and severity gates are fixed and bounded. Exact-match false-positive exceptions are configured as SHA-256 digests rather than plaintext; operators must restrict policy changes because allowlisting or disabling a category can suppress a release blocker.
- Provider credentials must come from runtime environment variables or an external secret manager.
- Offline deterministic evaluation requires no API keys or outbound network access.
- Future model-based judges and tool adapters must document network, data-retention, and prompt-injection boundaries before release.
