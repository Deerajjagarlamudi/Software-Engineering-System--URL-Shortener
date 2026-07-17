from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.orchestrator import engine, store, workspace

router = APIRouter(prefix="/api/v1/runs", tags=["orchestration"])


class CreateRunRequest(BaseModel):
    requirement: str = Field(..., min_length=5)
    scenario: str = Field("greenfield", pattern="^(greenfield|brownfield|ambiguous)$")
    chaos_nodes: list[str] = Field(default_factory=list,
                                   description="Nodes that fail on first attempt (demo/metrics)")


class ApprovalRequest(BaseModel):
    approved: bool
    rationale: str = Field(..., min_length=3)
    actor: str = "reviewer"


class RetryRequest(BaseModel):
    node_id: str


class ReplanRequest(BaseModel):
    requirement: str = Field(..., min_length=5)


def _load(run_id: str):
    run = store.load_run(run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    return run


def _view(run) -> dict:
    d = run.to_dict()
    d["latency_seconds"] = (run.finished_at or __import__("time").time()) - run.created_at
    return d


@router.post("", status_code=201)
def create_run(body: CreateRunRequest):
    run = engine.create_run(body.requirement, body.scenario, set(body.chaos_nodes))
    run = engine.step(run)
    return _view(run)


@router.get("")
def list_runs():
    return store.list_runs()


@router.get("/{run_id}")
def get_run(run_id: str):
    return _view(_load(run_id))


@router.get("/{run_id}/artifacts")
def get_artifacts(run_id: str):
    run = _load(run_id)
    return {aid: {
        "kind": a.kind, "node": a.node_id, "version": a.version, "hash": a.content_hash,
        "lineage": a.lineage, "content": a.content,
    } for aid, a in run.artifacts.items()}


@router.get("/{run_id}/audit")
def get_audit(run_id: str):
    _load(run_id)
    return store.audit_trail(run_id)


@router.get("/{run_id}/workspace")
def get_workspace(run_id: str):
    run = _load(run_id)
    if not run.workspace:
        return {"files": []}
    try:
        return {"files": workspace.list_files(run.workspace), "checkpoints": run.checkpoints}
    except Exception:
        return {"files": [], "checkpoints": run.checkpoints, "note": "workspace unavailable"}


@router.post("/{run_id}/approvals/{node_id}")
def decide(run_id: str, node_id: str, body: ApprovalRequest):
    run = _load(run_id)
    try:
        run = engine.approve(run, node_id, body.approved, body.rationale, body.actor)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return _view(run)


@router.post("/{run_id}/retry")
def retry(run_id: str, body: RetryRequest):
    run = _load(run_id)
    try:
        run = engine.retry_node(run, body.node_id)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return _view(run)


@router.post("/{run_id}/replan")
def replan(run_id: str, body: ReplanRequest):
    run = _load(run_id)
    return _view(engine.replan(run, body.requirement))


@router.post("/{run_id}/cancel")
def cancel(run_id: str):
    run = _load(run_id)
    return _view(engine.cancel(run, "cancelled via API"))


@router.get("/{run_id}/metrics")
def metrics(run_id: str):
    run = _load(run_id)
    nodes = run.nodes.values()
    completed = sum(1 for n in nodes if n.status.value == "completed")
    return {
        "run_id": run.run_id,
        "status": run.status.value,
        "node_success_rate": completed / len(run.nodes),
        "retries": run.metrics.get("retries", 0),
        "rollbacks": run.metrics.get("rollbacks", 0),
        "failures": run.metrics.get("failures", 0),
        "approvals": run.metrics.get("approvals", 0),
        "end_to_end_latency_s": (run.finished_at or __import__("time").time()) - run.created_at,
        "per_node": {
            n.node_id: {
                "status": n.status.value, "attempts": n.attempts,
                "duration_s": (n.finished_at - n.started_at) if n.started_at and n.finished_at else None,
            } for n in nodes
        },
    }
