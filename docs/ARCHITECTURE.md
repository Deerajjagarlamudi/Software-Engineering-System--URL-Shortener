# Architecture

## Components

```text
FastAPI application
├── URL shortener API → domain service → LinkRepository → SQLAlchemy/SQLite
├── Orchestration API → persisted DAG engine → SQLite run/audit store
├── Agent adapters → deterministic mock or Anthropic tool-use output
├── Policy scanner → isolated scenario Git workspace → pytest validation
└── Reviewer console → branched DAG, approvals, artifacts, audit, metrics
```

## Workflow graph

```text
requirement [ambiguous gate]
       ↓
architecture [approval]
       ↓
plan
       ↓
implementation [approval + patch commit]
       ├──────── test_generation ───┐
       ├──────── documentation ────┼── validation → release_readiness [approval]
       └──────── security_review ───┘
```

Nodes are persisted and become runnable only when dependencies and prior approvals
are complete. The three fan-out nodes execute concurrently; their file artifacts are
integrated in one deterministic commit before validation runs `pytest` inside the
scenario workspace.

## Governance and state

- Every artifact is Pydantic-validated, hashed, versioned, and linked to upstream lineage.
- Generated writes and deletes are policy-scanned and applied only in a temporary Git workspace.
- Approval gates record actor, rationale, and timestamp; rejection is a terminal safe stop.
- Each mutating stage has a checkpoint. Retry exhaustion rolls back to that stage’s checkpoint.
- Replanning resets the workspace to the scenario baseline, marks old artifacts inactive,
  increments versions, preserves supersession links, and requires renewed approvals.
- A process restart marks interrupted running nodes `recovery_required`; execution resumes
  only through `POST /api/v1/runs/{id}/resume`.

## Agent execution

`LLMAdapter.generate(AgentRequest, output_schema)` is the only agent boundary.
Mock mode calls deterministic scenario generators. Anthropic mode supplies each Pydantic
schema as a forced `submit_artifact` tool and validates the returned `tool_use.input` before
policy evaluation. Provider metadata includes model, latency, prompt version, token counts,
and a response hash.

## Reliability and security

- Per-run and aggregate metrics expose success rate, retries, rollbacks, approvals, MTTR,
  and latency percentiles.
- URL validation allows only credential-free HTTP(S) targets, rejects control characters,
  and enforces future expiry timestamps.
- Link creation has a configurable single-process rate limiter; Redis or gateway enforcement
  is the production extension for multiple workers.
- Workspace paths are checked for traversal and symlink escapes; only `python` and `pytest`
  are allowlisted for generated validation commands.
- Secret and destructive-pattern scanning runs before any generated file reaches the workspace.

## Deliberate prototype limits

Execution is synchronous and the console polls every four seconds. The default persistence
is SQLite, console/API authentication is not included, and metrics are JSON rather than
Prometheus/OpenTelemetry exporters. These are explicit local-prototype boundaries.
