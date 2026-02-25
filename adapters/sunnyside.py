"""
Sunnyside / Cresco Labs API adapter.
Docs: ../docs/sunnyside.md

CRITICAL: store_id is a request HEADER, not a query param.
"""
import requests

BASE = "https://api.crescolabs.com"
API_KEY = "hE1gQuwYcO54382jYNH0c9W0w4fEC3dJ8ljnwVau"
ORDERING_APP_ID = "9ha3c289-1260-4he2-nm62-4598bca34naa"
CATEGORIES = ["flower", "vapes", "concentrates", "edibles", "tinctures", "topicals", "capsules", "accessories"]

def _headers(store_id: int) -> dict:
    return {
        "x-api-key": API_KEY,
        "store_id": str(store_id),
        "ordering_app_id": ORDERING_APP_ID,
        "x-client-version": "4.31.0",
        "Origin": "https://www.sunnyside.shop",
        "Referer": "https://www.sunnyside.shop/",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    }

def fetch_inventory(store_id: int, category: str, offset: int = 0, usage_type: str = "medical") -> dict:
    r = requests.get(
        f"{BASE}/p/inventory/op/fifo-inventory",
        headers=_headers(store_id),
        params={
            "category": category,
            "inventory_type": "retail",
            "require_sellable_quantity": "true",
            "include_specials": "true",
            "sellable": "true",
            "order_by": "brand",
            "limit": 50,
            "offset": offset,
            "usage_type": usage_type,
            "hob_first": "true",
            "include_filters": "true",
            "include_facets": "true",
        },
        timeout=15,
    )
    r.raise_for_status()
    return r.json()

def fetch_all_products(store_id: int, category: str) -> list[dict]:
    products = []
    offset = 0
    while True:
        data = fetch_inventory(store_id, category, offset)
        products.extend(data.get("data", []))
        if offset + 50 >= data.get("total_rows", 0):
            break
        offset += 50
    return products
