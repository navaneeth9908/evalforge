# Deployment guide

EvalForge v0.1.0 supports a single-process local/private deployment. It does not include authentication, TLS, tenant isolation, or distributed scheduling. Read the [threat model](threat-model.md) before exposing any endpoint beyond loopback.

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

Open `http://127.0.0.1:8000/dashboard` for the read-only run summary. The OpenAPI document is at `/openapi.json`.

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

Provider generation is a separate CLI path. Its environment variables are documented in the [README](../README.md); the service does not require provider credentials for evaluation or the synthetic acceptance demo.

## Production boundary

If an operator chooses to place EvalForge behind a shared network, the surrounding platform must provide:

- TLS termination and authenticated identity;
- authorization for suites, runs, reports, and the dashboard;
- request rate, concurrency, and body-size limits at the edge;
- egress restrictions for provider access;
- database and artifact backup, encryption, retention, and access controls;
- structured log redaction and monitoring; and
- a deployment-specific recovery and incident response procedure.

The repository does not claim those controls are built into v0.1.0. SQLite is appropriate for a trusted local process and CI evidence, not a horizontally scaled multi-writer service.

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
