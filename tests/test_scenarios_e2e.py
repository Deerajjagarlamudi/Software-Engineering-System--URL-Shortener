"""End-to-end scenario tests through the HTTP API (mock provider)."""
import json
import os

SCENARIO_FILE = os.path.join(os.path.dirname(__file__), "..", "scenarios", "scenarios.json")


def _approve_pending(client, run):
    guard = 0
    while run["status"] == "waiting_approval" and guard < 10:
        waiting = [nid for nid, n in run["nodes"].items() if n["status"] == "waiting_approval"]
        run = client.post(
            f"/api/v1/runs/{run['run_id']}/approvals/{waiting[0]}",
            json={"approved": True, "rationale": "e2e approval", "actor": "e2e"},
        ).json()
        guard += 1
    return run


def _load_scenarios():
    with open(SCENARIO_FILE) as f:
        return json.load(f)


def test_greenfield_scenario(client):
    s = _load_scenarios()["greenfield"]
    run = client.post("/api/v1/runs", json=s["input"]).json()
    run = _approve_pending(client, run)
    assert run["status"] == "completed"
    ws = client.get(f"/api/v1/runs/{run['run_id']}/workspace").json()
    assert "shortcode/codec.py" in ws["files"]
    assert len(ws["checkpoints"]) >= 3  # initial + patch + tests/docs


def test_brownfield_scenario(client):
    s = _load_scenarios()["brownfield"]
    run = client.post("/api/v1/runs", json=s["input"]).json()
    run = _approve_pending(client, run)
    assert run["status"] == "completed"
    arts = client.get(f"/api/v1/runs/{run['run_id']}/artifacts").json()
    plans = [a for a in arts.values() if a["kind"] == "planner"]
    assert plans[0]["content"]["impacted_modules"]  # impact analysis present
    ws = client.get(f"/api/v1/runs/{run['run_id']}/workspace").json()
    assert "shortcode/expiry.py" in ws["files"]


def test_ambiguous_scenario(client):
    s = _load_scenarios()["ambiguous"]
    run = client.post("/api/v1/runs", json=s["input"]).json()
    assert run["status"] == "waiting_approval"
    assert run["nodes"]["requirement"]["status"] == "waiting_approval"
    run = _approve_pending(client, run)
    assert run["status"] == "completed"
    metrics = client.get(f"/api/v1/runs/{run['run_id']}/metrics").json()
    assert metrics["approvals"] >= 4  # requirement + architecture + patch + release


def test_chaos_scenario_metrics(client):
    run = client.post("/api/v1/runs", json={
        "requirement": "Build URL shortener", "scenario": "greenfield",
        "chaos_nodes": ["security_review"],
    }).json()
    run = _approve_pending(client, run)
    assert run["status"] == "completed"
    metrics = client.get(f"/api/v1/runs/{run['run_id']}/metrics").json()
    assert metrics["retries"] >= 1
    assert metrics["per_node"]["security_review"]["attempts"] == 2


def test_cancel_run(client):
    run = client.post("/api/v1/runs", json={
        "requirement": "Build URL shortener", "scenario": "greenfield"}).json()
    out = client.post(f"/api/v1/runs/{run['run_id']}/cancel").json()
    assert out["status"] == "cancelled"


def test_audit_trail_via_api(client):
    run = client.post("/api/v1/runs", json={
        "requirement": "Build URL shortener", "scenario": "greenfield"}).json()
    audit = client.get(f"/api/v1/runs/{run['run_id']}/audit").json()
    kinds = [e["kind"] for e in audit]
    assert "run_created" in kinds and "node_started" in kinds and "approval_requested" in kinds
