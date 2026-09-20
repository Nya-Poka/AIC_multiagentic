from __future__ import annotations

import os
import re

from .schemas import AgentDescriptor


class AgentNotFoundError(LookupError):
    pass


class LocalCapabilityRegistry:
    """A deterministic local discovery adapter with an ADP-shaped boundary.

    It is intentionally not presented as an official ADP server. The leader only
    depends on ``discover``, so this adapter can later be replaced by the official
    discovery-server client without changing orchestration logic.
    """

    def __init__(self, agents: list[AgentDescriptor]):
        self._agents = list(agents)

    def discover(self, required_skill: str, query: str = "") -> list[AgentDescriptor]:
        query_tokens = set(re.findall(r"[\w\u4e00-\u9fff]+", query.lower()))

        def score(agent: AgentDescriptor) -> tuple[int, int, str]:
            exact = 100 if required_skill in agent.skills else 0
            searchable = " ".join([agent.name, *agent.skills, *agent.tags]).lower()
            overlap = sum(1 for token in query_tokens if token and token in searchable)
            return (exact + overlap, agent.priority, agent.slug)

        candidates = [
            agent
            for agent in self._agents
            if agent.active and (required_skill in agent.skills or score(agent)[0] > 0)
        ]
        return sorted(candidates, key=score, reverse=True)

    def require(self, required_skill: str, query: str = "") -> AgentDescriptor:
        matches = self.discover(required_skill, query)
        if not matches:
            raise AgentNotFoundError(f"no active agent provides skill: {required_skill}")
        return matches[0]

    def list_agents(self) -> list[AgentDescriptor]:
        return list(self._agents)


PARTNER_PORTS = {
    "literature": 8011,
    "experiment": 8012,
    "analysis": 8013,
    "review": 8014,
}


def default_registry(
    endpoint_overrides: dict[str, str] | None = None,
) -> LocalCapabilityRegistry:
    """Build local discovery records for independently running Partner services."""

    overrides = endpoint_overrides or {}
    records = [
        (
            "literature",
            "文献证据智能体",
            "literature-search",
            ["文献", "证据", "引用", "Crossref", "DOI"],
        ),
        ("experiment", "实验设计智能体", "experiment-design", ["假设", "变量", "实验"]),
        ("analysis", "数据分析智能体", "data-analysis", ["统计", "数据", "复现"]),
        ("review", "规范复核智能体", "method-review", ["规范", "引用核验", "复核"]),
    ]
    return LocalCapabilityRegistry(
        [
            AgentDescriptor(
                slug=slug,
                aic=f"local.research-mesh.{slug}",
                name=name,
                endpoint=overrides.get(slug)
                or os.getenv(f"RESEARCH_MESH_{slug.upper()}_URL")
                or f"http://127.0.0.1:{PARTNER_PORTS[slug]}/rpc",
                skills=[skill],
                tags=tags,
                priority=10,
            )
            for slug, name, skill, tags in records
        ]
    )
