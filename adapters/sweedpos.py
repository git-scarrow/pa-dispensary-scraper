"""
SweedPOS SSR scraping adapter.
Docs: ../docs/sweedpos.md
"""
import json
import math
import re
import requests

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"


def _extract_js_assigned_json_object(html: str, variable: str) -> str:
    """Extract a JSON object assigned to a JS variable using brace balancing.

    More robust than a regex-only capture when the same <script> contains
    additional JS after the object literal.
    """
    assign_match = re.search(rf"{re.escape(variable)}\s*=\s*\{{", html)
    if not assign_match:
        raise ValueError(f"{variable} not found in SSR HTML")

    start = html.find("{", assign_match.start())
    if start == -1:
        raise ValueError(f"{variable} assignment object start not found")

    depth = 0
    in_string = False
    escape = False
    quote_char = ""
    for i in range(start, len(html)):
        ch = html[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote_char:
                in_string = False
            continue

        if ch in ('"', "'"):
            in_string = True
            quote_char = ch
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return html[start : i + 1]

    raise ValueError(f"{variable} assignment object not terminated")

def _get_sw_qc(domain: str, base_path: str, category_id: int, page: int = 1) -> dict:
    url = f"https://{domain}/{base_path}/menu"
    params = {"filters": json.dumps({"category": [category_id]}), "page": page}
    r = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=15)
    r.raise_for_status()
    sw_qc_json = _extract_js_assigned_json_object(r.text, "window.__sw_qc")
    return json.loads(sw_qc_json)


def _iter_queries(sw_qc: dict):
    """Yield (query_key, query_entry) from __sw_qc where queries may be dict or list."""
    queries = sw_qc.get("queries", {})
    if isinstance(queries, dict):
        yield from queries.items()
        return
    if isinstance(queries, list):
        for item in queries:
            if not isinstance(item, dict):
                continue
            qkey = item.get("queryKey")
            if isinstance(qkey, list):
                qkey = " ".join(str(x) for x in qkey)
            elif qkey is None:
                qkey = str(item.get("queryHash") or "")
            yield str(qkey), item
        return

def _extract_product_list(sw_qc: dict) -> dict:
    for key, val in _iter_queries(sw_qc):
        if "/Products/GetProductList" in key:
            return val.get("state", {}).get("data", {})
    raise ValueError("/Products/GetProductList not found in __sw_qc")

def get_category_ids(domain: str, base_path: str) -> dict[str, int]:
    """Return {category_name: id} from SSR cache."""
    sw_qc = _get_sw_qc(domain, base_path, category_id=0)  # load page without filter
    for key, val in _iter_queries(sw_qc):
        if "/Products/GetProductCategoryList" in key:
            cats = val.get("state", {}).get("data", [])
            return {c["name"]: c["id"] for c in cats}
    return {}

def _normalize_product(p: dict) -> dict:
    """Normalize a raw SweedPOS product to a standard shape.

    THC/CBD are nested at variants[0].labTests.{thc,cbd}.value[0].
    Price and promo info are also in variants[0].
    """
    variant = (p.get("variants") or [{}])[0]
    lab = variant.get("labTests") or {}

    thc_block = lab.get("thc") or lab.get("displayThc") or {}
    thc_vals = thc_block.get("value") or []
    percent_thc = thc_vals[0] if thc_vals else None

    cbd_block = lab.get("cbd") or {}
    cbd_vals = cbd_block.get("value") or []
    percent_cbd = cbd_vals[0] if cbd_vals else None

    price = variant.get("price")
    # promoPrice is the shelf price re-derived from a marketing "original" —
    # the discount is already baked into the displayed price.  Ignore it.

    promos = variant.get("promos") or []
    special_title = promos[0].get("shortName", "") if promos else ""

    brand = (p.get("brand") or {}).get("name")
    strain = p.get("strain") or {}
    strain_type = (strain.get("prevalence") or {}).get("name")
    terpenes = [t["name"] for t in (strain.get("terpenes") or []) if t.get("name")]
    product_type = (p.get("productType") or {}).get("name")

    return {
        "id": p.get("id"),
        "name": p.get("name"),
        "brand": brand,
        "kind": "vape",
        "kind_subtype": product_type,
        "strain_type": strain_type,
        "percent_thc": percent_thc,
        "percent_cbd": percent_cbd,
        "price": price,
        "discounted_price": None,
        "special_title": special_title,
        "terpenes": terpenes,
        "description": p.get("description"),
        "image_urls": p.get("images") or [],
    }


def fetch_all_products(domain: str, base_path: str, category_id: int) -> list[dict]:
    products = []
    sw_qc = _get_sw_qc(domain, base_path, category_id, page=1)
    data = _extract_product_list(sw_qc)
    total = data.get("total", 0)
    products.extend(data.get("list", []))
    pages = math.ceil(total / 24)
    for page in range(2, pages + 1):
        sw_qc = _get_sw_qc(domain, base_path, category_id, page)
        data = _extract_product_list(sw_qc)
        products.extend(data.get("list", []))
    return [_normalize_product(p) for p in products]
