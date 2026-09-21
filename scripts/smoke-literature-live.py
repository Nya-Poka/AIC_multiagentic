from __future__ import annotations

import asyncio

from research_mesh.literature import search_literature
from research_mesh.sample_data import sample_request


async def main() -> None:
    request = sample_request().model_copy(update={"documents": []})
    result = await search_literature(request)
    provider = result["provider"]
    if provider["available_source_count"] < 1:
        errors = {
            source["name"]: source["error"] for source in provider["sources"]
        }
        raise RuntimeError(f"all literature providers unavailable: {errors}")
    if result["external_count"] < 1:
        raise RuntimeError("literature providers returned no records")
    first = result["evidence"][0]
    source_summary = ", ".join(
        f"{source['name']}={source['status']}" for source in provider["sources"]
    )
    print(
        "live literature smoke: "
        f"{result['external_count']} merged records from "
        f"{provider['available_source_count']}/{provider['source_count']} sources, "
        f"first DOI={first.get('doi')}; {source_summary}"
    )


if __name__ == "__main__":
    asyncio.run(main())
