from __future__ import annotations

import asyncio
import json

import httpx
from pydantic import SecretStr

from research_mesh.llm import (
    LLMCompletionRequest,
    LLMCompletionResponse,
    LLMConfigurationError,
    LLMGatewayClient,
    LLMMessage,
    LLMSettings,
    LLMUsage,
    OpenAICompatibleClient,
)
from research_mesh.llm_service import create_llm_app


def completion_request(**updates) -> LLMCompletionRequest:
    values = {
        "messages": [LLMMessage(role="user", content="Summarize the evidence.")],
        "temperature": 0.2,
        "max_tokens": 256,
    }
    values.update(updates)
    return LLMCompletionRequest(**values)


def test_openai_compatible_adapter_normalizes_request_and_response() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/chat/completions"
            assert request.headers["authorization"] == "Bearer secret-key"
            payload = json.loads(request.content)
            assert payload == {
                "model": "test-model",
                "messages": [
                    {"role": "user", "content": "Summarize the evidence."}
                ],
                "max_tokens": 256,
                "stream": False,
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
            }
            return httpx.Response(
                200,
                headers={"x-request-id": "upstream-request-1"},
                json={
                    "id": "chatcmpl-1",
                    "model": "test-model-2026",
                    "choices": [
                        {
                            "message": {"content": '{"summary":"ok"}'},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 12,
                        "completion_tokens": 5,
                        "total_tokens": 17,
                    },
                },
            )

        settings = LLMSettings(
            provider="openai-compatible",
            base_url="https://provider.test/v1",
            api_key=SecretStr("secret-key"),
            model="test-model",
            retries=0,
        )
        client = OpenAICompatibleClient(
            settings, transport=httpx.MockTransport(handler)
        )
        response = await client.complete(
            completion_request(response_format="json_object")
        )

        assert response.request_id == "upstream-request-1"
        assert response.provider == "openai-compatible"
        assert response.model == "test-model-2026"
        assert response.content == '{"summary":"ok"}'
        assert response.usage == LLMUsage(
            input_tokens=12,
            output_tokens=5,
            total_tokens=17,
        )
        assert "secret-key" not in str(settings.public_configuration())

    asyncio.run(scenario())


def test_model_override_is_rejected_by_default() -> None:
    async def scenario() -> None:
        settings = LLMSettings(
            provider="openai-compatible",
            base_url="https://provider.test/v1",
            model="approved-model",
            retries=0,
        )
        client = OpenAICompatibleClient(settings)
        try:
            await client.complete(completion_request(model="unapproved-model"))
        except LLMConfigurationError as exc:
            assert "override" in str(exc)
        else:
            raise AssertionError("model override should have been rejected")

    asyncio.run(scenario())


def test_disabled_gateway_is_healthy_but_refuses_completions() -> None:
    async def scenario() -> None:
        app = create_llm_app(LLMSettings())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://llm.test",
        ) as client:
            health = await client.get("/health")
            completion = await client.post(
                "/v1/complete",
                json=completion_request().model_dump(mode="json"),
            )
        assert health.status_code == 200
        assert health.json()["status"] == "disabled"
        assert completion.status_code == 503

    asyncio.run(scenario())


def test_agents_use_authenticated_provider_neutral_gateway_client() -> None:
    class FakeProvider:
        async def complete(
            self, request: LLMCompletionRequest
        ) -> LLMCompletionResponse:
            return LLMCompletionResponse(
                request_id="fake-1",
                provider="fake-provider",
                model="fake-model",
                content=f"received:{request.messages[-1].content}",
                finish_reason="stop",
                usage=LLMUsage(total_tokens=9),
                latency_ms=1.25,
            )

    async def scenario() -> None:
        settings = LLMSettings(
            provider="openai-compatible",
            base_url="https://provider.test/v1",
            model="fake-model",
            gateway_token=SecretStr("gateway-secret"),
        )
        app = create_llm_app(settings, client=FakeProvider())
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://gateway.test",
        ) as unauthenticated:
            denied = await unauthenticated.post(
                "/v1/complete",
                json=completion_request().model_dump(mode="json"),
            )
        assert denied.status_code == 401

        gateway = LLMGatewayClient(
            base_url="http://gateway.test",
            token=SecretStr("gateway-secret"),
            transport=transport,
        )
        response = await gateway.complete(completion_request())
        assert response.provider == "fake-provider"
        assert response.content == "received:Summarize the evidence."

    asyncio.run(scenario())
