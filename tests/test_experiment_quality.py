from __future__ import annotations

import asyncio

from research_mesh.experiment import (
    deterministic_experiment_plan,
    experiment_plan_issues,
    generate_experiment_plan,
)
from research_mesh.partners import review_processor
from research_mesh.schemas import ResearchRequest


def sleep_request() -> ResearchRequest:
    return ResearchRequest(
        question="研究睡眠时长是否影响大学生学习表现，并设计一项可复现实验",
        objective="形成具有明确变量、样本量方案、统计模型与伦理边界的实验协议。",
    )


def literature_artifact() -> dict:
    return {
        "evidence": [
            {"title": "Sleep and academic performance", "doi": "10.1000/a"},
            {"title": "Sleep duration in college", "doi": "10.1000/b"},
            {"title": "Objective sleep measurement", "doi": "10.1000/c"},
        ],
        "quality_gate": {
            "passed": True,
            "relevant_count": 3,
            "required_count": 3,
        },
    }


def test_sleep_protocol_is_concrete_reproducible_and_reviewable() -> None:
    request = sleep_request()
    plan = deterministic_experiment_plan(request).model_dump(mode="json")

    assert "随机对照试验" in plan["design_type"]
    assert "腕式" in " ".join(plan["independent_variables"])
    assert "课程测验" in " ".join(plan["dependent_variables"])
    assert "功效" in plan["sample_size_plan"]
    assert len(plan["analysis_plan"]) >= 3
    assert len(plan["reproducibility"]) >= 4
    assert experiment_plan_issues(plan) == []

    review = review_processor(
        {
            "request": request.model_dump(mode="json"),
            "artifacts": {
                "literature": literature_artifact(),
                "experiment": plan,
                "analysis": {"external_count": 3, "doi_coverage": 1.0},
            },
        }
    )
    assert review["passed"] is True
    assert not any(item["severity"] == "error" for item in review["findings"])


def test_review_rejects_old_placeholder_experiment_template() -> None:
    request = sleep_request()
    old_template = {
        "hypothesis": "研究条件与目标结果之间存在可检验的关联。",
        "independent_variables": ["研究条件（需在正式试验前具体化）"],
        "dependent_variables": ["与研究目标一致的可观察结果指标"],
        "controls": ["统一数据采集流程"],
        "steps": ["登记研究假设和分析计划"],
    }

    issues = experiment_plan_issues(old_template)
    assert issues

    review = review_processor(
        {
            "request": request.model_dump(mode="json"),
            "artifacts": {
                "literature": literature_artifact(),
                "experiment": old_template,
                "analysis": {"external_count": 3, "doi_coverage": 1.0},
            },
        }
    )
    assert review["passed"] is False
    assert review["decision"] == "revise"
    assert any(
        item["severity"] == "error" and "实验方案" in item["message"]
        for item in review["findings"]
    )


def test_invalid_llm_protocol_falls_back_to_validated_local_plan() -> None:
    class InvalidLLM:
        async def complete(self, _request):
            class Response:
                content = '{"hypothesis":"待定"}'

            return Response()

    async def scenario() -> None:
        plan = await generate_experiment_plan(sleep_request(), llm_client=InvalidLLM())
        assert plan["generator"] == "deterministic-domain-template-v2"
        assert experiment_plan_issues(plan) == []

    asyncio.run(scenario())
