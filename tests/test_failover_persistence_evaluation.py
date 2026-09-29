from __future__ import annotations

import asyncio
import httpx

from research_mesh.evaluation import summarize_benchmark
from research_mesh.leader import ResearchLeader
from research_mesh.partner_service import create_partner_app
from research_mesh.persistence import RunStore
from research_mesh.registry import LocalCapabilityRegistry
from research_mesh.schemas import AgentDescriptor
from research_mesh.config import RuntimeSettings
from research_mesh.sample_data import sample_request


def test_leader_fails_over_to_second_compatible_candidate() -> None:
    first = AgentDescriptor(
        slug="literature",
        aic="local.failed.literature",
        name="Failed Literature",
        endpoint="http://failed.test/rpc",
        skills=["literature-search"],
        priority=20,
    )
    second = AgentDescriptor(
        slug="literature",
        aic="local.research-mesh.literature",
        name="Healthy Literature",
        endpoint="http://healthy.test/rpc",
        skills=["literature-search"],
        priority=10,
    )
    registry = LocalCapabilityRegistry([first, second])

    async def processor(_payload: dict) -> dict:
        return {"evidence": [{"title": "Verified fixture"}], "count": 1}

    healthy_app = create_partner_app("literature", processor=processor)

    async def failed_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "temporary failure"})

    def transport(agent: AgentDescriptor):
        if agent.aic == first.aic:
            return httpx.MockTransport(failed_handler)
        return httpx.ASGITransport(app=healthy_app)

    async def scenario() -> None:
        leader = ResearchLeader(
            registry,
            leader_aic="local.test.leader",
            transport_factory=transport,
            settings=RuntimeSettings.from_env(),
        )
        result, trace = await leader._run_agent(
            session_id="failover-session",
            step="collect-evidence",
            skill="literature-search",
            query="sleep",
            payload={"request": sample_request().model_dump(mode="json")},
        )
        assert result["count"] == 1
        assert trace.attempt == 2
        assert trace.failover is True
        assert trace.candidate_count == 2
        assert trace.prior_failures

    asyncio.run(scenario())


def test_run_store_persists_attempts_events_and_metrics(tmp_path) -> None:
    store = RunStore(tmp_path / "state.sqlite3")
    store.begin_run("run-1", {"question": "test"})
    store.record_attempt(
        session_id="run-1",
        step="collect",
        skill="literature-search",
        agent_aic="local.test",
        endpoint="https://example.test/rpc",
        attempt=1,
        status="completed",
        duration_ms=12.5,
    )
    store.event("test-event", {"safe": True}, session_id="run-1")
    store.finish_run("run-1", status="completed", report={"status": "completed"})

    restored = RunStore(tmp_path / "state.sqlite3").get_run("run-1")
    assert restored is not None
    assert restored["status"] == "completed"
    assert restored["attempts"][0]["duration_ms"] == 12.5
    metrics = store.metrics()
    assert metrics["run_count"] == 1
    assert metrics["completion_rate"] == 1.0


def test_benchmark_summary_reports_latency_quality_and_failures() -> None:
    summary = summarize_benchmark(
        [
            {
                "status": "completed",
                "duration_ms": 100,
                "report": {
                    "provenance": [{"step": "collect", "duration_ms": 20}],
                    "analysis": {
                        "doi_coverage": 1.0,
                        "abstract_coverage": 0.5,
                        "evidence_quality": {"mean_directness_score": 0.8},
                    },
                },
            },
            {
                "status": "failed",
                "duration_ms": 50,
                "error_type": "ConnectError",
            },
        ]
    )
    assert summary["completion_rate"] == 0.5
    assert summary["latency_ms"]["p50"] == 100
    assert summary["evidence"]["mean_doi_coverage"] == 1.0
    assert summary["failure_types"] == {"ConnectError": 1}
