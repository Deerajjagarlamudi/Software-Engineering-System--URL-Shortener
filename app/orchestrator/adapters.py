"""LLM provider adapters.

MockAdapter is deterministic and offline (default; used by tests and demos).
AnthropicAdapter is enabled by setting LLM_PROVIDER=anthropic + ANTHROPIC_API_KEY.
A chaos flag makes the mock fail selected nodes on their first attempt so
retry/rollback behaviour and reliability metrics are demonstrable.
"""
from __future__ import annotations

import json
import os
from typing import Protocol


class ProviderError(Exception):
    pass


class LLMAdapter(Protocol):
    def complete(self, system: str, prompt: str, node_id: str, attempt: int) -> str: ...


class MockAdapter:
    """Deterministic canned responses keyed by agent role embedded in the system prompt."""

    def __init__(self, chaos_nodes: set[str] | None = None):
        self.chaos_nodes = chaos_nodes or set()

    def complete(self, system: str, prompt: str, node_id: str, attempt: int) -> str:
        if node_id in self.chaos_nodes and attempt == 1:
            raise ProviderError(f"chaos: injected provider failure for node {node_id}")
        # Agents in mock mode construct their own structured output; the adapter
        # just acknowledges. Kept for interface symmetry with real providers.
        return json.dumps({"mock": True, "node": node_id})


class AnthropicAdapter:
    def __init__(self, model: str | None = None):
        self.model = model or os.environ.get("LLM_MODEL", "claude-sonnet-5")
        self.api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ProviderError("ANTHROPIC_API_KEY not set")

    def complete(self, system: str, prompt: str, node_id: str, attempt: int) -> str:
        import httpx

        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": 4096,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=120,
        )
        if resp.status_code != 200:
            raise ProviderError(f"provider returned {resp.status_code}: {resp.text[:300]}")
        return resp.json()["content"][0]["text"]


def get_adapter(chaos_nodes: set[str] | None = None) -> LLMAdapter:
    provider = os.environ.get("LLM_PROVIDER", "mock").lower()
    if provider == "anthropic":
        return AnthropicAdapter()
    return MockAdapter(chaos_nodes=chaos_nodes)
