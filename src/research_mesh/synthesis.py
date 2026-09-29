from __future__ import annotations

from collections import Counter
from typing import Any


def synthesize_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("evidence synthesis requires payload.artifacts")
    literature = artifacts.get("literature")
    if not isinstance(literature, dict):
        raise ValueError("evidence synthesis requires the literature artifact")
    evidence = [item for item in literature.get("evidence", []) if isinstance(item, dict)]
    if not evidence:
        raise ValueError("evidence synthesis received no evidence records")

    matrix: list[dict[str, Any]] = []
    full_text_count = 0
    abstract_count = 0
    type_counts: Counter[str] = Counter()
    quality_counts: Counter[str] = Counter()
    for item in evidence:
        full_text = item.get("full_text") if isinstance(item.get("full_text"), dict) else {}
        segments = full_text.get("segments") if isinstance(full_text.get("segments"), list) else []
        if segments:
            full_text_count += 1
            evidence_level = "open-full-text-segment"
            excerpt = str(segments[0].get("text") or "")[:500]
        elif item.get("has_abstract"):
            abstract_count += 1
            evidence_level = "abstract"
            excerpt = str(item.get("summary") or "")[:500]
        else:
            evidence_level = "bibliographic-metadata"
            excerpt = ""
        work_type = str(item.get("evidence_type") or item.get("work_type") or "unknown")
        quality = str(item.get("source_quality_tier") or "ungraded")
        type_counts[work_type] += 1
        quality_counts[quality] += 1
        matrix.append(
            {
                "title": item.get("title"),
                "doi": item.get("doi"),
                "year": item.get("year"),
                "work_type": work_type,
                "source_quality_tier": quality,
                "evidence_level": evidence_level,
                "evidence_excerpt": excerpt,
                "topic_relevance_score": item.get("topic_relevance_score"),
                "topic_directness_score": item.get("topic_directness_score"),
                "interpretation": "candidate evidence; direction of effect requires human or validated model extraction",
            }
        )

    direct = sum(
        float(item.get("topic_directness_score", 0)) >= 0.7
        for item in evidence
    )
    if full_text_count >= 3 and direct >= 3:
        certainty = "moderate-for-scoping"
    elif abstract_count + full_text_count >= 3 and direct >= 2:
        certainty = "low-for-scoping"
    else:
        certainty = "very-low"
    return {
        "record_count": len(evidence),
        "evidence_matrix": matrix,
        "evidence_type_distribution": dict(sorted(type_counts.items())),
        "source_quality_distribution": dict(sorted(quality_counts.items())),
        "full_text_supported_records": full_text_count,
        "abstract_supported_records": abstract_count,
        "certainty": certainty,
        "conclusion": (
            "The retrieved records are suitable for scoped research planning, but effect direction and causal claims require validated full-text extraction and human review."
        ),
        "limitations": [
            "This deterministic synthesis does not infer effect direction from abstracts alone.",
            "Evidence certainty is a workflow indicator, not a formal GRADE assessment.",
            "Machine-extracted full-text segments must be checked against the source document.",
        ],
        "engine": "research-mesh-evidence-synthesis-v1",
    }
