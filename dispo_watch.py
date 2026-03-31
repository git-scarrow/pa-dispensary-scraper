#!/usr/bin/env python3
"""
dispo_watch.py — Daily PA dispensary deal tracker.

Scrapes user-configured stores, appends price snapshots to prices.db,
then prints a rich terminal digest of new deals and price drops.

Usage:
    python dispo_watch.py            # scrape + digest
    python dispo_watch.py --digest   # digest only (no scrape)
    python dispo_watch.py --scrape   # scrape only (no digest)
    python dispo_watch.py --list     # list stores that match current config

Config: watch_config.json

Filter modes:
  radius  — scrape stores within N miles of a zip code
  stores  — scrape an explicit list by registry_index
  all     — scrape every supported store (default if no config)

Hunter Mode (Phase 2.5):
  - Define 'notifications' in config to highlight "Unicorn" strains.
  - Use 'aliases' to catch spelling variants (e.g. "LA Baker" vs "L.A. Baker").
  - Filter out house brands with 'ignore_brands'.
  - Toggle 'show_unicorn_summary' to hide/show unicorn rollups.
  - Toggle 'show_ignored_summary' to see what was filtered.
  - Add 'terp_thresholds' to filter for medicinal quality (e.g. total terps / terp ratio).

Systemd user timer: install dispo_watch.service + dispo_watch.timer
  cp dispo_watch.{service,timer} ~/.config/systemd/user/
  systemctl --user daemon-reload
  systemctl --user enable --now dispo_watch.timer
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sqlite_utils
from rich.console import Console
from rich.table import Table
from rich import box
from rich.text import Text

from adapters import iheartjane, sunnyside, sweedpos, trulieve

ROOT = Path(__file__).resolve().parent
STORES_PATH = ROOT / "data" / "stores.json"
ZIP_CACHE_PATH = ROOT / "data" / "zip_cache.json"
CONFIG_PATH = ROOT / "watch_config.json"
DB_PATH = ROOT / "prices.db"

SWEEDPOS_CATEGORY_PREFIXES = ("flower", "cartridge", "disposable", "concentrate")
TRULIEVE_CATEGORIES = ["flower", "vapes", "concentrates"]
CRESCO_CATEGORIES = ["flower", "vapes", "concentrates"]

log = logging.getLogger("dispo_watch")
console = Console()

# ---------------------------------------------------------------------------
# Config + store filtering
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: dict[str, Any] = {
    "filter": {"mode": "all"},
    "notifications": {
        "unicorns": [],
        "aliases": {},
        "ignore_brands": [],
        "iheartjane_text_fallback": False,
        "terp_ratio_only_categories": [],
        "terp_ratio_min_thc_pct": None,
        "terp_ratio_min_thc_by_category": {},
        "match_mode": "contains",
        "show_unicorn_summary": True,
        "show_ignored_summary": False,
        "terp_thresholds": {},
    },
}

EXAMPLE_CONFIG = """\
{
  "_help": "Filter modes: 'radius', 'stores', or 'all'",
  "filter": {
    "mode": "radius",
    "center_zip": "19002",
    "radius_miles": 30
  },
  "notifications": {
    "unicorns": ["Golden Pineapple", "LA Baker", "African Thai", "Bio Jesus"],
    "aliases": {
      "LA Baker": ["L.A. Baker"],
      "African Thai": ["African Thai #15"]
    },
    "ignore_brands": ["The Bank", "Seche", "Whole Plants", "Kind Tree"],
    "iheartjane_text_fallback": false,
    "terp_ratio_only_categories": ["flower", "vape", "concentrate"],
    "terp_ratio_min_thc_pct": 10,
    "terp_ratio_min_thc_by_category": {
      "flower": 10,
      "vape": 40,
      "cartridge": 40,
      "concentrate": 40
    },
    "match_mode": "contains",
    "show_unicorn_summary": true,
    "show_ignored_summary": true,
    "terp_thresholds": {
      "terp_total": 1.5,
      "terp_ratio": 0.08
    }
  }
}

