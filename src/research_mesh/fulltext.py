from __future__ import annotations

import asyncio
import hashlib
import io
import ipaddress
import os
import re
import socket
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse
from urllib.parse import urljoin

import httpx


class FullTextError(RuntimeError):
    """An open full-text document could not be fetched or parsed safely."""


_WHITESPACE = re.compile(r"\s+")
_TOKEN = re.compile(r"[A-Za-z0-9\u3400-\u9fff]{2,}")


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(os.getenv(name, str(default)))
    except ValueError:
        parsed = default
    return max(minimum, min(maximum, parsed))


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() in {"script", "style", "noscript", "svg"}:
            self._ignored += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"script", "style", "noscript", "svg"} and self._ignored:
            self._ignored -= 1
        elif tag.casefold() in {"p", "div", "section", "article", "li", "br", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored and data.strip():
            self.parts.append(data)

    def text(self) -> str:
        return "\n".join(
            _WHITESPACE.sub(" ", part).strip()
            for part in "".join(self.parts).splitlines()
            if part.strip()
        )


def _public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return not any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
        )
    )


async def _validate_public_https_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise FullTextError("full-text URL must be an absolute credential-free HTTPS URL")
    allowed = {
        item.strip().casefold()
        for item in os.getenv("RESEARCH_MESH_FULLTEXT_ALLOWED_HOSTS", "").split(",")
        if item.strip()
    }
    hostname = parsed.hostname.casefold()
    if allowed and not any(hostname == item or hostname.endswith(f".{item}") for item in allowed):
        raise FullTextError("full-text host is not in the configured allowlist")
    try:
        records = await asyncio.to_thread(
            socket.getaddrinfo, parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM
        )
    except socket.gaierror as exc:
        raise FullTextError("full-text host could not be resolved") from exc
    addresses = {record[4][0].split("%", 1)[0] for record in records}
    if not addresses or not all(_public_address(address) for address in addresses):
        raise FullTextError("full-text host resolved to a non-public address")


def _extract_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - optional deployment dependency
        raise FullTextError("PDF extraction requires the optional pypdf dependency") from exc
    reader = PdfReader(io.BytesIO(data), strict=False)
    if reader.is_encrypted:
        raise FullTextError("encrypted PDF is not supported")
    return "\n".join((page.extract_text() or "") for page in reader.pages[:200])


def _extract_text(data: bytes, media_type: str) -> str:
    if media_type == "application/pdf" or data.startswith(b"%PDF"):
        return _extract_pdf(data)
    try:
        decoded = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FullTextError("full-text document is not valid UTF-8 or PDF") from exc
    if media_type in {"text/html", "application/xhtml+xml", "application/xml", "text/xml"}:
        parser = _TextExtractor()
        parser.feed(decoded)
        return parser.text()
    if media_type.startswith("text/"):
        return decoded
    raise FullTextError(f"unsupported full-text media type: {media_type}")


def _segments(text: str, query: str, limit: int) -> list[dict[str, Any]]:
    paragraphs = [
        _WHITESPACE.sub(" ", part).strip()
        for part in re.split(r"\n+", text)
        if len(_WHITESPACE.sub(" ", part).strip()) >= 80
    ]
    if not paragraphs:
        paragraphs = [
            text[index : index + 1200].strip()
            for index in range(0, len(text), 1200)
            if len(text[index : index + 1200].strip()) >= 80
        ]
    query_terms = {token.casefold() for token in _TOKEN.findall(query)}
    ranked: list[tuple[int, int, str]] = []
    for index, paragraph in enumerate(paragraphs):
        terms = {token.casefold() for token in _TOKEN.findall(paragraph)}
        ranked.append((len(query_terms & terms), -index, paragraph))
    selected = sorted(ranked, reverse=True)[:limit]
    return [
        {
            "segment_id": f"segment-{index + 1}",
            "text": paragraph[:1800],
            "text_sha256": hashlib.sha256(paragraph.encode("utf-8")).hexdigest(),
            "query_term_overlap": overlap,
        }
        for index, (overlap, _position, paragraph) in enumerate(selected)
    ]


