from __future__ import annotations

from .schemas import DatasetInput, ResearchRequest, SourceDocument


def sample_request() -> ResearchRequest:
    return ResearchRequest(
        question="学习时间与小规模测验成绩之间是否存在值得进一步研究的关系？",
        objective="形成一个可复核的初步实验方案，并对示例数据完成描述性统计。",
        documents=[
            SourceDocument(
                title="示例研究记录：学习行为与形成性评价",
                authors=["示例数据团队"],
                year=2026,
                summary="这是一条随演示数据提供的本地研究记录，用于验证证据溯源流程。",
                identifier="demo-record-001",
            ),
            SourceDocument(
                title="示例方法说明：小样本描述性统计",
                authors=["示例方法团队"],
                year=2026,
                summary="说明小样本只能用于流程验证，不能据此形成一般化因果结论。",
                identifier="demo-method-001",
            ),
        ],
        dataset=DatasetInput(
            measure="测验成绩",
            values=[72, 75, 78, 81, 83, 88, 91],
            unit="分",
        ),
        constraints=["仅进行描述性统计", "不得声称因果关系", "所有结论保留证据来源"],
    )
