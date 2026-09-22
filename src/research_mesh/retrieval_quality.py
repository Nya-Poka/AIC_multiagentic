from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any


_WHITESPACE = re.compile(r"\s+")
_ENGLISH_TOKEN = re.compile(r"[a-z][a-z0-9-]{2,}")
_PLATFORM_TERMS = (
    "调用智能体",
    "多智能体协作",
    "dag",
    "aip",
    "rpc",
    "叮当",
    "上游产物摘要",
)
_STOPWORDS = {
    "about",
    "after",
    "among",
    "and",
    "are",
    "between",
    "does",
    "effect",
    "effects",
    "for",
    "from",
    "how",
    "impact",
    "influence",
    "into",
    "relationship",
    "research",
    "study",
    "that",
    "the",
    "their",
    "this",
    "with",
}


@dataclass(frozen=True)
class ConceptDefinition:
    dimension: str
    label: str
    aliases: tuple[str, ...]
    query_terms: tuple[str, ...]


_CONCEPTS: tuple[ConceptDefinition, ...] = (
    ConceptDefinition(
        "population",
        "college-students",
        ("大学生", "高校学生", "college student", "university student", "undergraduate"),
        ("college students", "university students", "undergraduates"),
    ),
    ConceptDefinition(
        "population",
        "adolescents",
        ("青少年", "中学生", "adolescent", "teenager", "high school student"),
        ("adolescents", "high school students"),
    ),
    ConceptDefinition(
        "population",
        "children",
        ("儿童", "小学生", "child", "children", "primary school student"),
        ("children",),
    ),
    ConceptDefinition(
        "population",
        "older-adults",
        ("老年人", "老年群体", "older adult", "elderly"),
        ("older adults",),
    ),
    ConceptDefinition(
        "exposure",
        "daytime-nap",
        ("午睡", "小睡", "daytime nap", "napping", "nap duration", "siesta"),
        ("daytime nap", "nap duration", "napping"),
    ),
    ConceptDefinition(
        "exposure",
        "sleep",
        (
            "睡眠",
            "睡眠时长",
            "sleep",
            "sleep duration",
            "sleepiness",
            "sleep deprivation",
        ),
        ("sleep duration", "sleep"),
    ),
    ConceptDefinition(
        "exposure",
        "physical-activity",
        ("运动", "身体活动", "锻炼", "exercise", "physical activity"),
        ("physical activity", "exercise"),
    ),
    ConceptDefinition(
        "exposure",
        "social-media",
        ("社交媒体", "短视频", "social media", "screen time"),
        ("social media", "screen time"),
    ),
    ConceptDefinition(
        "exposure",
        "stress",
        ("压力", "应激", "stress", "academic stress"),
        ("stress", "academic stress"),
    ),
    ConceptDefinition(
        "outcome",
        "attention",
        (
            "注意力",
            "持续注意",
            "警觉性",
            "attention",
            "sustained attention",
            "vigilance",
            "cognitive performance",
            "cognition",
            "learning",
            "memory",
        ),
        ("sustained attention", "vigilance", "cognitive performance"),
    ),
    ConceptDefinition(
        "outcome",
        "academic-performance",
        (
            "学习表现",
            "学业表现",
            "学习成绩",
            "academic performance",
            "academic achievement",
            "learning outcome",
            "grade point average",
            "gpa",
        ),
        ("academic performance", "academic achievement"),
    ),
    ConceptDefinition(
        "outcome",
        "mental-health",
        ("焦虑", "抑郁", "心理健康", "anxiety", "depression", "mental health"),
        ("mental health", "anxiety", "depression"),
    ),
    ConceptDefinition(
        "measurement",
        "attention-tests",
        ("pvt", "sart", "flanker", "psychomotor vigilance", "注意力测验"),
        ("PVT", "SART", "psychomotor vigilance test"),
    ),
)


@dataclass(frozen=True)
class IntentDimension:
    name: str
    labels: tuple[str, ...]
    aliases: tuple[str, ...]
    query_terms: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "labels": list(self.labels),
            "query_terms": list(self.query_terms),
        }


