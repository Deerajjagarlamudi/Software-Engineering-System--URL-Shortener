"""Deterministic, substantive scenario outputs for offline demos and tests."""

from __future__ import annotations

from app.orchestrator.adapters import AgentRequest


def _service_code(scenario: str) -> str:
    https_only = scenario == "ambiguous"
    rate_limit = scenario == "ambiguous"
    return f"""from datetime import datetime, timezone
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

app = FastAPI()
links: dict[str, dict] = {{}}
create_counts: dict[str, int] = {{}}
clicks: dict[str, int] = {{}}
HTTPS_ONLY = {https_only!r}
RATE_LIMIT = {3 if rate_limit else 1000}


class LinkIn(BaseModel):
    code: str
    target_url: str
    expires_at: datetime | None = None


def validate_target(url: str) -> str:
    parsed = urlsplit(url)
    allowed = ("https",) if HTTPS_ONLY else ("http", "https")
    if parsed.scheme not in allowed or not parsed.hostname or parsed.username or parsed.password:
        raise HTTPException(400, "unsafe target URL")
    return url


@app.post("/links", status_code=201)
def create_link(body: LinkIn, request: Request):
    client = request.client.host if request.client else "unknown"
    target = validate_target(body.target_url)
    create_counts[client] = create_counts.get(client, 0) + 1
    if create_counts[client] > RATE_LIMIT:
        raise HTTPException(429, "creation rate exceeded")
    links[body.code] = {{
        "target_url": target,
        "expires_at": body.expires_at,
        "click_count": 0,
    }}
    clicks[body.code] = 0
    return {{"code": body.code, **links[body.code]}}


@app.get("/links/{{code}}/analytics")
def analytics(code: str):
    if code not in links:
        raise HTTPException(404)
    return {{"code": code, "click_count": links[code]["click_count"]}}


@app.delete("/links/{{code}}", status_code=204)
def delete(code: str):
    if code not in links:
        raise HTTPException(404)
    del links[code]
    clicks.pop(code, None)


@app.get("/health/live")
def live():
    return {{"status": "ok"}}


@app.get("/{{code}}")
def resolve(code: str):
    if code not in links:
        raise HTTPException(404)
    link = links[code]
    expires = link["expires_at"]
    if expires is not None:
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) >= expires:
            raise HTTPException(410, "link expired")
    link["click_count"] += 1
    clicks[code] = link["click_count"]
    return RedirectResponse(link["target_url"], status_code=307)
"""


def _test_code(scenario: str) -> str:
    base = """from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from shortcode.app import app, clicks, create_counts, links


def setup_function():
    links.clear()
    create_counts.clear()
    clicks.clear()


def test_create_resolve_analytics_delete():
    client = TestClient(app)
    body = {"code": "abc", "target_url": "https://example.com"}
    assert client.post("/links", json=body).status_code == 201
    assert client.get("/abc", follow_redirects=False).status_code == 307
    assert client.get("/links/abc/analytics").json()["click_count"] == 1
    assert client.delete("/links/abc").status_code == 204


def test_expired_link_returns_410():
    client = TestClient(app)
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    body = {"code": "old", "target_url": "https://example.com", "expires_at": past}
    assert client.post("/links", json=body).status_code == 201
    assert client.get("/old", follow_redirects=False).status_code == 410
"""
    if scenario == "ambiguous":
        base += """

def test_security_and_reliability_assumptions_are_enforced():
    client = TestClient(app)
    assert client.post("/links", json={"code": "bad", "target_url": "http://example.com"}).status_code == 400
    for index in range(3):
        assert client.post("/links", json={"code": f"x{index}", "target_url": "https://example.com"}).status_code == 201
    assert client.post("/links", json={"code": "x4", "target_url": "https://example.com"}).status_code == 429
    assert client.get("/health/live").json() == {"status": "ok"}
"""
    return base


