from __future__ import annotations

import secrets
from typing import Annotated

import uvicorn
from fastapi import FastAPI, Header, HTTPException

from .llm import (
    LLMClient,
    LLMCompletionRequest,
    LLMCompletionResponse,
    LLMConfigurationError,
    LLMProviderError,
    LLMSettings,
    create_provider_client,
)


def create_llm_app(
    settings: LLMSettings | None = None,
    client: LLMClient | None = None,
) -> FastAPI:
    resolved_settings = settings or LLMSettings.from_environment()
    configuration_error: str | None = None
    resolved_client = client
    if resolved_client is None:
        try:
            resolved_client = create_provider_client(resolved_settings)
        except LLMConfigurationError as exc:
            configuration_error = str(exc)

    app = FastAPI(
        title="Research Mesh LLM Gateway",
        version="0.6.0",
        description="Provider-neutral completion gateway for research agents.",
    )
    app.state.llm_client = resolved_client

    def require_gateway_auth(authorization: str | None) -> None:
        if resolved_settings.gateway_token is None:
            return
        expected = f"Bearer {resolved_settings.gateway_token.get_secret_value()}"
        if authorization is None or not secrets.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="invalid gateway bearer token")

    @app.get("/health")
    async def health() -> dict[str, object]:
        if configuration_error:
            status = "misconfigured"
        elif resolved_client is None:
            status = "disabled"
        else:
            status = "ok"
        return {
            "status": status,
            "configuration": resolved_settings.public_configuration(),
            "configuration_error": configuration_error,
        }

    @app.get("/v1/models")
    async def models() -> dict[str, object]:
        return {
            "provider": resolved_settings.provider,
            "models": [resolved_settings.model] if resolved_settings.model else [],
            "allow_model_override": resolved_settings.allow_model_override,
        }

    @app.post("/v1/complete", response_model=LLMCompletionResponse)
    async def complete(
        request: LLMCompletionRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> LLMCompletionResponse:
        require_gateway_auth(authorization)
        if configuration_error:
            raise HTTPException(status_code=503, detail=configuration_error)
        if resolved_client is None:
            raise HTTPException(status_code=503, detail="LLM gateway is disabled")
        try:
            return await resolved_client.complete(request)
        except LLMConfigurationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except LLMProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return app


app = create_llm_app()


def serve() -> None:
    uvicorn.run("research_mesh.llm_service:app", host="127.0.0.1", port=8020)
