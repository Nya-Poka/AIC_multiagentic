from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx
from acps_sdk.aip.aip_base_model import StructuredDataItem, TaskResult, TaskState
from acps_sdk.aip.aip_rpc_client import AipRpcClient

from .registry import LocalCapabilityRegistry
from .schemas import AgentDescriptor, ResearchReport, ResearchRequest, TraceEvent


class AgentInputRequired(RuntimeError):
    pass


class AgentExecutionError(RuntimeError):
    pass


TransportFactory = Callable[[AgentDescriptor], httpx.AsyncBaseTransport]


class ResearchLeader:
    def __init__(
        self,
        registry: LocalCapabilityRegistry,
        *,
        leader_aic: str = "local.research-mesh.leader",
        transport_factory: TransportFactory | None = None,
        poll_interval: float = 0.05,
        max_polls: int = 100,
    ):
        self.registry = registry
        self.leader_aic = leader_aic
        self.transport_factory = transport_factory
        self.poll_interval = poll_interval
        self.max_polls = max_polls

    async def _run_agent(
        self,
        *,
        session_id: str,
        step: str,
        skill: str,
        query: str,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], TraceEvent]:
        agent = self.registry.require(skill, query)
        task_id = f"task-{agent.slug}-{uuid.uuid4()}"
        transport = self.transport_factory(agent) if self.transport_factory else None
        client = AipRpcClient(
            partner_url=agent.endpoint,
            leader_id=self.leader_aic,
            transport=transport,
            identity_binding_enabled=False,
        )
        started = time.perf_counter()
        try:
            task = await client.start_task(
                session_id=session_id,
                task_id=task_id,
                user_input=json.dumps(payload, ensure_ascii=False),
            )
            _validate_task_identity(task, agent, task_id, session_id)
            polls = 0
            while task.status.state in (TaskState.Accepted, TaskState.Working):
                if polls >= self.max_polls:
                    await client.cancel_task(task_id=task_id, session_id=session_id)
                    raise AgentExecutionError(f"{agent.slug} timed out")
                await asyncio.sleep(self.poll_interval)
                task = await client.get_task(task_id=task_id, session_id=session_id)
                _validate_task_identity(task, agent, task_id, session_id)
                polls += 1

            if task.status.state == TaskState.AwaitingInput:
                messages = [
                    item.text
                    for item in task.status.dataItems or []
                    if hasattr(item, "text")
                ]
                raise AgentInputRequired(
                    f"{agent.slug} requires input: {'; '.join(messages) or 'unspecified'}"
                )
            if task.status.state != TaskState.AwaitingCompletion:
                raise AgentExecutionError(
                    f"{agent.slug} ended before completion: {task.status.state.value}"
                )

            result = _extract_structured_result(task, agent)
            completed = await client.complete_task(task_id=task_id, session_id=session_id)
            _validate_task_identity(completed, agent, task_id, session_id)
            if completed.status.state != TaskState.Completed:
                raise AgentExecutionError(
                    f"{agent.slug} did not enter completed state: "
                    f"{completed.status.state.value}"
                )
            duration_ms = round((time.perf_counter() - started) * 1000, 3)
            trace = TraceEvent(
                step=step,
                agent_slug=agent.slug,
                agent_aic=agent.aic,
                skill=skill,
                endpoint=agent.endpoint,
                task_id=task_id,
                final_state=completed.status.state.value,
                duration_ms=duration_ms,
            )
            return result, trace
        finally:
            await client.close()

    async def run(self, request: ResearchRequest) -> ResearchReport:
        session_id = f"research-{uuid.uuid4()}"
        base_payload = {"request": request.model_dump(mode="json")}
        parallel_steps = (
            ("collect-evidence", "literature-search"),
            ("design-experiment", "experiment-design"),
            ("analyze-data", "data-analysis"),
        )
        first_results = await asyncio.gather(
            *[
                self._run_agent(
                    session_id=session_id,
                    step=step,
                    skill=skill,
                    query=f"{request.question} {request.objective}",
                    payload=base_payload,
                )
                for step, skill in parallel_steps
            ]
        )
        artifacts = {
            "literature": first_results[0][0],
            "experiment": first_results[1][0],
            "analysis": first_results[2][0],
        }
        review, review_trace = await self._run_agent(
            session_id=session_id,
            step="review-method-and-evidence",
            skill="method-review",
            query=f"复核 {request.question}",
            payload={**base_payload, "artifacts": artifacts},
        )
        if review.get("passed") is not True:
            raise AgentExecutionError(
                f"method-review rejected the research package: {review.get('findings', [])}"
            )
        traces = [item[1] for item in first_results] + [review_trace]
        return ResearchReport(
            session_id=session_id,
            status="completed",
            question=request.question,
            objective=request.objective,
            plan=[
                "并行检索用户提供的文献证据",
                "生成结构化实验方案",
                "执行可复现的描述性统计",
                "独立复核引用、实验控制和样本限制",
            ],
            literature=artifacts["literature"],
            experiment=artifacts["experiment"],
            analysis=artifacts["analysis"],
            review=review,
            provenance=traces,
        )


def _extract_structured_result(
    task: TaskResult, agent: AgentDescriptor
) -> dict[str, Any]:
    for product in task.products or []:
        for item in product.dataItems:
            if isinstance(item, StructuredDataItem):
                envelope = item.data
                result = envelope.get("result")
                if isinstance(result, dict):
                    return result
    raise AgentExecutionError(f"{agent.slug} returned no structured result")


def _validate_task_identity(
    task: TaskResult,
    agent: AgentDescriptor,
    expected_task_id: str,
    expected_session_id: str,
) -> None:
    """Fail closed when a shared store or wrong endpoint returns another task."""

    mismatches: list[str] = []
    if task.taskId != expected_task_id:
        mismatches.append(f"taskId={task.taskId!r}")
    if task.sessionId != expected_session_id:
        mismatches.append(f"sessionId={task.sessionId!r}")
    if task.senderId != agent.aic:
        mismatches.append(f"senderId={task.senderId!r}")
    if mismatches:
        raise AgentExecutionError(
            f"{agent.slug} returned mismatched task identity: {', '.join(mismatches)}"
        )
