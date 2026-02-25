# SweedPOS API

**Status: FULLY CRACKED**
SweedPOS serves-side renders menu data into `window.__sw_qc`. No auth, no direct API calls needed.

## Scraping Mechanism

```
GET https://{domain}/{basePath}/menu?filters={"category":[id]}&page=N
```

1. Parse `window.__sw_qc` from the SSR HTML response (embedded JS variable)
2. `__sw_qc.queries` → find key `/Products/GetProductList` → `state.data`
3. 24 products per page; `total` field gives total count; `ceil(total/24)` = page count
4. Repeat for each page and each category

### Category IDs

Category IDs are **store-specific** — fetch from SSR cache key `/Products/GetProductCategoryList`.

Apothecarium PA example:
```
Flower=257, Cartridges=255, Concentrate=256,
Accessories=260, Wellness=258, Disposable=259, Troches=3311
```

### Store ID Lookup (if needed for direct API calls)

```
POST https://{domain}/_api/Route/Resolve
Body: {"url": "https://{domain}/{basePath}"}
→ {storeId, basePath}
```
SSR scraping does **not** need the storeId.

## Product Schema

```json
{
  "id": 117139,
  "name": "Grape (Indica)",
  "description": "...",
  "category": {"id": 3311, "name": "Troches"},
  "subcategory": null,
  "images": ["https://media.sweedpos.com/..."],
  "brand": {"name": "Valhalla"},
  "strain": {
    "name": "Indica",
    "prevalence": {"name": "Indica"},
    "terpenes": [{"name": "Myrcene"}, ...],
    "flavors": []
  },
  "effects": [{"name": "Relaxed"}, ...],
  "productType": {"name": "Troche"},
  "variants": [{
    "id": 162864,
    "name": "100mg/10pk High Dose",
    "sku": "Val-Wel-Tro-",
    "labTests": {
      "thc": {"value": [1000], "unitAbbr": "MG"},
      "cbd": {"value": [0], "unitAbbr": "MG"},
      "displayThc": {"value": [1000], "unitAbbr": "MG", "label": "THC"}
    },
    "availableQty": 206,
    "unitSize": {"value": 100, "unitAbbr": "MG"},
    "unitsInPackage": 10,
    "price": 65,
    "promoPrice": 26,
    "promos": [{"id": 106415, "shortName": "30% OFF", ...}],
    "stockType": "Default"
  }]
}
```

### Response Envelope

```json
{
  "page": 1, "pageSize": 24, "total": 898,
  "filters": [...],
  "sortingMethods": [...],
  "list": [...]
}
```

## All 6 PA Apothecarium Locations (shop.apothecarium.com)

| Name | City | basePath | storeId |
|---|---|---|---|
| Plymouth Meeting | Plymouth Meeting | `pm` | 19 |
| Thorndale | Thorndale | `thorndale` | 20 |
| Lancaster | Lancaster | `lancaster` | 21 |
| Bethlehem | Bethlehem | `bethlehem` | 22 |
| Allentown | Allentown | `allentown` | 23 |
| Stroudsburg | Stroudsburg | `stroudsburg` | 24 |

## Other PA SweedPOS Operators

- Zen Leaf MD — suspected SweedPOS; needs verification
- More TBD — check PA MMJ licensees
