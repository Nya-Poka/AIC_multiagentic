from __future__ import annotations

import asyncio
import json
import sys

import httpx

from .api import asgi_transport_factory, create_app
from .cluster import create_partner_apps
from .registry import default_registry
from .sample_data import sample_request


async def run_demo() -> dict:
    endpoints = {
        slug: f"http://{slug}.research-mesh.local/rpc"
        for slug in ("literature", "experiment", "analysis", "review")
    }
    registry = default_registry(endpoints)
    partner_apps = create_partner_apps()
    app = create_app(registry)
    app.state.partner_transport_factory = asgi_transport_factory(partner_apps)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://research-mesh.local",
    ) as client:
        response = await client.post(
            "/research/run",
            json=sample_request().model_dump(mode="json"),
        )
        response.raise_for_status()
        return response.json()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    report = asyncio.run(run_demo())
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
