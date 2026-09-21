from __future__ import annotations

from .schemas import ResearchRequest, SourceDocument


def sample_request() -> ResearchRequest:
    return ResearchRequest(
        question="学习时间与小规模测验成绩之间是否存在值得进一步研究的关系？",
        objective="形成一个可复核的初步实验方案，并评估检索证据的覆盖范围与可追溯性。",
        literature_query="study time academic performance test scores",
        documents=[
            SourceDocument(
                title="示例研究记录：学习行为与形成性评价",
                authors=["示例数据团队"],
                year=2026,
                summary="这是一条随演示数据提供的本地研究记录，用于验证证据溯源流程。",
                identifier="demo-record-001",
            ),
            SourceDocument(
                title="示例方法说明：文献证据覆盖检查",
                authors=["示例方法团队"],
                year=2026,
                summary="说明书目元数据只能用于证据发现，不能替代对原始论文的人工核读。",
                identifier="demo-method-001",
            ),
        ],
        constraints=["不得声称因果关系", "所有结论保留证据来源"],
    )
