"""
Trulieve REST API adapter.
Docs: ../docs/trulieve.md
"""
import requests

BASE = "https://api.trulieve.com/api/v2/menu"
CATEGORIES = ["flower", "vapes", "concentrates", "edibles", "tinctures", "topicals", "pre-rolls"]

def fetch_menu(store_id: int, category: str, page: int = 1, pricing: str = "DEFAULT") -> dict:
    url = f"{BASE}/{store_id}/{category}/{pricing}"
    r = requests.get(url, params={"page": page, "sort_by": "default"}, timeout=15)
    r.raise_for_status()
    return r.json()

def fetch_all_products(store_id: int, category: str) -> list[dict]:
    products = []
    page = 1
    while True:
        data = fetch_menu(store_id, category, page)
        items = data.get("data")
        if not isinstance(items, list):
            items = data.get("products", [])
        products.extend(items)
        if page >= data.get("last_page", 1):
            break
        page += 1
    return products