// OR — explicit list by registry_index (run with --list to see indices):
{
  "filter": {
    "mode": "stores",
    "registry_indices": [28, 45, 69]
  }
}
"""


def load_config() -> dict[str, Any]:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return DEFAULT_CONFIG


def _load_zip_cache() -> dict[str, list[float]]:
    if ZIP_CACHE_PATH.exists():
        return json.loads(ZIP_CACHE_PATH.read_text())
    return {}


def _save_zip_cache(cache: dict[str, list[float]]) -> None:
    ZIP_CACHE_PATH.write_text(json.dumps(cache, indent=2))


def geocode_zip(zipcode: str, cache: dict[str, list[float]]) -> list[float] | None:
    """Return [lat, lon] for a US zip code, using Nominatim with local cache."""
    if zipcode in cache:
        return cache[zipcode]

    import urllib.request, urllib.parse
    url = (
        "https://nominatim.openstreetmap.org/search?"
        + urllib.parse.urlencode({
            "postalcode": zipcode,
            "country": "US",
            "format": "json",
            "limit": 1,
        })
    )
    req = urllib.request.Request(url, headers={"User-Agent": "dispo-watch/1.0 (personal)"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            results = json.loads(r.read())
        if results:
            coords = [float(results[0]["lat"]), float(results[0]["lon"])]
            cache[zipcode] = coords
            _save_zip_cache(cache)
            time.sleep(1.1)  # Nominatim ToS: max 1 req/sec
            return coords
    except Exception as e:
        log.warning("Geocode failed for zip %s: %s", zipcode, e)
    return None


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in miles between two lat/lon points."""
    R = 3958.8  # Earth radius in miles
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def filter_stores(
    stores: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[tuple[int, dict[str, Any]]]:
    """
    Return [(registry_index, store)] for stores matching the config filter.
    Skips dutchie_embedded regardless of filter.
    """
    filt = config.get("filter", {"mode": "all"})
    mode = filt.get("mode", "all")

    eligible = [
        (idx, s) for idx, s in enumerate(stores)
        if s["platform"] != "dutchie_embedded"
    ]

    if mode == "all":
        return eligible

    if mode == "stores":
        indices = set(filt.get("registry_indices", []))
        return [(idx, s) for idx, s in eligible if idx in indices]

    if mode == "radius":
        center_zip = str(filt.get("center_zip", ""))
        radius = float(filt.get("radius_miles", 30))
        if not center_zip:
            log.error("radius mode requires center_zip in config")
            return eligible

        zip_cache = _load_zip_cache()

        console.print(f"[dim]Geocoding center zip {center_zip}...[/dim]")
        center = geocode_zip(center_zip, zip_cache)
        if not center:
            console.print(f"[red]Could not geocode center zip {center_zip}, falling back to all stores[/red]")
            return eligible

        # Pre-geocode all store zips we don't have cached yet
        store_zips = {s["zip"] for _, s in eligible if s.get("zip") and s["zip"] not in zip_cache}
        if store_zips:
            console.print(f"[dim]Geocoding {len(store_zips)} store zip codes (one-time, ~{len(store_zips)}s)...[/dim]")
            for z in sorted(store_zips):
                geocode_zip(z, zip_cache)

        result = []
        for idx, s in eligible:
            store_zip = s.get("zip")
            if not store_zip:
                continue
            coords = zip_cache.get(store_zip)
            if not coords:
                log.warning("No coords for zip %s (%s), skipping", store_zip, s["city"])
                continue
            dist = haversine_miles(center[0], center[1], coords[0], coords[1])
            if dist <= radius:
                result.append((idx, s))

        return result

    log.warning("Unknown filter mode %r, using all", mode)
    return eligible


# ---------------------------------------------------------------------------
# Schema normalization  (one dict per product row)
# ---------------------------------------------------------------------------

PA_BIG_8_TERPENES = (
    "myrcene",
    "caryophyllene",
    "limonene",
    "terpinolene",
    "linalool",
    "pinene",
    "humulene",
    "ocimene",
)

TERP_DISPLAY_NAMES = {
    "myrcene": "myrcene",
    "caryophyllene": "caryophyllene",
    "limonene": "limonene",
    "terpinolene": "terpinolene",
    "linalool": "linalool",
    "pinene": "pinene",
    "humulene": "humulene",
    "ocimene": "ocimene",
}


def _coerce_percent_scalar(value: Any) -> float | None:
    """Best-effort parse for terp/cannabinoid percent-like values."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace("%", "")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    if isinstance(value, list):
        for item in value:
            parsed = _coerce_percent_scalar(item)
            if parsed is not None:
                return parsed
        return None
    if isinstance(value, dict):
        unit = value.get("unitAbbr")
        if unit and str(unit).strip() != "%":
            return None
        if "percent" in value:
            return _coerce_percent_scalar(value.get("percent"))
        if "displayValue" in value:
            return _coerce_percent_scalar(value.get("displayValue"))
        if "value" in value:
            return _coerce_percent_scalar(value.get("value"))
        return None
    return None


SIZE_RE = re.compile(r"(?<!\d)(\d+(?:\.\d+)?)\s*(mg|g)\b", re.IGNORECASE)


def _format_unit_size_label(unit_size_g: float | None) -> str:
    if unit_size_g is None or unit_size_g <= 0:
        return ""
    if abs(unit_size_g - round(unit_size_g)) < 1e-9:
        return f"{int(round(unit_size_g))}g"
    return f"{unit_size_g:g}g"


def _extract_unit_size_g(*values: Any) -> float | None:
    for value in values:
        if value is None:
            continue
        if isinstance(value, (int, float)):
            if value > 0:
                return float(value)
            continue
        text = str(value)
        for match in SIZE_RE.finditer(text):
            amount = float(match.group(1))
            unit = match.group(2).lower()
            if unit == "mg":
                amount /= 1000.0
            if amount > 0:
                return amount
    return None


def _normalize_terp_key(key: Any) -> str:
    text = str(key or "").strip().casefold()
    if not text:
        return ""
    chars = []
    for ch in text:
        chars.append(ch if (ch.isalnum() or ch == "_") else "_")
    norm = "".join(chars).strip("_")
    while "__" in norm:
        norm = norm.replace("__", "_")
    return norm


def _flatten_terp_source(source: Any) -> dict[str, Any]:
    """Flatten common terp payload shapes to a normalized dict keyed by terp name."""
    if source is None:
        return {}
    if isinstance(source, dict):
        flat: dict[str, Any] = {}
        for k, v in source.items():
            nk = _normalize_terp_key(k)
            if nk:
                flat[nk] = v
        return flat
    if isinstance(source, list):
        flat = {}
        for item in source:
            if not isinstance(item, dict):
                continue
            name_key = (
                item.get("name")
                or item.get("displayName")
                or item.get("key")
                or item.get("label")
                or (item.get("terpene") or {}).get("name")
                or (item.get("terpene") or {}).get("displayName")
                or ""
            )
            nk = _normalize_terp_key(name_key)
            if not nk:
                continue
            terp_meta = item.get("terpene") if isinstance(item.get("terpene"), dict) else {}
            if "value" in item:
                flat[nk] = item.get("value")
            elif "percent" in item:
                flat[nk] = item.get("percent")
            elif "displayValue" in item:
                flat[nk] = item.get("displayValue")
            elif "amount" in item:
                flat[nk] = item.get("amount")
            elif "quantity" in item:
                flat[nk] = item.get("quantity")
            elif "value" in terp_meta:
                flat[nk] = terp_meta.get("value")
            elif "percent" in terp_meta:
                flat[nk] = terp_meta.get("percent")
            elif "displayValue" in terp_meta:
                flat[nk] = terp_meta.get("displayValue")
        return flat
    return {}


def _extract_terps(data: dict[str, Any], key_map: dict[str, str]) -> dict[str, float | None]:
    """Extract PA Big 8 terpenes into standardized terp_* columns."""
    result: dict[str, float | None] = {}
    aliases_by_terp = {
        "myrcene": ("b_myrcene", "beta_myrcene", "beta myrcene"),
        "caryophyllene": ("b_caryophyllene", "beta_caryophyllene", "beta caryophyllene"),
        "limonene": ("d_limonene",),
        "terpinolene": ("alpha_terpinolene",),
        "linalool": (),
        "humulene": ("alpha_humulene",),
        "ocimene": ("beta_ocimene", "alpha_ocimene"),
    }
    for terp_name in PA_BIG_8_TERPENES:
        src_key = key_map.get(terp_name, terp_name)
        val = _coerce_percent_scalar(data.get(src_key))
        if val is None:
            for alias in aliases_by_terp.get(terp_name, ()):
                val = _coerce_percent_scalar(data.get(alias))
                if val is not None:
                    break

        if terp_name == "pinene" and val is None:
            alpha = (
                _coerce_percent_scalar(data.get("a_pinene"))
                or _coerce_percent_scalar(data.get("alpha_pinene"))
                or _coerce_percent_scalar(data.get("alpha pinene"))
                or 0.0
            )
            beta = (
                _coerce_percent_scalar(data.get("b_pinene"))
                or _coerce_percent_scalar(data.get("beta_pinene"))
                or _coerce_percent_scalar(data.get("beta pinene"))
                or 0.0
            )
            val = (alpha + beta) if (alpha or beta) else None

        result[f"terp_{terp_name}"] = val
    return result


def _compute_terp_total(values: dict[str, Any]) -> float | None:
    explicit_total = _coerce_percent_scalar(
        values.get("terp_total")
        or values.get("total_terpenes")
        or values.get("total terpenes")
        or values.get("terpenes_total")
    )
    if explicit_total is not None:
        return explicit_total

    total = 0.0
    found = False
    for terp_name in PA_BIG_8_TERPENES:
        parsed = _coerce_percent_scalar(values.get(f"terp_{terp_name}"))
        if parsed is None:
            continue
        total += parsed
        found = True
    return total if found else None


def _extract_iheartjane_terps_from_description(description: str) -> dict[str, Any]:
    """Conservative text parser: extract explicit terp percentages only.

    Supported examples:
    - "Myrcene 0.42%"
    - "0.42% Myrcene"
    - "Beta Caryophyllene: 0.31%"
    - "Total Terpenes 2.5%"
    """
    text = (description or "").replace("\u00a0", " ").strip()
    parsed = {f"terp_{name}": None for name in PA_BIG_8_TERPENES}
    parsed["terp_total"] = None
    parsed["terp_text_parsed"] = 0
    parsed["terp_names_present"] = 0
    if not text:
        return parsed

    text_cf = text.casefold()
    if any(name in text_cf for name in TERP_DISPLAY_NAMES.values()):
        parsed["terp_names_present"] = 1

    alias_patterns = {
        "myrcene": r"(?:myrcene|beta[\s-]*myrcene)",
        "caryophyllene": r"(?:caryophyllene|beta[\s-]*caryophyllene)",
        "limonene": r"(?:limonene|d[\s-]*limonene)",
        "terpinolene": r"(?:terpinolene|alpha[\s-]*terpinolene)",
        "linalool": r"linalool",
        "pinene": r"(?:pinene|alpha[\s-]*pinene|beta[\s-]*pinene)",
        "humulene": r"(?:humulene|alpha[\s-]*humulene)",
        "ocimene": r"(?:ocimene|alpha[\s-]*ocimene|beta[\s-]*ocimene)",
    }

    pct_num = r"(\d{1,2}(?:\.\d{1,4})?)"

    def _find_pct_for_name(pattern: str) -> float | None:
        # Name before value
        m = re.search(
            rf"{pattern}\s*(?:[:\-]|\bis\b)?\s*{pct_num}\s*%",
            text,
            re.IGNORECASE,
        )
        if m:
            return _coerce_percent_scalar(m.group(1))
        # Value before name
        m = re.search(
            rf"{pct_num}\s*%\s*(?:{pattern})",
            text,
            re.IGNORECASE,
        )
        if m:
            return _coerce_percent_scalar(m.group(1))
        return None

    for terp_name in PA_BIG_8_TERPENES:
        val = _find_pct_for_name(alias_patterns[terp_name])
        parsed[f"terp_{terp_name}"] = val
        if val is not None:
            parsed["terp_text_parsed"] = 1

    total_match = re.search(
        rf"(?:total\s+terpenes?|terpenes?\s+total)\s*(?:[:\-]|\bis\b)?\s*{pct_num}\s*%",
        text,
        re.IGNORECASE,
    )
    if total_match:
        parsed["terp_total"] = _coerce_percent_scalar(total_match.group(1))
        parsed["terp_text_parsed"] = 1
    else:
        # Conservative fallback: sum parsed Big 8 only if we found multiple explicit terp values.
        explicit_count = sum(
            1 for terp_name in PA_BIG_8_TERPENES
            if parsed.get(f"terp_{terp_name}") is not None
        )
        if explicit_count >= 2:
            parsed["terp_total"] = _compute_terp_total(parsed)
            if parsed["terp_total"] is not None:
                parsed["terp_text_parsed"] = 1

    return parsed


def _extract_iheartjane_terps_from_lab_results(lab_results: Any) -> dict[str, Any]:
    """Structured iHeartJane parser using Algolia `lab_results` payload (no text parsing)."""
    parsed = {f"terp_{name}": None for name in PA_BIG_8_TERPENES}
    parsed["terp_total"] = None
    parsed["terp_text_parsed"] = 0
    parsed["terp_structured_parsed"] = 0
    parsed["terp_names_present"] = 0

    compounds: list[dict[str, Any]] = []
    if isinstance(lab_results, list):
        for entry in lab_results:
            if not isinstance(entry, dict):
                continue
            nested = entry.get("lab_results")
            if isinstance(nested, list):
                for compound in nested:
                    if isinstance(compound, dict):
                        compounds.append(compound)
            elif any(k in entry for k in ("compound_name", "unit_id", "name")):
                compounds.append(entry)
    elif isinstance(lab_results, dict):
        nested = lab_results.get("lab_results")
        if isinstance(nested, list):
            for compound in nested:
                if isinstance(compound, dict):
                    compounds.append(compound)

    if not compounds:
        return parsed

    flat: dict[str, Any] = {}
    for c in compounds:
        unit = str(c.get("unit") or c.get("unitAbbr") or c.get("unit_abbr") or "").strip()
        if unit and unit != "%":
            continue
        name = c.get("compound_name") or c.get("unit_id") or c.get("name") or ""
        nk = _normalize_terp_key(name)
        if not nk:
            continue
        if any(t in nk for t in PA_BIG_8_TERPENES) or "terp" in nk:
            parsed["terp_names_present"] = 1
        val = _coerce_percent_scalar(c.get("value"))
        if val is None:
            val = _coerce_percent_scalar(c.get("percent"))
        if val is None:
            val = _coerce_percent_scalar(c.get("displayValue"))
        if val is None:
            continue
        prev = _coerce_percent_scalar(flat.get(nk))
        # Prefer a stable non-null value if repeated across price IDs; use max to avoid rounding drift.
        flat[nk] = max(prev, val) if prev is not None else val

    terps = _extract_terps(flat, {})
    parsed.update(terps)
    parsed["terp_total"] = _compute_terp_total({**terps, **flat})
    if parsed["terp_total"] is not None or any(parsed.get(f"terp_{n}") is not None for n in PA_BIG_8_TERPENES):
        parsed["terp_structured_parsed"] = 1
    return parsed


def _normalize_iheartjane(
    product: dict,
    idx: int,
    store: dict,
    iheartjane_text_fallback: bool = False,
) -> dict:
    terps = _extract_iheartjane_terps_from_lab_results(product.get("lab_results"))
    source = "none"
    structured_total = _coerce_percent_scalar(terps.get("terp_total"))
    if int(terps.get("terp_structured_parsed") or 0) != 0:
        source = "jane_structured"

    if iheartjane_text_fallback and (structured_total is None or structured_total <= 0):
        text_terps = _extract_iheartjane_terps_from_description(str(product.get("description") or ""))
        if product.get("terpenes") or terps.get("terp_names_present"):
            text_terps["terp_names_present"] = 1
        if int(text_terps.get("terp_text_parsed") or 0) != 0:
            terps = text_terps
            source = "jane_text"

    if product.get("terpenes") and not terps.get("terp_names_present"):
        terps["terp_names_present"] = 1
    row = {
        "registry_index": idx,
        "operator": store["operator"],
        "city": store["city"],
        "platform": "iheartjane",
        "product_id": str(product.get("objectID") or product.get("product_id") or ""),
        "name": product.get("name") or "",
        "brand": product.get("brand") or "",
        "category": product.get("kind") or "",
        "subcategory": product.get("kind_subtype") or "",
        "price": product.get("price"),
        "discounted_price": None,  # marketing theater — shelf price is the real price
        "unit_size_g": (
            float(product.get("unit_size_g"))
            if product.get("unit_size_g") is not None
            else _extract_unit_size_g(
                product.get("unit_size_label"),
                product.get("name"),
                product.get("kind_subtype"),
                product.get("description"),
            )
        ),
        "unit_size_label": (
            product.get("unit_size_label")
            or _format_unit_size_label(
                _extract_unit_size_g(
                    product.get("unit_size_g"),
                    product.get("name"),
                    product.get("kind_subtype"),
                    product.get("description"),
                )
            )
        ),
        "special_title": product.get("special_title") or "",
        "thc_pct": product.get("percent_thc"),
        "_meta_source": source,
    }
    row.update(terps)
    return row


def _normalize_trulieve(product: dict, idx: int, store: dict, category: str) -> dict:
    base_price = product.get("med_unit_price") or product.get("unit_price")
    # sale_unit_price is the shelf price re-derived from a marketing "original" —
    # the discount is already baked into the displayed price.  Ignore it.
    specials = product.get("specials") or []
    special_title = ""
    if specials and isinstance(specials[0], dict):
        special_title = (
            specials[0].get("title")
            or specials[0].get("name")
            or ((specials[0].get("menu_display_configuration") or {}).get("name"))
            or ""
        )
    thc = product.get("thc_content")
    thc_pct = float(thc) if thc and product.get("thc_content_unit") == "%" else None
    trulieve_terps_list = product.get("terpenes") or []
    variant_option = ""
    variants = product.get("variants") or []
    if isinstance(variants, list) and variants and isinstance(variants[0], dict):
        variant_option = str(variants[0].get("option") or "")
    unit_size_g = _extract_unit_size_g(
        variant_option,
        product.get("name"),
        product.get("subcategory"),
    )
    flat_terps = _flatten_terp_source(trulieve_terps_list)
    terps = _extract_terps(flat_terps, {})
    terps["terp_total"] = _compute_terp_total({**terps, **flat_terps})

    row = {
        "registry_index": idx,
        "operator": store["operator"],
        "city": store["city"],
        "platform": "trulieve_rest",
        "product_id": str(product.get("product_id") or product.get("id") or ""),
        "name": product.get("name") or "",
        "brand": product.get("brand") or "",
        "category": category,
        "subcategory": product.get("subcategory") or "",
        "price": float(base_price) if base_price else None,
        "discounted_price": None,
        "unit_size_g": unit_size_g,
        "unit_size_label": _format_unit_size_label(unit_size_g),
        "special_title": special_title,
        "thc_pct": thc_pct,
        "terp_names_present": 1 if trulieve_terps_list else 0,
        "_meta_source": "trulieve_nested",
    }
    row.update(terps)
    return row


def _normalize_cresco(product: dict, idx: int, store: dict, category: str) -> dict:
    sku = product.get("sku") or {}
    sku_product = sku.get("product") or {}
    brand = (sku_product.get("brand") or {}).get("name") or product.get("brand") or ""
    name = sku_product.get("name") or sku.get("name") or product.get("name") or ""
    base_price = product.get("price")
    # discounted_price is the shelf price re-derived from a marketing "original" —
    # the discount is already baked into the displayed price.  Ignore it.
    special = (product.get("applied_special") or {}).get("special_name") or ""
    potency = product.get("potency") or {}
    thc_raw = product.get("bt_potency_thc") or potency.get("thc")
    unit_size_g = _extract_unit_size_g(
        sku_product.get("weight_in_g"),
        sku_product.get("weight"),
        sku.get("name"),
        product.get("name"),
        (sku_product.get("sub_category") or ""),
    )
    terps = _extract_terps(
        potency,
        {
            "myrcene": "b_myrcene",
            "caryophyllene": "b_caryophyllene",
            "pinene": "pinene",
        },
    )
    terps["terp_total"] = _compute_terp_total(
        {
            **terps,
            "total_terpenes": potency.get("total_terpenes"),
        }
    )
    row = {
        "registry_index": idx,
        "operator": store["operator"],
        "city": store["city"],
        "platform": "cresco_labs",
        "product_id": str(product.get("id") or product.get("product_id") or ""),
        "name": name,
        "brand": brand,
        "category": category,
        "subcategory": (sku_product.get("sub_category") or ""),
        "price": float(base_price) if base_price else None,
        "discounted_price": None,
        "unit_size_g": unit_size_g,
        "unit_size_label": _format_unit_size_label(unit_size_g),
        "special_title": special,
        "thc_pct": float(thc_raw) if thc_raw else None,
        "terp_names_present": 1 if potency else 0,
        "_meta_source": "cresco_api",
    }
    row.update(terps)
    return row


def _normalize_sweedpos(product: dict, idx: int, store: dict, category_name: str) -> list[dict]:
    brand_field = product.get("brand")
    if isinstance(brand_field, dict):
        brand = brand_field.get("name") or ""
    elif isinstance(brand_field, str):
        brand = brand_field
    else:
        brand = ""
    name = product.get("name") or ""
    base_id = str(product.get("id") or "")
    rows = []
    variants = product.get("variants")
    if isinstance(variants, dict):
        variants = list(variants.values())
    if not isinstance(variants, list):
        variants = [{}]
    dict_variants = [variant for variant in variants if isinstance(variant, dict)] or [{}]
    for variant in dict_variants:
        vid = variant.get("id") or ""
        product_id = f"{base_id}:{vid}" if vid else base_id
        price = variant.get("price")
        # promoPrice is marketing theater — discount already in shelf price.
        promos = variant.get("promos") or []
        special_title = promos[0].get("shortName", "") if promos else ""
        lab = variant.get("labTests") or {}
        thc_data = lab.get("thc") or lab.get("displayThc") or {}
        thc_vals = thc_data.get("value") or []
        thc_unit = thc_data.get("unitAbbr") or "%"
        thc_pct = float(thc_vals[0]) if thc_vals and thc_unit == "%" else None

        terp_source: Any = (
            lab.get("terpenes")
            or variant.get("terpenes")
            or product.get("terpenes")
            or lab
        )
        flat_terps = _flatten_terp_source(terp_source)
        strain = product.get("strain") if isinstance(product.get("strain"), dict) else {}
        strain_terps_names = _flatten_terp_source(strain.get("terpenes"))
        terp_names_present = 1 if (flat_terps or strain_terps_names) else 0

        terps = _extract_terps(flat_terps, {})
        terps["terp_total"] = _compute_terp_total(
            {
                **terps,
                **flat_terps,
            }
        )
        vname = variant.get("name") or ""
        subcategory = product.get("subcategory")
        if isinstance(subcategory, dict):
            subcategory_name = subcategory.get("name") or ""
        elif isinstance(subcategory, str):
            subcategory_name = subcategory
        else:
            subcategory_name = ""
        unit_size_g = _extract_unit_size_g(vname, name, subcategory_name)
        row = {
            "registry_index": idx,
            "operator": store["operator"],
            "city": store["city"],
            "platform": "sweedpos",
            "product_id": product_id,
            "name": f"{name} — {vname}" if vname else name,
            "brand": brand,
            "category": category_name,
            "subcategory": subcategory_name,
            "price": float(price) if price is not None else None,
            "discounted_price": None,
            "unit_size_g": unit_size_g,
            "unit_size_label": _format_unit_size_label(unit_size_g),
            "special_title": special_title,
            "thc_pct": thc_pct,
            "terp_names_present": terp_names_present,
            "_meta_source": "sweed_labtests" if flat_terps else ("sweed_strain_names" if strain_terps_names else "none"),
        }
        row.update(terps)
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Per-platform scrape dispatchers
# ---------------------------------------------------------------------------

def _scrape_iheartjane(
    store: dict,
    idx: int,
    iheartjane_text_fallback: bool = False,
) -> list[dict]:
    products = iheartjane.fetch_all_products(int(store["jane_store_id"]))
    return [
        _normalize_iheartjane(
            p,
            idx,
            store,
            iheartjane_text_fallback=iheartjane_text_fallback,
        )
        for p in products
    ]


def _scrape_trulieve(store: dict, idx: int) -> list[dict]:
    rows = []
    for cat in TRULIEVE_CATEGORIES:
        try:
            rows.extend(_normalize_trulieve(p, idx, store, cat)
                        for p in trulieve.fetch_all_products(int(store["store_id"]), cat))
        except Exception as e:
            log.warning("Trulieve %s/%s: %s", store["city"], cat, e)
    return rows


def _scrape_cresco(store: dict, idx: int) -> list[dict]:
    rows = []
    for cat in CRESCO_CATEGORIES:
        try:
            rows.extend(_normalize_cresco(p, idx, store, cat)
                        for p in sunnyside.fetch_all_products(int(store["store_id"]), cat))
        except Exception as e:
            log.warning("Cresco %s/%s: %s", store["city"], cat, e)
    return rows


def _scrape_sweedpos(store: dict, idx: int) -> list[dict]:
    rows: list[dict] = []
    try:
        category_ids = sweedpos.get_category_ids(store["domain"], store["base_path"])
    except Exception as e:
        log.warning("SweedPOS %s category fetch: %s", store["city"], e)
        return rows
    for cat_name, cat_id in category_ids.items():
        if not any(cat_name.lower().startswith(p) for p in SWEEDPOS_CATEGORY_PREFIXES):
            continue
        try:
            for p in sweedpos.fetch_all_products(store["domain"], store["base_path"], cat_id):
                rows.extend(_normalize_sweedpos(p, idx, store, cat_name))
        except Exception as e:
            log.warning("SweedPOS %s/%s: %s", store["city"], cat_name, e)
    return rows


# ---------------------------------------------------------------------------
# Main scrape loop
# ---------------------------------------------------------------------------

def scrape_all(db: sqlite_utils.Database, config: dict[str, Any]) -> int:
    stores: list[dict[str, Any]] = json.loads(STORES_PATH.read_text())["stores"]
    selected = filter_stores(stores, config)
    notifications = config.get("notifications") if isinstance(config, dict) else {}
    if not isinstance(notifications, dict):
        notifications = {}
    iheartjane_text_fallback = bool(notifications.get("iheartjane_text_fallback", False))

    if not selected:
        console.print("[yellow]No stores matched the current filter. Check watch_config.json.[/yellow]")
        return 0

    console.print(f"[bold]Scraping {len(selected)} stores...[/bold]")
    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    total_rows = 0
    errors = 0

    for idx, store in selected:
        platform = store["platform"]
        label = f"{store['operator']} / {store['city']}"
        console.print(f"  [dim]{label}...[/dim]", end=" ")
        try:
            if platform == "iheartjane":
                rows = _scrape_iheartjane(
                    store,
                    idx,
                    iheartjane_text_fallback=iheartjane_text_fallback,
                )
            elif platform == "trulieve_rest":
                rows = _scrape_trulieve(store, idx)
            elif platform == "cresco_labs":
                rows = _scrape_cresco(store, idx)
            elif platform == "sweedpos":
                rows = _scrape_sweedpos(store, idx)
            else:
                console.print("[yellow]skipped[/yellow]")
                continue

            for r in rows:
                # Runtime metadata for diagnostics; never persist to snapshots.
                for k in [k for k in r.keys() if str(k).startswith("_meta_")]:
                    r.pop(k, None)
                r["scraped_at"] = scraped_at

            db["snapshots"].upsert_all(
                rows,
                pk=["registry_index", "product_id", "scraped_at"],
                alter=True,
            )
            total_rows += len(rows)
            console.print(f"[green]{len(rows)}[/green]")

        except Exception:
            errors += 1
            console.print("[red]ERROR[/red]")
            log.error("Failed %s:\n%s", label, traceback.format_exc())

    console.print(f"\n[bold]Done:[/bold] {total_rows} rows, {errors} errors")
    return total_rows


# ---------------------------------------------------------------------------
# Deal digest
# ---------------------------------------------------------------------------

DEAL_DIGEST_SQL = """
WITH ranked AS (
  SELECT *,
    LAG(discounted_price) OVER (
      PARTITION BY registry_index, product_id ORDER BY scraped_at
    ) AS prev_disc
  FROM snapshots
  WHERE registry_index IN ({placeholders})
)
SELECT *
FROM ranked
WHERE scraped_at = (SELECT MAX(scraped_at) FROM snapshots)
  AND discounted_price IS NOT NULL
  AND (prev_disc IS NULL OR discounted_price < prev_disc)
ORDER BY ROUND(100.0 * (price - discounted_price) / price, 1) DESC,
         discounted_price ASC
LIMIT {row_limit}
"""

CURRENT_DEALS_SQL = """
SELECT *
FROM snapshots
WHERE scraped_at = (SELECT MAX(scraped_at) FROM snapshots)
  AND registry_index IN ({placeholders})
  AND discounted_price IS NOT NULL
ORDER BY ROUND(100.0 * (price - discounted_price) / price, 1) DESC,
         discounted_price ASC
LIMIT {row_limit}
"""

DIGEST_QUERY_LIMIT = 500
DIGEST_RENDER_LIMIT = 100


def _pct_off(price: float | None, disc: float | None) -> float | None:
    if price and disc and price > 0:
        return round(100.0 * (price - disc) / price, 1)
    return None


def _pct_style(pct: float | None) -> str:
    if pct is None:
        return "dim"
    if pct >= 40:
        return "bold bright_red"
    if pct >= 25:
        return "bold yellow"
    return "green"


def _row_category_haystack(row: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("category", "subcategory"):
        value = str(row.get(key) or "").strip()
        if value:
            parts.append(value.casefold())
    return " / ".join(parts)


def _row_matches_category_needles(row: dict[str, Any], needles_cf: list[str]) -> bool:
    if not needles_cf:
        return True
    hay = _row_category_haystack(row)
    return any(needle in hay for needle in needles_cf)


def _ratio_min_thc_floor_for_row(
    row: dict[str, Any],
    default_floor: float | None,
    by_category: dict[str, float],
) -> float | None:
    if by_category:
        hay = _row_category_haystack(row)
        for needle_cf, floor in by_category.items():
            if needle_cf in hay:
                return floor
    return default_floor


def _notification_config(config: dict[str, Any]) -> dict[str, Any]:
    notifications = config.get("notifications") or {}
    unicorns = [str(x).strip() for x in notifications.get("unicorns", []) if str(x).strip()]
    aliases_raw = notifications.get("aliases") or {}
    aliases: dict[str, list[str]] = {}
    if isinstance(aliases_raw, dict):
        for key, value in aliases_raw.items():
            canon = str(key).strip()
            if not canon:
                continue
            if isinstance(value, list):
                vals = [str(v).strip() for v in value if str(v).strip()]
            elif value is None:
                vals = []
            else:
                vals = [str(value).strip()] if str(value).strip() else []
            aliases[canon] = vals
    else:
        log.warning("notifications.aliases should be an object; ignoring %r", type(aliases_raw).__name__)
    ignore_brands = [str(x).strip() for x in notifications.get("ignore_brands", []) if str(x).strip()]
    ratio_only_categories_raw = notifications.get("terp_ratio_only_categories") or []
    ratio_only_categories: list[str] = []
    if isinstance(ratio_only_categories_raw, list):
        ratio_only_categories = [
            str(x).strip() for x in ratio_only_categories_raw if str(x).strip()
        ]
    elif isinstance(ratio_only_categories_raw, str) and ratio_only_categories_raw.strip():
        ratio_only_categories = [ratio_only_categories_raw.strip()]
    elif ratio_only_categories_raw not in ({}, None, []):
        log.warning(
            "notifications.terp_ratio_only_categories should be a list (or string); ignoring %r",
            type(ratio_only_categories_raw).__name__,
        )
    ratio_min_thc_pct_raw = notifications.get("terp_ratio_min_thc_pct")
    ratio_min_thc_pct = _coerce_percent_scalar(ratio_min_thc_pct_raw)
    if ratio_min_thc_pct is not None and ratio_min_thc_pct < 0:
        log.warning("Ignoring negative notifications.terp_ratio_min_thc_pct=%r", ratio_min_thc_pct_raw)
        ratio_min_thc_pct = None
    ratio_min_thc_by_category_raw = notifications.get("terp_ratio_min_thc_by_category") or {}
    ratio_min_thc_by_category: dict[str, float] = {}
    if isinstance(ratio_min_thc_by_category_raw, dict):
        for raw_key, raw_val in ratio_min_thc_by_category_raw.items():
            key = str(raw_key).strip().casefold()
            if not key:
                continue
            parsed = _coerce_percent_scalar(raw_val)
            if parsed is None or parsed < 0:
                log.warning(
                    "Ignoring invalid notifications.terp_ratio_min_thc_by_category[%r]=%r",
                    raw_key,
                    raw_val,
                )
                continue
            ratio_min_thc_by_category[key] = parsed
    else:
        log.warning(
            "notifications.terp_ratio_min_thc_by_category should be an object; ignoring %r",
            type(ratio_min_thc_by_category_raw).__name__,
        )
    match_mode = str(notifications.get("match_mode", "contains")).strip().lower()
    show_unicorn_summary = bool(notifications.get("show_unicorn_summary", True))
    show_ignored_summary = bool(notifications.get("show_ignored_summary", False))
    terp_thresholds_raw = notifications.get("terp_thresholds") or {}
    terp_thresholds: dict[str, float] = {}
    if isinstance(terp_thresholds_raw, dict):
        for raw_key, raw_val in terp_thresholds_raw.items():
            key_text = str(raw_key).strip().casefold()
            if not key_text:
                continue
            if key_text == "terp_ratio":
                norm_key = "terp_ratio"
            elif key_text.startswith("terp_"):
                norm_key = key_text
            else:
                norm_key = f"terp_{key_text}"
            parsed = _coerce_percent_scalar(raw_val)
            if parsed is None:
                log.warning("Ignoring invalid terp threshold %r=%r", raw_key, raw_val)
                continue
            if parsed < 0:
                log.warning("Ignoring negative terp threshold %r=%r", raw_key, raw_val)
                continue
            terp_thresholds[norm_key] = parsed
    else:
        log.warning(
            "notifications.terp_thresholds should be an object; ignoring %r",
            type(terp_thresholds_raw).__name__,
        )
    if match_mode not in {"contains", "exact"}:
        log.warning("Unknown notifications.match_mode %r, using 'contains'", match_mode)
        match_mode = "contains"
    unicorn_needles: list[str] = []
    seen_needles: set[str] = set()
    unicorn_canonical_by_needle_cf: dict[str, str] = {}
    for needle in unicorns:
        key = needle.casefold()
        if key not in seen_needles:
            seen_needles.add(key)
            unicorn_needles.append(needle)
        unicorn_canonical_by_needle_cf[key] = needle
    for canon, variants in aliases.items():
        canon_cf = canon.casefold()
        if canon_cf not in unicorn_canonical_by_needle_cf:
            unicorn_canonical_by_needle_cf[canon_cf] = canon
        for needle in [canon, *variants]:
            key = needle.casefold()
            if key not in seen_needles:
                seen_needles.add(key)
                unicorn_needles.append(needle)
            unicorn_canonical_by_needle_cf[key] = canon
    return {
        "unicorns": unicorns,
        "aliases": aliases,
        "unicorn_needles": unicorn_needles,
        "unicorn_canonical_by_needle_cf": unicorn_canonical_by_needle_cf,
        "ignore_brands": ignore_brands,
        "terp_ratio_only_categories": [x.casefold() for x in ratio_only_categories],
        "terp_ratio_min_thc_pct": ratio_min_thc_pct,
        "terp_ratio_min_thc_by_category": ratio_min_thc_by_category,
        "match_mode": match_mode,
        "show_unicorn_summary": show_unicorn_summary,
        "show_ignored_summary": show_ignored_summary,
        "terp_thresholds": terp_thresholds,
    }


def _match_unicorn_canonical(
    name: str,
    unicorns: list[str],
    canonical_by_needle_cf: dict[str, str],
    match_mode: str,
) -> str | None:
    if not name or not unicorns:
        return None
    hay = name.casefold()
    if match_mode == "exact":
        return canonical_by_needle_cf.get(hay)
    for needle in unicorns:
        needle_cf = needle.casefold()
        if needle_cf in hay:
            return canonical_by_needle_cf.get(needle_cf, needle)
    return None


def _infer_terp_source_label(row: dict[str, Any]) -> tuple[str, bool]:
    """Return (display label, is_known_blind_source) for digest audit reporting."""
    platform = str(row.get("platform") or "")
    if platform == "iheartjane" and int(row.get("terp_structured_parsed") or 0) != 0:
        return ("iHeartJane Lab Results", False)
    if platform == "iheartjane" and int(row.get("terp_text_parsed") or 0) != 0:
        return ("iHeartJane Text Parse", False)
    if platform == "cresco_labs":
        return ("Cresco API", False)
    if platform == "sweedpos":
        return ("Sweed LabTests", False)
    if platform == "trulieve_rest":
        return ("Trulieve Structured", False)
    if platform == "iheartjane":
        return ("iHeartJane", True)
    return (platform or "Unknown", False)


def _infer_terp_parse_confidence(row: dict[str, Any]) -> str | None:
    """Runtime-only confidence marker for audit/reporting (not persisted as a schema field)."""
    platform = str(row.get("platform") or "")
    if platform == "iheartjane":
        if int(row.get("terp_structured_parsed") or 0) != 0:
            return "high"
        if int(row.get("terp_text_parsed") or 0) != 0:
            return "high"
        return None
    if platform in {"cresco_labs", "trulieve_rest"}:
        return "high"
    if platform == "sweedpos":
        # Sweed currently often exposes names without numeric terp percentages in this dataset shape.
        return None
    return None


def _render_data_quality_audit(db: sqlite_utils.Database, selected_indices: list[int]) -> None:
    if not selected_indices:
        return
    ph = ",".join("?" * len(selected_indices))
    audit_sql = f"""
    SELECT platform,
           COALESCE(terp_structured_parsed, 0) AS terp_structured_parsed,
           COALESCE(terp_text_parsed, 0) AS terp_text_parsed,
           COUNT(*) AS total_items,
           SUM(CASE WHEN terp_total IS NOT NULL THEN 1 ELSE 0 END) AS terp_rich_items,
           SUM(CASE WHEN COALESCE(terp_names_present, 0) != 0 THEN 1 ELSE 0 END) AS terp_name_items
    FROM snapshots
    WHERE scraped_at = (SELECT MAX(scraped_at) FROM snapshots)
      AND registry_index IN ({ph})
    GROUP BY platform, COALESCE(terp_structured_parsed, 0), COALESCE(terp_text_parsed, 0)
    ORDER BY total_items DESC, platform ASC
    """
    rows = list(db.query(audit_sql, selected_indices))
    if not rows:
        return

    jane_diag_sql = f"""
    SELECT
      SUM(
        CASE
          WHEN platform = 'iheartjane'
           AND COALESCE(terp_structured_parsed, 0) = 0
           AND COALESCE(terp_text_parsed, 0) = 0
           AND COALESCE(terp_names_present, 0) != 0
          THEN 1 ELSE 0
        END
      ) AS jane_names_only,
      SUM(
        CASE
          WHEN platform = 'iheartjane'
           AND COALESCE(terp_structured_parsed, 0) = 0
           AND COALESCE(terp_text_parsed, 0) = 0
           AND COALESCE(terp_names_present, 0) = 0
          THEN 1 ELSE 0
        END
      ) AS jane_blind,
      SUM(
        CASE
          WHEN platform = 'iheartjane'
           AND COALESCE(terp_structured_parsed, 0) = 0
           AND COALESCE(terp_text_parsed, 0) != 0
           AND terp_total IS NULL
          THEN 1 ELSE 0
        END
      ) AS jane_text_partial_or_malformed
    FROM snapshots
    WHERE scraped_at = (SELECT MAX(scraped_at) FROM snapshots)
      AND registry_index IN ({ph})
    """
    jane_diag = next(iter(db.query(jane_diag_sql, selected_indices)), {}) or {}
    jane_names_only = int(jane_diag.get("jane_names_only") or 0)
    jane_blind = int(jane_diag.get("jane_blind") or 0)
    jane_text_partial_or_malformed = int(jane_diag.get("jane_text_partial_or_malformed") or 0)

    audit = Table(title="Data Quality Audit", box=box.SIMPLE, header_style="dim cyan")
    audit.add_column("Source")
    audit.add_column("Items", justify="right")
    audit.add_column("Terp Coverage", justify="right")
    audit.add_column("Notes", style="dim")
    for r in rows:
        label, known_blind = _infer_terp_source_label(r)
        confidence = _infer_terp_parse_confidence(r)
        count = int(r.get("total_items") or 0)
        covered = int(r.get("terp_rich_items") or 0)
        name_items = int(r.get("terp_name_items") or 0)
        pct = (100.0 * covered / count) if count else 0.0
        note_parts = []
        if confidence:
            note_parts.append(f"CONF: {confidence.upper()}")
        if name_items > 0 and covered == 0:
            note_parts.append("NAMES ONLY (no % values in payload)")
        if known_blind and covered == 0 and name_items == 0:
            note_parts.append("BLIND")
        if label == "iHeartJane" and (jane_names_only or jane_blind):
            note_parts.append(
                f"fallback misses: names-only={jane_names_only}, blind={jane_blind}"
            )
        if label == "iHeartJane Text Parse" and count > covered:
            note_parts.append(
                "text misses: "
                f"{count - covered} (likely partial/malformed numeric={jane_text_partial_or_malformed})"
            )
        note = " | ".join(note_parts)
        audit.add_row(
            label,
            str(count),
            f"{covered}/{count} ({pct:.0f}%)",
            note,
        )
    console.print(audit)


def render_digest(db: sqlite_utils.Database, config: dict[str, Any]) -> None:
    if "snapshots" not in db.table_names():
        console.print("[yellow]No data yet. Run without --digest first.[/yellow]")
        return

    stores = json.loads(STORES_PATH.read_text())["stores"]
    selected_indices = [idx for idx, _ in filter_stores(stores, config)]
    if not selected_indices:
        console.print("[yellow]No stores matched config filter.[/yellow]")
        return

    ph = ",".join("?" * len(selected_indices))
    run_count = db.execute("SELECT COUNT(DISTINCT scraped_at) FROM snapshots").fetchone()[0]
    use_new = run_count >= 2
    notif = _notification_config(config)
    show_data_quality_audit = notif["show_ignored_summary"]

    sql = (DEAL_DIGEST_SQL if use_new else CURRENT_DEALS_SQL).format(
        placeholders=ph,
        row_limit=DIGEST_QUERY_LIMIT,
    )
    rows = list(db.query(sql, selected_indices))

    if not rows:
        console.print("[dim]No deals in latest snapshot.[/dim]")
        if show_data_quality_audit:
            _render_data_quality_audit(db, selected_indices)
        return

    ignore_brands_cf = {b.casefold() for b in notif["ignore_brands"]}
    unicorn_needles = notif["unicorn_needles"]
    unicorn_canonical_by_needle_cf = notif["unicorn_canonical_by_needle_cf"]
    match_mode = notif["match_mode"]
    show_unicorn_summary = notif["show_unicorn_summary"]
    show_ignored_summary = notif["show_ignored_summary"]
    terp_thresholds = notif["terp_thresholds"]
    ratio_only_categories = notif["terp_ratio_only_categories"]
    ratio_min_thc_pct = notif["terp_ratio_min_thc_pct"]
    ratio_min_thc_by_category = notif["terp_ratio_min_thc_by_category"]
    show_data_quality_audit = show_ignored_summary

    filtered_rows: list[dict[str, Any]] = []
    ignored_count = 0
    terp_reject_count = 0
    terp_reject_missing_count = 0
    terp_reject_below_count = 0
    terp_reject_guardrail_count = 0
    terp_reject_by_platform: dict[str, int] = {}
    unicorn_count = 0
    ignored_brand_summary: dict[str, dict[str, Any]] = {}
    unicorn_summary: dict[str, dict[str, Any]] = {}
    for r in rows:
        op = str(r.get("operator") or "")
        city = str(r.get("city") or "")
        name = str(r.get("name") or "")
        brand = r.get("brand")
        platform_name = str(r.get("platform") or "unknown")
        brand_text = str(brand or "").strip()
        if brand_text and brand_text.casefold() in ignore_brands_cf:
            ignored_count += 1
            if show_ignored_summary:
                entry = ignored_brand_summary.setdefault(
                    brand_text,
                    {"count": 0, "stores": set(), "sample_names": []},
                )
                entry["count"] += 1
                entry["stores"].add(f"{op} / {city}")
                if len(entry["sample_names"]) < 3 and name:
                    entry["sample_names"].append(str(name)[:42])
            continue

        terp_total = _coerce_percent_scalar(r.get("terp_total"))
        thc_pct = _coerce_percent_scalar(r.get("thc_pct"))
        terp_ratio = (terp_total / thc_pct) if (terp_total is not None and thc_pct and thc_pct > 0) else None

        if terp_thresholds:
            reject = False
            reject_reason = "below"
            for key, min_val in terp_thresholds.items():
                if key == "terp_ratio":
                    if ratio_only_categories and not _row_matches_category_needles(r, ratio_only_categories):
                        reject = True
                        reject_reason = "guardrail"
                        break
                    row_ratio_min_thc = _ratio_min_thc_floor_for_row(
                        r,
                        ratio_min_thc_pct,
                        ratio_min_thc_by_category,
                    )
                    if row_ratio_min_thc is not None:
                        if thc_pct is None:
                            reject = True
                            reject_reason = "missing"
                            break
                        if thc_pct < row_ratio_min_thc:
                            reject = True
                            reject_reason = "guardrail"
                            break
                    if terp_ratio is None:
                        reject = True
                        reject_reason = "missing"
                        break
                    if terp_ratio < min_val:
                        reject = True
                        reject_reason = "below"
                        break
                    continue
                val = _coerce_percent_scalar(r.get(key))
                if val is None:
                    reject = True
                    reject_reason = "missing"
                    break
                if val < min_val:
                    reject = True
                    reject_reason = "below"
                    break
            if reject:
                terp_reject_count += 1
                if reject_reason == "missing":
                    terp_reject_missing_count += 1
                elif reject_reason == "below":
                    terp_reject_below_count += 1
                else:
                    terp_reject_guardrail_count += 1
                terp_reject_by_platform[platform_name] = terp_reject_by_platform.get(platform_name, 0) + 1
                continue

        unicorn_canonical = _match_unicorn_canonical(
            name,
            unicorn_needles,
            unicorn_canonical_by_needle_cf,
            match_mode,
        )
        is_unicorn = unicorn_canonical is not None
        if unicorn_canonical:
            unicorn_count += 1
            entry = unicorn_summary.setdefault(
                unicorn_canonical,
                {"count": 0, "sample_names": []},
            )
            entry["count"] += 1
            if len(entry["sample_names"]) < 3 and name:
                entry["sample_names"].append(str(name)[:42])
        filtered_rows.append(
            {
                "row": r,
                "is_unicorn": is_unicorn,
                "unicorn_canonical": unicorn_canonical,
                "terp_total": terp_total,
                "terp_ratio": terp_ratio,
            }
        )

    if not filtered_rows:
        if ignored_count or terp_reject_count:
            reasons = []
            if ignored_count:
                reasons.append("ignored brands")
            if terp_reject_count:
                reasons.append("terp thresholds")
            console.print(f"[dim]Deals were found, but all were filtered by {', '.join(reasons)}.[/dim]")
        else:
            console.print("[dim]No deals in latest snapshot.[/dim]")
        if show_ignored_summary and ignored_brand_summary:
            summary = Table(
                title=f"Ignored Brand Summary ({ignored_count} filtered items)",
                box=box.SIMPLE,
                header_style="dim red",
            )
            summary.add_column("Brand")
            summary.add_column("Ignored Deals", justify="right")
            summary.add_column("Stores", justify="right")
            summary.add_column("Examples", style="dim")
            for brand_name, meta in sorted(
                ignored_brand_summary.items(),
                key=lambda item: (-item[1]["count"], item[0].casefold()),
            ):
                summary.add_row(
                    brand_name,
                    str(meta["count"]),
                    str(len(meta["stores"])),
                    ", ".join(meta["sample_names"]) or "—",
                )
            console.print(summary)
        if show_data_quality_audit:
            _render_data_quality_audit(db, selected_indices)
        return

    final_rows = filtered_rows[:DIGEST_RENDER_LIMIT]

    title_extra = f" — {unicorn_count} 🦄 Found" if unicorn_count else ""
    title = ("New & Improved Deals" if use_new else "All Current Deals") + title_extra
    table = Table(title=f"PA Dispensary Deal Digest — {title}",
                  box=box.ROUNDED, header_style="bold cyan",
                  border_style="dim", show_header=True)
    table.add_column("Store", style="dim", no_wrap=True)
    table.add_column("Product", ratio=4)
    table.add_column("Brand", style="dim")
    table.add_column("Subcat.", style="dim", no_wrap=True)
    table.add_column("Size", justify="right", style="dim", no_wrap=True)
    table.add_column("THC%", justify="right", style="dim")
    table.add_column("Terps", justify="right", style="bold yellow", no_wrap=True)
    table.add_column("Was", justify="right", style="dim red")
    table.add_column("Now", justify="right", style="bold green")
    table.add_column("% Off", justify="right", no_wrap=True)
    table.add_column("Special", style="dim italic")

    for item in final_rows:
        r = item["row"]
        is_unicorn = item["is_unicorn"]
        unicorn_canonical = item["unicorn_canonical"]
        op = str(r.get("operator") or "")
        city = str(r.get("city") or "")
        name = str(r.get("name") or "")
        brand = r.get("brand")
        cat = r.get("category")
        subcat = r.get("subcategory")
        size_label = str(r.get("unit_size_label") or "")
        price = _coerce_percent_scalar(r.get("price"))
        disc = _coerce_percent_scalar(r.get("discounted_price"))
        special = r.get("special_title")
        thc = _coerce_percent_scalar(r.get("thc_pct"))
        pct = _pct_off(price, disc)
        product_label = f"🦄 {name}" if is_unicorn else (name or "")
        product_cell = Text(
            str(product_label)[:62],
            style="bold magenta" if is_unicorn else "",
        )
        if is_unicorn and unicorn_canonical and unicorn_canonical.casefold() not in name.casefold():
            product_cell.append(f" ({unicorn_canonical})", style="dim magenta")
        terp_total = item["terp_total"]
        terp_ratio = item["terp_ratio"]
        if terp_total is None:
            terp_cell = "—"
        elif terp_ratio is None:
            terp_cell = f"{terp_total:.1f}%"
        else:
            terp_cell = f"{terp_total:.1f}% ({terp_ratio:.2f})"
        table.add_row(
            f"{op}\n{city}",
            product_cell,
            (brand or "")[:18],
            (subcat or cat or "")[:14],
            size_label or "—",
            f"{thc:.1f}%" if thc else "—",
            terp_cell,
            f"${price:.2f}" if price else "—",
            f"${disc:.2f}" if disc else "—",
            Text(f"{pct:.1f}%" if pct else "—", style=_pct_style(pct)),
            (special or "")[:28],
        )

    console.print(table)
    if show_unicorn_summary and unicorn_summary:
        u_table = Table(
            title=f"Unicorn Hits ({unicorn_count} total)",
            box=box.SIMPLE,
            header_style="bold magenta",
        )
        u_table.add_column("Canonical Unicorn")
        u_table.add_column("Hits", justify="right")
        u_table.add_column("Examples", style="dim")
        for canon_name, meta in sorted(
            unicorn_summary.items(),
            key=lambda item: (-item[1]["count"], item[0].casefold()),
        ):
            u_table.add_row(
                canon_name,
                str(meta["count"]),
                ", ".join(meta["sample_names"]) or "—",
            )
        console.print(u_table)

    if show_ignored_summary and ignored_brand_summary:
        summary = Table(
            title=f"Ignored Brand Summary ({ignored_count} filtered items)",
            box=box.SIMPLE,
            header_style="dim red",
        )
        summary.add_column("Brand")
        summary.add_column("Ignored Deals", justify="right")
        summary.add_column("Stores", justify="right")
        summary.add_column("Examples", style="dim")
        for brand_name, meta in sorted(
            ignored_brand_summary.items(),
            key=lambda item: (-item[1]["count"], item[0].casefold()),
        ):
            summary.add_row(
                brand_name,
                str(meta["count"]),
                str(len(meta["stores"])),
                ", ".join(meta["sample_names"]) or "—",
            )
        console.print(summary)

    if show_data_quality_audit:
        _render_data_quality_audit(db, selected_indices)

    if terp_reject_count:
        t_table = Table(
            title=f"Terp Filter Rejects ({terp_reject_count} rows)",
            box=box.SIMPLE,
            header_style="dim yellow",
        )
        t_table.add_column("Platform")
        t_table.add_column("Rejected", justify="right")
        for platform_name, count in sorted(
            terp_reject_by_platform.items(),
            key=lambda item: (-item[1], item[0]),
        ):
            t_table.add_row(platform_name, str(count))
        console.print(t_table)

    city_count = len({str(item["row"].get("city") or "") for item in final_rows})
    console.print(
        f"[dim]{len(final_rows)} deal{'s' if len(final_rows) != 1 else ''} "
        f"across {city_count} stores"
        f" (queried {len(rows)}, filtered {ignored_count} ignored-brand rows, "
        + (
            f"filtered {terp_reject_count} by terp thresholds, "
            if terp_reject_count
            else ""
        )
        + f"{unicorn_count} unicorn match{'es' if unicorn_count != 1 else ''}; "
        + (
            f"terp thresholds: {', '.join(f'{k}>={v:g}' for k, v in terp_thresholds.items())}; "
            if terp_thresholds
            else ""
        )
        + (
            "ratio guardrails: "
            + ", ".join(
                part for part in [
                    (
                        "cats="
                        + "/".join(ratio_only_categories)
                    ) if ratio_only_categories else "",
                    (
                        f"min_thc>={ratio_min_thc_pct:g}"
                    ) if ratio_min_thc_pct is not None else "",
                    (
                        "cat_thc_overrides="
                        + str(len(ratio_min_thc_by_category))
                    ) if ratio_min_thc_by_category else "",
                ] if part
            )
            + "; "
            if terp_thresholds.get("terp_ratio") is not None and (
                ratio_only_categories or ratio_min_thc_pct is not None or ratio_min_thc_by_category
            )
            else ""
        )
        + (
            f"terp rejects missing={terp_reject_missing_count}, below={terp_reject_below_count}, guardrail={terp_reject_guardrail_count}; "
            if terp_reject_count
            else ""
        )
        + f"showing top {len(final_rows)})[/dim]"
    )


# ---------------------------------------------------------------------------
# List matching stores
# ---------------------------------------------------------------------------

def list_stores(config: dict[str, Any]) -> None:
    stores = json.loads(STORES_PATH.read_text())["stores"]
    selected = filter_stores(stores, config)
    table = Table(title="Stores matching current config", box=box.SIMPLE,
                  header_style="bold cyan")
    table.add_column("idx", justify="right", style="dim")
    table.add_column("Operator")
    table.add_column("City")
    table.add_column("Zip")
    table.add_column("Platform", style="dim")
    for idx, s in selected:
        table.add_row(str(idx), s["operator"], s["city"],
                      s.get("zip", "—"), s["platform"])
    console.print(table)
    console.print(f"[dim]{len(selected)} stores[/dim]")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description="PA dispensary deal tracker")
    parser.add_argument("--scrape", action="store_true", help="Scrape only")
    parser.add_argument("--digest", action="store_true", help="Digest only")
    parser.add_argument("--list", action="store_true", help="List matching stores")
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--config", default=str(CONFIG_PATH))
    args = parser.parse_args()

    config_path = Path(args.config)
    if config_path.exists():
        config = json.loads(config_path.read_text())
    else:
        console.print(f"[dim]No config found at {config_path}. Using 'all' mode.[/dim]")
        console.print(f"[dim]Create watch_config.json to filter by zip radius or store list.[/dim]")
        config = DEFAULT_CONFIG

    if args.list:
        list_stores(config)
        return

    db = sqlite_utils.Database(args.db)
    do_scrape = not args.digest
    do_digest = not args.scrape

    if do_scrape:
        scrape_all(db, config)
    if do_digest:
        render_digest(db, config)


if __name__ == "__main__":
    main()
