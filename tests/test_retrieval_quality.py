from __future__ import annotations

from research_mesh.partners import review_processor
from research_mesh.retrieval_quality import build_search_intent
from research_mesh.sample_data import sample_request


def test_search_intent_separates_research_concepts_from_platform_terms() -> None:
    intent = build_search_intent(
        "每日午睡时长是否影响大学生下午的持续注意力表现？",
        "college students daytime nap duration sustained attention",
    )

    assert intent.required_dimensions == ("exposure", "outcome")
    assert [dimension.name for dimension in intent.dimensions] == [
        "population",
        "exposure",
        "outcome",
    ]
    assert all("agent" not in query.lower() for query in intent.suggested_queries())
    assert all("dag" not in query.lower() for query in intent.retry_queries())


def test_review_rejects_evidence_that_failed_topic_quality_gate() -> None:
    result = review_processor(
        {
            "request": sample_request().model_dump(mode="json"),
            "artifacts": {
                "literature": {
                    "evidence": [{"title": "Unrelated record", "doi": "10.1000/x"}],
                    "quality_gate": {
                        "passed": False,
                        "relevant_count": 1,
                        "required_count": 3,
                    },
                },
                "experiment": {"controls": ["统一数据采集流程"]},
                "analysis": {"external_count": 1, "doi_coverage": 1.0},
            },
        }
    )

    assert result["passed"] is False
    assert result["decision"] == "revise"
    assert any(
        finding["severity"] == "error" and "主题相关证据" in finding["message"]
        for finding in result["findings"]
    )
    assert "topic-relevance" in result["checks"]
    assert "query-contamination" in result["checks"]
