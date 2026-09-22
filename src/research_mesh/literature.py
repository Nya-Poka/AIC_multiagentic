from __future__ import annotations

import asyncio
import copy
import html
import json
import math
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

import httpx

from .llm import (
    LLMCompletionRequest,
    LLMGatewayClient,
    LLMMessage,
    LLMProviderError,
)
from .retrieval_quality import (
    ResearchSearchIntent,
    annotate_and_filter_records,
    build_search_intent,
)
from .schemas import ResearchRequest, SourceDocument


class LiteratureProviderError(RuntimeError):
    """A literature provider could not return a valid result."""


_HTML_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")
_DOI = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
_CACHE: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _env_float(name: str, default: float, *, minimum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(minimum, value)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _plain_text(value: str) -> str:
    return _WHITESPACE.sub(" ", html.unescape(_HTML_TAG.sub(" ", value))).strip()


def _first_text(value: Any) -> str | None:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.strip():
                return _plain_text(item)
    if isinstance(value, str) and value.strip():
        return _plain_text(value)
    return None


def _publication_year(item: dict[str, Any]) -> int | None:
    for field in ("published-print", "published-online", "published", "issued"):
        date_parts = item.get(field, {}).get("date-parts", [])
        if date_parts and date_parts[0] and isinstance(date_parts[0][0], int):
            return date_parts[0][0]
    created = item.get("created", {}).get("date-time")
    if isinstance(created, str) and re.match(r"^\d{4}", created):
        return int(created[:4])
    return None


def _authors(item: dict[str, Any]) -> list[str]:
    authors: list[str] = []
    for author in item.get("author", []):
        if not isinstance(author, dict):
            continue
        literal = author.get("name")
        if isinstance(literal, str) and literal.strip():
            authors.append(literal.strip())
            continue
        name = " ".join(
            part.strip()
            for part in (author.get("given"), author.get("family"))
            if isinstance(part, str) and part.strip()
        )
        if name:
            authors.append(name)
    return authors


def _normalise_doi(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    doi = value.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix) :]
    return doi if _DOI.match(doi) else None


def _abstract_from_inverted_index(value: Any) -> str | None:
    if not isinstance(value, dict) or not value:
        return None
    positioned: list[tuple[int, str]] = []
    for word, positions in value.items():
        if not isinstance(word, str) or not isinstance(positions, list):
            continue
        positioned.extend(
            (position, word) for position in positions if isinstance(position, int)
        )
    if not positioned:
        return None
    return _plain_text(" ".join(word for _, word in sorted(positioned)))


@dataclass(frozen=True)
class ProviderSearchResult:
    records: list[dict[str, Any]]
    total_results: int


class LiteratureClient(Protocol):
    name: str
    base_url: str

    async def search(self, query: str, rows: int) -> ProviderSearchResult: ...


