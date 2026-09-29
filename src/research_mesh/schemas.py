from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SourceDocument(BaseModel):
    """A user-provided or externally retrieved source record."""

    title: str = Field(min_length=2)
    authors: list[str] = Field(default_factory=list)
    year: int | None = Field(default=None, ge=1900, le=2100)
    summary: str = Field(min_length=8)
    identifier: str | None = None


class DatasetArtifactRef(BaseModel):
    """A validated reference to a dataset held by the local artifact store."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(pattern=r"^dataset-[a-f0-9]{32}$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=3, max_length=100)
    size_bytes: int = Field(gt=0, le=25 * 1024 * 1024)


class DatasetAnalysisSpec(BaseModel):
    """Variables and design hints for deterministic dataset analysis."""

    model_config = ConfigDict(extra="forbid")

    outcome: str | None = Field(default=None, min_length=1, max_length=128)
    exposures: list[str] = Field(default_factory=list, max_length=12)
    covariates: list[str] = Field(default_factory=list, max_length=24)
    design: str | None = Field(default=None, min_length=2, max_length=100)


class ResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=8)
    objective: str = Field(min_length=8)
    literature_query: str | None = Field(default=None, min_length=2, max_length=500)
    max_literature_results: int = Field(default=5, ge=1, le=20)
    documents: list[SourceDocument] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    dataset: DatasetArtifactRef | None = None
    analysis_spec: DatasetAnalysisSpec | None = None


class AgentDescriptor(BaseModel):
    """Local ACS-like descriptor; replaced by official ADP results later."""

    slug: str
    aic: str
    name: str
    endpoint: str
    skills: list[str]
    tags: list[str] = Field(default_factory=list)
    active: bool = True
    priority: int = 0


class TraceEvent(BaseModel):
    step: str
    agent_slug: str
    agent_aic: str
    skill: str
    endpoint: str
    task_id: str
    final_state: str
    duration_ms: float
    attempt: int = 1
    candidate_count: int = 1
    failover: bool = False
    prior_failures: list[str] = Field(default_factory=list)
    occurred_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class ResearchReport(BaseModel):
    session_id: str
    status: Literal["completed", "failed"]
    question: str
    objective: str
    plan: list[str]
    literature: dict[str, Any]
    experiment: dict[str, Any]
    analysis: dict[str, Any]
    dataset_analysis: dict[str, Any] = Field(default_factory=dict)
    synthesis: dict[str, Any] = Field(default_factory=dict)
    review: dict[str, Any]
    provenance: list[TraceEvent]
