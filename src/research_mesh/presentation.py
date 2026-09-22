from __future__ import annotations

from typing import Any

from .schemas import ResearchReport


def _list_items(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(value).strip() for value in values if str(value).strip()]


def _append_bullets(lines: list[str], values: Any) -> None:
    lines.extend(f"- {value}" for value in _list_items(values))


def render_research_report(result: dict[str, Any]) -> str:
    """Render the structured report as a Dingdang-friendly text/plain body."""

    report = ResearchReport.model_validate(result)
    literature = report.literature
    experiment = report.experiment
    analysis = report.analysis
    review = report.review

    lines = [
        "# 一站式科研协作报告",
        "",
        f"**研究问题：** {report.question}",
        f"**研究目标：** {report.objective}",
        f"**执行状态：** {report.status}",
        "",
        "## 协作计划",
    ]
    _append_bullets(lines, report.plan)

    lines.extend(["", "## 文献证据"])
    evidence = literature.get("evidence", [])
    if isinstance(evidence, list) and evidence:
        lines.append(f"共汇总 {len(evidence)} 条证据，以下列出前 5 条：")
        for index, item in enumerate(evidence[:5], start=1):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "未命名文献")
            year = item.get("year")
            identifier = item.get("doi") or item.get("identifier")
            suffix = ""
            if year:
                suffix += f"（{year}）"
            if identifier:
                suffix += f"，标识：{identifier}"
            lines.append(f"{index}. {title}{suffix}")
            summary = str(item.get("summary") or "").strip()
            if summary:
                lines.append(f"   摘要：{summary}")
    else:
        lines.append("未获得可展示的文献证据；请结合检索状态与限制说明复核。")

    lines.extend(["", "## 实验设计"])
    hypothesis = str(experiment.get("hypothesis") or "").strip()
    if hypothesis:
        lines.append(f"**研究假设：** {hypothesis}")
    for heading, key in (
        ("自变量", "independent_variables"),
        ("因变量", "dependent_variables"),
        ("控制条件", "controls"),
        ("执行步骤", "steps"),
    ):
        values = _list_items(experiment.get(key))
        if values:
            lines.extend(["", f"**{heading}：**"])
            _append_bullets(lines, values)

    lines.extend(
        [
            "",
            "## 证据与统计摘要",
            f"- 证据记录数：{analysis.get('record_count', 0)}",
            f"- 外部来源记录数：{analysis.get('external_count', 0)}",
            f"- DOI 覆盖率：{analysis.get('doi_coverage', 0)}",
            f"- 摘要覆盖率：{analysis.get('abstract_coverage', 0)}",
            f"- 开放获取覆盖率：{analysis.get('open_access_coverage', 0)}",
            "",
            "## 方法与规范复核",
            f"**复核结论：** {review.get('decision', '未提供')}（"
            f"{'通过' if review.get('passed') else '需修改'}）",
        ]
    )
    findings = review.get("findings", [])
    if isinstance(findings, list) and findings:
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            severity = str(finding.get("severity") or "info")
            message = str(finding.get("message") or "").strip()
            if message:
                lines.append(f"- [{severity}] {message}")
    else:
        lines.append("- 未发现需要单独列出的复核问题。")

    lines.extend(
        [
            "",
            "## 可追溯信息",
            f"- 会话编号：{report.session_id}",
            f"- 智能体调用次数：{len(report.provenance)}",
            "",
            "> 本报告用于科研辅助，不替代伦理审批、专业统计复核或高风险研究决策。",
        ]
    )
    return "\n".join(lines)