async def _request_json(
    *,
    provider_name: str,
    base_url: str,
    path: str,
    params: dict[str, str | int],
    headers: dict[str, str],
    timeout_seconds: float,
    retries: int,
    transport: httpx.AsyncBaseTransport | None,
) -> dict[str, Any]:
    last_error = "unknown provider error"
    async with httpx.AsyncClient(
        base_url=f"{base_url.rstrip('/')}/",
        headers=headers,
        timeout=timeout_seconds,
        follow_redirects=True,
        transport=transport,
    ) as client:
        for attempt in range(retries + 1):
            try:
                response = await client.get(path.lstrip("/"), params=params)
                if response.status_code == 429 or response.status_code >= 500:
                    last_error = f"{provider_name} returned HTTP {response.status_code}"
                    if attempt < retries:
                        retry_after = response.headers.get("Retry-After")
                        delay = min(float(retry_after or 0.5 * (2**attempt)), 5.0)
                        await asyncio.sleep(max(0.0, delay))
                        continue
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("response root is not an object")
                return payload
            except (httpx.HTTPError, ValueError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < retries:
                    await asyncio.sleep(0.5 * (2**attempt))
                    continue
    raise LiteratureProviderError(last_error)


@dataclass
class CrossrefClient:
    name: str = "Crossref"
    base_url: str = "https://api.crossref.org"
    mailto: str | None = None
    timeout_seconds: float = 12.0
    retries: int = 2
    transport: httpx.AsyncBaseTransport | None = None

    @classmethod
    def from_environment(cls) -> CrossrefClient:
        return cls(
            base_url=os.getenv(
                "RESEARCH_MESH_CROSSREF_BASE_URL", "https://api.crossref.org"
            ).rstrip("/"),
            mailto=os.getenv("CROSSREF_MAILTO") or None,
            timeout_seconds=_env_float(
                "RESEARCH_MESH_CROSSREF_TIMEOUT_SECONDS", 12.0, minimum=1.0
            ),
            retries=_env_int(
                "RESEARCH_MESH_CROSSREF_RETRIES", 2, minimum=0, maximum=5
            ),
        )

    async def search(self, query: str, rows: int) -> ProviderSearchResult:
        params: dict[str, str | int] = {
            "query.bibliographic": query,
            "rows": rows,
        }
        if self.mailto:
            params["mailto"] = self.mailto
        payload = await _request_json(
            provider_name=self.name,
            base_url=self.base_url,
            path="/works",
            params=params,
            headers={
                "Accept": "application/json",
                "User-Agent": (
                    "ResearchMesh/0.6 "
                    "(https://github.com/Nya-Poka/AIC_multiagentic)"
                ),
            },
            timeout_seconds=self.timeout_seconds,
            retries=self.retries,
            transport=self.transport,
        )
        if payload.get("status") != "ok" or not isinstance(payload.get("message"), dict):
            raise LiteratureProviderError("Crossref returned an invalid payload")
        message = payload["message"]
        records = [
            record
            for item in message.get("items", [])
            if isinstance(item, dict) and (record := _crossref_record(item)) is not None
        ]
        return ProviderSearchResult(records, int(message.get("total-results", 0)))


def _crossref_record(item: dict[str, Any]) -> dict[str, Any] | None:
    title = _first_text(item.get("title"))
    if not title:
        return None
    doi = _normalise_doi(item.get("DOI"))
    abstract = _first_text(item.get("abstract"))
    return {
        "title": title,
        "authors": _authors(item),
        "year": _publication_year(item),
        "summary": abstract or "Crossref 未提供摘要；本条仅使用可核验的书目元数据。",
        "identifier": doi,
        "doi": doi,
        "url": item.get("URL") or (f"https://doi.org/{doi}" if doi else None),
        "open_access_url": None,
        "venue": _first_text(item.get("container-title")),
        "publisher": item.get("publisher"),
        "work_type": item.get("type"),
        "language": item.get("language"),
        "reference_count": item.get("reference-count"),
        "cited_by_count": item.get("is-referenced-by-count"),
        "relevance_score": item.get("score"),
        "has_abstract": abstract is not None,
        "provider": "Crossref",
        "providers": ["Crossref"],
        "source_ids": {"Crossref": doi} if doi else {},
        "verification": "crossref-rest-api",
    }


@dataclass
class OpenAlexClient:
    name: str = "OpenAlex"
    base_url: str = "https://api.openalex.org"
    api_key: str | None = None
    mailto: str | None = None
    timeout_seconds: float = 12.0
    retries: int = 2
    transport: httpx.AsyncBaseTransport | None = None

    @classmethod
    def from_environment(cls) -> OpenAlexClient:
        return cls(
            base_url=os.getenv(
                "RESEARCH_MESH_OPENALEX_BASE_URL", "https://api.openalex.org"
            ).rstrip("/"),
            api_key=os.getenv("OPENALEX_API_KEY") or None,
            mailto=os.getenv("OPENALEX_MAILTO") or os.getenv("CROSSREF_MAILTO") or None,
            timeout_seconds=_env_float(
                "RESEARCH_MESH_OPENALEX_TIMEOUT_SECONDS", 12.0, minimum=1.0
            ),
            retries=_env_int(
                "RESEARCH_MESH_OPENALEX_RETRIES", 2, minimum=0, maximum=5
            ),
        )

    async def search(self, query: str, rows: int) -> ProviderSearchResult:
        params: dict[str, str | int] = {"search": query, "per-page": rows}
        if self.api_key:
            params["api_key"] = self.api_key
        if self.mailto:
            params["mailto"] = self.mailto
        payload = await _request_json(
            provider_name=self.name,
            base_url=self.base_url,
            path="/works",
            params=params,
            headers={"Accept": "application/json", "User-Agent": "ResearchMesh/0.6"},
            timeout_seconds=self.timeout_seconds,
            retries=self.retries,
            transport=self.transport,
        )
        records = [
            record
            for item in payload.get("results", [])
            if isinstance(item, dict) and (record := _openalex_record(item)) is not None
        ]
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        return ProviderSearchResult(records, int(meta.get("count", len(records))))


def _openalex_record(item: dict[str, Any]) -> dict[str, Any] | None:
    title = _first_text(item.get("title") or item.get("display_name"))
    if not title:
        return None
    doi = _normalise_doi(item.get("doi"))
    abstract = _abstract_from_inverted_index(item.get("abstract_inverted_index"))
    authors: list[str] = []
    for authorship in item.get("authorships", []):
        if not isinstance(authorship, dict):
            continue
        author = authorship.get("author")
        if isinstance(author, dict) and isinstance(author.get("display_name"), str):
            authors.append(author["display_name"].strip())
    primary = item.get("primary_location") if isinstance(item.get("primary_location"), dict) else {}
    source = primary.get("source") if isinstance(primary.get("source"), dict) else {}
    best_oa = item.get("best_oa_location") if isinstance(item.get("best_oa_location"), dict) else {}
    open_access = item.get("open_access") if isinstance(item.get("open_access"), dict) else {}
    source_id = str(item.get("id") or "").rsplit("/", 1)[-1] or None
    return {
        "title": title,
        "authors": authors,
        "year": item.get("publication_year"),
        "summary": abstract or "OpenAlex 未提供摘要；本条仅使用可核验的书目元数据。",
        "identifier": doi or source_id,
        "doi": doi,
        "url": primary.get("landing_page_url") or item.get("id"),
        "open_access_url": best_oa.get("pdf_url") or best_oa.get("landing_page_url"),
        "is_open_access": bool(open_access.get("is_oa")),
        "venue": source.get("display_name"),
        "publisher": source.get("host_organization_name"),
        "work_type": item.get("type"),
        "language": item.get("language"),
        "reference_count": len(item.get("referenced_works", [])),
        "cited_by_count": item.get("cited_by_count"),
        "relevance_score": item.get("relevance_score"),
        "has_abstract": abstract is not None,
        "provider": "OpenAlex",
        "providers": ["OpenAlex"],
        "source_ids": {"OpenAlex": source_id} if source_id else {},
        "verification": "openalex-rest-api",
    }


@dataclass
class SemanticScholarClient:
    name: str = "Semantic Scholar"
    base_url: str = "https://api.semanticscholar.org/graph/v1"
    api_key: str | None = None
    timeout_seconds: float = 12.0
    retries: int = 2
    transport: httpx.AsyncBaseTransport | None = None

    @classmethod
    def from_environment(cls) -> SemanticScholarClient:
        return cls(
            base_url=os.getenv(
                "RESEARCH_MESH_SEMANTIC_SCHOLAR_BASE_URL",
                "https://api.semanticscholar.org/graph/v1",
            ).rstrip("/"),
            api_key=os.getenv("SEMANTIC_SCHOLAR_API_KEY") or None,
            timeout_seconds=_env_float(
                "RESEARCH_MESH_SEMANTIC_SCHOLAR_TIMEOUT_SECONDS", 12.0, minimum=1.0
            ),
            retries=_env_int(
                "RESEARCH_MESH_SEMANTIC_SCHOLAR_RETRIES", 2, minimum=0, maximum=5
            ),
        )

    async def search(self, query: str, rows: int) -> ProviderSearchResult:
        headers = {"Accept": "application/json", "User-Agent": "ResearchMesh/0.6"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        payload = await _request_json(
            provider_name=self.name,
            base_url=self.base_url,
            path="/paper/search",
            params={
                "query": query,
                "limit": rows,
                "fields": (
                    "title,abstract,authors,year,url,venue,publicationTypes,"
                    "citationCount,referenceCount,externalIds,openAccessPdf"
                ),
            },
            headers=headers,
            timeout_seconds=self.timeout_seconds,
            retries=self.retries,
            transport=self.transport,
        )
        records = [
            record
            for item in payload.get("data", [])
            if isinstance(item, dict) and (record := _semantic_scholar_record(item)) is not None
        ]
        return ProviderSearchResult(records, int(payload.get("total", len(records))))


def _semantic_scholar_record(item: dict[str, Any]) -> dict[str, Any] | None:
    title = _first_text(item.get("title"))
    if not title:
        return None
    external_ids = item.get("externalIds") if isinstance(item.get("externalIds"), dict) else {}
    doi = _normalise_doi(external_ids.get("DOI"))
    abstract = _first_text(item.get("abstract"))
    oa = item.get("openAccessPdf") if isinstance(item.get("openAccessPdf"), dict) else {}
    paper_id = item.get("paperId") if isinstance(item.get("paperId"), str) else None
    return {
        "title": title,
        "authors": [
            author["name"].strip()
            for author in item.get("authors", [])
            if isinstance(author, dict) and isinstance(author.get("name"), str)
        ],
        "year": item.get("year"),
        "summary": abstract or "Semantic Scholar 未提供摘要；本条仅使用可核验的书目元数据。",
        "identifier": doi or paper_id,
        "doi": doi,
        "url": item.get("url") or (f"https://doi.org/{doi}" if doi else None),
        "open_access_url": oa.get("url"),
        "is_open_access": bool(oa.get("url")),
        "venue": item.get("venue"),
        "publisher": None,
        "work_type": ", ".join(item.get("publicationTypes") or []),
        "language": None,
        "reference_count": item.get("referenceCount"),
        "cited_by_count": item.get("citationCount"),
        "relevance_score": None,
        "has_abstract": abstract is not None,
        "provider": "Semantic Scholar",
        "providers": ["Semantic Scholar"],
        "source_ids": {"Semantic Scholar": paper_id} if paper_id else {},
        "verification": "semantic-scholar-rest-api",
    }


class QueryPlanner(Protocol):
    async def plan(self, question: str, query: str) -> list[str]: ...


@dataclass
class DeepSeekQueryPlanner:
    client: LLMGatewayClient
    max_queries: int = 3

    @classmethod
    def from_environment(cls) -> DeepSeekQueryPlanner:
        return cls(
            client=LLMGatewayClient.from_environment(),
            max_queries=_env_int(
                "RESEARCH_MESH_LITERATURE_MAX_QUERIES", 3, minimum=1, maximum=5
            ),
        )

    async def plan(self, question: str, query: str) -> list[str]:
        response = await self.client.complete(
            LLMCompletionRequest(
                messages=[
                    LLMMessage(
                        role="system",
                        content=(
                            "You are an academic search query planner. Return a JSON object "
                            'with a single key "queries" containing concise English scholarly '
                            "search queries. Do not include explanations."
                        ),
                    ),
                    LLMMessage(
                        role="user",
                        content=(
                            f"Research question: {question}\nCurrent query: {query}\n"
                            f"Return at most {self.max_queries} complementary queries."
                        ),
                    ),
                ],
                temperature=0.1,
                max_tokens=400,
                response_format="json_object",
            )
        )
        content = response.content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.IGNORECASE)
        payload = json.loads(content)
        raw_queries = payload.get("queries")
        if not isinstance(raw_queries, list):
            raise ValueError("LLM query planner omitted queries")
        return _unique_queries([query, *raw_queries], limit=self.max_queries)


def _unique_queries(values: list[Any], *, limit: int) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        query = _WHITESPACE.sub(" ", value).strip()[:500]
        key = query.casefold()
        if len(query) < 2 or key in seen:
            continue
        seen.add(key)
        output.append(query)
        if len(output) >= limit:
            break
    return output


def _rejected_record_audit(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "title": record.get("title"),
            "doi": record.get("doi"),
            "topic_relevance_score": record.get("topic_relevance_score"),
            "matched_dimensions": record.get("matched_dimensions", []),
            "reason": record.get("quality_reason"),
            "search_query": record.get("search_query"),
        }
        for record in records[:10]
    ]


