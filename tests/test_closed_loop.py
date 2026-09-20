from __future__ import annotations

import asyncio

import httpx

from research_mesh.api import asgi_transport_factory, create_app
from research_mesh.cluster import create_partner_apps
from research_mesh.registry import default_registry
from research_mesh.sample_data import sample_request


async def fake_literature_search(payload: dict) -> dict:
    evidence = [
        {**document, "verification": "test-provider"}
        for document in payload["request"]["documents"]
    ]
    return {
        "evidence": evidence,
        "count": len(evidence),
        "external_count": 0,
        "seed_count": len(evidence),
        "provider": {"name": "test-provider", "status": "ok"},
    }


def isolated_test_system():
    partner_apps = create_partner_apps(
        {"literature": fake_literature_search}
    )
    endpoints = {
        slug: f"http://{slug}.test/rpc" for slug in partner_apps
    }
    app = create_app(default_registry(endpoints))
    app.state.partner_transport_factory = asgi_transport_factory(partner_apps)
    return app


def test_full_research_loop_over_http_and_aip() -> None:
    async def scenario() -> None:
        app = isolated_test_system()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://research-mesh.test",
        ) as client:
            response = await client.post(
                "/research/run",
                json=sample_request().model_dump(mode="json"),
            )
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["status"] == "completed"
        assert report["literature"]["count"] == 2
        assert report["analysis"]["n"] == 7
        assert report["review"]["passed"] is True
        assert len(report["provenance"]) == 4
        assert {event["final_state"] for event in report["provenance"]} == {"completed"}
        assert {event["agent_slug"] for event in report["provenance"]} == {
            "literature",
            "experiment",
            "analysis",
            "review",
        }

    asyncio.run(scenario())


def test_pipeline_fails_closed_when_required_data_is_missing() -> None:
    async def scenario() -> None:
        app = isolated_test_system()
        request = sample_request().model_copy(update={"dataset": None})
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://research-mesh.test",
        ) as client:
            response = await client.post(
                "/research/run",
                json=request.model_dump(mode="json"),
            )
        assert response.status_code == 422
        assert "analysis requires input" in response.json()["detail"]

    asyncio.run(scenario())
