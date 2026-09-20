from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
PARTNERS = {
    "literature": 8011,
    "experiment": 8012,
    "analysis": 8013,
    "review": 8014,
}


def uvicorn_command(module: str, port: int) -> list[str]:
    return [
        str(PYTHON),
        "-m",
        "uvicorn",
        module,
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]


def main() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["PYTHONUTF8"] = "1"
    # This launcher is deliberately an isolated HTTP development profile even
    # when the user's .env contains production platform settings.
    env["RESEARCH_MESH_MODE"] = "local"
    env["RESEARCH_MESH_IDENTITY_BINDING"] = "false"
    env["RESEARCH_MESH_MTLS_ENABLED"] = "false"
    env["RESEARCH_MESH_DISCOVERY_URL"] = ""
    env["RESEARCH_MESH_LEADER_AIC"] = "local.research-mesh.leader"
    env["RESEARCH_MESH_LEADER_URL"] = "http://127.0.0.1:8000/rpc"
    for slug, port in PARTNERS.items():
        env[f"RESEARCH_MESH_{slug.upper()}_AIC"] = f"local.research-mesh.{slug}"
        env[f"RESEARCH_MESH_{slug.upper()}_URL"] = f"http://127.0.0.1:{port}/rpc"
    processes: list[subprocess.Popen] = []
    try:
        for slug, port in PARTNERS.items():
            processes.append(
                subprocess.Popen(
                    uvicorn_command("research_mesh.partner_service:app", port),
                    cwd=ROOT,
                    env={**env, "RESEARCH_MESH_PARTNER": slug},
                )
            )
        processes.append(
            subprocess.Popen(
                uvicorn_command("research_mesh.llm_service:app", 8020),
                cwd=ROOT,
                env=env,
            )
        )
        processes.append(
            subprocess.Popen(
                uvicorn_command("research_mesh.api:app", 8000),
                cwd=ROOT,
                env=env,
            )
        )
        print("Research Mesh is starting at http://127.0.0.1:8000/")
        print("Press Ctrl+C to stop all six services.")
        while all(process.poll() is None for process in processes):
            time.sleep(0.5)
        failed = next(process for process in processes if process.poll() is not None)
        raise RuntimeError(f"a service exited unexpectedly with code {failed.returncode}")
    except KeyboardInterrupt:
        print("Stopping Research Mesh...")
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
        for process in reversed(processes):
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    main()
