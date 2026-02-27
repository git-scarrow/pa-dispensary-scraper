#!/usr/bin/env python3
"""MCP server for PA dispensary store registry + scraper adapters.

This server exposes:
- Local research data/resources (README, docs, store registry)
- Store lookup/search tools
- Scraping tools that dispatch to existing platform adapters

Run (stdio transport, typical for agent clients):
    python mcp_server.py

Optional:
    python mcp_server.py --transport streamable-http
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from adapters import dutchie, iheartjane, sunnyside, sweedpos, trulieve

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"
PROMPTS_DIR = ROOT / "prompts"
README_PATH = ROOT / "README.md"
STORES_PATH = DATA_DIR / "stores.json"
BUDDY_PROFILE_PATH = DOCS_DIR / "buddy-profile.md"
BUDDY_PROMPT_PATH = PROMPTS_DIR / "buddy_system.md"

SUPPORTED_DOCS = {
    "trulieve": DOCS_DIR / "trulieve.md",
    "sweedpos": DOCS_DIR / "sweedpos.md",
    "sunnyside": DOCS_DIR / "sunnyside.md",
    "iheartjane": DOCS_DIR / "iheartjane.md",
    "liberty-dutchie": DOCS_DIR / "liberty-dutchie.md",
}

mcp = FastMCP(
    "PA Dispensary Scraper",
    json_response=True,
    host=os.environ.get("FASTMCP_HOST", "127.0.0.1"),
    port=int(os.environ.get("FASTMCP_PORT", "8000")),
)


def _load_store_registry() -> dict[str, Any]:
    return json.loads(STORES_PATH.read_text(encoding="utf-8"))


def _stores() -> list[dict[str, Any]]:
    return _load_store_registry().get("stores", [])


def _with_registry_index(store: dict[str, Any], idx: int) -> dict[str, Any]:
    return {"registry_index": idx, **store}


def _summarize_registry() -> dict[str, Any]:
    data = _load_store_registry()
    stores = data.get("stores", [])
    by_platform: dict[str, int] = {}
    by_operator: dict[str, int] = {}
    for store in stores:
        by_platform[store["platform"]] = by_platform.get(store["platform"], 0) + 1
        by_operator[store["operator"]] = by_operator.get(store["operator"], 0) + 1
    return {
        "meta": data.get("_meta", {}),
        "counts": {
            "platforms": by_platform,
            "operators": by_operator,
        },
    }


def _normalize(text: str | None) -> str | None:
    return text.casefold().strip() if isinstance(text, str) else None


def _match_store(
    store: dict[str, Any],
    *,
    platform: str | None = None,
    operator: str | None = None,
    city_contains: str | None = None,
) -> bool:
    if platform and _normalize(store.get("platform")) != _normalize(platform):
        return False
    if operator and _normalize(store.get("operator")) != _normalize(operator):
        return False
    if city_contains and _normalize(city_contains) not in _normalize(store.get("city", "")):
        return False
    return True


def _get_store_by_registry_index(registry_index: int) -> dict[str, Any]:
    stores = _stores()
    if registry_index < 0 or registry_index >= len(stores):
        raise IndexError(f"registry_index {registry_index} out of range (0..{len(stores)-1})")
    return stores[registry_index]


def _safe_call(fn, *args, **kwargs) -> dict[str, Any]:
    try:
        result = fn(*args, **kwargs)
        return {"ok": True, "result": result}
    except Exception as exc:  # pragma: no cover - runtime integration path
        return {
            "ok": False,
            "error": type(exc).__name__,
            "message": str(exc),
        }


def _category_requirements(platform: str) -> dict[str, Any]:
    if platform == "trulieve_rest":
        return {"required": True, "kind": "name", "allowed": trulieve.CATEGORIES}
    if platform == "cresco_labs":
        return {"required": True, "kind": "name", "allowed": sunnyside.CATEGORIES}
    if platform == "sweedpos":
        return {"required": True, "kind": "id", "note": "Use sweedpos_get_category_ids first."}
    if platform == "iheartjane":
        return {"required": False}
    if platform == "dutchie_embedded":
        return {"required": False, "note": "Use dutchie_fetch_embed_bootstrap; GraphQL schema is incomplete."}
    return {"required": False}


def _buddy_guidelines() -> dict[str, Any]:
    return {
        "name": "Buddy",
        "version": "v1",
        "persona": {
            "tone": ["friendly", "stoner-friendly", "clear", "non-judgmental"],
            "style": [
                "plain English first, scientific detail as needed",
                "separate anecdotal effects from evidence-supported information",
                "avoid certainty about subjective effects",
            ],
        },
        "domains": {
            "cannabis": [
                "cannabinoids",
                "terpenes",
                "product formats",
                "extraction methods",
                "dosing basics",
                "label literacy",
                "emerging product tech",
            ],
            "pa_mmj_data_ops": [
                "platform-aware scraping",
                "store registry lookup",
                "inventory/pricing summaries",
                "adapter troubleshooting",
            ],
        },
        "safety": [
            "No medical diagnosis or treatment claims.",
            "No illegal or unsafe-use coaching.",
            "Use harm-reduction framing for dosing, impairment, and route-of-administration risks.",
            "Recommend a clinician/pharmacist for clinical or high-risk questions.",
        ],
        "keystone_green_rules": [
            "Use list_stores before get_store/scrape_store_menu; never guess registry_index.",
            "For SweedPOS, call sweedpos_get_category_ids before scrape_store_menu.",
            "For Liberty/Dutchie, explain GraphQL is not cracked and offer dutchie_fetch_embed_bootstrap for recon.",
            "PA is medical-only; prioritize med pricing and summarize Brand -> Strain -> THC % -> Price.",
        ],
        "modes": {
            "shopper": "Product guidance, comparisons, and practical cannabis education.",
            "research": "Structured, concise summaries with technical caveats and data limits.",
            "hybrid": "Cannabis education plus live menu lookup through MCP tools.",
            "debug": "Platform-specific scraping troubleshooting and recon guidance.",
        },
    }


@mcp.resource("pa://readme")
def project_readme() -> str:
    """Project README with platform map and status."""
    return README_PATH.read_text(encoding="utf-8")


@mcp.resource("pa://stores")
def store_registry_resource() -> dict[str, Any]:
    """Full `data/stores.json` registry."""
    return _load_store_registry()


@mcp.resource("pa://summary")
def store_summary_resource() -> dict[str, Any]:
    """Registry summary counts by platform and operator."""
    return _summarize_registry()


@mcp.resource("pa://buddy/profile")
def buddy_profile_resource() -> str:
    """Buddy persona profile for agent clients (friendly cannabis guide + keystone-green data operator)."""
    if BUDDY_PROFILE_PATH.exists():
        return BUDDY_PROFILE_PATH.read_text(encoding="utf-8")
    return "Buddy profile not found. Expected docs/buddy-profile.md"


@mcp.resource("pa://docs/{doc_name}")
def research_doc_resource(doc_name: str) -> str:
    """Platform research docs from `docs/`."""
    path = SUPPORTED_DOCS.get(doc_name)
    if not path:
        raise ValueError(f"Unknown doc '{doc_name}'. Allowed: {sorted(SUPPORTED_DOCS)}")
    return path.read_text(encoding="utf-8")


@mcp.tool()
def get_registry_summary() -> dict[str, Any]:
    """Return metadata and counts for the local store registry."""
    return _summarize_registry()


@mcp.tool()
def list_stores(
    platform: str | None = None,
    operator: str | None = None,
    city_contains: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Search the local store registry and return matching stores with `registry_index`."""
    stores = _stores()
    matches = [
        _with_registry_index(store, idx)
        for idx, store in enumerate(stores)
        if _match_store(store, platform=platform, operator=operator, city_contains=city_contains)
    ]
    sliced = matches[offset : offset + max(0, limit)]
    return {
        "total_matches": len(matches),
        "offset": offset,
        "limit": limit,
        "items": sliced,
    }