def _quality_gate(
    records: list[dict[str, Any]],
    *,
    requested_count: int,
    minimum_ratio: float,
    minimum_score: float,
    intent: ResearchSearchIntent,
) -> dict[str, Any]:
    required_count = min(
        requested_count,
        max(1, math.ceil(requested_count * min(1.0, minimum_ratio))),
    )
    scores = [
        float(record.get("topic_relevance_score", 0.0))
        for record in records
    ]
    relevant_count = len(records)
    passed = relevant_count >= required_count
    return {
        "passed": passed,
        "relevant_count": relevant_count,
        "required_count": required_count,
        "minimum_relevance_score": minimum_score,
        "mean_relevance_score": (
            round(sum(scores) / len(scores), 4) if scores else 0.0
        ),
        "required_dimensions": list(intent.required_dimensions),
        "reason": (
            "sufficient-topic-relevance"
            if passed
            else "insufficient-topic-relevance"
        ),
    }


def _seed_record(document: SourceDocument) -> dict[str, Any]:
    record = document.model_dump(mode="json")
    doi = _normalise_doi(document.identifier)
    record.update(
        {
            "doi": doi,
            "url": f"https://doi.org/{doi}" if doi else None,
            "open_access_url": None,
            "has_abstract": True,
            "provider": "User",
            "providers": ["User"],
            "source_ids": {"User": document.identifier} if document.identifier else {},
            "relevance_score": None,
            "verification": "user-provided",
        }
    )
    return record


