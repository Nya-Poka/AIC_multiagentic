from __future__ import annotations

import asyncio

import httpx

from research_mesh.fulltext import fetch_open_full_text
from research_mesh.synthesis import synthesize_evidence


def test_open_html_fulltext_produces_hashed_evidence_segments() -> None:
    html = """
    <html><body><h1>Sleep and academic performance</h1>
    <p>University students with longer sleep duration had higher examination scores in this observational cohort. The association requires cautious interpretation.</p>
    <p>The study measured nightly sleep duration with actigraphy and assessed academic performance using standardized examination scores.</p>
    </body></html>
    """

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=html,
            headers={"Content-Type": "text/html; charset=utf-8"},
        )

    async def scenario() -> None:
        result = await fetch_open_full_text(
            "https://papers.example/study.html",
            query="sleep duration university academic performance",
            transport=httpx.MockTransport(handler),
        )
        assert result["status"] == "available"
        assert len(result["content_sha256"]) == 64
        assert result["segments"]
        assert len(result["segments"][0]["text_sha256"]) == 64

    asyncio.run(scenario())


def test_synthesis_distinguishes_fulltext_abstract_and_metadata() -> None:
    result = synthesize_evidence(
        {
            "artifacts": {
                "literature": {
                    "evidence": [
                        {
                            "title": "Full text study",
                            "doi": "10.1/full",
                            "topic_directness_score": 0.9,
                            "source_quality_tier": "A",
                            "full_text": {
                                "segments": [{"text": "Direct evidence segment."}]
                            },
                        },
                        {
                            "title": "Abstract study",
                            "has_abstract": True,
                            "summary": "Abstract-level evidence.",
                            "topic_directness_score": 0.8,
                            "source_quality_tier": "B",
                        },
                        {"title": "Metadata record", "source_quality_tier": "C"},
                    ]
                }
            }
        }
    )
    assert result["record_count"] == 3
    assert result["full_text_supported_records"] == 1
    assert result["abstract_supported_records"] == 1
    assert [item["evidence_level"] for item in result["evidence_matrix"]] == [
        "open-full-text-segment",
        "abstract",
        "bibliographic-metadata",
    ]
