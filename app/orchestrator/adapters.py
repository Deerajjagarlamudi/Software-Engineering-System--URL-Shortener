"""Structured LLM adapters used by every SDLC agent role."""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel

PROMPT_VERSION = "2026-07-17.1"


class ProviderError(Exception):
    pass


@dataclass
class AgentRequest:
    role: str
    requirement: str
    scenario: str
    upstream_artifacts: dict[str, Any]
    workspace_manifest: list[str]
    selected_sources: dict[str, str]
    workspace_revision: str
    attempt: int
    node_id: str
    autonomy_constraints: list[str] = field(
        default_factory=lambda: [
            "Do not access paths outside the supplied workspace",
            "Return only the requested structured artifact",
            "Do not include secrets or destructive commands",
            "High-impact changes require human approval",
        ]
    )

    def prompt_payload(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "requirement": self.requirement,
            "scenario": self.scenario,
            "upstream_artifacts": self.upstream_artifacts,
            "workspace": {
                "revision": self.workspace_revision,
                "manifest": self.workspace_manifest,
                "selected_sources": self.selected_sources,
            },
            "attempt": self.attempt,
            "autonomy_constraints": self.autonomy_constraints,
        }


@dataclass
class AdapterResult:
    content: dict[str, Any]
    provider: str
    model: str
    latency_ms: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    response_hash: str = ""
    prompt_version: str = PROMPT_VERSION

    def metadata(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "latency_ms": round(self.latency_ms, 2),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "response_hash": self.response_hash,
            "prompt_version": self.prompt_version,
        }


class LLMAdapter(Protocol):
    def generate(self, request: AgentRequest, output_model: type[BaseModel]) -> AdapterResult: ...


class MockAdapter:
    def __init__(self, chaos_nodes: set[str] | None = None):
        self.chaos_nodes = chaos_nodes or set()

    def generate(self, request: AgentRequest, output_model: type[BaseModel]) -> AdapterResult:
        if request.node_id in self.chaos_nodes and request.attempt == 1:
            raise ProviderError(f"chaos: injected provider failure for node {request.node_id}")
        started = time.perf_counter()
        from app.orchestrator.mock_agents import generate_mock

        content = generate_mock(request)
        validated = output_model.model_validate(content).model_dump()
        raw = json.dumps(validated, sort_keys=True, default=str)
        return AdapterResult(
            content=validated,
            provider="mock",
            model="deterministic-scenario-v1",
            latency_ms=(time.perf_counter() - started) * 1000,
            response_hash=hashlib.sha256(raw.encode()).hexdigest()[:16],
        )


class AnthropicAdapter:
    def __init__(self, model: str | None = None):
        self.model = model or os.environ.get("LLM_MODEL", "")
        self.api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not self.model:
            raise ProviderError("LLM_MODEL must name an available Anthropic model")
        if not self.api_key:
            raise ProviderError("ANTHROPIC_API_KEY not set")

    def generate(self, request: AgentRequest, output_model: type[BaseModel]) -> AdapterResult:
        import httpx

        api_key = self.api_key
        if api_key is None:
            raise ProviderError("ANTHROPIC_API_KEY not set")
        started = time.perf_counter()
        tool_name = "submit_artifact"
        response = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": 8192,
                "system": (
                    f"You are the {request.role} SDLC agent. Follow the autonomy constraints. "
                    "Submit exactly one artifact through the provided tool."
                ),
                "messages": [
                    {
                        "role": "user",
                        "content": json.dumps(request.prompt_payload(), default=str),
                    }
                ],
                "tools": [
                    {
                        "name": tool_name,
                        "description": "Submit the complete schema-valid SDLC artifact for this stage.",
                        "input_schema": output_model.model_json_schema(),
                    }
                ],
                "tool_choice": {"type": "tool", "name": tool_name},
            },
            timeout=120,
        )
        if response.status_code != 200:
            raise ProviderError(f"provider returned {response.status_code}: {response.text[:300]}")
        try:
            payload = response.json()
            block = next(
                item
                for item in payload["content"]
                if item.get("type") == "tool_use" and item.get("name") == tool_name
            )
            validated = output_model.model_validate(block["input"]).model_dump()
        except (KeyError, StopIteration, TypeError, ValueError) as exc:
            raise ProviderError(
                "provider response did not contain a valid structured artifact"
            ) from exc
        usage = payload.get("usage", {})
        raw = json.dumps(validated, sort_keys=True, default=str)
        return AdapterResult(
            content=validated,
            provider="anthropic",
            model=self.model,
            latency_ms=(time.perf_counter() - started) * 1000,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            response_hash=hashlib.sha256(raw.encode()).hexdigest()[:16],
        )


def get_adapter(chaos_nodes: set[str] | None = None) -> LLMAdapter:
    provider = os.environ.get("LLM_PROVIDER", "mock").lower()
    if provider == "anthropic":
        return AnthropicAdapter()
    if provider != "mock":
        raise ProviderError(f"unsupported LLM_PROVIDER: {provider}")
    return MockAdapter(chaos_nodes=chaos_nodes)