def _record_key(record: dict[str, Any]) -> str | None:
    doi = _normalise_doi(record.get("doi") or record.get("identifier"))
    if doi:
        return f"doi:{doi}"
    title = _WHITESPACE.sub(" ", str(record.get("title", "")).casefold()).strip()
    return f"title:{title}" if title else None


def _merge_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for raw in records:
        key = _record_key(raw)
        if key is None:
            continue
        record = copy.deepcopy(raw)
        if key not in merged:
            record["_rrf_score"] = float(record.get("_rrf_score", 0.0))
            merged[key] = record
            continue
        current = merged[key]
        current["_rrf_score"] = float(current.get("_rrf_score", 0.0)) + float(
            record.get("_rrf_score", 0.0)
        )
        current["providers"] = sorted(
            set(current.get("providers", [])) | set(record.get("providers", []))
        )
        current.setdefault("source_ids", {}).update(record.get("source_ids", {}))
        for field in (
            "doi",
            "identifier",
            "url",
            "open_access_url",
            "venue",
            "publisher",
            "work_type",
            "language",
            "year",
        ):
            if not current.get(field) and record.get(field):
                current[field] = record[field]
        if not current.get("has_abstract") and record.get("has_abstract"):
            current["summary"] = record.get("summary")
            current["has_abstract"] = True
        current["authors"] = current.get("authors") or record.get("authors") or []
        for field in ("cited_by_count", "reference_count"):
            values = [
                value
                for value in (current.get(field), record.get(field))
                if isinstance(value, int)
            ]
            current[field] = max(values) if values else None

    output = list(merged.values())
    for record in output:
        providers = record.get("providers", [])
        record["source_count"] = len(providers)
        if len(providers) > 1:
            record["verification"] = "multi-source-metadata"
    return output


