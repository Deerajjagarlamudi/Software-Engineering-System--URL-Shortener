"""Persisted, governed DAG execution for the SDLC agents."""

from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.orchestrator import store, workspace
from app.orchestrator.adapters import LLMAdapter, ProviderError, get_adapter
from app.orchestrator.agents import AgentOutputError, run_agent
from app.orchestrator.models import Artifact, Node, NodeStatus, RunStatus, WorkflowRun, new_id
from app.orchestrator.policies import PolicyViolation

PARALLEL_WORKERS = 4
SCENARIOS = {"greenfield", "brownfield", "ambiguous"}


def build_graph(scenario: str) -> dict[str, Node]:
    if scenario not in SCENARIOS:
        raise ValueError(f"unsupported scenario: {scenario}")
    req_approval = scenario == "ambiguous"
    nodes = [
        Node("requirement", agent="requirement", approval_required=req_approval),
        Node("architecture", agent="architect", depends_on=["requirement"], approval_required=True),
        Node("plan", agent="planner", depends_on=["architecture"]),
        Node("implementation", agent="coder", depends_on=["plan"], approval_required=True),
        Node("test_generation", agent="tester", depends_on=["implementation"]),
        Node("documentation", agent="doc", depends_on=["implementation"]),
        Node("security_review", agent="security", depends_on=["implementation"]),
        Node(
            "validation",
            agent="validation",
            depends_on=["test_generation", "documentation", "security_review"],
        ),
        Node(
            "release_readiness", agent="release", depends_on=["validation"], approval_required=True
        ),
    ]
    graph = {node.node_id: node for node in nodes}
    for node in graph.values():
        if any(dep not in graph for dep in node.depends_on):
            raise ValueError(f"invalid graph dependency for {node.node_id}")
    return graph


def create_run(requirement: str, scenario: str, chaos_nodes: set[str] | None = None) -> WorkflowRun:
    if scenario not in SCENARIOS:
        raise ValueError(f"unsupported scenario: {scenario}")
    unknown = set(chaos_nodes or ()) - set(build_graph(scenario))
    if unknown:
        raise ValueError(f"unknown chaos nodes: {sorted(unknown)}")
    run = WorkflowRun(run_id=new_id("run"), requirement=requirement, scenario=scenario)
    run.nodes = build_graph(scenario)
    run.workspace = workspace.create_workspace(run.run_id, scenario)
    run.baseline_revision = workspace.head(run.workspace)
    run.checkpoints = [run.baseline_revision]
    run.context["chaos_nodes"] = sorted(chaos_nodes or [])
    store.save_run(run)
    store.audit(run.run_id, "run_created", {"scenario": scenario, "requirement": requirement})
    return run


def _adapter_for(run: WorkflowRun) -> LLMAdapter:
    return get_adapter(chaos_nodes=set(run.context.get("chaos_nodes", [])))


def _ready_nodes(run: WorkflowRun) -> list[Node]:
    ready: list[Node] = []
    for node in run.nodes.values():
        if node.status not in (NodeStatus.PENDING, NodeStatus.INVALIDATED):
            continue
        if all(
            run.nodes[dep].status == NodeStatus.COMPLETED
            and (
                not run.nodes[dep].approval_required
                or bool((run.nodes[dep].approval or {}).get("approved"))
            )
            for dep in node.depends_on
        ):
            ready.append(node)
    return ready


def _next_version(run: WorkflowRun, node_id: str) -> tuple[int, str | None]:
    previous = [a for a in run.artifacts.values() if a.node_id == node_id]
    if not previous:
        return 1, None
    latest = max(previous, key=lambda artifact: artifact.version)
    latest.active = False
    return latest.version + 1, latest.artifact_id