@mcp.tool()
def get_store(registry_index: int) -> dict[str, Any]:
    """Return one store entry from `data/stores.json` by `registry_index`."""
    return _with_registry_index(_get_store_by_registry_index(registry_index), registry_index)


@mcp.tool()
def get_platform_capabilities(platform: str | None = None) -> dict[str, Any]:
    """Describe how to scrape each platform through this MCP server."""
    capabilities = {
        "trulieve_rest": {
            "supports": ["scrape_store_menu(page|all)"],
            "category": _category_requirements("trulieve_rest"),
        },
        "cresco_labs": {
            "supports": ["scrape_store_menu(page|all)"],
            "category": _category_requirements("cresco_labs"),
        },
        "iheartjane": {
            "supports": ["scrape_store_menu(page|all)", "iheartjane_fetch_store_info"],
            "category": _category_requirements("iheartjane"),
        },
        "sweedpos": {
            "supports": ["sweedpos_get_category_ids", "scrape_store_menu(page|all)"],
            "category": _category_requirements("sweedpos"),
        },
        "dutchie_embedded": {
            "supports": ["dutchie_fetch_embed_bootstrap"],
            "category": _category_requirements("dutchie_embedded"),
        },
    }
    if platform:
        if platform not in capabilities:
            raise ValueError(f"Unknown platform '{platform}'")
        return {platform: capabilities[platform]}
    return capabilities


