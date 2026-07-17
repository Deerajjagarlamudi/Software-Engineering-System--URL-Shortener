# Final Engineering Summary

## Plan and rationale

The prototype combines a working URL shortener with a governed SDLC orchestrator. The
orchestrator is a persisted dependency graph rather than a linear chain so it can pause at
human gates, fan out independent work, synchronize validation, retry bounded failures,
roll back changes, and re-plan when the requirement changes.

Mock mode is deterministic and offline. Anthropic mode uses the same schema and policy path,
but obtains the artifact from Claude’s structured tool output. Generated changes are never
written to the primary repository.

## Rubric-to-evidence map

| Requirement | Evidence |
|---|---|
| Requirement understanding / ambiguity | Requirement artifact records ambiguities, assumptions, acceptance criteria, and approval before decomposition; `test_ambiguous_requires_requirement_approval` |
| Task decomposition | Planner artifact contains task IDs, dependencies, and baseline manifest impact analysis |
| Brownfield reasoning | Brownfield baseline contains regression tests; generated plan identifies impacted files and adds an expiry migration |
| Workflow orchestration | `engine.py` implements persisted DAG scheduling, parallel fan-out, synchronization, state transitions, and lineage |
| Human oversight | Architecture, implementation, release, and ambiguous-requirement gates record actor, rationale, and timestamp |
| Retry / rollback / safe stop | Chaos tests inject provider failure; stage checkpoints support bounded retry and rollback; rejection and cancellation safe-stop |
| Policy guardrails | Secret, destructive-pattern, traversal, and symlink checks run before generated changes are applied |
| Audit observability | Every transition, decision, retry, rollback, patch, artifact hash, and provider result is stored in the audit trail |
| Reliability metrics | Per-run `/metrics` plus aggregate `/api/v1/metrics` expose success, retry, rollback, MTTR, approval, and latency data |
| Dynamic replanning | `/replan` resets to baseline, preserves inactive superseded artifacts, and re-runs renewed gates |
| Engineering output | Greenfield, brownfield, and ambiguous workflows generate HTTP services, migrations, tests, and documentation in isolated workspaces |
| Controlled autonomy | Agents can generate artifacts, but the engine alone can apply high-impact changes after approval |

## Validation

The repository has unit tests for URL validation and persistence, engine tests for graph
semantics and governance, and HTTP end-to-end tests for all three scenarios. Each workflow’s
validation node runs the generated HTTP test suite inside its own Git workspace.

Run the complete quality gate with:

```bash
uv sync --all-extras
uv run ruff check app tests
uv run mypy app
uv run pytest -q --cov=app
```

## Risks and trade-offs

- Synchronous stepping is easier to inspect in an interview demo; a worker queue is the next production step.
- SQLite and the in-memory limiter are reproducible local defaults; use PostgreSQL/Redis adapters when scaling out.
- Polling keeps the console dependency-free; SSE would be appropriate for a long-running worker architecture.
- Raw referrer and user-agent values are retained only for the configured recent-event window and require a documented privacy policy in production.
- No authentication is included for the local reviewer console or APIs.
