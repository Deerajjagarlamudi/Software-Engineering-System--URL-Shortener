"""DAG execution engine.

Non-linear, stateful execution: nodes become READY when all dependencies are
COMPLETED (and approved, where required). Independent READY nodes execute in
parallel and synchronize at the validation gate. Execution pauses at human
approval checkpoints and resumes via the approvals API. Bounded retries,
git-checkpoint rollback, safe-stop, artifact lineage, audit trail, and
metrics are handled here.
"""
from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.orchestrator import store, workspace
from app.orchestrator.adapters import LLMAdapter, ProviderError, get_adapter
from app.orchestrator.agents import AgentOutputError, run_agent
from app.orchestrator.models import (
    Artifact, Node, NodeStatus, RunStatus, WorkflowRun, new_id,
)
from app.orchestrator.policies import PolicyViolation

APPROVAL_GATES = {"architect", "coder", "release"}  # high-impact stages
PARALLEL_WORKERS = 4


def build_graph(scenario: str) -> dict[str, Node]:
    """Standard SDLC graph. The requirement node also gates on approval for
    ambiguous scenarios (assumptions must be signed off before decomposition)."""
    req_approval = scenario == "ambiguous"
    nodes = [
        Node("requirement", agent="requirement", approval_required=req_approval),
        Node("architecture", agent="architect", depends_on=["requirement"], approval_required=True),
        Node("plan", agent="planner", depends_on=["architecture"]),
        Node("implementation", agent="coder", depends_on=["plan"], approval_required=True),
        # Parallel branch: tests, docs, security fan out after implementation
        Node("test_generation", agent="tester", depends_on=["implementation"]),
        Node("documentation", agent="doc", depends_on=["implementation"]),
        Node("security_review", agent="security", depends_on=["implementation"]),
        # Synchronization point
        Node("validation", agent="validation",
             depends_on=["test_generation", "documentation", "security_review"]),
        Node("release_readiness", agent="release", depends_on=["validation"], approval_required=True),
    ]
    return {n.node_id: n for n in nodes}


def create_run(requirement: str, scenario: str, chaos_nodes: set[str] | None = None) -> WorkflowRun:
    run = WorkflowRun(run_id=new_id("run"), requirement=requirement, scenario=scenario)
    run.nodes = build_graph(scenario)
    run.workspace = workspace.create_workspace(run.run_id)
    run.checkpoints.append(workspace.head(run.workspace))
    run.context["chaos_nodes"] = sorted(chaos_nodes or [])
    store.save_run(run)
    store.audit(run.run_id, "run_created", {"scenario": scenario, "requirement": requirement})
    return run


def _adapter_for(run: WorkflowRun) -> LLMAdapter:
    return get_adapter(chaos_nodes=set(run.context.get("chaos_nodes", [])))


def _ready_nodes(run: WorkflowRun) -> list[Node]:
    ready = []
    for node in run.nodes.values():
        if node.status not in (NodeStatus.PENDING, NodeStatus.INVALIDATED):
            continue
        deps_ok = all(
            run.nodes[d].status == NodeStatus.COMPLETED
            and (not run.nodes[d].approval_required
                 or (run.nodes[d].approval or {}).get("approved"))
            for d in node.depends_on
        )
        if deps_ok:
            ready.append(node)
    return ready


def _execute_node(run: WorkflowRun, node: Node) -> None:
    node.attempts += 1
    node.status = NodeStatus.RUNNING
    node.started_at = time.time()
    store.audit(run.run_id, "node_started", {"node": node.node_id, "attempt": node.attempts})
    content = run_agent(node.agent, run, node, _adapter_for(run))

    lineage = [run.nodes[d].artifact_id for d in node.depends_on if run.nodes[d].artifact_id]
    artifact = Artifact(
        artifact_id=new_id("art"), node_id=node.node_id, kind=node.agent,
        content=content, produced_by=node.agent, lineage=lineage,
        content_hash=hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()[:16],
    )
    run.artifacts[artifact.artifact_id] = artifact
    node.artifact_id = artifact.artifact_id
    node.finished_at = time.time()
    node.status = NodeStatus.WAITING_APPROVAL if node.approval_required else NodeStatus.COMPLETED
    kind = "approval_requested" if node.approval_required else "node_completed"
    store.audit(run.run_id, kind, {"node": node.node_id, "artifact": artifact.artifact_id,
                                   "hash": artifact.content_hash})


def _handle_failure(run: WorkflowRun, node: Node, err: Exception) -> None:
    node.error = str(err)[:500]
    run.metrics["failures"] += 1
    if isinstance(err, PolicyViolation):
        node.status = NodeStatus.FAILED
        run.status = RunStatus.FAILED
        store.audit(run.run_id, "policy_violation", {"node": node.node_id, "error": node.error})
        return
    if node.attempts <= node.max_retries:
        node.status = NodeStatus.PENDING  # bounded retry
        run.metrics["retries"] += 1
        store.audit(run.run_id, "retry_scheduled",
                    {"node": node.node_id, "attempt": node.attempts, "error": node.error})
        return
    # Retry budget exhausted: roll back to last checkpoint, fail the run safely.
    node.status = NodeStatus.FAILED
    if run.checkpoints and run.workspace:
        workspace.rollback(run.workspace, run.checkpoints[0])
        run.metrics["rollbacks"] += 1
        store.audit(run.run_id, "rollback", {"to": run.checkpoints[0], "node": node.node_id})
    run.status = RunStatus.FAILED
    store.audit(run.run_id, "run_failed", {"node": node.node_id, "error": node.error})


