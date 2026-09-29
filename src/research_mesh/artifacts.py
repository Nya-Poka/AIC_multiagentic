from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .schemas import DatasetArtifactRef


class ArtifactError(ValueError):
    """A dataset artifact is invalid, missing, or outside the configured store."""


_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._\-\u3400-\u9fff]+")
_ALLOWED_EXTENSIONS = {".csv", ".json", ".xlsx"}
_ALLOWED_MEDIA_TYPES = {
    "text/csv",
    "application/csv",
    "application/json",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/octet-stream",
}


def _artifact_root() -> Path:
    return Path(
        os.getenv("RESEARCH_MESH_ARTIFACT_ROOT", "artifacts/uploads")
    ).expanduser()


def _max_dataset_bytes() -> int:
    raw = os.getenv("RESEARCH_MESH_DATASET_MAX_BYTES", str(10 * 1024 * 1024))
    try:
        value = int(raw)
    except ValueError:
        value = 10 * 1024 * 1024
    return max(1024, min(value, 25 * 1024 * 1024))


def _safe_filename(value: str) -> str:
    name = Path(value.strip()).name
    cleaned = _SAFE_FILENAME.sub("-", name).strip(".-")
    if not cleaned:
        cleaned = "dataset.csv"
    return cleaned[:255]


@dataclass(frozen=True)
class StoredArtifact:
    reference: DatasetArtifactRef
    path: Path


class LocalArtifactStore:
    """Small, content-addressed dataset store for the single-node deployment.

    AIP messages carry only the validated reference. Raw bytes remain on the
    server and are never forwarded to the LLM gateway.
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or _artifact_root()).resolve()

    def save_dataset(
        self,
        data: bytes,
        *,
        filename: str,
        media_type: str,
    ) -> DatasetArtifactRef:
        if not data:
            raise ArtifactError("dataset file is empty")
        if len(data) > _max_dataset_bytes():
            raise ArtifactError("dataset exceeds the configured size limit")
        safe_name = _safe_filename(filename)
        suffix = Path(safe_name).suffix.lower()
        if suffix not in _ALLOWED_EXTENSIONS:
            raise ArtifactError("dataset must be CSV, JSON, or XLSX")
        normalized_media = media_type.split(";", 1)[0].strip().lower()
        if normalized_media not in _ALLOWED_MEDIA_TYPES:
            raise ArtifactError(f"unsupported dataset media type: {normalized_media}")

        artifact_id = f"dataset-{uuid.uuid4().hex}"
        digest = hashlib.sha256(data).hexdigest()
        directory = self.root / artifact_id
        directory.mkdir(parents=True, exist_ok=False)
        path = directory / safe_name
        path.write_bytes(data)
        reference = DatasetArtifactRef(
            artifact_id=artifact_id,
            sha256=digest,
            filename=safe_name,
            media_type=normalized_media,
            size_bytes=len(data),
        )
        (directory / "metadata.json").write_text(
            reference.model_dump_json(indent=2), encoding="utf-8"
        )
        return reference

    def resolve_dataset(self, reference: DatasetArtifactRef) -> StoredArtifact:
        directory = (self.root / reference.artifact_id).resolve()
        try:
            directory.relative_to(self.root)
        except ValueError as exc:
            raise ArtifactError("dataset artifact escaped the configured store") from exc
        metadata_path = directory / "metadata.json"
        if not metadata_path.is_file():
            raise ArtifactError("dataset artifact metadata is missing")
        try:
            stored = DatasetArtifactRef.model_validate_json(
                metadata_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ArtifactError("dataset artifact metadata is invalid") from exc
        if stored != reference:
            raise ArtifactError("dataset reference does not match stored metadata")
        path = directory / stored.filename
        if not path.is_file():
            raise ArtifactError("dataset artifact file is missing")
        data = path.read_bytes()
        if len(data) != stored.size_bytes:
            raise ArtifactError("dataset artifact size changed after upload")
        if hashlib.sha256(data).hexdigest() != stored.sha256:
            raise ArtifactError("dataset artifact checksum mismatch")
        return StoredArtifact(reference=stored, path=path)
