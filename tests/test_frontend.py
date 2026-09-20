from __future__ import annotations

import asyncio
import json

import httpx

from research_mesh.api import create_app


def test_frontend_and_static_assets_are_served() -> None:
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
        assert 'id="llm-api-key" type="password"' in page.text
        assert "localStorage" in page.text
        assert stylesheet.status_code == 200
        assert "#e11d48" in stylesheet.text
        assert script.status_code == 200
        assert "localStorage.setItem" not in script.text
        assert 'fetch("/research/run"' in script.text
        assert 'fetch("/ui/llm/test"' in script.text

    asyncio.run(scenario())


def test_byok_connection_uses_key_once_and_does_not_echo_it() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/chat/completions"
            assert request.headers["authorization"] == "Bearer browser-secret"
            payload = json.loads(request.content)
            assert payload["model"] == "demo-model"
            assert payload["messages"][0]["content"] == "Reply CONNECTED"
            return httpx.Response(
                200,
                headers={"x-request-id": "ui-test-1"},
                json={
                    "model": "demo-model",
                    "choices": [
                        {
                            "message": {"content": "CONNECTED"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 3,
                        "completion_tokens": 1,
                        "total_tokens": 4,
                    },
                },
            )

        app = create_app()
        app.state.llm_transport = httpx.MockTransport(handler)
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
                    "prompt": "Reply CONNECTED",
                },
            )

        assert response.status_code == 200, response.text
        assert response.json()["content"] == "CONNECTED"
        assert response.json()["request_id"] == "ui-test-1"
        assert "browser-secret" not in response.text
        assert app.state.llm_transport is not None

    asyncio.run(scenario())


def test_byok_rejects_unencrypted_remote_provider() -> None:
    async def scenario() -> None:
        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://research-mesh.test",
        ) as client:
            response = await client.post(
                "/ui/llm/test",
                json={
                    "base_url": "http://provider.example/v1",
                    "model": "demo-model",
                    "api_key": "browser-secret",
                },
            )
        assert response.status_code == 422
        assert "must use HTTPS" in response.json()["detail"]
        assert "browser-secret" not in response.text

    asyncio.run(scenario())


def test_byok_connect_error_returns_actionable_network_diagnostics() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("network unavailable", request=request)

        app = create_app()
        app.state.llm_transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://research-mesh.test",
        ) as client:
            response = await client.post(
                "/ui/llm/test",
                json={
                    "base_url": "https://api.deepseek.com",
                    "model": "deepseek-flash",
                    "api_key": "browser-secret",
                },
            )
        assert response.status_code == 502
        detail = response.json()["detail"]
        assert "outbound network" in detail
        assert "DNS" in detail
        assert "browser-secret" not in response.text

    asyncio.run(scenario())
