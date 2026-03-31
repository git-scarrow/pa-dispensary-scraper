"""
iHeartJane API adapter.
Docs: ../docs/iheartjane.md

Covers: Verilife (9 stores), Rise/GTI (17), Insa PA (1), Vytal Options (6)

The old /v1/stores/{id}/menu endpoint is dead (404 as of 2026-02).
Full menu data is served by the Algolia search index:
  POST https://search.iheartjane.com/1/indexes/menu-products-production/query
  App ID: VFM4X0N23A  |  API Key: edc5435c65d771cecbd98bbd488aa8d3 (public)

dmerch API (still used):
  Bootstrap:     GET  https://api.iheartjane.com/v1/bootstrap  (sets jdid cookie)
  Featured rows: POST https://dmerch.iheartjane.com/v2/multi   (type=featured, ~50 curated items)
"""
import requests

BASE = "https://api.iheartjane.com/v1"
DMERCH_BASE = "https://dmerch.iheartjane.com"
ALGOLIA_BASE = "https://search.iheartjane.com"

# Shared across all Jane-powered operator sites (from jane-app-settings script tag)
DM_SDK_API_KEY = "ce5f15c9-3d09-441d-9bfd-26e87aff5925"
DM_SDK_VERSION = "2.14.0"
ALGOLIA_APP_ID = "VFM4X0N23A"
ALGOLIA_API_KEY = "edc5435c65d771cecbd98bbd488aa8d3"
ALGOLIA_INDEX = "menu-products-production"

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"
HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.verilife.com",
    "Referer": "https://www.verilife.com/",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "cross-site",
}

ALGOLIA_HEADERS = {
    "User-Agent": UA,
    "X-Algolia-Application-Id": ALGOLIA_APP_ID,
    "X-Algolia-API-Key": ALGOLIA_API_KEY,
    "Content-Type": "application/json",
}


def _get_session() -> requests.Session:
    """Return a requests.Session with a bootstrapped jdid cookie."""
    session = requests.Session()
    session.headers.update(HEADERS)
    # Bootstrap sets the jdid cookie required by dmerch
    session.get(f"{BASE}/bootstrap", timeout=10)
    return session


def _dmerch_url(path: str) -> str:
    return (
        f"{DMERCH_BASE}/{path}"
        f"?jdm_source=ecomm"
        f"&jdm_version={DM_SDK_VERSION}"
        f"&jdm_api_key={DM_SDK_API_KEY}"
    )


def fetch_menu(store_id: int, page: int = 1, per_page: int = 50) -> dict:
    """Fetch one page of menu products via Algolia.

    Returns a dict with:
      products (list), total_products, page, per_page, has_more
    """
    algolia_page = max(0, page - 1)  # Algolia pages are 0-indexed
    r = requests.post(
        f"{ALGOLIA_BASE}/1/indexes/{ALGOLIA_INDEX}/query",
        headers=ALGOLIA_HEADERS,
        json={"params": f"filters=store_id%3D{store_id}&hitsPerPage={per_page}&page={algolia_page}"},
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()

    products = [_normalize_product(hit) for hit in data.get("hits", [])]
    total = data.get("nbHits", len(products))
    nb_pages = data.get("nbPages", 1)

    return {
        "products": products,
        "total_products": total,
        "page": page,
        "per_page": per_page,
        "has_more": algolia_page + 1 < nb_pages,
    }


def fetch_all_products(store_id: int) -> list[dict]:
    """Fetch all menu products for a store via Algolia (single request)."""
    r = requests.post(
        f"{ALGOLIA_BASE}/1/indexes/{ALGOLIA_INDEX}/query",
        headers=ALGOLIA_HEADERS,
        json={"params": f"filters=store_id%3D{store_id}&hitsPerPage=1000&page=0"},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    return [_normalize_product(hit) for hit in data.get("hits", [])]


def fetch_store_info(store_id: int) -> dict:
    r = requests.get(f"{BASE}/stores/{store_id}", headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


def _normalize_product(sa: dict) -> dict:
    """Normalize an Algolia hit (or dmerch search_attributes) to a standard product shape."""
    # Determine price: prefer per-gram for carts/extracts, per-each otherwise
    price = (
        sa.get("price_gram")
        or sa.get("price_each")
        or sa.get("price_half_gram")
        or sa.get("bucket_price")
    )
    unit_size_g = None
    unit_size_label = ""
    if sa.get("price_half_gram") is not None:
        unit_size_g = 0.5
        unit_size_label = "0.5g"
    elif sa.get("price_gram") is not None:
        unit_size_g = 1.0
        unit_size_label = "1g"
    # iHeartJane's discounted_price_* fields reflect a marketing "original price"
    # reduced to the shelf price — the discount is already baked into price_*.
    # Surfacing discounted_price_* as a real discount double-applies the promo.
    # We keep special_title/special_amount as informational metadata only.

    return {
        "objectID": sa.get("objectID"),
        "product_id": sa.get("product_id"),
        "store_id": sa.get("store_id"),
        "name": sa.get("name"),
        "brand": sa.get("brand"),
        "kind": sa.get("kind"),
        "kind_subtype": sa.get("kind_subtype"),
        "root_subtype": sa.get("root_subtype"),
        "percent_thc": sa.get("percent_thc"),
        "percent_cbd": sa.get("percent_cbd"),
        "price": price,
        "discounted_price": None,
        "unit_size_g": unit_size_g,
        "unit_size_label": unit_size_label,
        "special_title": sa.get("special_title") or "",
        "special_amount": sa.get("special_amount") or "",
        "description": sa.get("description"),
        "image_urls": sa.get("image_urls", []),
        "feelings": sa.get("feelings", []),
        "terpenes": sa.get("terpenes", []),
        # Preserve structured lab payload for downstream terp parsing in dispo_watch.
        "lab_results": sa.get("lab_results", []),
    }