@dataclass(frozen=True)
class ResearchSearchIntent:
    question: str
    base_query: str
    dimensions: tuple[IntentDimension, ...]
    required_dimensions: tuple[str, ...]
    keywords: tuple[str, ...]
    excluded_terms: tuple[str, ...] = _PLATFORM_TERMS

    def as_dict(self) -> dict[str, Any]:
        return {
            "dimensions": [dimension.as_dict() for dimension in self.dimensions],
            "required_dimensions": list(self.required_dimensions),
            "keywords": list(self.keywords),
            "excluded_terms": list(self.excluded_terms),
        }

    def suggested_queries(self) -> list[str]:
        if len(self.dimensions) < 2:
            return []
        primary = " ".join(
            dimension.query_terms[0]
            for dimension in self.dimensions
            if dimension.query_terms
        )
        return [primary] if primary else []

    def retry_queries(self) -> list[str]:
        if not self.required_dimensions:
            return []
        dimensions = {
            dimension.name: dimension
            for dimension in self.dimensions
        }
        required = [
            dimensions[name]
            for name in self.required_dimensions
            if name in dimensions
        ]
        population = dimensions.get("population")
        queries: list[str] = []
        for variant in range(1, 3):
            parts: list[str] = []
            if population and population.query_terms:
                parts.append(population.query_terms[min(variant, len(population.query_terms) - 1)])
            for dimension in required:
                if dimension.query_terms:
                    parts.append(
                        dimension.query_terms[min(variant, len(dimension.query_terms) - 1)]
                    )
            if parts:
                queries.append(" ".join(parts))
        return queries


@dataclass(frozen=True)
class RelevanceAssessment:
    score: float
    accepted: bool
    matched_dimensions: tuple[str, ...]
    matched_concepts: tuple[str, ...]
    keyword_coverage: float
    concept_coverage: float
    title_dimension_coverage: float
    directness_score: float
    competing_concepts: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class SourceQualityAssessment:
    score: float
    tier: str
    evidence_type: str
    reasons: tuple[str, ...]


def _normalise(value: str) -> str:
    return _WHITESPACE.sub(" ", value.casefold()).strip()


def _contains(text: str, alias: str) -> bool:
    alias_key = alias.casefold()
    if re.fullmatch(r"[a-z0-9 -]+", alias_key):
        return re.search(rf"(?<![a-z0-9]){re.escape(alias_key)}(?![a-z0-9])", text) is not None
    return alias_key in text


def _unique(values: list[str]) -> tuple[str, ...]:
    output: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = raw.strip()
        key = value.casefold()
        if value and key not in seen:
            output.append(value)
            seen.add(key)
    return tuple(output)


def build_search_intent(question: str, base_query: str) -> ResearchSearchIntent:
    source = _normalise(f"{question} {base_query}")
    grouped: dict[str, list[ConceptDefinition]] = {}
    for concept in _CONCEPTS:
        if any(_contains(source, alias) for alias in concept.aliases):
            grouped.setdefault(concept.dimension, []).append(concept)

    order = ("population", "exposure", "outcome", "measurement")
    dimensions: list[IntentDimension] = []
    for name in order:
        concepts = grouped.get(name, [])
        if not concepts:
            continue
        dimensions.append(
            IntentDimension(
                name=name,
                labels=_unique([concept.label for concept in concepts]),
                aliases=_unique(
                    [alias for concept in concepts for alias in concept.aliases]
                ),
                query_terms=_unique(
                    [term for concept in concepts for term in concept.query_terms]
                ),
            )
        )

    dimension_names = {dimension.name for dimension in dimensions}
    if {"exposure", "outcome"}.issubset(dimension_names):
        required = ("exposure", "outcome")
    else:
        required = tuple(
            name for name in ("exposure", "outcome", "population")
            if name in dimension_names
        )

    english_keywords = [
        token
        for token in _ENGLISH_TOKEN.findall(source)
        if token not in _STOPWORDS
        and not any(token in term for term in _PLATFORM_TERMS)
    ]
    dimension_terms = [
        term
        for dimension in dimensions
        for term in dimension.query_terms
    ]
    keywords = _unique([*dimension_terms, *english_keywords])[:20]
    return ResearchSearchIntent(
        question=question,
        base_query=base_query,
        dimensions=tuple(dimensions),
        required_dimensions=required,
        keywords=keywords,
    )


