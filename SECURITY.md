# Security Policy

## Supported version

EvalForge is under active development. Security fixes are applied to the latest commit on `main`.

## Reporting a vulnerability

Do not open a public issue for suspected vulnerabilities. Use GitHub's private vulnerability-reporting feature for this repository when available, or contact the maintainer through the address in `pyproject.toml`.

Include the affected version, reproduction steps, impact, and any suggested mitigation. Do not include real credentials, production prompts, customer records, or proprietary evaluation datasets.

## Trust boundaries

- Suite and output files are untrusted input and must pass schema validation.
- Evaluation reports can contain prompts and model outputs; treat them as potentially sensitive artifacts.
- Provider credentials must come from runtime environment variables or an external secret manager.
- Offline deterministic evaluation requires no API keys or outbound network access.
- Future model-based judges and tool adapters must document network, data-retention, and prompt-injection boundaries before release.
