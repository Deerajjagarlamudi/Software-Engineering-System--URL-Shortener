# Software-Engineering-System--URL-Shortener
## Use the orchestration console
Bash path 
make setup
make demo
Open http://127.0.0.1:8000/console after starting the application.

1. Select a scenario:
   - **Greenfield:** Build a new URL-shortener service from a starter project.
   - **Brownfield:** Modify an existing service, such as adding link expiration.
   - **Ambiguous:** Convert an unclear requirement into measurable requirements before implementation.
2. Enter the requirement.
3. Click **Submit requirement**.
4. Select the new run under **Runs**.
5. Follow the workflow diagram as the agents complete each stage.

The workflow pauses at important human-review checkpoints:

- `architecture`
- `implementation`
- `release_readiness`
- `requirement` for ambiguous scenarios

When a stage shows `waiting_approval`, enter an approval rationale and click **Approve**. The workflow then continues automatically. Clicking **Reject** safely stops the run.

A successful run ends with the status `completed`.

## Understand the workflow

The system uses specialized agents to perform a governed software-development lifecycle:

```text
Requirement
    ↓
Architecture approval
    ↓
Planning
    ↓
Implementation approval
    ↓
Tests ─ Documentation ─ Security review
    ↓
Validation
    ↓
Release approval
    ↓
Completed
```

Tests, documentation, and security review run as parallel branches. Validation begins only after all three branches finish.

Every run records:

- Agent-generated artifacts
- Artifact versions and lineage
- Human approval decisions and rationales
- Git checkpoints and rollback activity
- Retries and failures
- Per-stage and end-to-end metrics
- A complete audit trail

Generated source code is applied only inside an isolated per-run Git sandbox. It is never automatically copied into the main project.

## Use mock mode without an API key

An Anthropic API key is not required for the assessment or demonstration.

Deterministic mock mode is enabled by default. It produces repeatable agent artifacts and allows the entire test suite and all scenarios to run offline.

To optionally use Claude-generated artifacts:

```bash
export LLM_PROVIDER=anthropic
export ANTHROPIC_API_KEY="your-key"
export LLM_MODEL="an-Anthropic-model-available-to-your-account"
uvicorn app.main:app --reload
```

Do not commit API keys or place them directly in source files.

## Verify the project

Run:

```bash
make test
```

The current test suite contains 29 passing tests and covers workflow execution, approval gates, retries, rollback, replanning, security policies, URL-shortener behavior, scenario demonstrations, and reliability metrics.

Additional checks:

```bash
make lint
make typecheck
```

## Current prototype boundaries

This is a submission-ready local prototype, not a public production platform.

- SQLite is used for local persistence.
- Workflow execution is synchronous.
- The console refreshes every four seconds.
- Authentication is not implemented.
- Mock agent mode is the assessed default.
- Redis, PostgreSQL deployment, distributed workers, SSE, and telemetry exporters are documented production extensions.
