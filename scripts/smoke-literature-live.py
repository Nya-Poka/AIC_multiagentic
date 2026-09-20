from __future__ import annotations

import asyncio

from research_mesh.literature import search_literature
from research_mesh.sample_data import sample_request


async def main() -> None:
    request = sample_request().model_copy(update={"documents": []})
    result = await search_literature(request)
    provider = result["provider"]
    if provider["status"] != "ok":
        raise RuntimeError(f"Crossref unavailable: {provider['error']}")
    if result["external_count"] < 1:
        raise RuntimeError("Crossref returned no literature records")
    first = result["evidence"][0]
    print(
        "live literature smoke: "
        f"{result['external_count']} Crossref records, first DOI={first.get('doi')}"
    )


if __name__ == "__main__":
    asyncio.run(main())