def _execute_node(run: WorkflowRun, node: Node) -> None:
    if not run.workspace:
        raise AgentOutputError("run has no workspace")
    node.attempts += 1
    node.status = NodeStatus.RUNNING
    node.started_at = time.time()
    node.checkpoint_before = workspace.head(run.workspace)
    store.audit(run.run_id, "node_started", {"node": node.node_id, "attempt": node.attempts})
    content, metadata = run_agent(node.agent, run, node, _adapter_for(run))
    lineage = [
        artifact_id
        for d in node.depends_on
        if (artifact_id := run.nodes[d].artifact_id) is not None
    ]
    version, supersedes = _next_version(run, node.node_id)
    digest = hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()[
        :16
    ]
    artifact = Artifact(
        artifact_id=new_id("art"),
        node_id=node.node_id,
        kind=node.agent,
        content=content,
        version=version,
        produced_by=node.agent,
        lineage=lineage,
        content_hash=digest,
        supersedes=supersedes,
        metadata=metadata,
    )
    run.artifacts[artifact.artifact_id] = artifact
    node.artifact_id = artifact.artifact_id
    node.finished_at = time.time()
    node.status = NodeStatus.WAITING_APPROVAL if node.approval_required else NodeStatus.COMPLETED
    event = "approval_requested" if node.approval_required else "node_completed"
    store.audit(
        run.run_id, event, {"node": node.node_id, "artifact": artifact.artifact_id, "hash": digest}
    )


def _rollback_node(run: WorkflowRun, node: Node) -> None:
    checkpoint = node.checkpoint_before or run.baseline_revision
    if run.workspace and checkpoint:
        workspace.rollback(run.workspace, checkpoint)
        run.metrics["rollbacks"] += 1
        store.audit(run.run_id, "rollback", {"to": checkpoint, "node": node.node_id})


def _handle_failure(run: WorkflowRun, node: Node, err: Exception) -> None:
    node.error = str(err)[:500]
    run.metrics["failures"] += 1
    if isinstance(err, PolicyViolation):
        node.status = NodeStatus.FAILED
        _rollback_node(run, node)
        run.status = RunStatus.FAILED
        store.audit(run.run_id, "policy_violation", {"node": node.node_id, "error": node.error})
        return
    if node.attempts <= node.max_retries:
        node.status = NodeStatus.PENDING
        run.metrics["retries"] += 1
        store.audit(
            run.run_id,
            "retry_scheduled",
            {"node": node.node_id, "attempt": node.attempts, "error": node.error},
        )
        return
    node.status = NodeStatus.FAILED
    _rollback_node(run, node)
    run.status = RunStatus.FAILED
    store.audit(run.run_id, "run_failed", {"node": node.node_id, "error": node.error})


def _apply_changes_for_nodes(run: WorkflowRun, nodes: list[Node], event_kind: str) -> None:
    if not run.workspace:
        raise AgentOutputError("run has no workspace")
    writes: dict[str, str] = {}
    deletes: list[str] = []
    source_nodes: list[str] = []
    for node in sorted(nodes, key=lambda item: item.node_id):
        if not node.artifact_id:
            continue
        content = run.artifacts[node.artifact_id].content
        writes.update(content.get("writes", {}))
        deletes.extend(content.get("deletes", []))
        source_nodes.append(node.node_id)
    if not source_nodes:
        return
    revision, changed = workspace.apply_changes(run.workspace, writes, deletes, event_kind)
    run.checkpoints.append(revision)
    for node in nodes:
        if node.node_id in source_nodes:
            node.checkpoint_before = node.checkpoint_before or revision
            run.checkpoint_by_node[node.node_id] = revision
    store.audit(
        run.run_id,
        event_kind,
        {
            "nodes": source_nodes,
            "checkpoint": revision,
            "changed": changed,
            "files": sorted(writes),
        },
    )


def step(run: WorkflowRun) -> WorkflowRun:
    """Advance until an approval gate, terminal state, or safe failure."""
    while run.status == RunStatus.RUNNING:
        ready = _ready_nodes(run)
        if not ready:
            statuses = {node.status for node in run.nodes.values()}
            if NodeStatus.WAITING_APPROVAL in statuses:
                run.status = RunStatus.WAITING_APPROVAL
            elif all(status == NodeStatus.COMPLETED for status in statuses):
                run.status = RunStatus.COMPLETED
                run.finished_at = time.time()
                store.audit(run.run_id, "run_completed", {"metrics": run.metrics})
            elif NodeStatus.RUNNING not in statuses:
                run.status = RunStatus.FAILED
                store.audit(run.run_id, "run_failed", {"reason": "no progress possible"})
            break
        with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as pool:
            futures = {pool.submit(_execute_node, run, node): node for node in ready}
            for future in as_completed(futures):
                node = futures[future]
                try:
                    future.result()
                except (ProviderError, AgentOutputError, PolicyViolation, Exception) as exc:
                    _handle_failure(run, node, exc)
        if run.status == RunStatus.FAILED:
            break
        parallel = [run.nodes["test_generation"], run.nodes["documentation"]]
        branch_ids = [node.artifact_id for node in parallel]
        if (
            all(node.status == NodeStatus.COMPLETED and node.artifact_id for node in parallel)
            and run.context.get("parallel_artifacts") != branch_ids
        ):
            try:
                _apply_changes_for_nodes(run, parallel, "parallel_artifacts_applied")
                run.context["parallel_artifacts"] = branch_ids
            except Exception as exc:
                for node in parallel:
                    if node.status == NodeStatus.COMPLETED:
                        _handle_failure(run, node, exc)
                break
        store.save_run(run)
    store.save_run(run)
    return run


