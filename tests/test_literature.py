from __future__ import annotations

import asyncio

import httpx

from research_mesh.literature import (
    CrossrefClient,
    OpenAlexClient,
    SemanticScholarClient,
    clear_literature_cache,
    search_literature,
)
from research_mesh.sample_data import sample_request


def test_crossref_metadata_is_mapped_to_auditable_evidence() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["query.bibliographic"] == "study time outcomes"
            assert request.url.params["rows"] == "3"
            assert request.url.params["mailto"] == "team@example.test"
            assert "authorization" not in request.headers
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "message": {
                        "total-results": 42,
                        "items": [
                            {
                                "DOI": "10.1000/TEST.1",
                                "title": ["Study time and learning outcomes"],
                                "author": [
                                    {"given": "Ada", "family": "Researcher"}
                                ],
                                "published-online": {"date-parts": [[2025, 4, 2]]},
                                "abstract": "<jats:p>Observed learning outcomes.</jats:p>",
                                "URL": "https://doi.org/10.1000/test.1",
                                "container-title": ["Journal of Testing"],
                                "publisher": "Test Publisher",
                                "type": "journal-article",
                                "is-referenced-by-count": 7,
                                "reference-count": 12,
                                "score": 18.5,
                            }
                        ],
                    },
                },
            )

        clear_literature_cache()
        request = sample_request().model_copy(
            update={
                "documents": [],
                "literature_query": "study time outcomes",
                "max_literature_results": 3,
            }
        )
        client = CrossrefClient(
            base_url="https://crossref.test",
            mailto="team@example.test",
            retries=0,
            transport=httpx.MockTransport(handler),
        )
        result = await search_literature(request, client=client)

        assert result["provider"]["status"] == "ok"
        assert result["provider"]["source_count"] == 1
        assert result["provider"]["sources"][0]["authentication"] == "none"
        assert result["provider"]["sources"][0]["total_results"] == 42
        assert result["external_count"] == 1
        assert result["seed_count"] == 0
        evidence = result["evidence"][0]
        assert evidence["identifier"] == "10.1000/test.1"
        assert evidence["authors"] == ["Ada Researcher"]
        assert evidence["year"] == 2025
        assert evidence["summary"] == "Observed learning outcomes."
        assert evidence["verification"] == "crossref-rest-api"

    asyncio.run(scenario())


def test_seed_documents_are_retained_when_crossref_is_unavailable() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("offline", request=request)

        clear_literature_cache()
        client = CrossrefClient(
            base_url="https://offline.test",
            retries=0,
            transport=httpx.MockTransport(handler),
        )
        result = await search_literature(sample_request(), client=client)

        assert result["provider"]["status"] == "unavailable"
        assert result["provider"]["sources"][0]["status"] == "unavailable"
        assert result["external_count"] == 0
        assert result["seed_count"] == 2
        assert result["count"] == 2
        assert all(
            item["verification"] == "user-provided" for item in result["evidence"]
        )

    asyncio.run(scenario())


def test_multiple_providers_are_merged_deduplicated_and_auditable() -> None:
    async def scenario() -> None:
        def crossref_handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "message": {
                        "total-results": 1,
                        "items": [
                            {
                                "DOI": "10.1000/shared",
                                "title": ["Shared research record"],
                                "author": [{"given": "Ada", "family": "One"}],
                                "published": {"date-parts": [[2024]]},
                                "URL": "https://doi.org/10.1000/shared",
                            }
                        ],
                    },
                },
            )

        def openalex_handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["search"] == "study time outcomes"
            return httpx.Response(
                200,
                json={
                    "meta": {"count": 1},
                    "results": [
                        {
                            "id": "https://openalex.org/W1",
                            "doi": "https://doi.org/10.1000/shared",
                            "title": "Shared research record",
                            "publication_year": 2024,
                            "authorships": [
                                {"author": {"display_name": "Ada One"}}
                            ],
                            "abstract_inverted_index": {
                                "Observed": [0],
                                "outcomes": [1],
                            },
                            "primary_location": {
                                "landing_page_url": "https://example.test/paper",
                                "source": {"display_name": "Test Journal"},
                            },
                            "best_oa_location": {
                                "pdf_url": "https://example.test/paper.pdf"
                            },
                            "open_access": {"is_oa": True},
                            "cited_by_count": 9,
                            "referenced_works": [],
                        }
                    ],
                },
            )

        def semantic_handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["query"] == "study time outcomes"
            return httpx.Response(
                200,
                json={
                    "total": 1,
                    "data": [
                        {
                            "paperId": "s2-paper-2",
                            "title": "A second independent result",
                            "abstract": "A traceable abstract from Semantic Scholar.",
                            "authors": [{"name": "Lin Two"}],
                            "year": 2025,
                            "url": "https://semanticscholar.org/paper/s2-paper-2",
                            "externalIds": {"DOI": "10.1000/second"},
                            "citationCount": 3,
                            "referenceCount": 4,
                            "openAccessPdf": None,
                        }
                    ],
                },
            )

        clear_literature_cache()
        request = sample_request().model_copy(
            update={
                "documents": [],
                "literature_query": "study time outcomes",
                "max_literature_results": 5,
            }
        )
        result = await search_literature(
            request,
            clients=[
                CrossrefClient(
                    base_url="https://crossref.test",
                    retries=0,
                    transport=httpx.MockTransport(crossref_handler),
                ),
                OpenAlexClient(
                    base_url="https://openalex.test",
                    retries=0,
                    transport=httpx.MockTransport(openalex_handler),
                ),
                SemanticScholarClient(
                    base_url="https://semantic.test",
                    retries=0,
                    transport=httpx.MockTransport(semantic_handler),
                ),
            ],
        )

        assert result["provider"]["status"] == "ok"
        assert result["provider"]["source_count"] == 3
        assert result["provider"]["available_source_count"] == 3
        assert result["external_count"] == 2
        shared = next(
            item for item in result["evidence"] if item["doi"] == "10.1000/shared"
        )
        assert shared["providers"] == ["Crossref", "OpenAlex"]
        assert shared["verification"] == "multi-source-metadata"
        assert shared["has_abstract"] is True
        assert shared["open_access_url"] == "https://example.test/paper.pdf"

    asyncio.run(scenario())


def test_query_planner_expands_queries_without_becoming_a_hard_dependency() -> None:
    class Planner:
        async def plan(self, _question: str, _query: str) -> list[str]:
            return ["academic achievement study duration"]

    async def scenario() -> None:
        observed: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            observed.append(request.url.params["query.bibliographic"])
            return httpx.Response(
                200,
                json={"status": "ok", "message": {"total-results": 0, "items": []}},
            )

        clear_literature_cache()
        request = sample_request().model_copy(update={"documents": []})
        result = await search_literature(
            request,
            client=CrossrefClient(
                base_url="https://crossref.test",
                retries=0,
                transport=httpx.MockTransport(handler),
            ),
            query_planner=Planner(),
        )
        assert observed == [
            "study time academic performance test scores",
            "academic achievement study duration",
        ]
        assert result["query_plan"]["expanded"] is True

    asyncio.run(scenario())