def step(run: WorkflowRun) -> WorkflowRun:
    """Advance the run until it blocks (approval), fails, or completes."""
    while run.status == RunStatus.RUNNING:
        ready = _ready_nodes(run)
        if not ready:
            statuses = {n.status for n in run.nodes.values()}
            if NodeStatus.WAITING_APPROVAL in statuses:
                run.status = RunStatus.WAITING_APPROVAL
            elif all(s == NodeStatus.COMPLETED for s in statuses):
                run.status = RunStatus.COMPLETED
                run.finished_at = time.time()
                store.audit(run.run_id, "run_completed", {"metrics": run.metrics})
            elif NodeStatus.RUNNING not in statuses:
                # nothing ready, nothing running, not all complete -> stuck/failed
                if run.status == RunStatus.RUNNING:
                    run.status = RunStatus.FAILED
                    store.audit(run.run_id, "run_failed", {"reason": "no progress possible"})
            break

        with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as pool:
            futures = {pool.submit(_execute_node, run, node): node for node in ready}
            for fut in as_completed(futures):
                node = futures[fut]
                try:
                    fut.result()
                except (ProviderError, AgentOutputError, PolicyViolation, Exception) as e:
                    _handle_failure(run, node, e)
        # Sequentially land generated test/doc files in the sandbox (git is not
        # safe under concurrent commits) so the validation gate can execute them.
        for node in ready:
            if node.status == NodeStatus.COMPLETED and node.agent in ("tester", "doc"):
                _apply_generated_files(run, node)
        store.save_run(run)
    store.save_run(run)
    return run


def approve(run: WorkflowRun, node_id: str, approved: bool, rationale: str, actor: str) -> WorkflowRun:
    node = run.nodes.get(node_id)
    if node is None or node.status != NodeStatus.WAITING_APPROVAL:
        raise ValueError(f"node {node_id} is not awaiting approval")
    node.approval = {"approved": approved, "rationale": rationale, "actor": actor, "at": time.time()}
    run.metrics["approvals"] += 1
    store.audit(run.run_id, "approval_decision",
                {"node": node_id, "approved": approved, "rationale": rationale, "actor": actor})
    if not approved:
        node.status = NodeStatus.FAILED
        run.status = RunStatus.CANCELLED  # safe stop on rejection
        store.audit(run.run_id, "safe_stop", {"reason": f"approval rejected at {node_id}"})
        store.save_run(run)
        return run

    node.status = NodeStatus.COMPLETED
    # Post-approval side effects: apply approved patches to the sandbox.
    if node.agent in ("coder",) and node.artifact_id:
        files = run.artifacts[node.artifact_id].content.get("files", {})
        sha = workspace.apply_files(run.workspace, files, f"apply {node_id}")
        run.checkpoints.append(sha)
        store.audit(run.run_id, "patch_applied", {"node": node_id, "checkpoint": sha,
                                                  "files": sorted(files)})
    run.status = RunStatus.RUNNING
    store.save_run(run)
    return step(run)


def _apply_generated_files(run: WorkflowRun, node: Node) -> None:
    """Test/doc artifacts also land in the sandbox so validation can run them."""
    if node.artifact_id:
        files = run.artifacts[node.artifact_id].content.get("files", {})
        if files:
            sha = workspace.apply_files(run.workspace, files, f"apply {node.node_id}")
            run.checkpoints.append(sha)
            store.audit(run.run_id, "files_applied", {"node": node.node_id, "checkpoint": sha})


def cancel(run: WorkflowRun, reason: str) -> WorkflowRun:
    run.status = RunStatus.CANCELLED
    run.finished_at = time.time()
    store.audit(run.run_id, "safe_stop", {"reason": reason})
    store.save_run(run)
    return run


def retry_node(run: WorkflowRun, node_id: str) -> WorkflowRun:
    node = run.nodes.get(node_id)
    if node is None or node.status != NodeStatus.FAILED:
        raise ValueError(f"node {node_id} is not in a retryable state")
    node.status = NodeStatus.PENDING
    node.attempts = 0
    node.error = None
    run.status = RunStatus.RUNNING
    store.audit(run.run_id, "manual_retry", {"node": node_id})
    store.save_run(run)
    return step(run)


def replan(run: WorkflowRun, new_requirement: str) -> WorkflowRun:
    """Dynamic re-planning: upstream change invalidates only affected descendants."""
    run.requirement = new_requirement
    req_node = run.nodes["requirement"]
    invalidated = run.downstream_of("requirement")
    for nid in invalidated:
        n = run.nodes[nid]
        n.status = NodeStatus.INVALIDATED if n.status != NodeStatus.PENDING else n.status
        n.artifact_id = None
        n.attempts = 0
        n.approval = None
        n.error = None
    req_node.status = NodeStatus.PENDING
    req_node.artifact_id = None
    req_node.attempts = 0
    req_node.approval = None
    run.status = RunStatus.RUNNING
    store.audit(run.run_id, "replan", {"invalidated": sorted(invalidated),
                                       "new_requirement": new_requirement})
    store.save_run(run)
    return step(run)