async def fetch_open_full_text(
    url: str,
    *,
    query: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    max_bytes = _env_int(
        "RESEARCH_MESH_FULLTEXT_MAX_BYTES", 8 * 1024 * 1024, 1024, 20 * 1024 * 1024
    )
    timeout = float(os.getenv("RESEARCH_MESH_FULLTEXT_TIMEOUT_SECONDS", "20"))
    async with httpx.AsyncClient(
        timeout=max(1.0, timeout),
        follow_redirects=False,
        transport=transport,
    ) as client:
        current_url = url
        for redirect_count in range(6):
            if transport is None:
                await _validate_public_https_url(current_url)
            async with client.stream(
                "GET",
                current_url,
                headers={"Accept": "text/html,application/pdf,text/plain;q=0.8"},
            ) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    if redirect_count >= 5:
                        raise FullTextError("full-text redirect limit exceeded")
                    location = response.headers.get("location", "").strip()
                    if not location:
                        raise FullTextError("full-text redirect omitted Location")
                    current_url = urljoin(str(response.url), location)
                    continue
                response.raise_for_status()
                content_length = response.headers.get("Content-Length")
                if content_length:
                    try:
                        declared_length = int(content_length)
                    except ValueError as exc:
                        raise FullTextError("invalid full-text Content-Length") from exc
                    if declared_length > max_bytes:
                        raise FullTextError("full-text document exceeds the configured size limit")
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise FullTextError("full-text document exceeds the configured size limit")
                    chunks.append(chunk)
                data = b"".join(chunks)
                media_type = response.headers.get(
                    "Content-Type", "application/octet-stream"
                ).split(";", 1)[0].strip().lower()
                final_url = str(response.url)
                break
        else:  # pragma: no cover - loop always exits through return or error
            raise FullTextError("full-text request did not produce a response")
    text = _extract_text(data, media_type)
    if len(text.strip()) < 200:
        raise FullTextError("full-text extraction produced too little text")
    return {
        "status": "available",
        "source_url": final_url,
        "media_type": media_type,
        "content_sha256": hashlib.sha256(data).hexdigest(),
        "characters_extracted": len(text),
        "segments": _segments(
            text,
            query,
            _env_int("RESEARCH_MESH_FULLTEXT_SEGMENTS_PER_DOCUMENT", 3, 1, 8),
        ),
        "limitations": [
            "Segments are machine-extracted from an open-access location and require human verification against the source document."
        ],
    }


async def enrich_open_full_text(
    records: list[dict[str, Any]],
    *,
    query: str,
) -> dict[str, Any]:
    enabled = _env_bool("RESEARCH_MESH_FULLTEXT_ENABLED", False)
    if not enabled:
        return {"enabled": False, "attempted": 0, "available": 0, "failed": 0}
    limit = _env_int("RESEARCH_MESH_FULLTEXT_MAX_DOCUMENTS", 3, 1, 10)
    candidates = [
        record
        for record in records
        if isinstance(record.get("open_access_url"), str)
        and record["open_access_url"].startswith("https://")
    ][:limit]

    async def enrich(record: dict[str, Any]) -> bool:
        try:
            record["full_text"] = await fetch_open_full_text(
                str(record["open_access_url"]), query=query
            )
            return True
        except (FullTextError, httpx.HTTPError, ValueError, OSError) as exc:
            record["full_text"] = {
                "status": "unavailable",
                "source_url": record.get("open_access_url"),
                "error": f"{type(exc).__name__}: {exc}",
                "segments": [],
            }
            return False

    outcomes = await asyncio.gather(*(enrich(record) for record in candidates))
    available = sum(outcomes)
    return {
        "enabled": True,
        "attempted": len(candidates),
        "available": available,
        "failed": len(candidates) - available,
        "policy": "open-access-https-only-with-public-address-validation",
    }
