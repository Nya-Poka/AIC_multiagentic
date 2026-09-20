from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from acps_sdk.acs import AgentCapabilitySpec  # noqa: E402
from research_mesh.acs import AGENT_CARDS, build_acs  # noqa: E402
from research_mesh.config import runtime_settings  # noqa: E402
from research_mesh.registry import PARTNER_PORTS  # noqa: E402


PORTS = {"leader": 8000, **PARTNER_PORTS}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate five ACPs v02.02 ACS files for Registry submission."
    )
    parser.add_argument(
        "--base-url",
        required=True,
        help="Public HTTPS origin, for example https://agents.example.edu.cn",
    )
    parser.add_argument(
        "--routing",
        choices=("ports", "paths"),
        default="ports",
        help=(
            "ports exposes each Uvicorn mTLS listener directly (recommended); paths "
            "requires an ingress that preserves verified peer-certificate identity"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "deploy" / "acps" / "generated",
    )
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.hostname:
        parser.error("--base-url must use HTTPS")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        parser.error("--base-url must be an origin without path, query, or fragment")
    if args.routing == "ports" and parsed.port is not None:
        parser.error("--base-url must not include a port when --routing ports is used")

    settings = replace(runtime_settings(), public_base_url=base_url)
    args.output.mkdir(parents=True, exist_ok=True)
    for slug in AGENT_CARDS:
        endpoint = (
            f"{base_url}:{PORTS[slug]}/rpc"
            if args.routing == "ports"
            else f"{base_url}/{slug}/rpc"
        )
        document = build_acs(
            slug,
            settings=settings,
            endpoint=endpoint,
            registration_template=True,
        )
        AgentCapabilitySpec.model_validate(document)
        path = args.output / f"{slug}.acs.json"
        path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(path)


if __name__ == "__main__":
    main()
