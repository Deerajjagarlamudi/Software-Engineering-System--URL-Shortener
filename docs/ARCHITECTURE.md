# Architecture

## Components

```
┌─────────────────────────────────────────────────────────────┐
│ FastAPI app (app/main.py)                                    │
│                                                               │
│  ┌──────────────────┐   ┌──────────────────────────────────┐ │
│  │ URL shortener     │   │ Orchestration layer               │ │
│  │  api.py           │   │  api.py     REST + governance     │ │
│  │  service.py       │   │  engine.py  DAG execution         │ │
│  │  models.py        │   │  agents.py  SDLC agent roles      │ │
│  │  (SQLAlchemy,     │   │  adapters.py mock / Anthropic     │ │
│  │   SQLite/Postgres)│   │  policies.py guardrails           │ │
│  └──────────────────┘   │  workspace.py git sandbox          │ │
│                          │  store.py   run + audit persist   │ │
│  /console  review UI     └──────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

## Orchestration model

Every run instantiates this dependency graph (`engine.build_graph`):

```
requirement ──► architecture ──► plan ──► implementation ──┬─► test_generation ──┐
   [gate*]        [gate]                     [gate]         ├─► documentation ────┼─► validation ─► release_readiness
                                                            └─► security_review ──┘                    [gate]
* requirement gate active for ambiguous scenarios
```

- **Non-linear, stateful execution.** Nodes become READY when all dependencies are COMPLETED and approved. Independent nodes (tests/docs/security) run **in parallel** in a thread pool and **synchronize** at the validation gate. All state is persisted to SQLite after every step; a run survives process restarts.
- **Entry/exit gates.** Entry: dependency + approval check (`_ready_nodes`). Exit: schema validation of agent output (Pydantic), policy guardrails on generated files, and for the validation node an actual `pytest` execution inside the sandbox.
- **Human approval checkpoints.** Architecture acceptance, patch application, and release readiness always require an approval with a recorded rationale and actor. Ambiguous requirements add a gate after requirement analysis so assumptions are signed off before decomposition. Rejection triggers a safe stop.
- **Bounded retries / fallback / rollback / safe-stop.** Each node has a retry budget (default 2). Provider failures and validation failures consume it; exhaustion rolls the git workspace back to the last known-good checkpoint and fails the run. Policy violations stop immediately (no retry). Cancellation is available at any time.
- **Context and decision lineage.** Every artifact records its producing agent, upstream artifact IDs, version, and content hash. Downstream agents receive upstream artifacts as context.
- **Dynamic re-planning.** `POST /runs/{id}/replan` with a changed requirement invalidates only the transitive descendants of the requirement node and re-executes that portion of the graph — approvals are re-required.
- **Sandboxed change management.** Generated code never touches the primary repository. Each run gets a temporary git repo; approved patches are committed there, each commit is a rollback checkpoint, and path traversal is blocked.
- **Audit-grade observability.** Every transition, approval decision, retry, rollback, policy outcome, and artifact hash is an immutable audit event. Reliability metrics per run: node success rate, retries, rollbacks, failures, approvals, per-node attempts/duration, end-to-end latency.

## Key decisions

| Decision | Rationale |
|---|---|
| Hand-rolled DAG engine over LangGraph/Airflow | ~300 lines, fully inspectable, demonstrates understanding rather than framework wiring; no heavyweight deps |
| Deterministic mock adapter as default | Reproducible demos and CI without API keys; same schema-validation path as real providers |
| Synchronous engine stepping (advance until blocked) | Approval gates make runs naturally pause; avoids background-worker complexity for a prototype |
| Git as the checkpoint/rollback mechanism | Battle-tested, auditable, and mirrors real change management |
| Pydantic schemas between agents and state | Malformed agent output is rejected at the boundary, never persisted |
| SQLite + repository pattern | Zero-setup demo; DATABASE_URL swap to PostgreSQL preserved |
| 307 redirects | Preserves method; avoids permanent caching during rollout |

## Security posture

- URL scheme allowlist (http/https), length caps, reserved/validated aliases
- Secret scanning and destructive-pattern blocking on all generated code (`policies.py`)
- Workspace path-traversal protection; allowlisted commands only (git, python, pytest)
- Approval required before any generated change is applied
- Request IDs on every API response for traceability
