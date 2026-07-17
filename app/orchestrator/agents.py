"""Schema-bound SDLC agents and sandbox validation."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from app.orchestrator import policies, workspace
from app.orchestrator.adapters import AdapterResult, AgentRequest, LLMAdapter
from app.orchestrator.models import Node, WorkflowRun


class AgentOutputError(Exception):
    """Raised when an agent cannot produce a safe, schema-valid artifact."""


class RequirementArtifact(BaseModel):
    normalized_requirement: str
    ambiguities: list[str]
    assumptions: list[str]
    acceptance_criteria: list[str]
    needs_clarification: bool


class ArchitectureArtifact(BaseModel):
    components: list[str]
    decisions: list[dict[str, str]]
    risks: list[str]


class PlanArtifact(BaseModel):
    tasks: list[dict[str, Any]]
    impacted_modules: list[str]


class PatchArtifact(BaseModel):
    writes: dict[str, str]
    deletes: list[str] = []
    summary: str
    base_revision: str


class TestArtifact(BaseModel):
    writes: dict[str, str]
    deletes: list[str] = []
    strategy: str


class DocArtifact(BaseModel):
    writes: dict[str, str]
    deletes: list[str] = []


class SecurityArtifact(BaseModel):
    findings: list[dict[str, str]]
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
    result: dict[str, Any] = {}
    for dep in node.depends_on:
        dep_node = run.nodes[dep]
        if dep_node.artifact_id:
            artifact = run.artifacts[dep_node.artifact_id]
            result[dep] = {"kind": artifact.kind, "content": artifact.content}
    return result


def _request(run: WorkflowRun, node: Node) -> AgentRequest:
    snapshot: dict[str, Any] = (
        workspace.context_snapshot(run.workspace)
        if run.workspace
        else {"manifest": [], "sources": {}, "revision": ""}
    )
    return AgentRequest(
        role=node.agent,
        requirement=run.requirement,
        scenario=run.scenario,
        upstream_artifacts=upstream_content(run, node),
        workspace_manifest=list(snapshot["manifest"]),
        selected_sources=dict(snapshot["sources"]),
        workspace_revision=str(snapshot["revision"]),
        attempt=node.attempts,
        node_id=node.node_id,
    )


def run_agent(
    role: str, run: WorkflowRun, node: Node, adapter: LLMAdapter
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Execute one role and return validated content plus provider metadata."""
    if role == "validation":
        return _run_validation(run), {"provider": "sandbox", "model": "pytest"}
    schema = SCHEMAS.get(role)
    if schema is None:
        raise AgentOutputError(f"unknown agent role: {role}")
    try:
        result: AdapterResult = adapter.generate(_request(run, node), schema)
        content = schema.model_validate(result.content).model_dump()
    except Exception as exc:
        if isinstance(exc, AgentOutputError):
            raise
        raise AgentOutputError(f"agent output failed schema validation: {exc}") from exc
    if "writes" in content:
        policies.check_patch(content["writes"])
    return content, result.metadata()


def _run_validation(run: WorkflowRun) -> dict[str, Any]:
    if not run.workspace:
        raise AgentOutputError("no workspace to validate")
    result = workspace.run_allowed(
        run.workspace, ["python", "-m", "pytest", "-q", "--no-header", "tests/"], timeout=120
    )
    output = (result.stdout + result.stderr)[-4000:]
    content = ValidationArtifact(tests_passed=result.returncode == 0, output=output).model_dump()
    if not content["tests_passed"]:
        raise AgentOutputError(f"validation gate failed: {json.dumps(content)[-1000:]}")
    return content
