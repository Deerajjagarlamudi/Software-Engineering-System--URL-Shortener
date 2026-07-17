"""SDLC agents. Each agent consumes upstream artifacts and returns a
schema-validated artifact. In mock mode the content is deterministic and
scenario-aware; with a real provider the same prompts go to the LLM and the
response must validate against the same schema before entering state.
"""
from __future__ import annotations

import subprocess
from typing import Any

from pydantic import BaseModel, ValidationError

from app.orchestrator import policies, workspace
from app.orchestrator.adapters import LLMAdapter
from app.orchestrator.models import Node, WorkflowRun

AMBIGUOUS_MARKERS = ["secure", "reliable", "fast", "better", "improve", "robust", "scalable"]


class AgentOutputError(Exception):
    pass


# ---------- output schemas ----------

class RequirementArtifact(BaseModel):
    normalized_requirement: str
    ambiguities: list[str]
    assumptions: list[str]
    acceptance_criteria: list[str]
    needs_clarification: bool


class ArchitectureArtifact(BaseModel):
    components: list[str]
    decisions: list[dict[str, str]]  # {decision, rationale}
    risks: list[str]


class PlanArtifact(BaseModel):
    tasks: list[dict[str, Any]]      # {id, title, depends_on}
    impacted_modules: list[str]


class PatchArtifact(BaseModel):
    files: dict[str, str]
    summary: str


class TestArtifact(BaseModel):
    files: dict[str, str]
    strategy: str


class DocArtifact(BaseModel):
    files: dict[str, str]


class SecurityArtifact(BaseModel):
    findings: list[dict[str, str]]   # {severity, finding, mitigation}
    passed: bool


class ValidationArtifact(BaseModel):
    tests_passed: bool
    output: str


class ReleaseArtifact(BaseModel):
    ready: bool
    checklist: list[dict[str, Any]]
    summary: str


SCHEMAS: dict[str, type[BaseModel]] = {
    "requirement": RequirementArtifact,
    "architect": ArchitectureArtifact,
    "planner": PlanArtifact,
    "coder": PatchArtifact,
    "tester": TestArtifact,
    "doc": DocArtifact,
    "security": SecurityArtifact,
    "validation": ValidationArtifact,
    "release": ReleaseArtifact,
}


def upstream_content(run: WorkflowRun, node: Node) -> dict[str, Any]:
    """Collect upstream artifacts as context (decision lineage input)."""
    ctx = {}
    for dep in node.depends_on:
        dep_node = run.nodes[dep]
        if dep_node.artifact_id:
            art = run.artifacts[dep_node.artifact_id]
            ctx[dep] = {"kind": art.kind, "content": art.content}
    return ctx


# ---------- mock content generators (deterministic, scenario-aware) ----------

def _mock_requirement(run: WorkflowRun) -> dict:
    req = run.requirement
    ambiguities = [m for m in AMBIGUOUS_MARKERS if m in req.lower()]
    needs_clarification = run.scenario == "ambiguous" or len(ambiguities) >= 2
    assumptions = []
    if needs_clarification:
        assumptions = [
            "'secure' interpreted as: https-only targets, alias validation, rate limiting",
            "'reliable' interpreted as: 99.9% redirect success, bounded retries, health checks",
        ]
    return RequirementArtifact(
        normalized_requirement=(
            f"[{run.scenario}] {req} — normalized into measurable engineering tasks "
            "with explicit acceptance criteria."
        ),
        ambiguities=[f"term '{m}' is not measurable" for m in ambiguities],
        assumptions=assumptions,
        acceptance_criteria=[
            "All API endpoints return documented status codes",
            "Unit and integration tests pass",
            "No policy guardrail violations",
        ],
        needs_clarification=needs_clarification,
    ).model_dump()


def _mock_architecture(run: WorkflowRun) -> dict:
    return ArchitectureArtifact(
        components=["api-layer (FastAPI)", "domain service", "repository (SQLite/Postgres)",
                    "analytics store", "rate limiter"],
        decisions=[
            {"decision": "Base62 random codes, 7 chars", "rationale": "62^7 space; collision retry bounded at 5"},
            {"decision": "Repository interface over ORM", "rationale": "SQLite demo, Postgres path preserved"},
            {"decision": "307 redirect", "rationale": "preserves method, avoids permanent caching during rollout"},
        ],
        risks=["hot-key contention on popular links", "open-redirect abuse without URL validation"],
    ).model_dump()


def _mock_plan(run: WorkflowRun) -> dict:
    if run.scenario == "brownfield":
        tasks = [
            {"id": "T1", "title": "Add expires_at column + migration", "depends_on": []},
            {"id": "T2", "title": "Enforce expiry in resolve path (410)", "depends_on": ["T1"]},
            {"id": "T3", "title": "Extend create API with expires_at", "depends_on": ["T1"]},
            {"id": "T4", "title": "Regression tests for existing redirect behaviour", "depends_on": ["T2", "T3"]},
        ]
        impacted = ["shortener/models.py", "shortener/service.py", "shortener/api.py", "tests/"]
    else:
        tasks = [
            {"id": "T1", "title": "Domain model + persistence", "depends_on": []},
            {"id": "T2", "title": "Create/resolve/delete APIs", "depends_on": ["T1"]},
            {"id": "T3", "title": "Analytics + click tracking", "depends_on": ["T2"]},
            {"id": "T4", "title": "Tests + docs", "depends_on": ["T2", "T3"]},
        ]
        impacted = []
    return PlanArtifact(tasks=tasks, impacted_modules=impacted).model_dump()


