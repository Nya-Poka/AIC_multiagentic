from research_mesh.registry import AgentNotFoundError, default_registry


def test_registry_discovers_exact_skill() -> None:
    registry = default_registry({"analysis": "http://analysis.test/rpc"})
    agent = registry.require("data-analysis", "统计分析")
    assert agent.slug == "analysis"
    assert agent.endpoint == "http://analysis.test/rpc"


def test_registry_rejects_unknown_skill() -> None:
    registry = default_registry()
    try:
        registry.require("unknown-skill")
    except AgentNotFoundError as exc:
        assert "unknown-skill" in str(exc)
    else:
        raise AssertionError("unknown skill should not resolve")
