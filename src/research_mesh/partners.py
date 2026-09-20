from __future__ import annotations

import inspect
import json
import math
import statistics
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from acps_sdk.aip.aip_base_model import (
    Product,
    StructuredDataItem,
    TaskCommand,
    TaskResult,
    TaskState,
    TextDataItem,
)
from acps_sdk.aip.aip_rpc_server import CommandHandlers, DefaultHandlers, TaskManager

from .literature import search_literature
from .schemas import ResearchRequest


class PartnerInputError(ValueError):
    """The partner needs additional caller input before it can work."""


Processor = Callable[
    [dict[str, Any]], dict[str, Any] | Awaitable[dict[str, Any]]
]


@dataclass(frozen=True)
class PartnerSpec:
    slug: str
    aic: str
    name: str
    skill: str
    processor: Processor


def _read_payload(command: TaskCommand) -> dict[str, Any]:
    for item in command.dataItems or []:
        if isinstance(item, TextDataItem) and item.text.strip():
            try:
                payload = json.loads(item.text)
            except json.JSONDecodeError as exc:
                raise PartnerInputError(f"input is not valid JSON: {exc.msg}") from exc
            if not isinstance(payload, dict):
                raise PartnerInputError("input JSON must be an object")
            return payload
    raise PartnerInputError("a JSON TextDataItem is required")


def _with_sender(task: TaskResult, aic: str) -> TaskResult:
    task.senderId = aic
    return task


def _awaiting_input(command: TaskCommand, spec: PartnerSpec, message: str) -> TaskResult:
    task = TaskManager.create_task(
        command,
        initial_state=TaskState.AwaitingInput,
        data_items=[TextDataItem(text=message)],
    )
    return _with_sender(task, spec.aic)


async def _run_processor(spec: PartnerSpec, payload: dict[str, Any]) -> dict[str, Any]:
    result = spec.processor(payload)
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, dict):
        raise ValueError(f"{spec.slug} processor must return an object")
    return result


async def _execute(command: TaskCommand, spec: PartnerSpec) -> TaskResult:
    try:
        payload = _read_payload(command)
        result = await _run_processor(spec, payload)
    except (PartnerInputError, ValueError) as exc:
        return _awaiting_input(command, spec, str(exc))

    task = TaskManager.create_task(command, initial_state=TaskState.AwaitingCompletion)
    TaskManager.set_products(
        task.taskId,
        [
            Product(
                id=f"product-{spec.slug}-{task.taskId}",
                name=f"{spec.slug}-result",
                description=f"Structured result produced by {spec.name}",
                dataItems=[
                    StructuredDataItem(
                        data={
                            "agent": spec.slug,
                            "skill": spec.skill,
                            "result": result,
                        }
                    )
                ],
            )
        ],
    )
    current = TaskManager.get_task(task.taskId) or task
    return _with_sender(current, spec.aic)


def make_handlers(spec: PartnerSpec) -> CommandHandlers:
    async def on_start(command: TaskCommand, task: TaskResult | None) -> TaskResult:
        if task is not None:
            return _with_sender(task, spec.aic)
        return await _execute(command, spec)

    async def on_continue(command: TaskCommand, task: TaskResult) -> TaskResult:
        TaskManager.add_command_to_history(task.taskId, command)
        if task.status.state not in (
            TaskState.AwaitingInput,
            TaskState.AwaitingCompletion,
        ):
            return _with_sender(task, spec.aic)
        try:
            payload = _read_payload(command)
            result = await _run_processor(spec, payload)
        except (PartnerInputError, ValueError) as exc:
            updated = TaskManager.update_task_status(
                task.taskId,
                TaskState.AwaitingInput,
                [TextDataItem(text=str(exc))],
            )
            return _with_sender(updated, spec.aic)
        TaskManager.set_products(
            task.taskId,
            [
                Product(
                    id=f"product-{spec.slug}-{task.taskId}",
                    name=f"{spec.slug}-result",
                    dataItems=[
                        StructuredDataItem(
                            data={"agent": spec.slug, "skill": spec.skill, "result": result}
                        )
                    ],
                )
            ],
        )
        updated = TaskManager.update_task_status(task.taskId, TaskState.AwaitingCompletion)
        return _with_sender(updated, spec.aic)

    async def on_get(command: TaskCommand, task: TaskResult) -> TaskResult:
        return _with_sender(await DefaultHandlers.get(command, task), spec.aic)

    async def on_cancel(command: TaskCommand, task: TaskResult) -> TaskResult:
        return _with_sender(await DefaultHandlers.cancel(command, task), spec.aic)

    async def on_complete(command: TaskCommand, task: TaskResult) -> TaskResult:
        return _with_sender(await DefaultHandlers.complete(command, task), spec.aic)

    return CommandHandlers(
        on_start=on_start,
        on_get=on_get,
        on_cancel=on_cancel,
        on_complete=on_complete,
        on_continue=on_continue,
    )


