from __future__ import annotations

import asyncio
import inspect
import json
import ssl
import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx
from acps_sdk.aip.aip_base_model import StructuredDataItem, TaskResult, TaskState
from acps_sdk.aip.aip_rpc_client import AipRpcClient

from .config import RuntimeSettings, runtime_settings
from .observability import AmpRuntime
from .persistence import RunStore
from .registry import CapabilityRegistry
from .routing import CandidateHealthTracker
from .schemas import AgentDescriptor, ResearchReport, ResearchRequest, TraceEvent
from .tls import build_client_ssl_context


class AgentInputRequired(RuntimeError):
    pass


class AgentExecutionError(RuntimeError):
    pass


TransportFactory = Callable[[AgentDescriptor], httpx.AsyncBaseTransport]


class ResearchLeader:
    def __init__(
        self,
        registry: CapabilityRegistry,
        *,
        leader_aic: str | None = None,
        transport_factory: TransportFactory | None = None,
        settings: RuntimeSettings | None = None,
        ssl_context: ssl.SSLContext | None = None,
        amp_runtime: AmpRuntime | None = None,
        run_store: RunStore | None = None,
        health_tracker: CandidateHealthTracker | None = None,
        poll_interval: float = 0.05,
        max_polls: int = 100,
    ):
        self.settings = settings or runtime_settings()
        self.registry = registry
        self.leader_aic = leader_aic or self.settings.leader_aic
        self.transport_factory = transport_factory
        material = self.settings.client_tls_material()
        self.ssl_context = ssl_context or (
            build_client_ssl_context(material)
            if self.settings.mtls_enabled and material is not None
            else None
        )
        self.amp_runtime = amp_runtime or AmpRuntime.disabled()
        self.run_store = run_store or RunStore.disabled()
        self.health_tracker = health_tracker or CandidateHealthTracker(
            failure_threshold=self.settings.routing_failure_threshold,
            cooldown_seconds=self.settings.routing_cooldown_seconds,
        )
        self.poll_interval = poll_interval
        self.max_polls = max_polls

    async def _candidate_agents(self, skill: str, query: str) -> list[AgentDescriptor]:
        discover = getattr(self.registry, "discover", None)
        if callable(discover):
            result = discover(skill, query)
        else:
            result = self.registry.require(skill, query)
        if inspect.isawaitable(result):
            result = await result
        candidates = result if isinstance(result, list) else [result]
        active = [
            candidate
            for candidate in candidates
            if candidate.active
            and any(
                candidate_skill == skill
                or candidate_skill.endswith(f".{skill}")
                for candidate_skill in candidate.skills
            )
        ]
        available = self.health_tracker.available(active)
        limit = max(1, self.settings.routing_max_candidates)
        return available[:limit]

    async def _run_agent(
        self,
        *,
        session_id: str,
        step: str,
        skill: str,
        query: str,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], TraceEvent]:
        candidates = await self._candidate_agents(skill, query)
        if not candidates:
            raise AgentExecutionError(f"no active candidate provides skill: {skill}")
        prior_failures: list[str] = []
        for attempt, agent in enumerate(candidates, start=1):
            try:
                result, trace = await self._run_agent_candidate(
                    session_id=session_id,
                    step=step,
                    skill=skill,
                    payload=payload,
                    agent=agent,
                    attempt=attempt,
                    candidate_count=len(candidates),
                    prior_failures=prior_failures,
                )
            except AgentInputRequired:
                raise
            except (AgentExecutionError, httpx.HTTPError, OSError, ssl.SSLError) as exc:
                self.health_tracker.failure(agent)
                failure = f"{agent.aic}: {type(exc).__name__}: {exc}"
                prior_failures.append(failure)
                self.run_store.record_attempt(
                    session_id=session_id,
                    step=step,
                    skill=skill,
                    agent_aic=agent.aic,
                    endpoint=agent.endpoint,
                    attempt=attempt,
                    status="failed",
                    error=failure,
                )
                self.run_store.event(
                    "partner-attempt-failed",
                    {
                        "step": step,
                        "skill": skill,
                        "agent_aic": agent.aic,
                        "attempt": attempt,
                        "error": failure,
                    },
                    session_id=session_id,
                )
                continue
            self.health_tracker.success(agent)
            return result, trace
        raise AgentExecutionError(
            f"all {len(candidates)} candidates failed for {skill}: "
            + " | ".join(prior_failures)
        )

    async def _run_agent_candidate(
        self,
        *,
        session_id: str,
        step: str,
        skill: str,
        payload: dict[str, Any],
        agent: AgentDescriptor,
        attempt: int,
        candidate_count: int,
        prior_failures: list[str],
    ) -> tuple[dict[str, Any], TraceEvent]:
        task_id = f"task-{agent.slug}-{uuid.uuid4()}"
        transport = self.transport_factory(agent) if self.transport_factory else None
        client = AipRpcClient(
            partner_url=agent.endpoint,
            leader_id=self.leader_aic,
            ssl_context=self.ssl_context,
            transport=transport,
            access_emitter=self.amp_runtime.access,
            callee_aic=agent.aic,
            caller_service="research-mesh-leader",
            callee_service=f"research-mesh-{agent.slug}",
            expected_partner_aic=agent.aic,
            identity_binding_enabled=self.settings.identity_binding_enabled,
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
                attempt=attempt,
                candidate_count=candidate_count,
                failover=attempt > 1,
                prior_failures=list(prior_failures),
            )
            self.run_store.record_attempt(
                session_id=session_id,
                step=step,
                skill=skill,
                agent_aic=agent.aic,
                endpoint=agent.endpoint,
                attempt=attempt,
                status="completed",
                duration_ms=duration_ms,
            )
            self.run_store.event(
                "partner-attempt-completed",
                trace.model_dump(mode="json"),
                session_id=session_id,
            )
            return result, trace
        except (AgentInputRequired, AgentExecutionError):
            raise
        except Exception as exc:
            # The reference SDK normalizes some HTTP/RPC failures to a plain
            # Exception. Convert them at the candidate boundary so the router
            # can safely try another compatible endpoint.
            raise AgentExecutionError(
                f"{agent.slug} transport or protocol failure: {exc}"
            ) from exc
        finally:
            await client.close()

    async def run(self, request: ResearchRequest) -> ResearchReport:
        session_id = f"research-{uuid.uuid4()}"
        self.run_store.begin_run(session_id, request.model_dump(mode="json"))
        self.run_store.event(
            "research-run-started",
            {"has_dataset": request.dataset is not None},
            session_id=session_id,
        )
        base_payload = {"request": request.model_dump(mode="json")}
        try:
            parallel_steps = (
                ("collect-evidence", "literature-search"),
                ("design-experiment", "experiment-design"),
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
            }
            dependent_steps: list[tuple[str, Any]] = [
                ("analysis", self._run_agent(
                    session_id=session_id,
                    step="analyze-evidence",
                    skill="data-analysis",
                    query=f"分析文献证据覆盖范围 {request.question}",
                    payload={**base_payload, "artifacts": artifacts},
                )),
            ]
            if self.settings.evidence_synthesis_enabled:
                dependent_steps.append(
                    ("synthesis", self._run_agent(
                        session_id=session_id,
                        step="synthesize-evidence",
                        skill="evidence-synthesis",
                        query=f"综合证据并表达不确定性 {request.question}",
                        payload={**base_payload, "artifacts": artifacts},
                    ))
                )
            if request.dataset is not None:
                if not self.settings.dataset_analysis_enabled:
                    raise AgentExecutionError(
                        "dataset analysis is disabled until the dataset Partner is registered and trusted"
                    )
                dependent_steps.append(
                    ("dataset_analysis", self._run_agent(
                        session_id=session_id,
                        step="analyze-dataset",
                        skill="dataset-analysis",
                        query=f"分析研究数据 {request.question}",
                        payload=base_payload,
                    ))
                )
            dependent_values = await asyncio.gather(
                *(call for _name, call in dependent_steps)
            )
            dependent_results = dict(
                zip((name for name, _call in dependent_steps), dependent_values, strict=True)
            )
            for name, (result, _trace) in dependent_results.items():
                artifacts[name] = result
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
            traces = (
                [item[1] for item in first_results]
                + [item[1] for item in dependent_results.values()]
                + [review_trace]
            )
            plan = [
                "并行检索多个公开学术数据源并聚合证据",
                "生成结构化实验方案",
                "分析证据来源、摘要、DOI 与开放获取覆盖率",
            ]
            if self.settings.evidence_synthesis_enabled:
                plan.append("建立证据矩阵并表达不确定性")
            if request.dataset is not None:
                plan.append("校验数据集哈希并执行确定性统计分析")
            plan.append("独立复核引用、实验控制、数据完整性和证据限制")
            report = ResearchReport(
                session_id=session_id,
                status="completed",
                question=request.question,
                objective=request.objective,
                plan=plan,
                literature=artifacts["literature"],
                experiment=artifacts["experiment"],
                analysis=artifacts["analysis"],
                dataset_analysis=artifacts.get("dataset_analysis", {}),
                synthesis=artifacts.get(
                    "synthesis",
                    {
                        "status": "disabled",
                        "limitations": [
                            "Evidence synthesis remains disabled until its trusted Partner is registered."
                        ],
                    },
                ),
                review=review,
                provenance=traces,
            )
            report_json = report.model_dump(mode="json")
            self.run_store.finish_run(
                session_id, status="completed", report=report_json
            )
            self.run_store.event(
                "research-run-completed",
                {"partner_calls": len(traces)},
                session_id=session_id,
            )
            return report
        except Exception as exc:
            self.run_store.finish_run(
                session_id,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
            )
            self.run_store.event(
                "research-run-failed",
                {"error_type": type(exc).__name__, "error": str(exc)},
                session_id=session_id,
            )
            raise


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
