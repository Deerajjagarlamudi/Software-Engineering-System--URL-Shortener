"""Engine-level tests: gates, retries, rollback, re-planning, policy, approvals."""

from app.orchestrator import engine, store
from app.orchestrator.models import NodeStatus, RunStatus
from app.orchestrator.policies import PolicyViolation, check_patch


def _approve_all(run):
    """Approve pending gates until run completes or fails."""
    guard = 0
    while run.status == RunStatus.WAITING_APPROVAL and guard < 10:
        for nid, node in run.nodes.items():
            if node.status == NodeStatus.WAITING_APPROVAL:
                run = engine.approve(run, nid, True, "test approval", "pytest")
                break
        guard += 1
    return run


def test_greenfield_full_run_completes():
    run = engine.create_run("Build URL shortener with analytics", "greenfield")
    run = engine.step(run)
    assert run.status == RunStatus.WAITING_APPROVAL  # architecture gate
    run = _approve_all(run)
    assert run.status == RunStatus.COMPLETED
    assert all(n.status == NodeStatus.COMPLETED for n in run.nodes.values())
    # validation actually ran the generated tests in the sandbox
    val = run.artifacts[run.nodes["validation"].artifact_id]
    assert val.content["tests_passed"] is True
    # lineage recorded
    rel = run.artifacts[run.nodes["release_readiness"].artifact_id]
    assert rel.lineage


def test_parallel_branch_synchronization():
    run = _approve_all(engine.step(engine.create_run("Build service", "greenfield")))
    impl_end = run.nodes["implementation"].finished_at
    for nid in ("test_generation", "documentation", "security_review"):
        assert run.nodes[nid].started_at >= impl_end
    val_start = run.nodes["validation"].started_at
    for nid in ("test_generation", "documentation", "security_review"):
        assert run.nodes[nid].finished_at <= val_start


def test_chaos_retry_and_metrics():
    run = engine.create_run("Build service", "greenfield", chaos_nodes={"plan"})
    run = _approve_all(engine.step(run))
    assert run.status == RunStatus.COMPLETED
    assert run.metrics["retries"] >= 1
    assert run.nodes["plan"].attempts == 2
    kinds = [e["kind"] for e in store.audit_trail(run.run_id)]
    assert "retry_scheduled" in kinds


def test_parallel_branch_retry_still_integrates_all_outputs():
    run = engine.create_run("Build service", "greenfield", chaos_nodes={"test_generation"})
    run = _approve_all(engine.step(run))
    assert run.status == RunStatus.COMPLETED
    assert "tests/test_generated_http.py" in engine.workspace.list_files(run.workspace)


def test_approval_rejection_is_safe_stop():
    run = engine.step(engine.create_run("Build service", "greenfield"))
    run = engine.approve(run, "architecture", False, "design rejected", "pytest")
    assert run.status == RunStatus.CANCELLED
    kinds = [e["kind"] for e in store.audit_trail(run.run_id)]
    assert "safe_stop" in kinds


def test_ambiguous_requires_requirement_approval():
    run = engine.step(engine.create_run("make shortened links secure and reliable", "ambiguous"))
    assert run.status == RunStatus.WAITING_APPROVAL
    assert run.nodes["requirement"].status == NodeStatus.WAITING_APPROVAL
    art = run.artifacts[run.nodes["requirement"].artifact_id]
    assert art.content["needs_clarification"] is True
    assert art.content["ambiguities"]
    assert art.content["assumptions"]
    # downstream must not have started
    assert run.nodes["architecture"].status == NodeStatus.PENDING


def test_replan_invalidates_downstream_only():
    run = _approve_all(engine.step(engine.create_run("Build service", "greenfield")))
    assert run.status == RunStatus.COMPLETED
    run = engine.replan(run, "Build service with rate limiting")
    # replan re-executes; it should pause again at the architecture gate
    assert run.status == RunStatus.WAITING_APPROVAL
    kinds = [e["kind"] for e in store.audit_trail(run.run_id)]
    assert "replan" in kinds


def test_replan_reexecutes_with_superseded_artifacts():
    run = _approve_all(engine.step(engine.create_run("Build service", "greenfield")))
    old_ids = set(run.artifacts)
    run = engine.replan(run, "Build service with rate limiting", "new scope", "reviewer")
    assert run.status == RunStatus.WAITING_APPROVAL
    run = _approve_all(run)
    assert run.status == RunStatus.COMPLETED
    assert old_ids
    assert any(not artifact.active for artifact in run.artifacts.values())
    assert any(artifact.version > 1 for artifact in run.artifacts.values())


def test_workspace_apply_is_idempotent(tmp_path):
    from app.orchestrator.workspace import apply_changes

    workspace_path = str(tmp_path)
    import subprocess

    (tmp_path / ".gitkeep").write_text("")
    subprocess.run(["git", "init", "-q"], cwd=workspace_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=workspace_path, check=True
    )
    subprocess.run(["git", "config", "user.name", "pytest"], cwd=workspace_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=workspace_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=workspace_path, check=True)
    revision, changed = apply_changes(workspace_path, {"x.txt": "same"}, [], "add")
    same_revision, changed_again = apply_changes(workspace_path, {"x.txt": "same"}, [], "repeat")
    assert changed and not changed_again and revision == same_revision


def test_invalid_run_inputs_and_recovery():
    import pytest

    with pytest.raises(ValueError):
        engine.build_graph("unsupported")
    with pytest.raises(ValueError):
        engine.create_run("Build service", "greenfield", {"not-a-node"})
    run = engine.create_run("Build service", "greenfield")
    run.nodes["requirement"].status = NodeStatus.RUNNING
    recovered = engine.recover(run)
    assert recovered.status == RunStatus.RECOVERY_REQUIRED
    resumed = engine.resume(recovered)
    assert resumed.status == RunStatus.WAITING_APPROVAL


def test_policy_guardrails():
    try:
        check_patch({"bad.py": "password = 'supersecretvalue123'"})
        assert False, "should have raised"
    except PolicyViolation as e:
        assert e.rule == "secret-material"
    try:
        check_patch({"bad2.py": "import os\nos.system('ls')"})
        assert False, "should have raised"
    except PolicyViolation:
        pass


def test_run_persistence_roundtrip():
    run = engine.step(engine.create_run("Build service", "greenfield"))
    loaded = store.load_run(run.run_id)
    assert loaded is not None
    assert loaded.run_id == run.run_id
    assert set(loaded.nodes) == set(run.nodes)
    assert loaded.status == run.status
