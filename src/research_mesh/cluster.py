from __future__ import annotations

from fastapi import FastAPI

from .partner_service import create_partner_app
from .partners import PARTNER_SPECS


def create_partner_apps() -> dict[str, FastAPI]:
    """Create four isolated ASGI apps for deterministic in-process tests."""

    return {spec.slug: create_partner_app(spec.slug) for spec in PARTNER_SPECS}
