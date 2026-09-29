from __future__ import annotations

import asyncio
from io import BytesIO

import httpx
from openpyxl import Workbook

from research_mesh.api import asgi_transport_factory, create_app
from research_mesh.artifacts import LocalArtifactStore
from research_mesh.cluster import create_partner_apps
from research_mesh.dataset_analysis import analyze_dataset
from research_mesh.registry import default_registry
from research_mesh.sample_data import sample_request
from research_mesh.schemas import DatasetAnalysisSpec


def test_dataset_artifact_is_hash_verified_and_analyzed(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path / "uploads")
    reference = store.save_dataset(
        (
            "sleep_hours,exam_score,group\n"
            "6,70,A\n7,75,A\n8,84,B\n9,91,B\n,77,A\n"
        ).encode(),
        filename="sleep.csv",
        media_type="text/csv",
    )
    result = analyze_dataset(
        reference,
        DatasetAnalysisSpec(
            outcome="exam_score",
            exposures=["sleep_hours"],
            covariates=["group"],
        ),
        store=store,
    )

    assert result["row_count"] == 5
    assert result["columns"]["sleep_hours"]["missing"] == 1
    assert result["associations"][0]["result"]["pearson_r"] > 0.9
    assert result["reproducibility"]["input_sha256"] == reference.sha256
    assert result["reproducibility"]["raw_rows_returned"] is False


def test_xlsx_dataset_is_read_and_analyzed(tmp_path) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["sleep_hours", "exam_score"])
    worksheet.append([6, 70])
    worksheet.append([7, 76])
    worksheet.append([8, 83])
    worksheet.append([9, 89])
    buffer = BytesIO()
    workbook.save(buffer)
    workbook.close()

    store = LocalArtifactStore(tmp_path / "uploads")
    reference = store.save_dataset(
        buffer.getvalue(),
        filename="sleep.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    result = analyze_dataset(
        reference,
        DatasetAnalysisSpec(outcome="exam_score", exposures=["sleep_hours"]),
        store=store,
    )

    assert result["row_count"] == 4
    assert result["associations"][0]["result"]["pearson_r"] > 0.99


def test_uploaded_dataset_runs_through_dataset_partner(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("RESEARCH_MESH_ARTIFACT_ROOT", str(tmp_path / "uploads"))

    async def fake_literature(payload: dict) -> dict:
        documents = payload["request"]["documents"]
        evidence = [
            {
                **document,
                "verification": "fixture",
                "providers": ["fixture"],
                "has_abstract": True,
                "source_quality_tier": "B",
            }
            for document in documents
        ]
        return {"evidence": evidence, "count": len(evidence)}

    async def scenario() -> None:
        partner_apps = create_partner_apps({"literature": fake_literature})
        endpoints = {slug: f"http://{slug}.test/rpc" for slug in partner_apps}
        app = create_app(default_registry(endpoints))
        app.state.partner_transport_factory = asgi_transport_factory(partner_apps)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://research-mesh.test",
        ) as client:
            upload = await client.post(
                "/artifacts/datasets",
                content=b"sleep_hours,exam_score\n6,70\n7,76\n8,83\n9,89\n",
                headers={"X-Filename": "sleep.csv", "Content-Type": "text/csv"},
            )
            assert upload.status_code == 200, upload.text
            request = sample_request().model_dump(mode="json")
            request["dataset"] = upload.json()
            request["analysis_spec"] = {
                "outcome": "exam_score",
                "exposures": ["sleep_hours"],
                "covariates": [],
                "design": "observational",
            }
            response = await client.post("/research/run", json=request)
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["dataset_analysis"]["row_count"] == 4
        assert report["review"]["passed"] is True
        assert {item["agent_slug"] for item in report["provenance"]} >= {
            "dataset",
            "synthesis",
        }

    asyncio.run(scenario())
