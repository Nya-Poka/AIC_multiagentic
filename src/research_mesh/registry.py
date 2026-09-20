from __future__ import annotations

import os
import re
import ssl
from collections.abc import Awaitable
from typing import Protocol
from urllib.parse import urljoin, urlparse

import httpx
from acps_sdk.adp import DiscoveryRequest, DiscoveryResponse

from .config import PlatformConfigurationError, RuntimeSettings, runtime_settings
from .schemas import AgentDescriptor
from .tls import build_client_ssl_context


class AgentNotFoundError(LookupError):
    pass


class DiscoveryUnavailableError(RuntimeError):
    pass


class CapabilityRegistry(Protocol):
    def require(
        self, required_skill: str, query: str = ""
    ) -> AgentDescriptor | Awaitable[AgentDescriptor]: ...

    def list_agents(self) -> list[AgentDescriptor]: ...


class LocalCapabilityRegistry:
    """Deterministic local discovery used for development and explicit fallback."""

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


SKILL_TO_SLUG = {
    "literature-search": "literature",
    "experiment-design": "experiment",
    "data-analysis": "analysis",
    "method-review": "review",
}


def _jsonrpc_endpoint(acs: dict[str, object]) -> str | None:
    endpoints = acs.get("endPoints")
    if not isinstance(endpoints, list):
        return None
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            continue
        if str(endpoint.get("transport", "")).upper() != "JSONRPC":
            continue
        url = endpoint.get("url")
        if isinstance(url, str) and url.strip():
            return url.strip()
    return None


def _skill_records(acs: dict[str, object]) -> list[dict[str, object]]:
    raw = acs.get("skills")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


