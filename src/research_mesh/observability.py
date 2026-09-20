from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from acps_sdk.amp import AccessEmitter, HeartbeatEmitter

from .config import RuntimeSettings


@dataclass
class AmpRuntime:
    access: AccessEmitter | None
    heartbeat: HeartbeatEmitter | None
    heartbeat_interval_seconds: float
    _heartbeat_task: asyncio.Task[None] | None = None

    @classmethod
    def disabled(cls) -> "AmpRuntime":
        return cls(None, None, 30.0)

    @classmethod
    def create(
        cls,
        settings: RuntimeSettings,
        *,
        aic: str,
        service_name: str,
    ) -> "AmpRuntime":
        if not settings.amp_enabled:
            return cls.disabled()
        safe_name = service_name.replace("/", "-").replace("\\", "-")
        root = Path(settings.amp_log_dir)
        resource = {"service.name": service_name, "agent.aic": aic}
        return cls(
            access=AccessEmitter(root / f"{safe_name}-access.ndjson", aic=aic, resource=resource),
            heartbeat=HeartbeatEmitter(root / f"{safe_name}-heartbeat.ndjson", aic=aic),
            heartbeat_interval_seconds=settings.amp_heartbeat_interval_seconds,
        )

    def start(self) -> None:
        if self.heartbeat is None or self._heartbeat_task is not None:
            return
        self._heartbeat_task = asyncio.create_task(
            self.heartbeat.run_periodic(self.heartbeat_interval_seconds),
            name="research-mesh-amp-heartbeat",
        )

    async def stop(self) -> None:
        if self._heartbeat_task is None:
            return
        self._heartbeat_task.cancel()
        try:
            await self._heartbeat_task
        except asyncio.CancelledError:
            pass
        self._heartbeat_task = None
