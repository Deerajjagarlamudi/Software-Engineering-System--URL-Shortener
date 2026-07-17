from app.orchestrator.adapters import AgentRequest, AnthropicAdapter, ProviderError
from app.orchestrator.agents import RequirementArtifact


def _request() -> AgentRequest:
    return AgentRequest(
        role="requirement",
        requirement="Build service",
        scenario="greenfield",
        upstream_artifacts={},
        workspace_manifest=[],
        selected_sources={},
        workspace_revision="abc",
        attempt=1,
        node_id="requirement",
    )


def test_anthropic_tool_output_is_used(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    class Response:
        status_code = 200

        def json(self):
            return {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "submit_artifact",
                        "input": {
                            "normalized_requirement": "Build service",
                            "ambiguities": [],
                            "assumptions": [],
                            "acceptance_criteria": ["tests pass"],
                            "needs_clarification": False,
                        },
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 12},
            }

    monkeypatch.setattr("httpx.post", lambda *args, **kwargs: Response())
    result = AnthropicAdapter(model="available-model").generate(_request(), RequirementArtifact)
    assert result.provider == "anthropic"
    assert result.content["normalized_requirement"] == "Build service"
    assert result.input_tokens == 10


def test_anthropic_malformed_tool_output_is_rejected(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    class Response:
        status_code = 200

        def json(self):
            return {"content": [{"type": "text", "text": "not structured"}]}

    monkeypatch.setattr("httpx.post", lambda *args, **kwargs: Response())
    try:
        AnthropicAdapter(model="available-model").generate(_request(), RequirementArtifact)
        raise AssertionError("malformed provider output must fail")
    except ProviderError:
        pass
