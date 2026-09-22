from __future__ import annotations

import ast
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import ValidationError

from .llm import LLMCompletionRequest, LLMMessage
from .schemas import ResearchRequest


class InputNormalizationError(ValueError):
    """The caller input cannot be converted into a valid research request."""


class CompletionClient(Protocol):
    async def complete(self, request: LLMCompletionRequest): ...


_FENCED_BLOCK = re.compile(
    r"```(?:json|jsonc|javascript|python)?\s*(.*?)\s*```",
    flags=re.IGNORECASE | re.DOTALL,
)
_TRAILING_COMMA = re.compile(r",\s*([}\]])")
_BARE_KEY = re.compile(
    r'([{,]\s*)([A-Za-z_][A-Za-z0-9_]*|[\u3400-\u9fff]+)\s*:'
)
_WHITESPACE = re.compile(r"\s+")
_FULLWIDTH_TRANSLATION = str.maketrans(
    {
        "｛": "{",
        "｝": "}",
        "［": "[",
        "］": "]",
        "：": ":",
        "，": ",",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
    }
)

_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "question": (
        "question",
        "query",
        "topic",
        "research_question",
        "researchQuestion",
        "问题",
        "研究问题",
        "课题",
        "主题",
    ),
    "objective": (
        "objective",
        "goal",
        "research_objective",
        "researchObjective",
        "目标",
        "研究目标",
        "任务目标",
    ),
    "literature_query": (
        "literature_query",
        "literatureQuery",
        "search_query",
        "searchQuery",
        "检索式",
        "检索词",
        "文献检索词",
    ),
    "max_literature_results": (
        "max_literature_results",
        "maxLiteratureResults",
        "max_results",
        "maxResults",
        "文献数量",
        "最大文献数",
    ),
    "documents": ("documents", "sources", "文献", "已有文献"),
    "constraints": (
        "constraints",
        "requirements",
        "limitations",
        "约束",
        "限制",
        "要求",
    ),
}


def _clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = _WHITESPACE.sub(" ", value).strip()
    return cleaned or None


def _candidate_fragments(text: str) -> list[str]:
    cleaned = text.strip().lstrip("\ufeff")
    candidates = [cleaned]
    candidates.extend(match.group(1).strip() for match in _FENCED_BLOCK.finditer(cleaned))

    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", cleaned):
        fragment = cleaned[match.start() :]
        try:
            _value, end = decoder.raw_decode(fragment)
        except json.JSONDecodeError:
            continue
        candidates.append(fragment[:end])

    first = cleaned.find("{")
    last = cleaned.rfind("}")
    if first >= 0 and last > first:
        candidates.append(cleaned[first : last + 1])

    output: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate and candidate not in seen:
            output.append(candidate)
            seen.add(candidate)
    return output


