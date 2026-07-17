# Final Engineering Summary

## Plan and rationale

The assignment has two deliverables in one: a production-style URL shortener and — the differentiator — an agentic orchestration layer that runs the SDLC for it under governance. I built the orchestrator as a first-class, persisted DAG engine rather than a linear chain, because the rubric's hardest requirements (re-planning, parallel synchronization, approval gates, rollback) all require explicit graph state.

The system defaults to a deterministic mock LLM adapter so every scenario, test, and metric is reproducible offline; a real Anthropic adapter is one environment variable away and flows through the same schema-validation and policy guardrails.

## Rubric-to-evidence map

| Requirement | Evidence |
|---|---|
| Requirement understanding / ambiguity | `agents.py::_mock_requirement` detects unmeasurable terms, records assumptions; ambiguous runs pause for sign-off (`test_ambiguous_requires_requirement_approval`) |
| Task decomposition | Planner artifact with task IDs + dependencies; brownfield includes impacted-module impact analysis (`test_brownfield_scenario`) |
| Codebase reasoning (brownfield) | Plan artifact lists impacted modules/APIs; regression tests generated alongside the change |
| Workflow orchestration | `engine.py`: dependency graph, entry/exit gates, parallel fan-out + synchronization (`test_parallel_branch_synchronization`), persisted state, decision lineage on every artifact |
| Human approval checkpoints | Architecture, patch application, release gates; rationale + actor recorded; rejection = safe stop (`test_approval_rejection_is_safe_stop`) |
| Bounded retries / rollback / safe-stop | Retry budget per node, git rollback on exhaustion, cancel endpoint (`test_chaos_retry_and_metrics`) |
| Policy guardrails | `policies.py` secret + destructive-pattern scanning; violations halt without retry (`test_policy_guardrails`) |
| Audit observability | Immutable audit events for every transition/decision; `/audit` endpoint; artifact content hashes |
| Reliability metrics | `/metrics`: success rate, retries, rollbacks, approvals, per-node latency, end-to-end latency; chaos injection makes them non-trivial |
| Dynamic re-planning | `/replan` invalidates only downstream nodes and re-executes (`test_replan_invalidates_downstream_only`) |
| Engineering output quality | Typed Python, layered modules, 20 passing tests (unit/engine/e2e), OpenAPI docs |
| Controlled autonomy | Agents execute; humans own approvals — enforced by the engine, not convention |

## Validation approach

Three levels: unit tests on domain logic (aliases, expiry, collisions, idempotency, policies), engine tests on orchestration semantics (gates, retries, rollback, re-planning, persistence round-trip), and end-to-end scenario tests through the HTTP API using the reproducible inputs in `scenarios/scenarios.json`. The validation node in each run additionally executes the *generated* test suite inside the run's sandbox — the workflow proves its own output.

## Risks and trade-offs

- **Synchronous stepping:** runs execute in-request and pause at gates. Simple and debuggable; a production system would use a worker queue. Chosen deliberately for prototype clarity.
- **Mock-first agents:** mock outputs are deterministic templates. This trades generative variety for reproducibility; the adapter seam and shared schema validation mean real-LLM output takes the identical governed path.
- **Rollback granularity:** rollback resets the sandbox to the initial checkpoint on retry exhaustion. Finer-grained (per-stage) restoration is a straightforward extension since every commit is a checkpoint.
- **Single-process SQLite:** appropriate for the demo; repository pattern and DATABASE_URL keep the PostgreSQL path open.
- **Rate limiting:** designed (architecture artifact) but not enforced in middleware — noted as a limitation rather than half-implemented.

## Assumptions

- Local-first deployment; containers provided, cloud optional.
- Generated changes are only ever applied to per-run sandboxes; promotion to a real repo would sit behind the release approval gate.
- The console is a reviewer tool, not an end-user product.

## Limitations

- No SSE streaming (console polls every 4s), no OpenTelemetry/Prometheus exporters (metrics are exposed as JSON; exporters are additive).
- Real-LLM mode is wired but responses are not yet parsed into role schemas per-provider; mock mode is the assessed path.
- No authentication on the console/APIs — out of scope for a local prototype, required before any deployment.