def assess_record_relevance(
    record: dict[str, Any],
    intent: ResearchSearchIntent,
    *,
    minimum_score: float,
    minimum_directness: float = 0.45,
) -> RelevanceAssessment:
    title = _normalise(str(record.get("title") or ""))
    text = _normalise(
        " ".join(
            str(record.get(field) or "")
            for field in ("title", "summary", "venue")
        )
    )
    matched_dimensions: list[str] = []
    matched_concepts: list[str] = []
    for dimension in intent.dimensions:
        aliases = [alias for alias in dimension.aliases if _contains(text, alias)]
        if aliases:
            matched_dimensions.append(dimension.name)
            matched_concepts.extend(aliases[:3])

    title_dimensions: list[str] = []
    for dimension in intent.dimensions:
        if any(_contains(title, alias) for alias in dimension.aliases):
            title_dimensions.append(dimension.name)

    required = set(intent.required_dimensions)
    required_matches = required.intersection(matched_dimensions)
    concept_coverage = len(required_matches) / len(required) if required else 1.0
    title_required_matches = required.intersection(title_dimensions)
    title_dimension_coverage = (
        len(title_required_matches) / len(required) if required else 1.0
    )
    directness_score = (
        0.7 * title_dimension_coverage + 0.3 * concept_coverage
        if required
        else 1.0
    )
    requested_exposures = {
        label
        for dimension in intent.dimensions
        if dimension.name == "exposure"
        for label in dimension.labels
    }
    competing_concepts = _unique(
        [
            concept.label
            for concept in _CONCEPTS
            if concept.dimension == "exposure"
            and concept.label not in requested_exposures
            and any(_contains(title, alias) for alias in concept.aliases)
        ]
    )
    keyword_hits = sum(_contains(text, keyword) for keyword in intent.keywords)
    keyword_coverage = (
        keyword_hits / len(intent.keywords) if intent.keywords else 0.0
    )
    rrf_score = min(1.0, float(record.get("_rrf_score", 0.0)) * 60.0)
    provider_raw = record.get("relevance_score")
    provider_score = 0.0
    if isinstance(provider_raw, (int, float)) and provider_raw > 0:
        provider_score = math.log1p(float(provider_raw))
        provider_score = provider_score / (1.0 + provider_score)
    multi_source = min(1.0, max(0, int(record.get("source_count", 1)) - 1) / 2)

    if required:
        score = (
            0.45 * concept_coverage
            + 0.17 * keyword_coverage
            + 0.16 * directness_score
            + 0.12 * rrf_score
            + 0.07 * provider_score
            + 0.03 * multi_source
        )
        competing_primary_topic = bool(competing_concepts) and title_dimension_coverage < 1.0
        accepted = (
            required.issubset(matched_dimensions)
            and score >= minimum_score
            and directness_score >= minimum_directness
            and not competing_primary_topic
        )
        reason = (
            "accepted"
            if accepted
            else "missing-required-dimension"
            if not required.issubset(matched_dimensions)
            else "competing-primary-topic"
            if competing_primary_topic
            else "insufficient-topic-directness"
            if directness_score < minimum_directness
            else "below-relevance-threshold"
        )
    else:
        score = (
            0.55 * keyword_coverage
            + 0.25 * rrf_score
            + 0.15 * provider_score
            + 0.05 * multi_source
        )
        # Without a recognised concept model, ranking is advisory rather than a hard filter.
        accepted = True
        reason = "accepted-with-generic-intent"

    return RelevanceAssessment(
        score=round(score, 4),
        accepted=accepted,
        matched_dimensions=tuple(matched_dimensions),
        matched_concepts=_unique(matched_concepts),
        keyword_coverage=round(keyword_coverage, 4),
        concept_coverage=round(concept_coverage, 4),
        title_dimension_coverage=round(title_dimension_coverage, 4),
        directness_score=round(directness_score, 4),
        competing_concepts=competing_concepts,
        reason=reason,
    )


