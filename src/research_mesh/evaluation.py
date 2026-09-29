from __future__ import annotations

import math
import statistics
from collections import Counter
from typing import Any


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 3)
    value = ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    return round(value, 3)


def summarize_benchmark(results: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [item for item in results if item.get("status") == "completed"]
    failed = [item for item in results if item.get("status") != "completed"]
    total_latencies = [
        float(item["duration_ms"])
        for item in completed
        if isinstance(item.get("duration_ms"), (int, float))
    ]
    step_latencies: dict[str, list[float]] = {}
    failure_types: Counter[str] = Counter()
    doi_coverages: list[float] = []
    abstract_coverages: list[float] = []
    directness_scores: list[float] = []
    for item in failed:
        failure_types[str(item.get("error_type") or "unknown")] += 1
    for item in completed:
        report = item.get("report") if isinstance(item.get("report"), dict) else {}
        for trace in report.get("provenance", []):
            if not isinstance(trace, dict) or not isinstance(trace.get("duration_ms"), (int, float)):
                continue
            step_latencies.setdefault(str(trace.get("step") or "unknown"), []).append(
                float(trace["duration_ms"])
            )
        analysis = report.get("analysis") if isinstance(report.get("analysis"), dict) else {}
        if isinstance(analysis.get("doi_coverage"), (int, float)):
            doi_coverages.append(float(analysis["doi_coverage"]))
        if isinstance(analysis.get("abstract_coverage"), (int, float)):
            abstract_coverages.append(float(analysis["abstract_coverage"]))
        evidence_quality = analysis.get("evidence_quality") if isinstance(analysis.get("evidence_quality"), dict) else {}
        if isinstance(evidence_quality.get("mean_directness_score"), (int, float)):
            directness_scores.append(float(evidence_quality["mean_directness_score"]))

    return {
        "case_count": len(results),
        "completed": len(completed),
        "failed": len(failed),
        "completion_rate": round(len(completed) / len(results), 6) if results else None,
        "latency_ms": {
            "mean": round(statistics.fmean(total_latencies), 3) if total_latencies else None,
            "p50": percentile(total_latencies, 0.50),
            "p95": percentile(total_latencies, 0.95),
        },
        "step_latency_ms": {
            step: {
                "count": len(values),
                "p50": percentile(values, 0.50),
                "p95": percentile(values, 0.95),
            }
            for step, values in sorted(step_latencies.items())
        },
        "evidence": {
            "mean_doi_coverage": round(statistics.fmean(doi_coverages), 6) if doi_coverages else None,
            "mean_abstract_coverage": round(statistics.fmean(abstract_coverages), 6) if abstract_coverages else None,
            "mean_topic_directness": round(statistics.fmean(directness_scores), 6) if directness_scores else None,
        },
        "failure_types": dict(sorted(failure_types.items())),
    }
