from __future__ import annotations

from fastapi import FastAPI

from .partner_service import create_partner_app
from .partners import PARTNER_SPECS, Processor


def create_partner_apps(
    processor_overrides: dict[str, Processor] | None = None,
) -> dict[str, FastAPI]:
    """Create four isolated ASGI apps for deterministic in-process tests."""

    overrides = processor_overrides or {}
    return {
        spec.slug: create_partner_app(spec.slug, processor=overrides.get(spec.slug))
        for spec in PARTNER_SPECS
    }