def approve(
    run: WorkflowRun, node_id: str, approved: bool, rationale: str, actor: str
) -> WorkflowRun:
    node = run.nodes.get(node_id)
    if node is None or node.status != NodeStatus.WAITING_APPROVAL:
        raise ValueError(f"node {node_id} is not awaiting approval")
    if not rationale.strip() or not actor.strip():
        raise ValueError("approval rationale and actor are required")
    node.approval = {
        "approved": approved,
        "rationale": rationale,
        "actor": actor,
        "at": time.time(),
    }
    run.metrics["approvals"] += 1
    store.audit(
        run.run_id,
        "approval_decision",
        {"node": node_id, "approved": approved, "rationale": rationale, "actor": actor},
    )
    if not approved:
        node.status = NodeStatus.FAILED
        run.status = RunStatus.CANCELLED
        store.audit(run.run_id, "safe_stop", {"reason": f"approval rejected at {node_id}"})
        store.save_run(run)
        return run
    if node.agent == "coder" and node.artifact_id:
        content = run.artifacts[node.artifact_id].content
        if not run.workspace:
            raise ValueError("run has no workspace")
        if content.get("base_revision") != workspace.head(run.workspace):
            raise ValueError("generated patch is stale; replan before applying it")
        _apply_changes_for_nodes(run, [node], "patch_applied")
    node.status = NodeStatus.COMPLETED
    run.status = RunStatus.RUNNING
    store.save_run(run)
    return step(run)


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
    if run.workspace and (node.checkpoint_before or run.baseline_revision):
        checkpoint = node.checkpoint_before or run.baseline_revision
        if checkpoint:
            workspace.rollback(run.workspace, checkpoint)
    node.status = NodeStatus.PENDING
    node.attempts = 0
    node.error = None
    node.artifact_id = None
    run.status = RunStatus.RUNNING
    store.audit(run.run_id, "manual_retry", {"node": node_id})
    return step(run)


def replan(
    run: WorkflowRun,
    new_requirement: str,
    reason: str = "upstream requirement changed",
    actor: str = "system",
) -> WorkflowRun:
    if not new_requirement.strip() or not reason.strip() or not actor.strip():
        raise ValueError("requirement, reason, and actor are required")
    invalidated = {"requirement", *run.downstream_of("requirement")}
    for node_id in invalidated:
        node = run.nodes[node_id]
        if node.artifact_id and node.artifact_id in run.artifacts:
            run.artifacts[node.artifact_id].active = False
        node.status = NodeStatus.PENDING
        node.artifact_id = None
        node.attempts = 0
        node.approval = None
        node.error = None
    run.requirement = new_requirement
    if run.workspace and run.baseline_revision:
        workspace.rollback(run.workspace, run.baseline_revision)
    run.checkpoints = [run.baseline_revision] if run.baseline_revision else []
    run.checkpoint_by_node.clear()
    run.context.pop("parallel_artifacts", None)
    run.status = RunStatus.RUNNING
    store.audit(
        run.run_id,
        "replan",
        {
            "invalidated": sorted(invalidated),
            "new_requirement": new_requirement,
            "reason": reason,
            "actor": actor,
        },
    )
    return step(run)


def recover(run: WorkflowRun) -> WorkflowRun:
    changed = []
    for node in run.nodes.values():
        if node.status == NodeStatus.RUNNING:
            node.status = NodeStatus.PENDING
            changed.append(node.node_id)
    if changed:
        run.status = RunStatus.RECOVERY_REQUIRED
        store.audit(run.run_id, "run_recovery_required", {"nodes": changed})
        store.save_run(run)
    return run


def resume(run: WorkflowRun) -> WorkflowRun:
    if run.status != RunStatus.RECOVERY_REQUIRED:
        raise ValueError("run does not require recovery")
    run.status = RunStatus.RUNNING
    store.audit(run.run_id, "run_resumed", {})
    return step(run)
