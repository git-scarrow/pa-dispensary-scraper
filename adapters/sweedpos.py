"""
SweedPOS SSR scraping adapter.
Docs: ../docs/sweedpos.md
"""
import json
import math
import re
import requests

UA = "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"

def _get_sw_qc(domain: str, base_path: str, category_id: int, page: int = 1) -> dict:
    url = f"https://{domain}/{base_path}/menu"
    params = {"filters": json.dumps({"category": [category_id]}), "page": page}
    r = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=15)
    r.raise_for_status()
    match = re.search(r'window\.__sw_qc\s*=\s*(\{.+?\});\s*</script>', r.text, re.DOTALL)
    if not match:
        raise ValueError("window.__sw_qc not found in SSR HTML")
    return json.loads(match.group(1))

def _extract_product_list(sw_qc: dict) -> dict:
    for key, val in sw_qc.get("queries", {}).items():
        if "/Products/GetProductList" in key:
            return val.get("state", {}).get("data", {})
    raise ValueError("/Products/GetProductList not found in __sw_qc")

def get_category_ids(domain: str, base_path: str) -> dict[str, int]:
    """Return {category_name: id} from SSR cache."""
    sw_qc = _get_sw_qc(domain, base_path, category_id=0)  # load page without filter
    for key, val in sw_qc.get("queries", {}).items():
        if "/Products/GetProductCategoryList" in key:
            cats = val.get("state", {}).get("data", [])
            return {c["name"]: c["id"] for c in cats}
    return {}

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
    return products
