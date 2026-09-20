from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from acps_sdk.aic import validate_aic_format


class PlatformConfigurationError(RuntimeError):
    """Raised when platform mode would start without a trusted identity boundary."""


PARTNER_SLUGS = ("literature", "experiment", "analysis", "review")


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise PlatformConfigurationError(
        f"{name} must be one of true/false, 1/0, yes/no, or on/off"
    )


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError as exc:
        raise PlatformConfigurationError(f"{name} must be a number") from exc
    if parsed <= 0:
        raise PlatformConfigurationError(f"{name} must be greater than zero")
    return parsed


@dataclass(frozen=True)
class TLSMaterial:
    cert_file: Path
    key_file: Path
    trust_bundle_file: Path

    def validate(self, label: str) -> None:
        missing = [
            str(path)
            for path in (self.cert_file, self.key_file, self.trust_bundle_file)
            if not path.is_file()
        ]
        if missing:
            raise PlatformConfigurationError(
                f"{label} TLS material is missing: {', '.join(missing)}"
            )


def _tls_material(prefix: str) -> TLSMaterial | None:
    cert = os.getenv(f"RESEARCH_MESH_{prefix}_CERT_FILE", "").strip()
    key = os.getenv(f"RESEARCH_MESH_{prefix}_KEY_FILE", "").strip()
    trust = os.getenv(f"RESEARCH_MESH_{prefix}_TRUST_BUNDLE_FILE", "").strip()
    provided = [bool(cert), bool(key), bool(trust)]
    if not any(provided):
        return None
    if not all(provided):
        raise PlatformConfigurationError(
            f"RESEARCH_MESH_{prefix}_CERT_FILE, _KEY_FILE and "
            "_TRUST_BUNDLE_FILE must be configured together"
        )
    return TLSMaterial(Path(cert).expanduser(), Path(key).expanduser(), Path(trust).expanduser())


def _validate_https(url: str, label: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise PlatformConfigurationError(f"{label} must be an absolute HTTPS URL")


def _validate_formal_aic(aic: str, label: str) -> None:
    valid, reason = validate_aic_format(aic)
    if not valid:
        raise PlatformConfigurationError(f"{label} is not a formal AIC: {reason}")


@dataclass(frozen=True)
class RuntimeSettings:
    mode: str
    identity_binding_enabled: bool
    mtls_enabled: bool
    leader_aic: str
    discovery_url: str
    discovery_fallback_local: bool
    discovery_timeout_seconds: float
    amp_enabled: bool
    amp_log_dir: Path
    amp_heartbeat_interval_seconds: float
    public_base_url: str

    @classmethod
    def from_env(cls) -> "RuntimeSettings":
        mode = os.getenv("RESEARCH_MESH_MODE", "local").strip().lower()
        if mode not in {"local", "platform"}:
            raise PlatformConfigurationError(
                "RESEARCH_MESH_MODE must be 'local' or 'platform'"
            )
        platform = mode == "platform"
        return cls(
            mode=mode,
            identity_binding_enabled=_env_bool(
                "RESEARCH_MESH_IDENTITY_BINDING", platform
            ),
            mtls_enabled=_env_bool("RESEARCH_MESH_MTLS_ENABLED", platform),
            leader_aic=os.getenv(
                "RESEARCH_MESH_LEADER_AIC", "local.research-mesh.leader"
            ).strip(),
            discovery_url=os.getenv("RESEARCH_MESH_DISCOVERY_URL", "").strip().rstrip("/"),
            discovery_fallback_local=_env_bool(
                "RESEARCH_MESH_DISCOVERY_FALLBACK_LOCAL", not platform
            ),
            discovery_timeout_seconds=_env_float(
                "RESEARCH_MESH_DISCOVERY_TIMEOUT_SECONDS", 15.0
            ),
            amp_enabled=_env_bool("RESEARCH_MESH_AMP_ENABLED", platform),
            amp_log_dir=Path(
                os.getenv("RESEARCH_MESH_AMP_LOG_DIR", "artifacts/amp")
            ).expanduser(),
            amp_heartbeat_interval_seconds=_env_float(
                "RESEARCH_MESH_AMP_HEARTBEAT_INTERVAL_SECONDS", 30.0
            ),
            public_base_url=os.getenv("RESEARCH_MESH_PUBLIC_BASE_URL", "").strip().rstrip("/"),
        )

    def aic_for(self, slug: str) -> str:
        if slug == "leader":
            return self.leader_aic
        if slug not in PARTNER_SLUGS:
            raise PlatformConfigurationError(f"unknown agent slug: {slug}")
        return os.getenv(
            f"RESEARCH_MESH_{slug.upper()}_AIC",
            f"local.research-mesh.{slug}",
        ).strip()

    def endpoint_for(self, slug: str, default_port: int) -> str:
        configured = os.getenv(f"RESEARCH_MESH_{slug.upper()}_URL", "").strip()
        if configured:
            return configured
        if self.public_base_url:
            return f"{self.public_base_url}/{slug}/rpc"
        return f"http://127.0.0.1:{default_port}/rpc"

    def client_tls_material(self) -> TLSMaterial | None:
        return _tls_material("LEADER_CLIENT")

    def server_tls_material(self, slug: str) -> TLSMaterial | None:
        return _tls_material(f"{slug.upper()}_SERVER")

    def validate_agent(self, slug: str, *, require_discovery: bool = False) -> None:
        if self.mode != "platform":
            return
        if not self.identity_binding_enabled:
            raise PlatformConfigurationError(
                "platform mode requires RESEARCH_MESH_IDENTITY_BINDING=true"
            )
        if not self.mtls_enabled:
            raise PlatformConfigurationError(
                "platform mode requires RESEARCH_MESH_MTLS_ENABLED=true"
            )
        _validate_formal_aic(self.aic_for(slug), f"{slug} AIC")
        server_material = self.server_tls_material(slug)
        if server_material is None:
            raise PlatformConfigurationError(
                f"platform mode requires {slug.upper()}_SERVER TLS material"
            )
        server_material.validate(f"{slug} server")
        endpoint = os.getenv(f"RESEARCH_MESH_{slug.upper()}_URL", "").strip()
        if not endpoint and self.public_base_url:
            endpoint = f"{self.public_base_url}/{slug}/rpc"
        _validate_https(endpoint, f"{slug} endpoint")
        if slug == "leader" or require_discovery:
            client_material = self.client_tls_material()
            if client_material is None:
                raise PlatformConfigurationError(
                    "platform Leader requires LEADER_CLIENT TLS material"
                )
            client_material.validate("leader client")
        if require_discovery:
            if not self.discovery_url:
                raise PlatformConfigurationError(
                    "platform Leader requires RESEARCH_MESH_DISCOVERY_URL"
                )
            _validate_https(self.discovery_url, "Discovery URL")


def runtime_settings() -> RuntimeSettings:
    return RuntimeSettings.from_env()
