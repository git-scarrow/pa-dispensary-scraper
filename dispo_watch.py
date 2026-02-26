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

Config: watch_config.json (see example below)

Filter modes:
  radius  — scrape stores within N miles of a zip code
  stores  — scrape an explicit list by registry_index
  all     — scrape every supported store (default if no config)

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
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sqlite_utils
from rich.console import Console
from rich.table import Table
from rich import box

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

DEFAULT_CONFIG: dict[str, Any] = {"filter": {"mode": "all"}}

EXAMPLE_CONFIG = """\
{
  "_help": "Filter modes: 'radius', 'stores', or 'all'",
  "filter": {
    "mode": "radius",
    "center_zip": "19002",
    "radius_miles": 30
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

def _normalize_iheartjane(product: dict, idx: int, store: dict) -> dict:
    return {
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
        "discounted_price": product.get("discounted_price"),
        "special_title": product.get("special_title") or "",
        "thc_pct": product.get("percent_thc"),
    }


def _normalize_trulieve(product: dict, idx: int, store: dict, category: str) -> dict:
    base_price = product.get("med_unit_price") or product.get("unit_price")
    sale_price = None
    for v in product.get("variants") or []:
        sp = v.get("sale_unit_price")
        if sp and sp != base_price:
            sale_price = sp
            break
    specials = product.get("specials") or []
    special_title = specials[0].get("title", "") if specials else ""
    thc = product.get("thc_content")
    thc_pct = float(thc) if thc and product.get("thc_content_unit") == "%" else None
    return {
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
        "discounted_price": float(sale_price) if sale_price else None,
        "special_title": special_title,
        "thc_pct": thc_pct,
    }


def _normalize_cresco(product: dict, idx: int, store: dict, category: str) -> dict:
    sku = product.get("sku") or {}
    sku_product = sku.get("product") or {}
    brand = (sku_product.get("brand") or {}).get("name") or product.get("brand") or ""
    name = sku_product.get("name") or sku.get("name") or product.get("name") or ""
    base_price = product.get("price")
    disc_price = product.get("discounted_price")
    special = (product.get("applied_special") or {}).get("special_name") or ""
    thc_raw = product.get("bt_potency_thc") or (product.get("potency") or {}).get("thc")
    return {
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
        "discounted_price": float(disc_price) if disc_price and disc_price != base_price else None,
        "special_title": special,
        "thc_pct": float(thc_raw) if thc_raw else None,
    }


def _normalize_sweedpos(product: dict, idx: int, store: dict, category_name: str) -> list[dict]:
    brand = (product.get("brand") or {}).get("name") or ""
    name = product.get("name") or ""
    base_id = str(product.get("id") or "")
    rows = []
    for variant in product.get("variants") or [{}]:
        vid = variant.get("id") or ""
        product_id = f"{base_id}:{vid}" if vid else base_id
        price = variant.get("price")
        promo = variant.get("promoPrice")
        promos = variant.get("promos") or []
        special_title = promos[0].get("shortName", "") if promos else ""
        lab = variant.get("labTests") or {}
        thc_data = lab.get("thc") or lab.get("displayThc") or {}
        thc_vals = thc_data.get("value") or []
        thc_unit = thc_data.get("unitAbbr") or "%"
        thc_pct = float(thc_vals[0]) if thc_vals and thc_unit == "%" else None
        vname = variant.get("name") or ""
        rows.append({
            "registry_index": idx,
            "operator": store["operator"],
            "city": store["city"],
            "platform": "sweedpos",
            "product_id": product_id,
            "name": f"{name} — {vname}" if vname else name,
            "brand": brand,
            "category": category_name,
            "subcategory": (product.get("subcategory") or {}).get("name") or "",
            "price": float(price) if price is not None else None,
            "discounted_price": float(promo) if promo and promo != price else None,
            "special_title": special_title,
            "thc_pct": thc_pct,
        })
    return rows


# ---------------------------------------------------------------------------
# Per-platform scrape dispatchers
# ---------------------------------------------------------------------------

def _scrape_iheartjane(store: dict, idx: int) -> list[dict]:
    products = iheartjane.fetch_all_products(int(store["jane_store_id"]))
    return [_normalize_iheartjane(p, idx, store) for p in products]


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
                rows = _scrape_iheartjane(store, idx)
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
  SELECT
    registry_index, operator, city, name, brand, category, subcategory,
    price, discounted_price, special_title, thc_pct, scraped_at,
    LAG(discounted_price) OVER (
      PARTITION BY registry_index, product_id ORDER BY scraped_at
    ) AS prev_disc
  FROM snapshots
  WHERE registry_index IN ({placeholders})
)
SELECT operator, city, name, brand, category, subcategory,
       price, discounted_price, special_title, thc_pct, prev_disc
FROM ranked
WHERE scraped_at = (SELECT MAX(scraped_at) FROM snapshots)
  AND discounted_price IS NOT NULL
  AND (prev_disc IS NULL OR discounted_price < prev_disc)
ORDER BY ROUND(100.0 * (price - discounted_price) / price, 1) DESC,
         discounted_price ASC
LIMIT 100
"""

CURRENT_DEALS_SQL = """
SELECT operator, city, name, brand, category, subcategory,
       price, discounted_price, special_title, thc_pct
FROM snapshots
WHERE scraped_at = (SELECT MAX(scraped_at) FROM snapshots)
  AND registry_index IN ({placeholders})
  AND discounted_price IS NOT NULL
ORDER BY ROUND(100.0 * (price - discounted_price) / price, 1) DESC,
         discounted_price ASC
LIMIT 100
"""


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

    sql = (DEAL_DIGEST_SQL if use_new else CURRENT_DEALS_SQL).format(placeholders=ph)
    rows = list(db.execute(sql, selected_indices).fetchall())

    if not rows:
        console.print("[dim]No deals in latest snapshot.[/dim]")
        return

    from rich.text import Text

    title = "New & Improved Deals" if use_new else "All Current Deals"
    table = Table(title=f"PA Dispensary Deal Digest — {title}",
                  box=box.ROUNDED, header_style="bold cyan",
                  border_style="dim", show_header=True)
    table.add_column("Store", style="dim", no_wrap=True)
    table.add_column("Product", ratio=4)
    table.add_column("Brand", style="dim")
    table.add_column("Subcat.", style="dim", no_wrap=True)
    table.add_column("THC%", justify="right", style="dim")
    table.add_column("Was", justify="right", style="dim red")
    table.add_column("Now", justify="right", style="bold green")
    table.add_column("% Off", justify="right", no_wrap=True)
    table.add_column("Special", style="dim italic")

    col_count = 11 if use_new else 10
    for r in rows:
        if use_new:
            op, city, name, brand, cat, subcat, price, disc, special, thc, prev_disc = r
        else:
            op, city, name, brand, cat, subcat, price, disc, special, thc = r
        pct = _pct_off(price, disc)
        table.add_row(
            f"{op}\n{city}",
            name[:60],
            (brand or "")[:18],
            (subcat or cat or "")[:14],
            f"{thc:.1f}%" if thc else "—",
            f"${price:.2f}" if price else "—",
            f"${disc:.2f}" if disc else "—",
            Text(f"{pct:.1f}%" if pct else "—", style=_pct_style(pct)),
            (special or "")[:28],
        )

    console.print(table)
    console.print(f"[dim]{len(rows)} deal{'s' if len(rows) != 1 else ''} "
                  f"across {len({r[1] for r in rows})} stores[/dim]")


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
