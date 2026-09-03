# Architecture

EvalForge starts with a deterministic, offline release gate and expands toward a vendor-neutral evaluation control plane. Documentation distinguishes implemented behavior from planned components.

## Implemented baseline

```mermaid
flowchart LR
    S[Versioned suite JSON] --> V[Pydantic validation]
    O[Candidate outputs JSON] --> V
    V --> E[Deterministic evaluation engine]
    E --> R[Case-level evidence]
    R --> G[Minimum pass-rate gate]
    G --> J[JSON report]
    G --> X[Process exit status]
```

The current engine performs normalized exact matching. It validates non-empty suites, unique case IDs, one output per registered case, and a finite release threshold between zero and one. Reports preserve input case order and include expected and actual values for auditability.

## Target architecture

```mermaid
flowchart TB
    subgraph Inputs
      D[Versioned datasets]
      C[Candidate adapters]
      T[Tool and agent traces]
    end
    subgraph Execution
      Q[Bounded runner]
      M[Metric registry]
      J[Structured judge]
      S[Safety evaluators]
    end
    subgraph Control
      P[Release policy]
      B[Baseline comparison]
      H[Human review]
    end
    subgraph Evidence
      DB[(Run registry)]
      OT[OpenTelemetry]
      API[FastAPI]
      UI[Dashboard]
      CI[CI reports]
    end
    Inputs --> Execution --> Control --> Evidence
```

## Design boundaries

- **Deterministic by default:** core tests and examples make no network calls.
- **Fail closed:** missing evidence, invalid thresholds, and mismatched case IDs stop evaluation.
- **Evidence before verdict:** aggregate decisions retain ordered case-level results.
- **Provider isolation:** future model adapters remain outside the metric and policy core.
- **No secret persistence:** credentials are supplied at runtime and never written to reports.
- **Reproducibility:** schemas, dataset hashes, candidate metadata, and tool versions will identify every run.

See [ROADMAP.md](../ROADMAP.md) for implementation status.
