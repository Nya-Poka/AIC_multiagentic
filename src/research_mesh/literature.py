from __future__ import annotations

import asyncio
import copy
import html
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from .schemas import ResearchRequest, SourceDocument


class LiteratureProviderError(RuntimeError):
    """A literature provider could not return a valid result."""


_HTML_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")
_CACHE: dict[tuple[str, int, str], tuple[float, dict[str, Any]]] = {}


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
    return doi or None


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
        "venue": _first_text(item.get("container-title")),
        "publisher": item.get("publisher"),
        "work_type": item.get("type"),
        "language": item.get("language"),
        "reference_count": item.get("reference-count"),
        "cited_by_count": item.get("is-referenced-by-count"),
        "relevance_score": item.get("score"),
        "has_abstract": abstract is not None,
        "verification": "crossref-rest-api",
    }


@dataclass
class CrossrefClient:
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

    async def search(self, query: str, rows: int) -> dict[str, Any]:
        params: dict[str, str | int] = {
            "query.bibliographic": query,
            "rows": rows,
        }
        if self.mailto:
            params["mailto"] = self.mailto
        headers = {
            "Accept": "application/json",
            "User-Agent": (
                "ResearchMesh/0.2 "
                "(https://github.com/Nya-Poka/AIC_multiagentic)"
            ),
        }
        last_error = "unknown provider error"
        async with httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=self.timeout_seconds,
            follow_redirects=True,
            transport=self.transport,
        ) as client:
            for attempt in range(self.retries + 1):
                try:
                    response = await client.get("/works", params=params)
                    if response.status_code == 429 or response.status_code >= 500:
                        last_error = f"Crossref returned HTTP {response.status_code}"
                        if attempt < self.retries:
                            retry_after = response.headers.get("Retry-After")
                            delay = min(float(retry_after or 0.5 * (2**attempt)), 5.0)
                            await asyncio.sleep(max(0.0, delay))
                            continue
                    response.raise_for_status()
                    payload = response.json()
                    if payload.get("status") != "ok":
                        raise LiteratureProviderError("Crossref returned a non-ok payload")
                    message = payload.get("message")
                    if not isinstance(message, dict):
                        raise LiteratureProviderError("Crossref response has no message object")
                    return message
                except (httpx.HTTPError, ValueError) as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    if attempt < self.retries:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
        raise LiteratureProviderError(last_error)


def _seed_record(document: SourceDocument) -> dict[str, Any]:
    record = document.model_dump(mode="json")
    doi = _normalise_doi(document.identifier)
    record.update(
        {
            "doi": doi,
            "url": f"https://doi.org/{doi}" if doi else None,
            "relevance_score": None,
            "verification": "user-provided",
        }
    )
    return record


def _deduplicate(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        doi = _normalise_doi(record.get("doi") or record.get("identifier"))
        title_key = _WHITESPACE.sub(" ", str(record.get("title", "")).lower()).strip()
        key = f"doi:{doi}" if doi else f"title:{title_key}"
        if not title_key or key in seen:
            continue
        seen.add(key)
        output.append(record)
    return output


async def search_literature(
    request: ResearchRequest,
    *,
    client: CrossrefClient | None = None,
) -> dict[str, Any]:
    query = (request.literature_query or request.question).strip()[:500]
    rows = request.max_literature_results
    provider = client or CrossrefClient.from_environment()
    cache_ttl = _env_int(
        "RESEARCH_MESH_LITERATURE_CACHE_TTL_SECONDS",
        900,
        minimum=0,
        maximum=86400,
    )
    cache_key = (query, rows, provider.base_url)
    cached = _CACHE.get(cache_key)
    if cache_ttl > 0 and cached and cached[0] >= time.monotonic():
        result = copy.deepcopy(cached[1])
        result["provider"]["cache_hit"] = True
        return result

    retrieved_at = datetime.now(timezone.utc).isoformat()
    status = "ok"
    error: str | None = None
    total_results = 0
    external_records: list[dict[str, Any]] = []
    try:
        message = await provider.search(query, rows)
        total_results = int(message.get("total-results", 0))
        for item in message.get("items", []):
            if isinstance(item, dict):
                record = _crossref_record(item)
                if record:
                    external_records.append(record)
    except LiteratureProviderError as exc:
        status = "unavailable"
        error = str(exc)

    seed_records = [_seed_record(document) for document in request.documents]
    evidence = _deduplicate(external_records + seed_records)
    missing_abstracts = sum(
        1 for record in external_records if not record.get("has_abstract")
    )
    limitations = [
        "Crossref 返回书目元数据，不保证提供全文或摘要。",
        "检索结果用于证据发现，正式引用前仍需人工核读原文。",
    ]
    if status != "ok":
        limitations.append("Crossref 当前不可用，结果仅包含用户提供的种子文献。")
    if missing_abstracts:
        limitations.append(f"{missing_abstracts} 条外部记录没有摘要。")

    result = {
        "query": query,
        "evidence": evidence,
        "count": len(evidence),
        "external_count": len(external_records),
        "seed_count": len(seed_records),
        "fabricated_citations": 0,
        "provider": {
            "name": "Crossref",
            "status": status,
            "base_url": provider.base_url,
            "authentication": "none",
            "retrieved_at": retrieved_at,
            "total_results": total_results,
            "cache_hit": False,
            "error": error,
        },
        "limitations": limitations,
    }
    if status == "ok" and cache_ttl > 0:
        _CACHE[cache_key] = (time.monotonic() + cache_ttl, copy.deepcopy(result))
    return result


def clear_literature_cache() -> None:
    _CACHE.clear()
