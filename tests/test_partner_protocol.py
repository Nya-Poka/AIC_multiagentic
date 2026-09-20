from __future__ import annotations

import asyncio
import json

import httpx
from acps_sdk.aip.aip_base_model import TaskState
from acps_sdk.aip.aip_rpc_client import AipRpcClient

from research_mesh.partner_service import create_partner_app
from research_mesh.sample_data import sample_request


async def fake_literature_search(payload: dict) -> dict:
    documents = payload["request"].get("documents", [])
    evidence = [
        {**document, "verification": "test-provider"} for document in documents
    ] or [
        {
            "title": "Retrieved test paper",
            "authors": ["Test Author"],
            "year": 2025,
            "summary": "Deterministic external metadata used by the protocol test.",
            "identifier": "10.5555/test-paper",
            "verification": "test-provider",
        }
    ]
    return {"evidence": evidence, "count": len(evidence)}


def test_literature_partner_runs_official_aip_state_machine() -> None:
    async def scenario() -> None:
        app = create_partner_app(
            "literature",
            "http://literature.test/rpc",
            processor=fake_literature_search,
        )
        client = AipRpcClient(
            partner_url="http://literature.test/rpc",
            leader_id="local.test.leader",
            transport=httpx.ASGITransport(app=app),
            identity_binding_enabled=False,
        )
        try:
            task = await client.start_task(
                session_id="session-test",
                task_id="task-literature-test",
                user_input=json.dumps(
                    {"request": sample_request().model_dump(mode="json")},
                    ensure_ascii=False,
                ),
            )
            assert task.status.state == TaskState.AwaitingCompletion
            assert task.senderId == "local.research-mesh.literature"
            completed = await client.complete_task(
                task_id=task.taskId,
                session_id="session-test",
            )
            assert completed.status.state == TaskState.Completed
        finally:
            await client.close()

    asyncio.run(scenario())


def test_literature_partner_searches_without_seed_documents() -> None:
    async def scenario() -> None:
        app = create_partner_app(
            "literature",
            "http://literature.test/rpc",
            processor=fake_literature_search,
        )
        client = AipRpcClient(
            partner_url="http://literature.test/rpc",
            leader_id="local.test.leader",
            transport=httpx.ASGITransport(app=app),
            identity_binding_enabled=False,
        )
        try:
            request = sample_request().model_copy(update={"documents": []})
            task = await client.start_task(
                session_id="session-missing",
                task_id="task-literature-missing",
                user_input=json.dumps(
                    {"request": request.model_dump(mode="json")},
                    ensure_ascii=False,
                ),
            )
            assert task.status.state == TaskState.AwaitingCompletion
        finally:
            await client.close()

    asyncio.run(scenario())


def test_literature_partner_requests_invalid_research_input() -> None:
    async def scenario() -> None:
        app = create_partner_app("literature", "http://literature.test/rpc")
        client = AipRpcClient(
            partner_url="http://literature.test/rpc",
            leader_id="local.test.leader",
            transport=httpx.ASGITransport(app=app),
            identity_binding_enabled=False,
        )
        try:
            task = await client.start_task(
                session_id="session-invalid",
                task_id="task-literature-invalid",
                user_input=json.dumps({"request": {}}, ensure_ascii=False),
            )
            assert task.status.state == TaskState.AwaitingInput
        finally:
            await client.close()

    asyncio.run(scenario())