def generate_mock(request: AgentRequest) -> dict:
    role = request.role
    scenario = request.scenario
    if role == "requirement":
        markers = [
            word
            for word in ("secure", "reliable", "fast", "better", "scalable")
            if word in request.requirement.lower()
        ]
        ambiguous = scenario == "ambiguous" or len(markers) >= 2
        return {
            "normalized_requirement": f"[{scenario}] {request.requirement}",
            "ambiguities": [f"term '{word}' requires a measurable definition" for word in markers],
            "assumptions": (
                [
                    "secure means HTTPS targets, validated hosts, and bounded link creation",
                    "reliable means health visibility, deterministic errors, and regression coverage",
                ]
                if ambiguous
                else []
            ),
            "acceptance_criteria": [
                "Generated HTTP integration tests pass in the sandbox",
                "All high-impact changes receive recorded human approval",
                "No generated patch violates security policy",
            ],
            "needs_clarification": ambiguous,
        }
    if role == "architect":
        return {
            "components": ["FastAPI boundary", "link domain", "analytics", "scenario sandbox"],
            "decisions": [
                {
                    "decision": "Use explicit HTTP contracts",
                    "rationale": "Makes generated behavior reviewable",
                },
                {
                    "decision": "Validate in isolated Git workspace",
                    "rationale": "Prevents unapproved source mutation",
                },
            ],
            "risks": [
                "redirect abuse",
                "concurrent analytics updates",
                "stale generated artifacts",
            ],
        }
    if role == "planner":
        manifest = request.workspace_manifest
        return {
            "tasks": [
                {"id": "T1", "title": "Inspect baseline and normalize contracts", "depends_on": []},
                {"id": "T2", "title": "Implement service change", "depends_on": ["T1"]},
                {"id": "T3", "title": "Generate HTTP regression tests", "depends_on": ["T2"]},
                {"id": "T4", "title": "Validate and document", "depends_on": ["T3"]},
            ],
            "impacted_modules": manifest or ["shortcode/app.py", "tests/"],
        }
    if role == "coder":
        writes = {
            "shortcode/app.py": _service_code(scenario),
            "shortcode/__init__.py": "",
        }
        if scenario == "brownfield":
            writes["migrations/001_add_expires_at.sql"] = (
                "ALTER TABLE links ADD COLUMN expires_at TIMESTAMP NULL;\n"
            )
        return {
            "writes": writes,
            "deletes": [],
            "summary": f"Generated a complete HTTP URL-shortener outcome for {scenario}",
            "base_revision": request.workspace_revision,
        }
    if role == "tester":
        return {
            "writes": {"tests/test_generated_http.py": _test_code(scenario)},
            "deletes": [],
            "strategy": "HTTP integration coverage plus preservation of seeded brownfield regressions",
        }
    if role == "doc":
        return {
            "writes": {
                "docs/CHANGES.md": (
                    f"# Generated change\n\nScenario: {scenario}\n\n"
                    f"Requirement: {request.requirement}\n\n"
                    "Validated through sandboxed HTTP integration tests.\n"
                )
            },
            "deletes": [],
        }
    if role == "security":
        return {
            "findings": [
                {
                    "severity": "medium",
                    "finding": "redirect abuse",
                    "mitigation": "scheme and host validation",
                },
                {
                    "severity": "low",
                    "finding": "creation abuse",
                    "mitigation": "bounded creation in secure scenario",
                },
            ],
            "passed": True,
        }
    if role == "release":
        validation = request.upstream_artifacts.get("validation", {}).get("content", {})
        return {
            "ready": bool(validation.get("tests_passed")),
            "checklist": [
                {"item": "sandbox tests passed", "ok": bool(validation.get("tests_passed"))},
                {"item": "human approvals recorded", "ok": True},
                {"item": "security review completed", "ok": True},
            ],
            "summary": "Governed artifacts are ready for final human review",
        }
    raise ValueError(f"unsupported mock agent role: {role}")
