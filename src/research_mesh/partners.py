from __future__ import annotations

import inspect
import json
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

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


class InputNormalizer(Protocol):
    async def normalize(
        self,
        structured_inputs: list[dict[str, Any]],
        text_inputs: list[str],
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class PartnerSpec:
    slug: str
    aic: str
    name: str
    skill: str
    processor: Processor
    input_normalizer: InputNormalizer | None = None


async def _read_payload(command: TaskCommand, spec: PartnerSpec) -> dict[str, Any]:
    structured_inputs: list[dict[str, Any]] = []
    text_inputs: list[str] = []
    for item in command.dataItems or []:
        if isinstance(item, StructuredDataItem):
            structured_inputs.append(item.data)
        elif isinstance(item, TextDataItem) and item.text.strip():
            text_inputs.append(item.text)

    if spec.input_normalizer is not None:
        return await spec.input_normalizer.normalize(structured_inputs, text_inputs)

    if structured_inputs:
        return structured_inputs[-1]
    last_error: json.JSONDecodeError | None = None
    for text in reversed(text_inputs):
        if text.strip():
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                last_error = exc
                continue
            if not isinstance(payload, dict):
                continue
            return payload
    if last_error is not None:
        raise PartnerInputError(f"input is not valid JSON: {last_error.msg}") from last_error
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
        payload = await _read_payload(command, spec)
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
            payload = await _read_payload(command, spec)
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
    return {
        "hypothesis": f"围绕“{request.question}”，研究条件与目标结果之间存在可检验的关联。",
        "independent_variables": ["研究条件（需在正式试验前具体化）"],
        "dependent_variables": ["与研究目标一致的可观察结果指标"],
        "controls": ["统一数据采集流程", "统一纳入与排除标准", "固定分析版本与随机种子"],
        "steps": [
            "登记研究假设和分析计划",
            "按统一标准采集或导入数据",
            "执行证据覆盖分析和质量检查",
            "按预注册方案完成比较或建模",
            "由独立复核智能体检查证据和方法",
        ],
        "constraints": request.constraints,
        "requires_human_approval": True,
    }


def analysis_processor(payload: dict[str, Any]) -> dict[str, Any]:
    _request(payload)
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, dict):
        raise PartnerInputError("证据分析需要 payload.artifacts")
    literature = artifacts.get("literature")
    if not isinstance(literature, dict):
        raise PartnerInputError("证据分析需要 payload.artifacts.literature")
    evidence = literature.get("evidence")
    if not isinstance(evidence, list):
        raise PartnerInputError("文献智能体没有返回 evidence 列表")

    external = [item for item in evidence if item.get("verification") != "user-provided"]
    years = [item["year"] for item in evidence if isinstance(item.get("year"), int)]
    source_counter: Counter[str] = Counter()
    for item in evidence:
        providers = item.get("providers")
        if isinstance(providers, list):
            source_counter.update(str(provider) for provider in providers)
        elif item.get("provider"):
            source_counter[str(item["provider"])] += 1

    count = len(evidence)
    with_doi = sum(bool(item.get("doi")) for item in evidence)
    with_abstract = sum(bool(item.get("has_abstract")) for item in evidence)
    open_access = sum(bool(item.get("open_access_url")) for item in evidence)
    return {
        "record_count": count,
        "external_count": len(external),
        "provider_count": len(source_counter),
        "source_distribution": dict(sorted(source_counter.items())),
        "with_doi": with_doi,
        "with_abstract": with_abstract,
        "open_access_count": open_access,
        "doi_coverage": round(with_doi / count, 4) if count else 0.0,
        "abstract_coverage": round(with_abstract / count, 4) if count else 0.0,
        "open_access_coverage": round(open_access / count, 4) if count else 0.0,
        "year_min": min(years) if years else None,
        "year_max": max(years) if years else None,
        "reproducibility": {
            "engine": "research-mesh-evidence-audit-v1",
            "network_access": False,
            "input_records_recorded": True,
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
    if analysis.get("external_count", 0) < 3:
        findings.append({"severity": "warning", "message": "外部文献证据少于 3 条，结论覆盖有限"})
    if analysis.get("doi_coverage", 0) < 0.5:
        findings.append({"severity": "warning", "message": "不足一半的证据具有 DOI 或稳定标识"})
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
        "证据分析智能体",
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
