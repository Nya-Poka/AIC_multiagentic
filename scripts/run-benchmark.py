from __future__ import annotations

import argparse
import csv
import html
import json
import os
import ssl
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from research_mesh.evaluation import summarize_benchmark  # noqa: E402


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict) or not payload.get("case_id"):
            raise ValueError(f"invalid benchmark case at line {line_number}")
        cases.append(payload)
    return cases


def _ssl_context(args: argparse.Namespace) -> ssl.SSLContext | bool:
    if not args.ca_file:
        return True
    context = ssl.create_default_context(cafile=args.ca_file)
    if args.cert_file and args.key_file:
        context.load_cert_chain(args.cert_file, args.key_file)
    return context


def _write_html(path: Path, summary: dict[str, Any], results: list[dict[str, Any]]) -> None:
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('case_id')))}</td>"
        f"<td>{html.escape(str(item.get('status')))}</td>"
        f"<td>{html.escape(str(round(float(item.get('duration_ms', 0)), 2)))}</td>"
        f"<td>{html.escape(str(item.get('error', '')))}</td>"
        "</tr>"
        for item in results
    )
    path.write_text(
        "<!doctype html><meta charset='utf-8'><title>Research Mesh Benchmark</title>"
        "<style>body{font-family:system-ui;margin:2rem;max-width:1100px}"
        "table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:.5rem}"
        "pre{background:#f5f5f5;padding:1rem;overflow:auto}</style>"
        "<h1>Research Mesh Benchmark</h1>"
        f"<pre>{html.escape(json.dumps(summary, ensure_ascii=False, indent=2))}</pre>"
        "<table><thead><tr><th>Case</th><th>Status</th><th>ms</th><th>Error</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run and archive a Research Mesh benchmark")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--cases", type=Path, default=ROOT / "benchmarks" / "cases.jsonl")
    parser.add_argument("--output-root", type=Path, default=ROOT / "artifacts" / "evaluations")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--username", default=os.getenv("RESEARCH_MESH_BENCHMARK_USER"))
    parser.add_argument("--password", default=os.getenv("RESEARCH_MESH_BENCHMARK_PASSWORD"))
    parser.add_argument("--ca-file")
    parser.add_argument("--cert-file")
    parser.add_argument("--key-file")
    args = parser.parse_args()

    if bool(args.cert_file) != bool(args.key_file):
        parser.error("--cert-file and --key-file must be provided together")
    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    sha = _git_sha()
    output = args.output_root / f"{run_stamp}-{sha}"
    output.mkdir(parents=True, exist_ok=False)
    cases = _load_cases(args.cases)
    auth = (args.username, args.password) if args.username and args.password else None
    results: list[dict[str, Any]] = []
    with httpx.Client(
        base_url=args.base_url.rstrip("/"),
        timeout=args.timeout,
        verify=_ssl_context(args),
        auth=auth,
    ) as client:
        for endpoint, filename in (("/health", "health.json"), ("/acs", "leader-acs.json")):
            response = client.get(endpoint)
            response.raise_for_status()
            (output / filename).write_text(
                json.dumps(response.json(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
        for case in cases:
            payload = {key: value for key, value in case.items() if key != "case_id"}
            started = time.perf_counter()
            result: dict[str, Any] = {"case_id": case["case_id"]}
            try:
                response = client.post("/research/run", json=payload)
                response.raise_for_status()
                result.update(status="completed", report=response.json())
            except (httpx.HTTPError, ValueError) as exc:
                result.update(
                    status="failed",
                    error_type=type(exc).__name__,
                    error=str(exc)[:2000],
                )
            result["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
            results.append(result)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": sha,
        "base_url": args.base_url,
        **summarize_benchmark(results),
    }
    (output / "cases.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in cases) + "\n",
        encoding="utf-8",
    )
    (output / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (output / "latency.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["case_id", "status", "duration_ms", "error_type"])
        writer.writeheader()
        for item in results:
            writer.writerow({key: item.get(key) for key in writer.fieldnames})
    _write_html(output / "report.html", summary, results)
    print(output)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
