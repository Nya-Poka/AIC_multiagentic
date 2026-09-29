from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from acps_sdk.amp import AccessEmitter, HeartbeatEmitter

from .config import RuntimeSettings


@dataclass
class AmpRuntime:
    access: AccessEmitter | None
    heartbeat: HeartbeatEmitter | None
    heartbeat_interval_seconds: float
    access_path: Path | None = None
    heartbeat_path: Path | None = None
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
        access_path = root / f"{safe_name}-access.ndjson"
        heartbeat_path = root / f"{safe_name}-heartbeat.ndjson"
        return cls(
            access=AccessEmitter(access_path, aic=aic, resource=resource),
            heartbeat=HeartbeatEmitter(heartbeat_path, aic=aic),
            heartbeat_interval_seconds=settings.amp_heartbeat_interval_seconds,
            access_path=access_path,
            heartbeat_path=heartbeat_path,
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

    def status(self) -> dict[str, Any]:
        """Return non-sensitive AMP emitter health for service readiness checks."""

        if self.heartbeat is None or self.heartbeat_path is None:
            return {"enabled": False}
        task_running = self._heartbeat_task is not None and not self._heartbeat_task.done()
        try:
            stat = self.heartbeat_path.stat()
        except OSError:
            file_exists = False
            file_size = 0
            age_seconds: float | None = None
        else:
            file_exists = self.heartbeat_path.is_file()
            file_size = stat.st_size
            age_seconds = max(0.0, time.time() - stat.st_mtime)
        freshness_limit = max(5.0, self.heartbeat_interval_seconds * 2.5)
        fresh = bool(
            file_exists
            and file_size > 0
            and age_seconds is not None
            and age_seconds <= freshness_limit
        )
        return {
            "enabled": True,
            "task_running": task_running,
            "heartbeat_file_exists": file_exists,
            "heartbeat_fresh": fresh,
            "last_write_age_seconds": round(age_seconds, 3)
            if age_seconds is not None
            else None,
            "interval_seconds": self.heartbeat_interval_seconds,
        }
