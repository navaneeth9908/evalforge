# Deployment guide

EvalForge v0.2.0 supports a single-process local/private deployment without built-in access control. Current development builds add opt-in bearer authentication, role authorization, and tenant-scoped suite, evaluation, run, report, and dashboard access. TLS, credential lifecycle management, encrypted storage, and distributed scheduling remain external or incomplete. Read the [threat model](threat-model.md) before exposing any endpoint beyond loopback.

## Local server

Install the frozen environment and choose a writable database path:

```bash
uv sync --frozen --group dev
# Replace this example with an absolute writable path on your machine.
EVALFORGE_DATABASE_PATH='C:/Users/you/evalforge/artifacts/evalforge.db' \
uv run --frozen uvicorn --factory evalforge.server:create_app_from_environment \
  --host 127.0.0.1 --port 8000
```

Verify health and the dashboard from another shell:

```bash
uv run --frozen evalforge-smoke \
  --base-url http://127.0.0.1:8000 --timeout-seconds 5
```

Open `http://127.0.0.1:8000/dashboard` for the read-only run summary. Runs link to `/dashboard/trends`, which revalidates and renders at most 20 exact-suite, exact-lineage unique records with adjacent `/dashboard/compare` evidence. Comparison rows link to filtered histories; use exactly one of the `?metric=`, `?category=`, or `?tag=` selectors alongside `run_id`. Metric filters show weighted mean case scores, while category and tag filters show weighted slice pass rates. Unknown dimensions and ambiguous selector combinations fail closed. Identical deterministic reruns appear once, and “Recorded” is the tenant-membership time rather than an execution-attempt time. Comparison renders bounded descriptive regression and slice evidence only after verifying full compatibility, and case rows link to `/dashboard/cases` for one redacted score, threshold, dimension, and optional resource-evidence view. These pages omit prompt, candidate, expected, and normalized metric text. The OpenAPI document is at `/openapi.json`.

## Container image

Build and run the image with a named volume:

```bash
docker build -t evalforge:local .
docker run --rm \
  --name evalforge \
  -p 127.0.0.1:8000:8000 \
  -e EVALFORGE_DATABASE_PATH=/var/lib/evalforge/evalforge.db \
  -v evalforge-data:/var/lib/evalforge \
  evalforge:local
```

The image installs the project wheel into an isolated virtual environment, runs as the unprivileged `evalforge` user, exposes port 8000, and includes a bounded health check. The database directory—not source or credentials—is the intended writable volume.

## Docker Compose

```bash
docker compose up --build --wait
docker compose ps
docker compose down
```

`compose.yaml` binds `127.0.0.1:8000` and stores the SQLite database in the `evalforge-data` volume. `docker compose down` preserves the named volume; add `--volumes` only when intentionally deleting local evidence.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `EVALFORGE_DATABASE_PATH` | `/var/lib/evalforge/evalforge.db` | Absolute writable SQLite file used by the API |
| `EVALFORGE_ACCESS_CREDENTIALS` | unset | Optional JSON array of bearer credentials with `token`, `tenant_id`, and `role` (`reader`, `editor`, or `admin`) |

When `EVALFORGE_ACCESS_CREDENTIALS` is unset, EvalForge preserves the trusted local v0.2.0 mode. When
it is set, it must be a non-empty duplicate-key-free JSON array with at most 100 entries and no
more than 16 levels of JSON nesting. Tokens must be 32–512 visible ASCII characters without
whitespace, tenant IDs use lowercase letters, digits, and internal hyphens, and duplicate tokens fail
startup. The process removes the configuration from its environment and retains only SHA-256 token
digests in the initialized application. Factory initialization is serialized; repeated calls reuse that
application, and attempts to change its database or access credentials are rejected so a consumed
credential value cannot silently downgrade authentication. Failed initialization remains fail-closed
for the process lifetime. Inject this value
from a deployment secret manager; do not commit it, place it in Compose files, or pass it on a command
line. Health remains public. Suite, evaluation, run, report, and dashboard routes require a bearer
token; readers can inspect evidence, while editors and admins can also create suites and evaluations.

Suite membership is scoped to the authenticated principal's tenant. Suite totals and pagination are
filtered before response construction, and evaluation treats a foreign suite digest exactly like an
absent digest. Canonical suite evidence remains globally content-addressed and may be independently
registered by more than one tenant; each tenant receives its own membership timestamp. Existing
schema-v1 suites migrate only to an internal trusted-local namespace, never to a configured tenant.
Switching access modes does not transfer ownership, and rolling an upgraded database back to an
older binary is unsupported without restoring a pre-migration backup.

Run membership is also scoped to the authenticated tenant. Run pages, direct run and report reads,
and dashboard rows filter through immutable tenant-to-run memberships before response construction.
Foreign run IDs receive the same not-found envelopes as absent IDs. Identical content-addressed run
evidence can be independently evaluated by multiple tenants without transferring membership or its
timestamp. Existing schema-v2 runs migrate only to the trusted-local namespace.

The pairwise comparison route is read-only and available to reader, editor, and admin roles. Both
run IDs are resolved through tenant membership before compatibility checks. Visible runs must share
the exact suite, dataset lineage, evaluator semantics, and canonicalization version. The view shows
descriptive deltas rather than a comparison-policy release verdict and caps case, category, and tag
tables at 100 rows each. Metric and slice names link to a tenant-scoped trend projection. Trend
authorization resolves the anchor before interpreting filters, preserves the 20-point cap, and
revalidates every stored report before deriving the selected score.

The case-evidence route is also read-only. It resolves the run through tenant membership, revalidates
the complete persisted report, and returns the same generic not-found envelope for foreign runs,
absent runs, and absent cases. It renders one bounded metadata projection and never carries prompt,
candidate, expected, or normalized metric text into the HTML renderer.

Provider generation is a separate CLI path. Its environment variables are documented in the [README](../README.md); the service does not require provider credentials for evaluation or the synthetic acceptance demo.

## Production boundary

If an operator chooses to place EvalForge behind a shared network, the surrounding platform must provide:

- TLS termination and authenticated identity (or the opt-in bearer mode for a private deployment);
- credential issuance, rotation, revocation, and tenant lifecycle controls;
- request rate, concurrency, and body-size limits at the edge;
- egress restrictions for provider access;
- database and artifact backup, encryption, retention, and access controls;
- structured log redaction and monitoring; and
- a deployment-specific recovery and incident response procedure.

The repository does not claim those controls are built into v0.2.0. SQLite is appropriate for a trusted local process and CI evidence, not a horizontally scaled multi-writer service.

## Verification

The release contract tests image metadata, the server entrypoint, health behavior, Compose loopback binding, and the smoke command. A real container runtime smoke should also run before deployment:

```bash
docker build -t evalforge:local .
docker compose up --build --wait
docker compose exec -T evalforge \
  evalforge-smoke --base-url http://127.0.0.1:8000 --timeout-seconds 5
docker compose down
```

When Docker is unavailable, report that runtime check as unavailable rather than claiming it passed; the test suite validates contracts but cannot prove the local daemon or host networking.
