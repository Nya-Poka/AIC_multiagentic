from __future__ import annotations

import argparse
import hashlib
import json
import re
import ssl
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[1]
SENSITIVE_ASSIGNMENT = re.compile(
    r"(?im)^([A-Z0-9_]*(?:KEY|TOKEN|PASSWORD|SECRET)[A-Z0-9_]*\s*=).*$"
)
BEARER = re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[A-Za-z0-9._~+\-/=]+")
EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")


def redact(value: str) -> str:
    value = SENSITIVE_ASSIGNMENT.sub(r"\1<redacted>", value)
    value = BEARER.sub(r"\1<redacted>", value)
    value = EMAIL.sub("<redacted-email>", value)
    return PHONE.sub("<redacted-phone>", value)


def run_command(command: list[str], cwd: Path = ROOT) -> str:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"command unavailable: {type(exc).__name__}: {exc}\n"
    return redact(completed.stdout)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ssl_context(args: argparse.Namespace) -> ssl.SSLContext | bool:
    if not args.ca_file:
        return True
    context = ssl.create_default_context(cafile=args.ca_file)
    if args.cert_file and args.key_file:
        context.load_cert_chain(args.cert_file, args.key_file)
    return context


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect a redacted, checksummed competition evidence bundle"
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument(
        "--endpoint",
        action="append",
        default=[],
        metavar="NAME=URL",
        help="Additional agent origin, for example literature=https://host:8011",
    )
    parser.add_argument("--output-root", type=Path, default=ROOT / "artifacts" / "platform-evidence")
    parser.add_argument("--ca-file")
    parser.add_argument("--cert-file")
    parser.add_argument("--key-file")
    parser.add_argument("--amp-dir", type=Path)
    parser.add_argument("--since", default="30 minutes ago")
    args = parser.parse_args()
    if bool(args.cert_file) != bool(args.key_file):
        parser.error("--cert-file and --key-file must be provided together")

    endpoints: dict[str, str] = {"leader": args.base_url.rstrip("/")}
    for value in args.endpoint:
        if "=" not in value:
            parser.error("--endpoint must use NAME=URL")
        name, url = value.split("=", 1)
        endpoints[name.strip()] = url.rstrip("/")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = args.output_root / timestamp
    output.mkdir(parents=True, exist_ok=False)
    auth = None
    verify = ssl_context(args)
    fetch_status: list[dict[str, Any]] = []
    with httpx.Client(timeout=30, verify=verify, auth=auth) as client:
        for name, origin in endpoints.items():
            agent_dir = output / "agents" / name
            agent_dir.mkdir(parents=True)
            for route, filename in (("/health", "health.json"), ("/acs", "acs.json")):
                try:
                    response = client.get(f"{origin}{route}")
                    response.raise_for_status()
                    payload = response.json()
                    (agent_dir / filename).write_text(
                        json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    fetch_status.append(
                        {"agent": name, "route": route, "status": response.status_code}
                    )
                except (httpx.HTTPError, ValueError) as exc:
                    fetch_status.append(
                        {
                            "agent": name,
                            "route": route,
                            "status": "failed",
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )

    (output / "git-sha.txt").write_text(
        run_command(["git", "rev-parse", "HEAD"]), encoding="utf-8"
    )
    (output / "git-status.txt").write_text(
        run_command(["git", "status", "--short"]), encoding="utf-8"
    )
    services = [
        "research-mesh-leader.service",
        "research-mesh-partner@literature.service",
        "research-mesh-partner@experiment.service",
        "research-mesh-partner@analysis.service",
        "research-mesh-partner@dataset.service",
        "research-mesh-partner@synthesis.service",
        "research-mesh-partner@review.service",
        "research-mesh-llm.service",
    ]
    (output / "systemd-status.txt").write_text(
        run_command(["systemctl", "is-active", *services]), encoding="utf-8"
    )
    (output / "journal.log").write_text(
        run_command(
            [
                "journalctl",
                "--since",
                args.since,
                "--no-pager",
                "-o",
                "short-iso",
                *[item for service in services for item in ("-u", service)],
            ]
        ),
        encoding="utf-8",
    )
    if args.amp_dir and args.amp_dir.is_dir():
        amp_output = output / "amp"
        amp_output.mkdir()
        for source in args.amp_dir.glob("*.ndjson"):
            (amp_output / source.name).write_text(
                redact(source.read_text(encoding="utf-8", errors="replace")),
                encoding="utf-8",
            )

    (output / "fetch-status.json").write_text(
        json.dumps(fetch_status, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    manifest = {
        str(path.relative_to(output)).replace("\\", "/"): {
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in sorted(output.rglob("*"))
        if path.is_file()
    }
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "redacted": True,
                "files": manifest,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(output)
    return 0 if all(item["status"] != "failed" for item in fetch_status) else 1


if __name__ == "__main__":
    raise SystemExit(main())