def assess_source_quality(record: dict[str, Any]) -> SourceQualityAssessment:
    """Grade metadata completeness without claiming that a paper is scientifically true."""

    score = 0.0
    reasons: list[str] = []
    if record.get("doi"):
        score += 0.22
        reasons.append("persistent-identifier")
    if record.get("has_abstract"):
        score += 0.25
        reasons.append("abstract-available")
    if record.get("venue"):
        score += 0.10
        reasons.append("venue-identified")
    if record.get("authors"):
        score += 0.08
        reasons.append("authors-identified")
    if isinstance(record.get("year"), int):
        score += 0.05
        reasons.append("publication-year-identified")

    work_type = str(record.get("work_type") or "").casefold()
    if any(term in work_type for term in ("review", "meta-analysis")):
        evidence_type = "evidence-synthesis"
        score += 0.08
        reasons.append("evidence-synthesis-metadata")
    elif any(term in work_type for term in ("journal", "article")):
        evidence_type = "journal-article"
        score += 0.08
        reasons.append("article-type-identified")
    elif any(term in work_type for term in ("proceeding", "conference", "abstract")):
        evidence_type = "conference-material"
        score += 0.03
        reasons.append("conference-material")
    elif any(term in work_type for term in ("preprint", "posted-content")):
        evidence_type = "preprint"
        score += 0.02
        reasons.append("not-formally-published")
    else:
        evidence_type = "unclassified-scholarly-record"

    providers = record.get("providers")
    provider_count = len(providers) if isinstance(providers, list) else 0
    if provider_count > 1:
        score += 0.06
        reasons.append("multi-source-metadata")
    elif record.get("verification") and record.get("provider"):
        score += 0.08
        reasons.append("provider-verified-metadata")

    citations = record.get("cited_by_count")
    if isinstance(citations, int) and citations > 0:
        score += min(0.05, math.log1p(citations) / 100)
        reasons.append("citation-metadata-available")
    if record.get("open_access_url"):
        score += 0.03
        reasons.append("open-access-location")

    score = round(min(1.0, score), 4)
    tier = "A" if score >= 0.80 else "B" if score >= 0.65 else "C" if score >= 0.45 else "D"
    return SourceQualityAssessment(
        score=score,
        tier=tier,
        evidence_type=evidence_type,
        reasons=tuple(reasons),
    )


def annotate_and_filter_records(
    records: list[dict[str, Any]],
    intent: ResearchSearchIntent,
    *,
    minimum_score: float,
    minimum_directness: float = 0.45,
    minimum_source_quality: float = 0.35,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for raw in records:
        record = dict(raw)
        assessment = assess_record_relevance(
            record,
            intent,
            minimum_score=minimum_score,
            minimum_directness=minimum_directness,
        )
        source_quality = assess_source_quality(record)
        accepted_record = assessment.accepted and source_quality.score >= minimum_source_quality
        reason = assessment.reason
        if assessment.accepted and source_quality.score < minimum_source_quality:
            reason = "insufficient-source-quality"
        ranking_score = (
            0.65 * assessment.score
            + 0.20 * assessment.directness_score
            + 0.15 * source_quality.score
        )
        record.update(
            {
                "topic_relevance_score": assessment.score,
                "matched_dimensions": list(assessment.matched_dimensions),
                "matched_concepts": list(assessment.matched_concepts),
                "keyword_coverage": assessment.keyword_coverage,
                "concept_coverage": assessment.concept_coverage,
                "title_dimension_coverage": assessment.title_dimension_coverage,
                "topic_directness_score": assessment.directness_score,
                "competing_concepts": list(assessment.competing_concepts),
                "source_quality_score": source_quality.score,
                "source_quality_tier": source_quality.tier,
                "source_quality_reasons": list(source_quality.reasons),
                "evidence_type": source_quality.evidence_type,
                "ranking_score": round(ranking_score, 4),
                "quality_decision": "accepted" if accepted_record else "rejected",
                "quality_reason": reason,
            }
        )
        (accepted if accepted_record else rejected).append(record)

    accepted.sort(
        key=lambda record: (
            float(record.get("ranking_score", 0.0)),
            float(record.get("topic_relevance_score", 0.0)),
            float(record.get("source_quality_score", 0.0)),
            float(record.get("_rrf_score", 0.0)),
            bool(record.get("has_abstract")),
            math.log1p(record.get("cited_by_count") or 0),
        ),
        reverse=True,
    )
    rejected.sort(
        key=lambda record: float(record.get("topic_relevance_score", 0.0)),
        reverse=True,
    )
    return accepted, rejected
