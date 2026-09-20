from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
import uvicorn
from acps_sdk.aip.aip_peer_cert import (
    AipPeerCertH11Protocol,
    AipPeerCertificateMiddleware,
)
from acps_sdk.aip.aip_rpc_server import add_aip_rpc_router
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .acs import build_acs
from .config import RuntimeSettings, runtime_settings
from .leader import AgentExecutionError, AgentInputRequired, ResearchLeader
from .llm import (
    LLMCompletionRequest,
    LLMCompletionResponse,
    LLMConfigurationError,
    LLMConnectionTestRequest,
    LLMMessage,
    LLMProviderError,
    LLMSettings,
    OpenAICompatibleClient,
)
from .observability import AmpRuntime
from .partners import PartnerInputError, PartnerSpec, make_handlers
from .registry import (
    AgentNotFoundError,
    CapabilityRegistry,
    DiscoveryUnavailableError,
    configured_registry,
)
from .schemas import ResearchReport, ResearchRequest
from .tls import build_server_ssl_context


WEB_ROOT = Path(__file__).with_name("web")


def create_app(
    registry: CapabilityRegistry | None = None,
    *,
    settings: RuntimeSettings | None = None,
) -> FastAPI:
    resolved_settings = settings or runtime_settings()
    resolved_registry = registry or configured_registry(settings=resolved_settings)
    leader_aic = resolved_settings.aic_for("leader")
    amp_runtime = AmpRuntime.create(
        resolved_settings,
        aic=leader_aic,
        service_name="research-mesh-leader",
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        amp_runtime.start()
        try:
            yield
        finally:
            await amp_runtime.stop()

    app = FastAPI(
        title="Research Mesh MVP",
        version="0.5.0",
        description="AIP Direct RPC minimal loop for research collaboration.",
        lifespan=lifespan,
    )
    app.state.partner_transport_factory = None
    app.state.llm_transport = None
    app.state.amp_runtime = amp_runtime
    if resolved_settings.identity_binding_enabled:
        app.add_middleware(AipPeerCertificateMiddleware)
    app.mount("/assets", StaticFiles(directory=WEB_ROOT), name="assets")

    def new_leader() -> ResearchLeader:
        return ResearchLeader(
            resolved_registry,
            leader_aic=leader_aic,
            transport_factory=app.state.partner_transport_factory,
            settings=resolved_settings,
            amp_runtime=amp_runtime,
        )

    async def leader_processor(payload: dict[str, Any]) -> dict[str, Any]:
        raw_request = payload.get("request", payload)
        if not isinstance(raw_request, dict):
            raise PartnerInputError("payload.request must be a JSON object")
        request = ResearchRequest.model_validate(raw_request)
        report = await new_leader().run(request)
        return report.model_dump(mode="json")

    leader_spec = PartnerSpec(
        slug="leader",
        aic=leader_aic,
        name="科研协作 Leader 智能体",
        skill="research-collaboration.orchestration",
        processor=leader_processor,
    )
    add_aip_rpc_router(
        app,
        "/rpc",
        make_handlers(leader_spec),
        local_aic=leader_aic,
        identity_binding_enabled=resolved_settings.identity_binding_enabled,
    )

    @app.get("/", include_in_schema=False)
    async def frontend() -> FileResponse:
        return FileResponse(WEB_ROOT / "index.html")

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "mode": resolved_settings.mode,
            "protocol": "AIP-JSONRPC",
            "aic": leader_aic,
            "identity_binding": resolved_settings.identity_binding_enabled,
            "discovery": "adp" if resolved_settings.discovery_url else "local",
            "agents": len(resolved_registry.list_agents()),
        }

    @app.get("/acs")
    async def acs() -> dict[str, object]:
        return build_acs(
            "leader",
            settings=resolved_settings,
            endpoint=resolved_settings.endpoint_for("leader", 8000),
        )

    @app.get("/dev/agents")
    async def list_agents() -> list[dict[str, object]]:
        return [agent.model_dump(mode="json") for agent in resolved_registry.list_agents()]

    @app.post("/research/run", response_model=ResearchReport)
    async def run_research(request: ResearchRequest) -> ResearchReport:
        leader = new_leader()
        try:
            return await leader.run(request)
        except AgentInputRequired as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except AgentExecutionError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail="一个或多个 Partner 服务当前不可用",
            ) from exc
        except (AgentNotFoundError, DiscoveryUnavailableError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/ui/llm/test", response_model=LLMCompletionResponse)
    async def test_llm_connection(
        request: LLMConnectionTestRequest,
    ) -> LLMCompletionResponse:
        """Use a browser-supplied key once; never persist it in application state."""

        settings = LLMSettings(
            provider="openai-compatible",
            base_url=request.base_url,
            api_key=request.api_key,
            model=request.model,
            timeout_seconds=30,
            retries=0,
            max_tokens_limit=128,
        )
        try:
            client = OpenAICompatibleClient(
                settings,
                transport=app.state.llm_transport,
            )
            return await client.complete(
                LLMCompletionRequest(
                    messages=[LLMMessage(role="user", content=request.prompt)],
                    max_tokens=64,
                    temperature=0,
                )
            )
        except LLMConfigurationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except LLMProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return app


app = create_app()


def serve() -> None:
    settings = runtime_settings()
    settings.validate_agent("leader", require_discovery=settings.mode == "platform")
    host = os.getenv(
        "RESEARCH_MESH_HOST", "0.0.0.0" if settings.mode == "platform" else "127.0.0.1"
    )
    port = int(os.getenv("RESEARCH_MESH_PORT", "8000"))
    app_to_run = create_app(settings=settings)
    if settings.mtls_enabled:
        material = settings.server_tls_material("leader")
        if material is None:
            raise RuntimeError("leader server TLS material is not configured")

        def ssl_context_factory(_config: object, _default_factory: object):
            return build_server_ssl_context(material)

        uvicorn.run(
            app_to_run,
            host=host,
            port=port,
            http=AipPeerCertH11Protocol,
            ssl_context_factory=ssl_context_factory,
        )
        return
    uvicorn.run(app_to_run, host=host, port=port, reload=False)


def asgi_transport_factory(partner_apps: dict[str, FastAPI]):
    """Route each discovered endpoint to its independent ASGI Partner app."""

    return lambda agent: httpx.ASGITransport(app=partner_apps[agent.slug])


if __name__ == "__main__":
    serve()