class ADPCapabilityRegistry:
    """Online ACPs ADP client using the SDK's v02.02 models and mTLS."""

    def __init__(
        self,
        base_url: str,
        *,
        ssl_context: ssl.SSLContext | None,
        timeout_seconds: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
        max_redirects: int = 5,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.ssl_context = ssl_context
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self.max_redirects = max_redirects

    async def discover(
        self, required_skill: str, query: str = ""
    ) -> list[AgentDescriptor]:
        request = DiscoveryRequest(
            type="explicit",
            query=" ".join(part for part in (required_skill, query.strip()) if part),
            limit=10,
        )
        url = f"{self.base_url}/discover"
        client_kwargs: dict[str, object] = {
            "timeout": self.timeout_seconds,
            "follow_redirects": False,
        }
        if self.transport is not None:
            client_kwargs["transport"] = self.transport
        elif self.ssl_context is not None:
            client_kwargs["verify"] = self.ssl_context

        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                response: httpx.Response | None = None
                for redirect_count in range(self.max_redirects + 1):
                    response = await client.post(url, json=request.to_dict())
                    if response.status_code != 307:
                        break
                    if redirect_count >= self.max_redirects:
                        raise DiscoveryUnavailableError("ADP redirect limit exceeded")
                    location = response.headers.get("location", "").strip()
                    if not location:
                        raise DiscoveryUnavailableError(
                            "ADP redirect omitted the Location header"
                        )
                    redirected = urljoin(url, location)
                    if urlparse(redirected).scheme != "https" and self.transport is None:
                        raise DiscoveryUnavailableError(
                            "ADP refused a redirect to a non-HTTPS endpoint"
                        )
                    url = redirected
                if response is None:
                    raise DiscoveryUnavailableError("ADP produced no response")
                response.raise_for_status()
                envelope = DiscoveryResponse.model_validate(response.json())
        except DiscoveryUnavailableError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise DiscoveryUnavailableError(f"ADP request failed: {exc}") from exc

        if envelope.error is not None:
            raise DiscoveryUnavailableError(
                f"ADP error {envelope.error.code}: {envelope.error.message}"
            )
        if envelope.result is None:
            return []

        slug = SKILL_TO_SLUG.get(required_skill)
        candidates: list[AgentDescriptor] = []
        seen: set[str] = set()
        for aic, acs, matched_skill, _group in envelope.result.iter_agent_skills():
            if aic in seen:
                continue
            if not bool(acs.get("active", True)):
                continue
            alive_view = (envelope.result.alive_map or {}).get(aic)
            if isinstance(alive_view, dict) and alive_view.get("alive") is False:
                continue
            endpoint = _jsonrpc_endpoint(acs)
            if endpoint is None:
                continue
            if urlparse(endpoint).scheme != "https" and self.transport is None:
                continue
            records = _skill_records(acs)
            skill_ids = [
                str(item.get("id")) for item in records if item.get("id") is not None
            ]
            if required_skill not in skill_ids and not any(
                skill_id.endswith(f".{required_skill}") for skill_id in skill_ids
            ):
                if not matched_skill.skill_id.endswith(required_skill):
                    continue
            matched_record = next(
                (
                    item
                    for item in records
                    if str(item.get("id")) == matched_skill.skill_id
                ),
                {},
            )
            tags = matched_record.get("tags", [])
            candidates.append(
                AgentDescriptor(
                    slug=slug or re.sub(r"[^a-z0-9]+", "-", aic.lower()).strip("-"),
                    aic=aic,
                    name=str(acs.get("name") or aic),
                    endpoint=endpoint,
                    skills=skill_ids or [matched_skill.skill_id],
                    tags=[str(tag) for tag in tags] if isinstance(tags, list) else [],
                    active=True,
                    priority=max(0, 10_000 - matched_skill.ranking),
                )
            )
            seen.add(aic)
        return sorted(candidates, key=lambda item: item.priority, reverse=True)

    async def require(self, required_skill: str, query: str = "") -> AgentDescriptor:
        matches = await self.discover(required_skill, query)
        if not matches:
            raise AgentNotFoundError(f"no ADP agent provides skill: {required_skill}")
        return matches[0]

    def list_agents(self) -> list[AgentDescriptor]:
        return []


class HybridCapabilityRegistry:
    """Prefer live ADP discovery and fall back only when explicitly configured."""

    def __init__(
        self,
        online: ADPCapabilityRegistry,
        local: LocalCapabilityRegistry,
        *,
        fallback_local: bool,
    ) -> None:
        self.online = online
        self.local = local
        self.fallback_local = fallback_local

    async def require(self, required_skill: str, query: str = "") -> AgentDescriptor:
        try:
            return await self.online.require(required_skill, query)
        except (DiscoveryUnavailableError, AgentNotFoundError):
            if not self.fallback_local:
                raise
            return self.local.require(required_skill, query)

    def list_agents(self) -> list[AgentDescriptor]:
        return self.local.list_agents()


PARTNER_PORTS = {
    "literature": 8011,
    "experiment": 8012,
    "analysis": 8013,
    "review": 8014,
}


def default_registry(
    endpoint_overrides: dict[str, str] | None = None,
    *,
    settings: RuntimeSettings | None = None,
) -> LocalCapabilityRegistry:
    """Build local discovery records for independently running Partner services."""

    resolved = settings or runtime_settings()
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
                aic=resolved.aic_for(slug),
                name=name,
                endpoint=overrides.get(slug)
                or os.getenv(f"RESEARCH_MESH_{slug.upper()}_URL")
                or resolved.endpoint_for(slug, PARTNER_PORTS[slug]),
                skills=[skill],
                tags=tags,
                priority=10,
            )
            for slug, name, skill, tags in records
        ]
    )


def configured_registry(
    *,
    settings: RuntimeSettings | None = None,
) -> CapabilityRegistry:
    resolved = settings or runtime_settings()
    local = default_registry(settings=resolved)
    if not resolved.discovery_url:
        return local
    material = resolved.client_tls_material()
    if material is None:
        raise PlatformConfigurationError(
            "online ADP discovery requires LEADER_CLIENT TLS material"
        )
    online = ADPCapabilityRegistry(
        resolved.discovery_url,
        ssl_context=build_client_ssl_context(material),
        timeout_seconds=resolved.discovery_timeout_seconds,
    )
    return HybridCapabilityRegistry(
        online,
        local,
        fallback_local=resolved.discovery_fallback_local,
    )
