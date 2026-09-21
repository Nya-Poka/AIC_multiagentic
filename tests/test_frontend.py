from __future__ import annotations

import asyncio

import httpx

from research_mesh.api import create_app


def test_frontend_and_static_assets_are_served_without_browser_byok() -> None:
    async def scenario() -> None:
        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://research-mesh.test",
        ) as client:
            page = await client.get("/")
            stylesheet = await client.get("/assets/app.css")
            script = await client.get("/assets/app.js")

        assert page.status_code == 200
        assert 'id="research-form"' in page.text
        assert "四个专业角色，一条完整链路" in page.text
        assert "llm-api-key" not in page.text
        assert "llm-form" not in page.text
        assert "API Key" not in page.text
        assert 'id="measure"' not in page.text
        assert 'id="unit"' not in page.text
        assert 'id="values"' not in page.text
        assert "无需输入具体数值" in page.text
        assert "OpenAlex" in page.text
        assert "Semantic Scholar" in page.text

        assert stylesheet.status_code == 200
        assert "#e11d48" in stylesheet.text
        assert "@media (max-width: 680px)" in stylesheet.text

        assert script.status_code == 200
        assert 'fetch("/research/run"' in script.text
        assert 'fetch("/health"' in script.text
        assert "/ui/llm/test" not in script.text
        assert "api_key" not in script.text
        assert "localStorage" not in script.text
        assert "parseNumbers" not in script.text
        assert "dataset:" not in script.text
        assert "provider-status-list" in script.text

    asyncio.run(scenario())


def test_browser_byok_endpoint_is_not_exposed() -> None:
    async def scenario() -> None:
        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://research-mesh.test",
        ) as client:
            response = await client.post(
                "/ui/llm/test",
                json={
                    "base_url": "https://provider.test/v1",
                    "model": "demo-model",
                    "api_key": "browser-secret",
                },
            )

        assert response.status_code == 404
        assert "browser-secret" not in response.text

    asyncio.run(scenario())
