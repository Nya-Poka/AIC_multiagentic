from __future__ import annotations

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException

from .leader import AgentExecutionError, AgentInputRequired, ResearchLeader
from .registry import LocalCapabilityRegistry, default_registry
from .schemas import ResearchReport, ResearchRequest


def create_app(registry: LocalCapabilityRegistry | None = None) -> FastAPI:
    app = FastAPI(
        title="Research Mesh MVP",
        version="0.2.0",
        description="AIP Direct RPC minimal loop for research collaboration.",
    )
    resolved_registry = registry or default_registry()
    app.state.partner_transport_factory = None

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "mode": "local-aip-direct-rpc",
            "agents": len(resolved_registry.list_agents()),
        }

    @app.get("/dev/agents")
    async def list_agents() -> list[dict[str, object]]:
        return [agent.model_dump(mode="json") for agent in resolved_registry.list_agents()]

    @app.post("/research/run", response_model=ResearchReport)
    async def run_research(request: ResearchRequest) -> ResearchReport:
        leader = ResearchLeader(
            resolved_registry,
            transport_factory=app.state.partner_transport_factory,
        )
        try:
            return await leader.run(request)
        except AgentInputRequired as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except AgentExecutionError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return app


app = create_app()


def serve() -> None:
    uvicorn.run("research_mesh.api:app", host="127.0.0.1", port=8000, reload=False)


def asgi_transport_factory(partner_apps: dict[str, FastAPI]):
    """Route each discovered endpoint to its independent ASGI Partner app."""

    return lambda agent: httpx.ASGITransport(app=partner_apps[agent.slug])
