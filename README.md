# Agentic URL Shortener

A URL shortener service built and governed by an **agentic SDLC orchestration layer**. Submit a requirement; specialized agents (requirement analysis, architecture, planning, coding, testing, documentation, security, release) traverse a persisted dependency graph with human approval gates, bounded retries, sandboxed patch application, rollback, and a full audit trail.

## Quick start (no API key needed — deterministic mock mode is the default)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Open:
- **Console:** http://localhost:8000/console — submit requirements, watch the DAG, approve gates
- **API docs (OpenAPI):** http://localhost:8000/docs

Or with Docker:

```bash
docker compose up --build
```

## Run the tests

```bash
pytest -q            # 20 tests: unit, engine, e2e scenarios via HTTP API
```

## Try the three assessment scenarios

From the console (`/console`) pick a scenario and submit, or via curl:

```bash
# Greenfield
curl -sX POST localhost:8000/api/v1/runs -H 'Content-Type: application/json' \
  -d '{"requirement":"Build a URL shortener with analytics","scenario":"greenfield"}'

# Brownfield (impact analysis + expiry enhancement)
curl -sX POST localhost:8000/api/v1/runs -H 'Content-Type: application/json' \
  -d '{"requirement":"Add link expiration; 410 for expired; no regressions","scenario":"brownfield"}'

# Ambiguous (pauses for human sign-off on assumptions)
curl -sX POST localhost:8000/api/v1/runs -H 'Content-Type: application/json' \
  -d '{"requirement":"make shortened links secure and reliable","scenario":"ambiguous"}'

# Chaos demo: inject a provider failure to see bounded retry + metrics
curl -sX POST localhost:8000/api/v1/runs -H 'Content-Type: application/json' \
  -d '{"requirement":"Build URL shortener","scenario":"greenfield","chaos_nodes":["security_review"]}'
```

Runs pause at approval gates. Approve via console buttons or:

```bash
curl -sX POST localhost:8000/api/v1/runs/<run_id>/approvals/architecture \
  -H 'Content-Type: application/json' \
  -d '{"approved":true,"rationale":"design reviewed","actor":"me"}'
```

Reproducible scenario inputs and expected outcomes: [`scenarios/scenarios.json`](scenarios/scenarios.json).

## Real LLM mode

```bash
export LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=sk-...
```

Mock mode remains the default so every demo and test is reproducible offline.

## Key endpoints

| Endpoint | Purpose |
|---|---|
| `POST /api/v1/links`, `GET /{code}`, `GET /api/v1/links/{code}/analytics`, `DELETE /api/v1/links/{code}` | URL shortener |
| `POST /api/v1/runs` | Submit a requirement (scenario: greenfield/brownfield/ambiguous) |
| `GET /api/v1/runs/{id}` / `/artifacts` / `/audit` / `/metrics` / `/workspace` | Run state, lineage, audit trail, reliability metrics, sandbox contents |
| `POST /api/v1/runs/{id}/approvals/{node}` / `/retry` / `/replan` / `/cancel` | Human governance controls |

## Documentation

- [Architecture and orchestration model](docs/ARCHITECTURE.md)
- [Engineering summary — rubric-to-evidence map, risks, trade-offs, limitations](docs/ENGINEERING_SUMMARY.md)
