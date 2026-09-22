from __future__ import annotations

import asyncio

import httpx

from research_mesh.literature import (
    CrossrefClient,
    _merge_records,
    clear_literature_cache,
    search_literature,
)
from research_mesh.schemas import ResearchRequest


def request_for_sleep(*, max_results: int = 5) -> ResearchRequest:
    return ResearchRequest(
        question="睡眠时长是否影响大学生学习表现？",
        objective="检索直接证据并形成可复核实验方案。",
        literature_query="college students sleep duration academic performance",
        max_literature_results=max_results,
    )


def crossref_item(
    *,
    doi: str | None,
    title: str,
    abstract: str | None,
    year: int = 2019,
) -> dict:
    item: dict = {
        "title": [title],
        "type": "journal-article",
        "container-title": ["Journal of Student Sleep"],
        "author": [{"given": "Ada", "family": "Researcher"}],
        "published": {"date-parts": [[year]]},
        "is-referenced-by-count": 12,
    }
    if doi:
        item["DOI"] = doi
    if abstract:
        item["abstract"] = abstract
    return item


def test_semantic_title_deduplication_merges_distinct_dois() -> None:
    async def scenario() -> None:
        first_title = (
            "Sleep quality, duration, and consistency are associated with better "
            "academic performance in college students"
        )
        variant_title = (
            "Sleep quality duration and consistency associated with better "
            "academic performance in college students"
        )

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "message": {
                        "total-results": 3,
                        "items": [
                            crossref_item(
                                doi="10.1000/sleep-main",
                                title=first_title,
                                abstract=(
                                    "Sleep duration and academic performance were measured "
                                    "in college students."
                                ),
                            ),
                            crossref_item(
                                doi="10.1000/sleep-conference",
                                title=variant_title,
                                abstract=None,
                            ),
                            crossref_item(
                                doi="10.1000/sleep-second",
                                title=(
                                    "Objective sleep duration and academic performance "
                                    "among university students"
                                ),
                                abstract=(
                                    "Objective sleep duration predicted academic performance "
                                    "among university students."
                                ),
                            ),
                        ],
                    },
                },
            )

        clear_literature_cache()
        result = await search_literature(
            request_for_sleep(max_results=2),
            client=CrossrefClient(
                base_url="https://crossref.test",
                retries=0,
                transport=httpx.MockTransport(handler),
            ),
        )

        assert result["external_count"] == 2
        assert result["deduplication"]["title_duplicate_groups"] >= 1
        assert result["deduplication"]["duplicate_records_removed"] >= 1
        merged = next(
            item for item in result["evidence"] if item["doi"] == "10.1000/sleep-main"
        )
        assert "10.1000/sleep-conference" in merged["deduplication"]["alternate_dois"]
        assert "near-duplicate-title" in merged["deduplication"]["match_methods"]
        assert merged["source_quality_tier"] in {"A", "B"}
        assert merged["evidence_type"] == "journal-article"

    asyncio.run(scenario())


def test_indirect_and_low_quality_records_are_filtered_with_audit() -> None:
    async def scenario() -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "message": {
                        "total-results": 3,
                        "items": [
                            crossref_item(
                                doi="10.1000/direct",
                                title=(
                                    "Sleep duration and academic performance among "
                                    "university students"
                                ),
                                abstract=(
                                    "Sleep duration and academic performance were assessed "
                                    "in college students."
                                ),
                            ),
                            crossref_item(
                                doi="10.1000/social-media",
                                title=(
                                    "Effect of social media use on learning, social interactions, "
                                    "and sleep duration among university students"
                                ),
                                abstract=(
                                    "Social media affected sleep duration and academic performance "
                                    "among university students."
                                ),
                            ),
                            {
                                "title": [
                                    "Sleep duration and academic performance in college "
                                    "students: a brief record"
                                ],
                                "abstract": (
                                    "Sleep duration and academic performance were mentioned "
                                    "without stable bibliographic metadata."
                                ),
                            },
                        ],
                    },
                },
            )

        clear_literature_cache()
        result = await search_literature(
            request_for_sleep(max_results=1),
            client=CrossrefClient(
                base_url="https://crossref.test",
                retries=0,
                transport=httpx.MockTransport(handler),
            ),
        )

        assert result["quality_gate"]["passed"] is True
        assert [item["doi"] for item in result["evidence"]] == ["10.1000/direct"]
        rejected = {item["doi"]: item for item in result["rejected_records"]}
        assert rejected["10.1000/social-media"]["reason"] == "competing-primary-topic"
        assert rejected["10.1000/social-media"]["competing_concepts"] == ["social-media"]
        low_quality = next(
            item
            for item in result["rejected_records"]
            if item["reason"] == "insufficient-source-quality"
        )
        assert low_quality["source_quality_tier"] == "D"
        assert any("主题关系不直接" in item for item in result["limitations"])
        assert any("书目质量不足" in item for item in result["limitations"])

    asyncio.run(scenario())


def test_semantic_deduplication_does_not_merge_numbered_study_series() -> None:
    records = [
        {
            "title": f"Sleep and academic performance cohort {index}",
            "doi": f"10.1000/cohort-{index}",
            "authors": ["Ada Researcher"],
            "year": 2020,
            "providers": ["Crossref"],
        }
        for index in range(1, 21)
    ]

    merged = _merge_records(records)

    assert len(merged) == 20
    assert all(item["deduplication"]["merged_record_count"] == 1 for item in merged)
