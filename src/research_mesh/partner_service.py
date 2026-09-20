from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI, HTTPException

from acps_sdk.aip.aip_peer_cert import (
    AipPeerCertH11Protocol,
    AipPeerCertificateMiddleware,
)
from acps_sdk.aip.aip_rpc_server import add_aip_rpc_router

from .acs import build_acs
from .config import RuntimeSettings, runtime_settings
from .observability import AmpRuntime
from .partners import PARTNER_SPECS, PartnerSpec, Processor, make_handlers
from .registry import PARTNER_PORTS
from .tls import build_server_ssl_context


def get_partner_spec(slug: str) -> PartnerSpec:
    for spec in PARTNER_SPECS:
        if spec.slug == slug:
            return spec
    raise ValueError(f"unknown partner slug: {slug}")


def create_partner_app(
    slug: str,
    rpc_url: str | None = None,
    processor: Processor | None = None,
    settings: RuntimeSettings | None = None,
) -> FastAPI:
    resolved_settings = settings or runtime_settings()
    spec = replace(get_partner_spec(slug), aic=resolved_settings.aic_for(slug))
    if processor is not None:
        spec = replace(spec, processor=processor)
    resolved_rpc_url = rpc_url or resolved_settings.endpoint_for(
        slug, PARTNER_PORTS[slug]
    )
    amp_runtime = AmpRuntime.create(
        resolved_settings,
        aic=spec.aic,
        service_name=f"research-mesh-{slug}",
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        amp_runtime.start()
        try:
            yield
        finally:
            await amp_runtime.stop()

    app = FastAPI(
        title=f"Research Mesh - {spec.name}",
        version="0.5.0",
        description=f"Independent AIP Partner providing {spec.skill}.",
        lifespan=lifespan,
    )
    app.state.amp_runtime = amp_runtime
    if resolved_settings.identity_binding_enabled:
        app.add_middleware(AipPeerCertificateMiddleware)
    add_aip_rpc_router(
        app,
        "/rpc",
        make_handlers(spec),
        local_aic=spec.aic,
        identity_binding_enabled=resolved_settings.identity_binding_enabled,
    )

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "role": "partner",
            "slug": spec.slug,
            "aic": spec.aic,
            "skill": spec.skill,
            "mode": resolved_settings.mode,
            "identity_binding": resolved_settings.identity_binding_enabled,
        }

    @app.get("/acs")
    async def acs() -> dict[str, object]:
        return build_acs(
            slug,
            settings=resolved_settings,
            endpoint=resolved_rpc_url,
        )

    @app.get("/dev/spec")
    async def service_spec() -> dict[str, object]:
        return {
            "slug": spec.slug,
            "port": PARTNER_PORTS[slug],
            "rpc": "/rpc",
        }

    return app


def serve() -> None:
    slug = os.getenv("RESEARCH_MESH_PARTNER", "literature")
    if slug not in PARTNER_PORTS:
        raise ValueError(f"unknown partner slug: {slug}")
    settings = runtime_settings()
    settings.validate_agent(slug)
    port = int(os.getenv("RESEARCH_MESH_PORT", str(PARTNER_PORTS[slug])))
    host = os.getenv(
        "RESEARCH_MESH_HOST", "0.0.0.0" if settings.mode == "platform" else "127.0.0.1"
    )
    app_to_run = create_partner_app(slug, settings=settings)
    if settings.mtls_enabled:
        material = settings.server_tls_material(slug)
        if material is None:
            raise RuntimeError(f"{slug} server TLS material is not configured")

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
    uvicorn.run(app_to_run, host=host, port=port)


_configured_slug = os.getenv("RESEARCH_MESH_PARTNER", "literature")
try:
    app = create_partner_app(_configured_slug)
except ValueError as exc:
    app = FastAPI(title="Research Mesh - invalid partner configuration")
    error_message = str(exc)

    @app.get("/health")
    async def invalid_health() -> dict[str, str]:
        raise HTTPException(status_code=500, detail=error_message)


if __name__ == "__main__":
    serve()
