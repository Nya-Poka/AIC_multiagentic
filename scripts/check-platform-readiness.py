from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from acps_sdk.aic import validate_aic_format  # noqa: E402
from research_mesh.acs import AGENT_CARDS  # noqa: E402
from research_mesh.config import (  # noqa: E402
    PlatformConfigurationError,
    runtime_settings,
)
from research_mesh.registry import PARTNER_PORTS  # noqa: E402


PORTS = {"leader": 8000, **PARTNER_PORTS}


def main() -> int:
    failures: list[str] = []
    try:
        settings = runtime_settings()
    except PlatformConfigurationError as exc:
        print(f"[FAIL] configuration: {exc}")
        return 1

    if settings.mode != "platform":
        failures.append("RESEARCH_MESH_MODE must be platform")
    if settings.discovery_fallback_local:
        failures.append(
            "RESEARCH_MESH_DISCOVERY_FALLBACK_LOCAL must be false for platform readiness"
        )
    if not settings.amp_enabled:
        failures.append("RESEARCH_MESH_AMP_ENABLED must be true for platform readiness")
    for slug in AGENT_CARDS:
        try:
            settings.validate_agent(slug, require_discovery=slug == "leader")
        except PlatformConfigurationError as exc:
            failures.append(str(exc))

    generated = ROOT / "deploy" / "acps" / "generated"
    for slug in AGENT_CARDS:
        path = generated / f"{slug}.acs.json"
        if not path.is_file():
            failures.append(f"missing generated ACS: {path}")
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            failures.append(f"invalid ACS {path}: {exc}")
            continue
        if document.get("protocolVersion") != "02.02":
            failures.append(f"{path.name}: protocolVersion must be 02.02")
        aic = document.get("aic")
        if not isinstance(aic, str) or not validate_aic_format(aic)[0]:
            failures.append(f"{path.name}: run agent sync after approval to write formal AIC")
        elif aic != settings.aic_for(slug):
            failures.append(f"{path.name}: ACS AIC does not match .env")
        endpoints = document.get("endPoints") or []
        first_endpoint = endpoints[0] if endpoints and isinstance(endpoints[0], dict) else {}
        endpoint = str(first_endpoint.get("url", ""))
        if urlparse(endpoint).scheme != "https":
            failures.append(f"{path.name}: JSONRPC endpoint must use HTTPS")
        if endpoint != settings.endpoint_for(slug, PORTS[slug]):
            failures.append(f"{path.name}: ACS endpoint does not match runtime .env")
        endpoint_host = urlparse(endpoint).hostname or ""
        certificate = document.get("certificate")
        certificate = certificate if isinstance(certificate, dict) else {}
        alt_names = certificate.get("altNames")
        alt_names = alt_names if isinstance(alt_names, dict) else {}
        declared_hosts = [
            *(alt_names.get("dns") or []),
            *(alt_names.get("ip") or []),
        ]
        if endpoint_host not in declared_hosts:
            failures.append(
                f"{path.name}: certificate.altNames does not cover endpoint host"
            )
        provider = document.get("provider")
        provider = provider if isinstance(provider, dict) else {}
        if provider.get("organization") in {None, "", "参赛团队待填写"}:
            failures.append(f"{path.name}: provider.organization is still a placeholder")
        registrations = provider.get("domainRegistrations") or []
        registered_domains = [
            str(item.get("domain", "")).lower().strip(".")
            for item in registrations
            if isinstance(item, dict)
        ]
        invalid_registration_types = [
            item
            for item in registrations
            if isinstance(item, dict)
            and item.get("registrationType") not in {"ICP", "WHOIS"}
        ]
        if invalid_registration_types:
            failures.append(
                f"{path.name}: domain registration type must be ICP or WHOIS"
            )
        if not registered_domains:
            failures.append(f"{path.name}: provider.domainRegistrations is missing")
        else:
            urls_to_check = [("endpoint", endpoint)]
            if provider.get("url"):
                urls_to_check.append(("provider.url", str(provider["url"])))
            for label, candidate_url in urls_to_check:
                candidate_host = urlparse(candidate_url).hostname or ""
                if not any(
                    candidate_host.lower() == domain
                    or candidate_host.lower().endswith(f".{domain}")
                    for domain in registered_domains
                    if domain
                ):
                    failures.append(
                        f"{path.name}: {label} host is not covered by domainRegistrations"
                    )

    if failures:
        print("Platform readiness check failed:")
        for failure in dict.fromkeys(failures):
            print(f"  - {failure}")
        return 1
    print("Platform readiness check passed for Leader and four Partners.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
