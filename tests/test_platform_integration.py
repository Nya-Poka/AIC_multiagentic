from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from acps_sdk.acs import AgentCapabilitySpec
from acps_sdk.aip.aip_base_model import StructuredDataItem, TaskState
from acps_sdk.aip.aip_rpc_client import AipRpcClient

from research_mesh.acs import build_acs
from research_mesh.api import asgi_transport_factory, create_app
from research_mesh.cluster import create_partner_apps
from research_mesh.config import PlatformConfigurationError, RuntimeSettings
from research_mesh.observability import AmpRuntime
from research_mesh.registry import ADPCapabilityRegistry, default_registry
from research_mesh.sample_data import sample_request


async def fake_literature_search(payload: dict) -> dict:
    evidence = [
        {**document, "verification": "test-provider"}
        for document in payload["request"]["documents"]
    ]
    return {"evidence": evidence, "count": len(evidence)}


def test_platform_mode_fails_closed_without_formal_identity(monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_MESH_MODE", "platform")
    monkeypatch.delenv("RESEARCH_MESH_LEADER_AIC", raising=False)
    settings = RuntimeSettings.from_env()
    assert settings.discovery_fallback_local is False
    with pytest.raises(PlatformConfigurationError, match="formal AIC"):
        settings.validate_agent("leader", require_discovery=True)


def test_registration_acs_declares_mtls_and_uses_empty_unassigned_aic(monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_MESH_MODE", "local")
    settings = RuntimeSettings.from_env()
    acs = build_acs(
        "literature",
        settings=settings,
        endpoint="https://agents.example.edu.cn/literature/rpc",
        registration_template=True,
    )
    assert acs["aic"] == ""
    assert acs["active"] is False
    assert "+08:00" in acs["lastModifiedTime"]
    assert acs["protocolVersion"] == "02.02"
    assert acs["securitySchemes"]["mtls"]["type"] == "mutualTLS"
    assert acs["endPoints"][0]["security"] == [{"mtls": []}]
    assert acs["certificate"]["altNames"]["dns"] == [
        "agents.example.edu.cn"
    ]
    parsed = AgentCapabilitySpec.model_validate(acs)
    assert parsed.protocol_version == "02.02"
    assert parsed.skills[0].id == "literature-search"


def test_leader_acs_exposes_searchable_research_capabilities(monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_MESH_MODE", "local")
    settings = RuntimeSettings.from_env()
    acs = build_acs(
        "leader",
        settings=settings,
        endpoint="https://agents.example.edu.cn/leader/rpc",
        registration_template=True,
    )

    skill = acs["skills"][0]
    assert acs["version"] == "0.6.0"
    assert skill["version"] == "0.6.0"
    assert skill["id"] == "research-collaboration.orchestration"
    assert skill["name"] == "一站式科研助理与多智能体科研协作编排"
    assert {
        "文献检索",
        "实验设计",
        "数据分析",
        "统计分析",
        "规范复核",
        "可追溯",
    }.issubset(skill["tags"])
    assert len(skill["examples"]) >= 5


def test_adp_registry_maps_discovery_response() -> None:
    async def scenario() -> None:
        aic = "1.2.156.3088.1.1.D55UOU.NEBZUA.1.0QLD"
        dead_aic = "1.2.156.3088.1.1.SC64YN.Z5LSGY.1.0NMQ"

        async def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert request.url.path == "/discovery/acps-adp-v2/discover"
            assert body["type"] == "filtered"
            assert body["filter"] == {
                "conditions": [
                    {
                        "field": "skills.id",
                        "op": "eq",
                        "value": "literature-search",
                    },
                    {"field": "active", "op": "eq", "value": True},
                ],
                "logic": "and",
            }
            return httpx.Response(
                200,
                json={
                    "result": {
                        "acsMap": {
                            dead_aic: {
                                "aic": dead_aic,
                                "active": True,
                                "name": "Offline Literature",
                                "endPoints": [
                                    {
                                        "url": "https://offline.example/rpc",
                                        "transport": "JSONRPC",
                                    }
                                ],
                                "skills": [{"id": "literature-search"}],
                            },
                            aic: {
                                "aic": aic,
                                "active": True,
                                "name": "Remote Literature",
                                "endPoints": [
                                    {
                                        "url": "https://partner.example/rpc",
                                        "transport": "JSONRPC",
                                    }
                                ],
                                "skills": [
                                    {
                                        "id": "literature-search",
                                        "tags": ["Crossref", "DOI"],
                                    }
                                ],
                            }
                        },
                        "agents": [
                            {
                                "group": "literature",
                                "agentSkills": [
                                    {
                                        "aic": dead_aic,
                                        "skillId": "literature-search",
                                        "ranking": 0,
                                    },
                                    {
                                        "aic": aic,
                                        "skillId": "literature-search",
                                        "ranking": 1,
                                    }
                                ],
                            }
                        ],
                        "aliveMap": {
                            dead_aic: {"alive": False},
                            aic: {"alive": True},
                        },
                    }
                },
            )

        registry = ADPCapabilityRegistry(
            "https://registry.example/discovery",
            ssl_context=None,
            transport=httpx.MockTransport(handler),
        )
        agent = await registry.require("literature-search", "sleep")
        assert agent.aic == aic
        assert agent.slug == "literature"
        assert agent.endpoint == "https://partner.example/rpc"

    asyncio.run(scenario())


def test_amp_runtime_writes_periodic_heartbeat(monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_MESH_MODE", "local")
    log_dir = Path(".pytest_cache") / "amp-tests" / uuid.uuid4().hex
    settings = replace(
        RuntimeSettings.from_env(),
        amp_enabled=True,
        amp_log_dir=log_dir,
        amp_heartbeat_interval_seconds=0.01,
    )

    async def scenario() -> None:
        runtime = AmpRuntime.create(
            settings,
            aic="local.research-mesh.leader",
            service_name="research-mesh-leader",
        )
        runtime.start()
        await asyncio.sleep(0.03)
        await runtime.stop()

    asyncio.run(scenario())
    lines = (log_dir / "research-mesh-leader-heartbeat.ndjson").read_text(
        encoding="utf-8"
    ).splitlines()
    assert lines
    assert json.loads(lines[0])["aic"] == "local.research-mesh.leader"


def test_leader_is_callable_through_aip_rpc(monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_MESH_MODE", "local")
    log_dir = Path(".pytest_cache") / "amp-tests" / uuid.uuid4().hex
    settings = replace(
        RuntimeSettings.from_env(),
        amp_enabled=True,
        amp_log_dir=log_dir,
    )

    async def scenario() -> None:
        partner_apps = create_partner_apps(
            {"literature": fake_literature_search}
        )
        endpoints = {slug: f"http://{slug}.test/rpc" for slug in partner_apps}
        app = create_app(default_registry(endpoints), settings=settings)
        app.state.partner_transport_factory = asgi_transport_factory(partner_apps)
        client = AipRpcClient(
            partner_url="http://leader.test/rpc",
            leader_id="local.external.caller",
            transport=httpx.ASGITransport(app=app),
            identity_binding_enabled=False,
        )
        try:
            task = await client.start_task(
                session_id="external-session",
                task_id="external-task",
                user_input=json.dumps(
                    {"request": sample_request().model_dump(mode="json")},
                    ensure_ascii=False,
                ),
            )
            assert task.status.state == TaskState.AwaitingCompletion
            assert task.senderId == "local.research-mesh.leader"
            products = [
                item.data
                for product in task.products or []
                for item in product.dataItems
                if isinstance(item, StructuredDataItem)
            ]
            assert products[0]["result"]["status"] == "completed"
            completed = await client.complete_task(
                task_id=task.taskId,
                session_id=task.sessionId,
            )
            assert completed.status.state == TaskState.Completed
        finally:
            await client.close()

    asyncio.run(scenario())
    access_lines = (log_dir / "research-mesh-leader-access.ndjson").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(access_lines) >= 4


def test_leader_accepts_natural_language_and_embedded_json(monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_MESH_MODE", "local")
    monkeypatch.setenv("RESEARCH_MESH_INPUT_NORMALIZER_USE_LLM", "false")
    settings = RuntimeSettings.from_env()

    async def normalization_literature_search(payload: dict) -> dict:
        documents = payload["request"]["documents"]
        evidence = [
            {**document, "verification": "test-provider"}
            for document in documents
        ]
        if not evidence:
            evidence = [
                {
                    "title": "Input normalization integration fixture",
                    "authors": ["Research Mesh Tests"],
                    "year": 2026,
                    "summary": "A traceable fixture used to verify the AIP input path.",
                    "identifier": "10.0000/research-mesh.fixture",
                    "doi": "10.0000/research-mesh.fixture",
                    "provider": "test-provider",
                    "providers": ["test-provider"],
                    "has_abstract": True,
                    "verification": "test-provider",
                }
            ]
        return {"evidence": evidence, "count": len(evidence)}

    async def scenario() -> None:
        partner_apps = create_partner_apps(
            {"literature": normalization_literature_search}
        )
        endpoints = {slug: f"http://{slug}.test/rpc" for slug in partner_apps}
        app = create_app(default_registry(endpoints), settings=settings)
        app.state.partner_transport_factory = asgi_transport_factory(partner_apps)
        client = AipRpcClient(
            partner_url="http://leader.test/rpc",
            leader_id="local.external.caller",
            transport=httpx.ASGITransport(app=app),
            identity_binding_enabled=False,
        )
        try:
            natural_task = await client.start_task(
                session_id="natural-session",
                task_id="natural-task",
                user_input="研究睡眠时长是否影响大学生学习表现，并给出可复现实验方案",
            )
            assert natural_task.status.state == TaskState.AwaitingCompletion

            embedded_task = await client.start_task(
                session_id="embedded-session",
                task_id="embedded-task",
                user_input=(
                    "叮当上下文：用户已经补充输入。用户回答："
                    + json.dumps(
                        {
                            "question": "睡眠时长是否会影响大学生的学习表现和注意力水平",
                            "objective": "完成相关文献检索并设计一项可复现的观察性研究方案",
                        },
                        ensure_ascii=False,
                    )
                ),
            )
            assert embedded_task.status.state == TaskState.AwaitingCompletion
        finally:
            await client.close()

    asyncio.run(scenario())
