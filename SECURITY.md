# Security policy

## Supported versions

| Version | Security updates |
|---|---|
| 0.2.x | Yes |
| 0.1.x | No |
| Earlier development snapshots | No |

Fixes are applied to the latest `0.2.x` release and `main`.

## Reporting a vulnerability

Please do not open a public GitHub issue for a suspected vulnerability. Use GitHub's private vulnerability reporting for this repository, or contact the maintainer at `navaneeththota410@gmail.com` with the subject `EvalForge security report` if private reporting is unavailable.

Include, when safe:

- the affected version or commit;
- the deployment model and required configuration;
- minimal reproduction steps;
- the expected and observed security impact; and
- whether credentials, private data, or public infrastructure may already be exposed.

Do not include live credentials, customer data, or destructive proof-of-concept payloads. You should receive an acknowledgement within seven days. The maintainer will validate scope, coordinate a fix and disclosure timeline, and credit reporters who request attribution.

## Security model

Read the [threat model](docs/threat-model.md) for assets, actors, data flows, trust boundaries, controls, and residual risks. In particular:

- EvalForge v0.2.0 is designed for a trusted local operator or private CI runner.
- The API and dashboard have no built-in authentication, authorization, tenant isolation, or TLS.
- The Compose example binds to `127.0.0.1`; do not change that for an untrusted network without an authenticated reverse proxy, TLS, request limits, and deployment-specific authorization.
- SQLite files, reports, JUnit, SARIF, prompts, and candidate output can contain sensitive business evidence. Protect them with operating-system and CI artifact permissions.
- Provider output and user-authored policies are untrusted input. Deterministic scans reduce risk but do not prove safety.

## Secrets and privacy

Provider credentials must be supplied only through environment variables. Never commit `.env` files, authorization headers, private keys, real customer prompts, candidate outputs, or raw sensitive-data matches. EvalForge intentionally emits category and location metadata instead of matched secrets in leakage reports.

If exposure is suspected, revoke the credential first, preserve only redacted evidence, and then report the incident privately.

## Dependency and release security

Release candidates require a frozen lockfile, test/lint/type/build gates, installed-wheel and synthetic acceptance smokes, staged secret/artifact review, independent code review, and green GitHub Actions on the exact commit. GitHub Actions are pinned to immutable commit SHAs and use least-privilege permissions.