@mcp.tool()
def read_research_doc(doc_name: str) -> dict[str, Any]:
    """Read a local platform research doc (`trulieve`, `sunnyside`, `sweedpos`, `iheartjane`, `liberty-dutchie`)."""
    path = SUPPORTED_DOCS.get(doc_name)
    if not path:
        raise ValueError(f"Unknown doc '{doc_name}'. Allowed: {sorted(SUPPORTED_DOCS)}")
    return {"doc_name": doc_name, "path": str(path.relative_to(ROOT)), "content": path.read_text(encoding="utf-8")}


@mcp.tool()
def get_buddy_guidelines(mode: str | None = None) -> dict[str, Any]:
    """Return Buddy persona/safety/tooling guidance. Optionally filter to one mode."""
    data = _buddy_guidelines()
    if mode is None:
        if BUDDY_PROMPT_PATH.exists():
            data["prompt_path"] = str(BUDDY_PROMPT_PATH.relative_to(ROOT))
        if BUDDY_PROFILE_PATH.exists():
            data["profile_path"] = str(BUDDY_PROFILE_PATH.relative_to(ROOT))
        return data
    modes = data.get("modes", {})
    if mode not in modes:
        raise ValueError(f"Unknown mode '{mode}'. Allowed: {sorted(modes)}")
    return {
        "name": data["name"],
        "version": data["version"],
        "mode": {mode: modes[mode]},
        "safety": data["safety"],
        "keystone_green_rules": data["keystone_green_rules"],
    }


@mcp.tool()
def sweedpos_get_category_ids(registry_index: int) -> dict[str, Any]:
    """Get SweedPOS category IDs for a store. Store must have `platform == sweedpos`."""
    store = _get_store_by_registry_index(registry_index)
    if store.get("platform") != "sweedpos":
        raise ValueError("Store is not a sweedpos entry")
    return _safe_call(sweedpos.get_category_ids, store["domain"], store["base_path"])


@mcp.tool()
def iheartjane_fetch_store_info(registry_index: int) -> dict[str, Any]:
    """Fetch iHeartJane store metadata for a store in the local registry."""
    store = _get_store_by_registry_index(registry_index)
    if store.get("platform") != "iheartjane":
        raise ValueError("Store is not an iheartjane entry")
    return _safe_call(iheartjane.fetch_store_info, int(store["jane_store_id"]))