def _repair_json_text(value: str) -> str:
    repaired = value.translate(_FULLWIDTH_TRANSLATION)
    repaired = _TRAILING_COMMA.sub(r"\1", repaired)
    repaired = _BARE_KEY.sub(r'\1"\2":', repaired)
    return repaired


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Extract one object from strict, fenced, embedded, or common loose JSON."""

    for candidate in _candidate_fragments(text):
        repaired = _repair_json_text(candidate)
        for value in (candidate, repaired):
            try:
                parsed = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                parsed = None
            if isinstance(parsed, dict):
                return parsed

        try:
            parsed_literal = ast.literal_eval(repaired)
        except (SyntaxError, ValueError):
            continue
        if isinstance(parsed_literal, dict):
            return parsed_literal
    return None


def _lookup(mapping: dict[str, Any], canonical_name: str) -> Any:
    for alias in _FIELD_ALIASES[canonical_name]:
        if alias in mapping:
            return mapping[alias]
    return None


def _unwrap_request(payload: dict[str, Any]) -> dict[str, Any]:
    current = payload
    for key in ("request", "input", "payload", "data"):
        nested = current.get(key)
        if isinstance(nested, dict):
            current = nested
            break
    return current


def _normalise_constraints(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = re.split(r"[;；\n]+", value)
    elif isinstance(value, (list, tuple, set)):
        parts = list(value)
    else:
        parts = [value]
    output: list[str] = []
    for part in parts:
        cleaned = _clean_text(str(part))
        if cleaned and cleaned not in output:
            output.append(cleaned)
    return output


def _canonical_request(payload: dict[str, Any]) -> ResearchRequest:
    source = _unwrap_request(payload)
    question = _clean_text(_lookup(source, "question"))
    objective = _clean_text(_lookup(source, "objective"))
    if question and not objective:
        objective = f"围绕该问题完成文献检索、实验设计、数据分析和规范复核：{question}"

    candidate: dict[str, Any] = {
        "question": question,
        "objective": objective,
        "constraints": _normalise_constraints(_lookup(source, "constraints")),
    }
    for field in ("literature_query", "max_literature_results", "documents"):
        value = _lookup(source, field)
        if value is not None:
            candidate[field] = value

    try:
        return ResearchRequest.model_validate(candidate)
    except ValidationError as exc:
        fields = sorted({str(error["loc"][-1]) for error in exc.errors()})
        raise InputNormalizationError(
            "research input is missing or invalid fields: " + ", ".join(fields)
        ) from exc


def _request_from_natural_language(text: str) -> ResearchRequest:
    cleaned = _clean_text(text)
    if cleaned is None or len(cleaned) < 8:
        raise InputNormalizationError(
            "请提供至少 8 个字符的科研问题，或提交包含 question 和 objective 的 JSON 对象"
        )
    question = cleaned[:10_000]
    objective = (
        "围绕用户科研问题完成文献检索、实验设计、数据分析、统计摘要和规范复核，"
        f"形成可追溯科研报告：{question}"
    )[:10_000]
    literature_query = question[:500]
    return ResearchRequest(
        question=question,
        objective=objective,
        literature_query=literature_query,
    )


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class ResearchInputNormalizer:
    """Convert AIP structured data, loose JSON, or natural language to one schema."""

    llm_client: CompletionClient | None = None
    use_llm: bool = False

    async def normalize(
        self,
        structured_inputs: list[dict[str, Any]],
        text_inputs: list[str],
    ) -> dict[str, Any]:
        errors: list[InputNormalizationError] = []

        for payload in reversed(structured_inputs):
            try:
                return {"request": _canonical_request(payload).model_dump(mode="json")}
            except InputNormalizationError as exc:
                errors.append(exc)

        for text in reversed(text_inputs):
            payload = extract_json_object(text)
            if payload is None:
                continue
            try:
                return {"request": _canonical_request(payload).model_dump(mode="json")}
            except InputNormalizationError as exc:
                errors.append(exc)

        natural_language = next(
            (text.strip() for text in reversed(text_inputs) if text.strip()),
            "",
        )
        if natural_language and self.use_llm and self.llm_client is not None:
            try:
                request = await self._normalise_with_llm(natural_language)
                return {"request": request.model_dump(mode="json")}
            except Exception:
                # Input conversion must remain available when the optional LLM is down.
                pass

        if natural_language:
            return {
                "request": _request_from_natural_language(natural_language).model_dump(
                    mode="json"
                )
            }
        if errors:
            raise errors[-1]
        raise InputNormalizationError(
            "需要 JSON、结构化 data item，或非空自然语言科研问题"
        )

    async def _normalise_with_llm(self, text: str) -> ResearchRequest:
        assert self.llm_client is not None
        response = await self.llm_client.complete(
            LLMCompletionRequest(
                messages=[
                    LLMMessage(
                        role="system",
                        content=(
                            "Convert the user input into exactly one JSON object for a research "
                            "assistant. Allowed keys: question, objective, literature_query, "
                            "max_literature_results, documents, constraints. question and objective "
                            "must each contain at least 8 characters. constraints must be an array "
                            "of strings. Do not invent source documents, citations, or numeric data. "
                            "Return JSON only."
                        ),
                    ),
                    LLMMessage(role="user", content=text[:20_000]),
                ],
                temperature=0,
                max_tokens=800,
                response_format="json_object",
            )
        )
        payload = extract_json_object(response.content)
        if payload is None:
            raise InputNormalizationError("LLM input normalizer returned no JSON object")
        return _canonical_request(payload)

    @classmethod
    def from_environment(cls, *, llm_client: CompletionClient | None = None):
        provider_enabled = (
            os.getenv("RESEARCH_MESH_LLM_PROVIDER", "disabled").strip().lower()
            != "disabled"
        )
        use_llm = _env_bool(
            "RESEARCH_MESH_INPUT_NORMALIZER_USE_LLM", provider_enabled
        )
        if not use_llm:
            return cls(llm_client=None, use_llm=False)
        if llm_client is None:
            from .llm import LLMGatewayClient

            llm_client = LLMGatewayClient.from_environment()
        return cls(llm_client=llm_client, use_llm=True)