def _request(payload: dict[str, Any]) -> ResearchRequest:
    raw = payload.get("request")
    if not isinstance(raw, dict):
        raise PartnerInputError("payload.request is required")
    return ResearchRequest.model_validate(raw)


async def literature_processor(payload: dict[str, Any]) -> dict[str, Any]:
    request = _request(payload)
    return await search_literature(request)


def experiment_processor(payload: dict[str, Any]) -> dict[str, Any]:
    request = _request(payload)
    measure = request.dataset.measure if request.dataset else "目标研究指标"
    return {
        "hypothesis": f"围绕“{request.question}”，{measure}会随研究条件产生可测变化。",
        "independent_variables": ["研究条件（需在正式试验前具体化）"],
        "dependent_variables": [measure],
        "controls": ["统一数据采集流程", "统一纳入与排除标准", "固定分析版本与随机种子"],
        "steps": [
            "登记研究假设和分析计划",
            "按统一标准采集或导入数据",
            "执行描述性统计和质量检查",
            "按预注册方案完成比较或建模",
            "由独立复核智能体检查证据和方法",
        ],
        "constraints": request.constraints,
        "requires_human_approval": True,
    }


def analysis_processor(payload: dict[str, Any]) -> dict[str, Any]:
    request = _request(payload)
    if request.dataset is None:
        raise PartnerInputError("数据分析需要 payload.request.dataset")
    values = request.dataset.values
    stdev = statistics.stdev(values) if len(values) > 1 else 0.0
    return {
        "measure": request.dataset.measure,
        "unit": request.dataset.unit,
        "n": len(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "minimum": min(values),
        "maximum": max(values),
        "sample_standard_deviation": stdev,
        "all_values_finite": all(math.isfinite(value) for value in values),
        "reproducibility": {
            "engine": "python-statistics",
            "network_access": False,
            "input_values_recorded": True,
        },
    }


def review_processor(payload: dict[str, Any]) -> dict[str, Any]:
    request = _request(payload)
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, dict):
        raise PartnerInputError("payload.artifacts is required for review")

    findings: list[dict[str, str]] = []
    literature = artifacts.get("literature", {})
    experiment = artifacts.get("experiment", {})
    analysis = artifacts.get("analysis", {})

    if not literature.get("evidence"):
        findings.append({"severity": "error", "message": "缺少可追溯文献证据"})
    if not experiment.get("controls"):
        findings.append({"severity": "error", "message": "实验方案缺少控制条件"})
    if analysis.get("n", 0) < 3:
        findings.append({"severity": "warning", "message": "样本量过小，不能外推结论"})
    if not request.constraints:
        findings.append({"severity": "warning", "message": "尚未登记研究约束"})

    has_errors = any(item["severity"] == "error" for item in findings)
    return {
        "passed": not has_errors,
        "findings": findings,
        "checks": [
            "citation-presence",
            "experimental-controls",
            "minimum-sample-warning",
            "constraint-registration",
        ],
        "decision": "revise" if has_errors else "accept-with-limitations",
    }


PARTNER_SPECS: tuple[PartnerSpec, ...] = (
    PartnerSpec(
        "literature",
        "local.research-mesh.literature",
        "文献证据智能体",
        "literature-search",
        literature_processor,
    ),
    PartnerSpec(
        "experiment",
        "local.research-mesh.experiment",
        "实验设计智能体",
        "experiment-design",
        experiment_processor,
    ),
    PartnerSpec(
        "analysis",
        "local.research-mesh.analysis",
        "数据分析智能体",
        "data-analysis",
        analysis_processor,
    ),
    PartnerSpec(
        "review",
        "local.research-mesh.review",
        "规范复核智能体",
        "method-review",
        review_processor,
    ),
)