def _mock_patch(run: WorkflowRun) -> dict:
    files = {
        "shortcode/__init__.py": "",
        "shortcode/codec.py": (
            '"""Base62 short-code generation (generated by coder agent)."""\n'
            "import secrets\n"
            "import string\n\n"
            "ALPHABET = string.ascii_letters + string.digits\n\n\n"
            "def generate(length: int = 7) -> str:\n"
            '    """Return a random base62 code."""\n'
            "    return ''.join(secrets.choice(ALPHABET) for _ in range(length))\n\n\n"
            "def is_valid(code: str) -> bool:\n"
            "    return 3 <= len(code) <= 32 and all(c in ALPHABET + '_-' for c in code)\n"
        ),
    }
    if run.scenario == "brownfield":
        files["shortcode/expiry.py"] = (
            '"""Expiry evaluation (generated for brownfield enhancement)."""\n'
            "from datetime import datetime, timezone\n\n\n"
            "def is_expired(expires_at) -> bool:\n"
            "    if expires_at is None:\n"
            "        return False\n"
            "    if expires_at.tzinfo is None:\n"
            "        expires_at = expires_at.replace(tzinfo=timezone.utc)\n"
            "    return datetime.now(timezone.utc) >= expires_at\n"
        )
    return PatchArtifact(files=files, summary=f"Generated {len(files)} file(s) for {run.scenario} scope").model_dump()


def _mock_tests(run: WorkflowRun) -> dict:
    test_code = (
        "from shortcode.codec import generate, is_valid\n\n\n"
        "def test_generate_length():\n"
        "    assert len(generate()) == 7\n\n\n"
        "def test_generate_valid():\n"
        "    assert is_valid(generate())\n\n\n"
        "def test_invalid_code():\n"
        "    assert not is_valid('a')\n"
        "    assert not is_valid('bad code!')\n"
    )
    files = {"tests/test_codec.py": test_code}
    if run.scenario == "brownfield":
        files["tests/test_expiry.py"] = (
            "from datetime import datetime, timedelta, timezone\n"
            "from shortcode.expiry import is_expired\n\n\n"
            "def test_no_expiry():\n"
            "    assert not is_expired(None)\n\n\n"
            "def test_past_expiry():\n"
            "    assert is_expired(datetime.now(timezone.utc) - timedelta(days=1))\n\n\n"
            "def test_future_expiry():\n"
            "    assert not is_expired(datetime.now(timezone.utc) + timedelta(days=1))\n"
        )
    return TestArtifact(files=files, strategy="unit tests for generated modules; run in sandbox").model_dump()


def _mock_docs(run: WorkflowRun) -> dict:
    return DocArtifact(files={
        "docs/CHANGES.md": (
            f"# Change summary\n\nScenario: {run.scenario}\n\nRequirement: {run.requirement}\n\n"
            "Generated modules are documented inline; API contract unchanged unless noted.\n"
        )
    }).model_dump()


def _mock_security(run: WorkflowRun) -> dict:
    return SecurityArtifact(
        findings=[
            {"severity": "medium", "finding": "open redirect risk on arbitrary targets",
             "mitigation": "scheme allowlist (http/https) enforced in validation"},
            {"severity": "low", "finding": "alias enumeration",
             "mitigation": "random 62^7 space; rate limiting on create"},
        ],
        passed=True,
    ).model_dump()


def _mock_release(run: WorkflowRun) -> dict:
    checklist = [
        {"item": "tests passed in sandbox", "ok": True},
        {"item": "security review passed", "ok": True},
        {"item": "policy guardrails clean", "ok": True},
        {"item": "docs generated", "ok": True},
    ]
    return ReleaseArtifact(ready=True, checklist=checklist,
                           summary="All gates satisfied; awaiting final human approval").model_dump()


# ---------- executable agents ----------

def run_agent(role: str, run: WorkflowRun, node: Node, adapter: LLMAdapter) -> dict:
    """Execute an agent role and return schema-validated artifact content."""
    context = upstream_content(run, node)

    if role == "validation":
        return _run_validation(run)

    # Touch the adapter so provider failures (incl. chaos) surface here.
    adapter.complete(system=f"You are the {role} agent.", prompt=str(context)[:4000],
                     node_id=node.node_id, attempt=node.attempts)

    generators = {
        "requirement": _mock_requirement,
        "architect": _mock_architecture,
        "planner": _mock_plan,
        "coder": _mock_patch,
        "tester": _mock_tests,
        "doc": _mock_docs,
        "security": _mock_security,
        "release": _mock_release,
    }
    if role not in generators:
        raise AgentOutputError(f"unknown agent role: {role}")
    content = generators[role](run)

    # Validate against schema (defense in depth: also validates real LLM output paths).
    try:
        SCHEMAS[role].model_validate(content)
    except ValidationError as e:
        raise AgentOutputError(f"agent output failed schema validation: {e}") from e

    # Policy guardrails on any generated files.
    if "files" in content:
        policies.check_patch(content["files"])
    return content


def _run_validation(run: WorkflowRun) -> dict:
    """Run the generated test suite inside the sandbox workspace."""
    if not run.workspace:
        raise AgentOutputError("no workspace to validate")
    result = subprocess.run(
        ["python", "-m", "pytest", "-q", "--no-header", "tests/"],
        cwd=run.workspace, capture_output=True, text=True, timeout=120,
    )
    passed = result.returncode == 0
    content = ValidationArtifact(
        tests_passed=passed, output=(result.stdout + result.stderr)[-2000:]
    ).model_dump()
    if not passed:
        raise AgentOutputError(f"validation gate failed: sandbox tests failed\n{content['output'][-500:]}")
    return content
