from __future__ import annotations

import asyncio

import httpx

from research_mesh.literature import (
    CrossrefClient,
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
        assert result["provider"]["authentication"] == "none"
        assert result["provider"]["total_results"] == 42
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
        assert result["external_count"] == 0
        assert result["seed_count"] == 2
        assert result["count"] == 2
        assert all(
            item["verification"] == "user-provided" for item in result["evidence"]
        )

    asyncio.run(scenario())
