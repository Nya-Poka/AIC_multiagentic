from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

from acps_sdk.aip.aip_base_model import (
    StructuredDataItem,
    TaskCommand,
    TaskCommandType,
)

from research_mesh.input_normalization import (
    ResearchInputNormalizer,
    clean_research_text,
    extract_json_object,
)
from research_mesh.partners import PartnerSpec, _read_payload


QUESTION = "睡眠时长是否会影响大学生的学习表现和注意力水平"
OBJECTIVE = "完成相关文献检索并设计一项可复现的观察性研究方案"


def test_extracts_embedded_json_from_dingdang_style_context() -> None:
    text = (
        "叮当请求补充：请提供有效 JSON。\n"
        "用户回答："
        + json.dumps(
            {"question": QUESTION, "objective": OBJECTIVE},
            ensure_ascii=False,
        )
        + "\n请继续执行。"
    )

    assert extract_json_object(text) == {
        "question": QUESTION,
        "objective": OBJECTIVE,
    }


def test_normalizes_loose_json_and_chinese_field_names() -> None:
    async def scenario() -> None:
        normalizer = ResearchInputNormalizer()
        result = await normalizer.normalize(
            [],
            [
                "```json\n"
                "{研究问题: '睡眠时长是否影响大学生学习表现', "
                "研究目标: '形成文献综述和可复现实验设计', "
                "约束: ['仅使用公开文献'],}\n"
                "```"
            ],
        )
        request = result["request"]
        assert request["question"] == "睡眠时长是否影响大学生学习表现"
        assert request["objective"] == "形成文献综述和可复现实验设计"
        assert request["constraints"] == ["仅使用公开文献"]

    asyncio.run(scenario())


def test_natural_language_has_deterministic_fallback() -> None:
    async def scenario() -> None:
        normalizer = ResearchInputNormalizer(use_llm=False)
        result = await normalizer.normalize([], [QUESTION])
        request = result["request"]
        assert request["question"] == QUESTION
        assert request["literature_query"] is None
        assert "文献检索" in request["objective"]

    asyncio.run(scenario())


def test_dingdang_routing_metadata_is_removed_before_fallback() -> None:
    text = (
        "调用智能体「基于多智能体协作的一站式科研助理平台」，"
        "研究睡眠时长是否影响大学生学习表现，并设计一项可复现实验。 "
        "DAG 指定的上游产物摘要：叮当已完成协作规划并开始按 DAG 派发。"
    )

    assert clean_research_text(text) == (
        "研究睡眠时长是否影响大学生学习表现，并设计一项可复现实验"
    )

    async def scenario() -> None:
        result = await ResearchInputNormalizer(use_llm=False).normalize([], [text])
        request = result["request"]
        assert request["question"] == (
            "研究睡眠时长是否影响大学生学习表现，并设计一项可复现实验"
        )
        assert request["literature_query"] is None
        assert "调用智能体" not in request["objective"]
        assert "DAG" not in request["objective"]

    asyncio.run(scenario())


def test_llm_can_structure_natural_language() -> None:
    class FakeLLM:
        async def complete(self, _request):
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "question": QUESTION,
                        "objective": OBJECTIVE,
                        "constraints": ["仅使用公开信息"],
                    },
                    ensure_ascii=False,
                )
            )

    async def scenario() -> None:
        normalizer = ResearchInputNormalizer(llm_client=FakeLLM(), use_llm=True)
        result = await normalizer.normalize([], ["请帮我研究睡眠和学习表现的关系"])
        assert result["request"]["question"] == QUESTION
        assert result["request"]["objective"] == OBJECTIVE
        assert result["request"]["constraints"] == ["仅使用公开信息"]

    asyncio.run(scenario())


def test_generic_aip_reader_accepts_structured_data_item() -> None:
    async def processor(payload: dict) -> dict:
        return payload

    async def scenario() -> None:
        command = TaskCommand(
            id="command-1",
            sentAt=datetime.now(timezone.utc).isoformat(),
            senderRole="leader",
            senderId="local.test.leader",
            command=TaskCommandType.Start,
            taskId="task-1",
            sessionId="session-1",
            dataItems=[StructuredDataItem(data={"answer": 42})],
        )
        spec = PartnerSpec(
            slug="test",
            aic="local.test.partner",
            name="Test Partner",
            skill="test",
            processor=processor,
        )
        assert await _read_payload(command, spec) == {"answer": 42}

    asyncio.run(scenario())
