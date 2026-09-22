from __future__ import annotations

from research_mesh.presentation import render_research_report


def test_render_research_report_produces_readable_text_body() -> None:
    text = render_research_report(
        {
            "session_id": "research-test",
            "status": "completed",
            "question": "睡眠时长是否影响大学生的学习表现和注意力水平？",
            "objective": "形成可复现的观察性研究方案并汇总可追溯证据。",
            "plan": ["检索公开证据", "设计实验并完成方法复核"],
            "literature": {
                "evidence": [
                    {
                        "title": "Sleep and academic performance",
                        "year": 2025,
                        "doi": "10.0000/example",
                        "summary": "A traceable test summary for the report renderer.",
                    }
                ]
            },
            "experiment": {
                "hypothesis": "睡眠时长与学习表现存在可检验关联。",
                "independent_variables": ["平均睡眠时长"],
                "dependent_variables": ["课程成绩", "注意力测验得分"],
                "controls": ["年级", "专业"],
                "steps": ["预注册", "采集数据", "执行分析"],
            },
            "analysis": {
                "record_count": 1,
                "external_count": 1,
                "doi_coverage": 1.0,
                "abstract_coverage": 1.0,
                "open_access_coverage": 0.0,
            },
            "review": {
                "passed": True,
                "decision": "accept-with-limitations",
                "findings": [{"severity": "warning", "message": "需要伦理审批"}],
            },
            "provenance": [
                {
                    "step": "collect-evidence",
                    "agent_slug": "literature",
                    "agent_aic": "local.literature",
                    "skill": "literature-search",
                    "endpoint": "http://literature.test/rpc",
                    "task_id": "task-test",
                    "final_state": "completed",
                    "duration_ms": 12.5,
                }
            ],
        }
    )

    assert "# 一站式科研协作报告" in text
    assert "睡眠时长是否影响大学生" in text
    assert "Sleep and academic performance（2025），标识：10.0000/example" in text
    assert "**研究假设：** 睡眠时长与学习表现存在可检验关联。" in text
    assert "[warning] 需要伦理审批" in text
    assert "会话编号：research-test" in text
