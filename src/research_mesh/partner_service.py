from __future__ import annotations

import os
from dataclasses import replace

from fastapi import FastAPI, HTTPException

from acps_sdk.aip.aip_rpc_server import add_aip_rpc_router

from .partners import PARTNER_SPECS, PartnerSpec, Processor, make_handlers
from .registry import PARTNER_PORTS


def get_partner_spec(slug: str) -> PartnerSpec:
    for spec in PARTNER_SPECS:
        if spec.slug == slug:
            return spec
    raise ValueError(f"unknown partner slug: {slug}")


def local_acs(spec: PartnerSpec, rpc_url: str) -> dict[str, object]:
    """Return a local ACS-shaped document for development and later registration."""

    capability_description = (
        "通过 Crossref 检索可追溯的真实文献元数据、DOI、作者和来源。"
        if spec.slug == "literature"
        else f"执行科研协作中的 {spec.skill} 任务。"
    )
    return {
        "aic": spec.aic,
        "active": True,
        "protocolVersion": "02.02",
        "name": spec.name,
        "description": f"科研协作平台的{spec.name}。{capability_description}",
        "version": "0.3.0",
        "provider": {"organization": "参赛团队待填写"},
        "securitySchemes": {},
        "endPoints": [{"url": rpc_url, "transport": "JSONRPC"}],
        "capabilities": {
            "streaming": False,
            "notification": False,
            "messageQueue": [],
        },
        "defaultInputModes": ["application/json", "text/plain"],
        "defaultOutputModes": ["application/json"],
        "skills": [
            {
                "id": f"research-collaboration.{spec.skill}",
                "name": spec.name,
                "description": capability_description,
                "version": "0.3.0",
                "tags": ["科研协作", spec.skill]
                + (["Crossref", "DOI"] if spec.slug == "literature" else []),
                "inputModes": ["application/json", "text/plain"],
                "outputModes": ["application/json"],
            }
        ],
    }


def create_partner_app(
    slug: str,
    rpc_url: str | None = None,
    processor: Processor | None = None,
) -> FastAPI:
    spec = get_partner_spec(slug)
    if processor is not None:
        spec = replace(spec, processor=processor)
    resolved_rpc_url = rpc_url or f"http://127.0.0.1:{PARTNER_PORTS[slug]}/rpc"
    app = FastAPI(
        title=f"Research Mesh - {spec.name}",
        version="0.3.0",
        description=f"Independent AIP Partner providing {spec.skill}.",
    )
    add_aip_rpc_router(
        app,
        "/rpc",
        make_handlers(spec),
        local_aic=spec.aic,
        identity_binding_enabled=False,
    )

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "role": "partner",
            "slug": spec.slug,
            "aic": spec.aic,
            "skill": spec.skill,
        }

    @app.get("/acs")
    async def acs() -> dict[str, object]:
        return local_acs(spec, resolved_rpc_url)

    @app.get("/dev/spec")
    async def service_spec() -> dict[str, object]:
        return {
            "slug": spec.slug,
            "port": PARTNER_PORTS[slug],
            "rpc": "/rpc",
        }

    return app


_configured_slug = os.getenv("RESEARCH_MESH_PARTNER", "literature")
try:
    app = create_partner_app(_configured_slug)
except ValueError as exc:
    app = FastAPI(title="Research Mesh - invalid partner configuration")
    error_message = str(exc)

    @app.get("/health")
    async def invalid_health() -> dict[str, str]:
        raise HTTPException(status_code=500, detail=error_message)