async def _run_provider(
    provider: LiteratureClient,
    queries: list[str],
    rows: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    async def run_query(
        query: str,
    ) -> tuple[str, ProviderSearchResult | None, str | None]:
        try:
            return query, await provider.search(query, rows), None
        except LiteratureProviderError as exc:
            return query, None, str(exc)

    query_results = await asyncio.gather(*[run_query(query) for query in queries])
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    total_results = 0
    successful_queries = 0
    for query, result, error in query_results:
        if result is None:
            if error:
                errors.append(error)
            continue
        successful_queries += 1
        total_results = max(total_results, result.total_results)
        for rank, record in enumerate(result.records, start=1):
            enriched = copy.deepcopy(record)
            enriched["search_query"] = query
            enriched["_rrf_score"] = 1.0 / (60 + rank)
            records.append(enriched)
    status = (
        "unavailable"
        if successful_queries == 0
        else "partial"
        if errors
        else "ok"
    )
    return records, {
        "name": provider.name,
        "status": status,
        "base_url": provider.base_url,
        "authentication": "server-side" if getattr(provider, "api_key", None) else "none",
        "total_results": total_results,
        "result_count": len(records),
        "successful_queries": successful_queries,
        "error": "; ".join(errors) if errors else None,
    }


def _providers_from_environment() -> list[LiteratureClient]:
    configured = os.getenv(
        "RESEARCH_MESH_LITERATURE_PROVIDERS",
        "crossref,openalex,semantic_scholar",
    )
    names = {name.strip().lower().replace("-", "_") for name in configured.split(",")}
    providers: list[LiteratureClient] = []
    if "crossref" in names:
        providers.append(CrossrefClient.from_environment())
    if "openalex" in names:
        providers.append(OpenAlexClient.from_environment())
    if "semantic_scholar" in names:
        providers.append(SemanticScholarClient.from_environment())
    if not providers:
        raise ValueError("RESEARCH_MESH_LITERATURE_PROVIDERS selected no supported provider")
    return providers


async def search_literature(
    request: ResearchRequest,
    *,
    client: CrossrefClient | None = None,
    clients: list[LiteratureClient] | None = None,
    query_planner: QueryPlanner | None = None,
) -> dict[str, Any]:
    if client is not None and clients is not None:
        raise ValueError("provide either client or clients, not both")
    query = (request.literature_query or request.question).strip()[:500]
    rows = request.max_literature_results
    providers: list[LiteratureClient] = (
        [client]
        if client is not None
        else list(clients)
        if clients is not None
        else _providers_from_environment()
    )

    intent = build_search_intent(request.question, query)
    queries = _unique_queries(
        [query, *intent.suggested_queries()],
        limit=5,
    ) or [query]

    planner = query_planner
    if planner is None and _env_bool("RESEARCH_MESH_LITERATURE_QUERY_EXPANSION"):
        planner = DeepSeekQueryPlanner.from_environment()
    planner_error: str | None = None
    if planner is not None:
        try:
            planned = await planner.plan(request.question, query)
            queries = _unique_queries([*queries, *planned], limit=5) or [query]
        except (LLMProviderError, ValueError) as exc:
            planner_error = f"{type(exc).__name__}: {exc}"

    fetch_multiplier = _env_int(
        "RESEARCH_MESH_LITERATURE_FETCH_MULTIPLIER",
        5,
        minimum=1,
        maximum=20,
    )
    fetch_rows = min(100, max(rows, rows * fetch_multiplier))
    minimum_score = _env_float(
        "RESEARCH_MESH_LITERATURE_MIN_RELEVANCE",
        0.35,
        minimum=0.0,
    )
    minimum_ratio = _env_float(
        "RESEARCH_MESH_LITERATURE_MIN_RELEVANT_RATIO",
        0.6,
        minimum=0.0,
    )
    auto_retry = _env_bool("RESEARCH_MESH_LITERATURE_AUTO_RETRY", True)

    cache_ttl = _env_int(
        "RESEARCH_MESH_LITERATURE_CACHE_TTL_SECONDS",
        900,
        minimum=0,
        maximum=86400,
    )
    cache_key = (
        query,
        rows,
        fetch_rows,
        minimum_score,
        minimum_ratio,
        tuple(provider.name for provider in providers),
        tuple(queries),
    )
    cached = _CACHE.get(cache_key)
    if cache_ttl > 0 and cached and cached[0] >= time.monotonic():
        result = copy.deepcopy(cached[1])
        result["provider"]["cache_hit"] = True
        return result

    retrieved_at = datetime.now(timezone.utc).isoformat()
    provider_runs = await asyncio.gather(
        *[_run_provider(provider, queries, fetch_rows) for provider in providers]
    )
    candidate_records = _merge_records(
        [record for records, _status in provider_runs for record in records]
    )
    accepted_records, rejected_records = annotate_and_filter_records(
        candidate_records,
        intent,
        minimum_score=minimum_score,
    )

    initial_gate = _quality_gate(
        accepted_records[:rows],
        requested_count=rows,
        minimum_ratio=minimum_ratio,
        minimum_score=minimum_score,
        intent=intent,
    )
    retry_queries: list[str] = []
    retry_runs: list[tuple[list[dict[str, Any]], dict[str, Any]]] = []
    if auto_retry and not initial_gate["passed"]:
        retry_queries = [
            retry_query
            for retry_query in _unique_queries(intent.retry_queries(), limit=3)
            if retry_query.casefold() not in {item.casefold() for item in queries}
        ]
        if retry_queries:
            retry_runs = await asyncio.gather(
                *[
                    _run_provider(provider, retry_queries, fetch_rows)
                    for provider in providers
                ]
            )
            candidate_records = _merge_records(
                [
                    *candidate_records,
                    *[
                        record
                        for records, _status in retry_runs
                        for record in records
                    ],
                ]
            )
            accepted_records, rejected_records = annotate_and_filter_records(
                candidate_records,
                intent,
                minimum_score=minimum_score,
            )

    external_records = accepted_records[:rows]
    quality_gate = _quality_gate(
        external_records,
        requested_count=rows,
        minimum_ratio=minimum_ratio,
        minimum_score=minimum_score,
        intent=intent,
    )
    for record in external_records:
        record.pop("_rrf_score", None)

    seed_records = [_seed_record(document) for document in request.documents]
    evidence = _merge_records(external_records + seed_records)
    for record in evidence:
        record.pop("_rrf_score", None)

    source_statuses = [status for _records, status in provider_runs]
    available = sum(status["status"] != "unavailable" for status in source_statuses)
    aggregate_status = (
        "unavailable"
        if available == 0
        else "partial"
        if available < len(source_statuses)
        or any(status["status"] == "partial" for status in source_statuses)
        else "ok"
    )
    missing_abstracts = sum(
        1 for record in external_records if not record.get("has_abstract")
    )
    limitations = [
        "检索源返回公开书目元数据，不保证提供全文。",
        "检索结果用于证据发现，正式引用前仍需人工核读原文。",
    ]
    if aggregate_status != "ok":
        limitations.append("部分或全部外部数据源当前不可用，请查看 provider.sources。")
    if missing_abstracts:
        limitations.append(f"{missing_abstracts} 条外部记录没有摘要。")
    if planner_error:
        limitations.append("LLM 检索词扩展不可用，本次使用原始检索词。")
    if not quality_gate["passed"]:
        limitations.append(
            "主题相关证据未达到质量门禁，结果不得用于形成确定性结论。"
        )

    result = {
        "query": query,
        "queries": queries,
        "query_plan": {
            "expanded": len(queries) > 1,
            "planner": "deepseek-via-llm-gateway" if planner is not None else "disabled",
            "error": planner_error,
            "intent": intent.as_dict(),
            "candidate_fetch_rows": fetch_rows,
            "candidate_count": len(candidate_records),
            "retry_performed": bool(retry_runs),
            "retry_queries": retry_queries,
        },
        "evidence": evidence,
        "count": len(evidence),
        "external_count": len(external_records),
        "seed_count": sum(
            item.get("verification") == "user-provided" for item in evidence
        ),
        "fabricated_citations": 0,
        "quality_gate": quality_gate,
        "rejected_count": len(rejected_records),
        "rejected_records": _rejected_record_audit(rejected_records),
        "provider": {
            "name": " + ".join(provider.name for provider in providers),
            "status": aggregate_status,
            "retrieved_at": retrieved_at,
            "cache_hit": False,
            "source_count": len(providers),
            "available_source_count": available,
            "sources": source_statuses,
        },
        "limitations": limitations,
    }
    if aggregate_status != "unavailable" and cache_ttl > 0:
        _CACHE[cache_key] = (
            time.monotonic() + cache_ttl,
            copy.deepcopy(result),
        )
    return result


def clear_literature_cache() -> None:
    _CACHE.clear()