@mcp.tool()
def dutchie_fetch_embed_bootstrap(
    registry_index: int,
    max_chars: int = 12000,
) -> dict[str, Any]:
    """Fetch Dutchie embedded bootstrap JS for Liberty stores (GraphQL schema recon helper)."""
    store = _get_store_by_registry_index(registry_index)
    if store.get("platform") != "dutchie_embedded":
        raise ValueError("Store is not a dutchie_embedded entry")
    token = store.get("embed_token")
    if not token:
        raise ValueError("Store is missing embed_token")
    raw = _safe_call(dutchie.fetch_embed_bootstrap, token)
    if not raw.get("ok"):
        return raw
    text = raw["result"]
    return {
        "ok": True,
        "token": token,
        "truncated": len(text) > max_chars,
        "text": text[:max_chars],
    }


@mcp.tool()
def scrape_store_menu(
    registry_index: int,
    mode: str = "page",
    category: str | None = None,
    page: int = 1,
    per_page: int = 50,
    offset: int = 0,
    sweedpos_category_id: int | None = None,
) -> dict[str, Any]:
    """Dispatch a scrape request to the correct platform adapter for one store.

    Notes:
    - `mode='page'` returns one page/chunk where supported.
    - `mode='all'` paginates and may return large payloads.
    - SweedPOS requires `sweedpos_category_id`; use `sweedpos_get_category_ids` first.
    """
    if mode not in {"page", "all"}:
        raise ValueError("mode must be 'page' or 'all'")

    store = _get_store_by_registry_index(registry_index)
    platform = store["platform"]

    if platform == "trulieve_rest":
        if not category:
            raise ValueError(f"`category` required for {platform}: {trulieve.CATEGORIES}")
        if mode == "page":
            return _safe_call(trulieve.fetch_menu, int(store["store_id"]), category, page)
        return _safe_call(trulieve.fetch_all_products, int(store["store_id"]), category)

    if platform == "cresco_labs":
        if not category:
            raise ValueError(f"`category` required for {platform}: {sunnyside.CATEGORIES}")
        if mode == "page":
            return _safe_call(sunnyside.fetch_inventory, int(store["store_id"]), category, offset)
        return _safe_call(sunnyside.fetch_all_products, int(store["store_id"]), category)

    if platform == "iheartjane":
        if mode == "page":
            return _safe_call(iheartjane.fetch_menu, int(store["jane_store_id"]), page, per_page)
        return _safe_call(iheartjane.fetch_all_products, int(store["jane_store_id"]))

    if platform == "sweedpos":
        if sweedpos_category_id is None:
            raise ValueError("`sweedpos_category_id` required for sweedpos. Call `sweedpos_get_category_ids` first.")
        if mode == "page":
            # Reuse internal helpers for one-page access to avoid fetching all pages.
            return _safe_call(
                lambda: sweedpos._extract_product_list(  # type: ignore[attr-defined]
                    sweedpos._get_sw_qc(  # type: ignore[attr-defined]
                        store["domain"], store["base_path"], sweedpos_category_id, page
                    )
                )
            )
        return _safe_call(sweedpos.fetch_all_products, store["domain"], store["base_path"], sweedpos_category_id)

    if platform == "dutchie_embedded":
        return {
            "ok": False,
            "error": "NotImplemented",
            "message": "Dutchie GraphQL menu scraping is not cracked yet. Use `dutchie_fetch_embed_bootstrap` for recon.",
        }

    return {
        "ok": False,
        "error": "UnsupportedPlatform",
        "message": f"Unsupported platform '{platform}'",
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PA dispensary scraper MCP server")
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "streamable-http", "sse"],
        help="MCP transport (default: stdio)",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.transport == "stdio":
        mcp.run()
        return
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
