from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

from research_mesh.sample_data import sample_request


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
SERVICES = {
    "literature": 8011,
    "experiment": 8012,
    "analysis": 8013,
    "review": 8014,
}


def wait_ready(url: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=1)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"service did not become ready: {url}")


def main() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["PYTHONUTF8"] = "1"
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    processes: list[subprocess.Popen] = []
    try:
        for slug, port in SERVICES.items():
            partner_env = {**env, "RESEARCH_MESH_PARTNER": slug}
            processes.append(
                subprocess.Popen(
                    [
                        str(PYTHON),
                        "-m",
                        "uvicorn",
                        "research_mesh.partner_service:app",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(port),
                        "--log-level",
                        "warning",
                    ],
                    cwd=ROOT,
                    env=partner_env,
                    creationflags=creation_flags,
                )
            )
        llm_env = {**env, "RESEARCH_MESH_LLM_PROVIDER": "disabled"}
        processes.append(
            subprocess.Popen(
                [
                    str(PYTHON),
                    "-m",
                    "uvicorn",
                    "research_mesh.llm_service:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8020",
                    "--log-level",
                    "warning",
                ],
                cwd=ROOT,
                env=llm_env,
                creationflags=creation_flags,
            )
        )
        processes.append(
            subprocess.Popen(
                [
                    str(PYTHON),
                    "-m",
                    "uvicorn",
                    "research_mesh.api:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8000",
                    "--log-level",
                    "warning",
                ],
                cwd=ROOT,
                env=env,
                creationflags=creation_flags,
            )
        )

        for slug, port in SERVICES.items():
            wait_ready(f"http://127.0.0.1:{port}/health")
            acs = httpx.get(f"http://127.0.0.1:{port}/acs", timeout=3).json()
            if acs["name"] == "":
                raise RuntimeError(f"{slug} returned an empty ACS")
        wait_ready("http://127.0.0.1:8000/health")
        wait_ready("http://127.0.0.1:8020/health")
        frontend = httpx.get("http://127.0.0.1:8000/", timeout=3)
        assert frontend.status_code == 200
        assert 'id="research-form"' in frontend.text
        llm_health = httpx.get("http://127.0.0.1:8020/health", timeout=3).json()
        assert llm_health["status"] == "disabled"

        response = httpx.post(
            "http://127.0.0.1:8000/research/run",
            json=sample_request().model_dump(mode="json"),
            timeout=30,
        )
        response.raise_for_status()
        report = response.json()
        assert report["status"] == "completed"
        assert len(report["provenance"]) == 4
        print(
            "independent-process smoke: UI + 4 AIP calls + LLM gateway completed"
        )
    finally:
        for process in reversed(processes):
            process.terminate()
        for process in reversed(processes):
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    main()
