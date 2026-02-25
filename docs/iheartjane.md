# iHeartJane API

**Status: FULLY CRACKED**
iHeartJane is a shared menu platform used by Verilife, Rise/GTI, Insa PA, and Vytal Options.
One adapter covers 33 PA stores across 4 operators.

## Architecture (updated 2026-02)

The old `/v1/stores/{id}/menu` REST endpoint is **dead** (404 as of 2026-02). The platform
migrated to two parallel systems:

1. **Algolia** — full product catalog search with per-store pricing. Used by the adapter for full menus.
2. **Digital Merch (dmerch)** — curated/featured menu rows (~50 items). Used for quick featured views.

### Algolia Full Menu (primary)

```
POST https://search.iheartjane.com/1/indexes/menu-products-production/query
X-Algolia-Application-Id: VFM4X0N23A
X-Algolia-API-Key: edc5435c65d771cecbd98bbd488aa8d3
```

Request body (all products for one store, one page):
```json
{"params": "filters=store_id%3D{store_id}&hitsPerPage=1000&page=0"}
```

- Returns full product records with all pricing fields in one request
- `nbHits` = total products; `nbPages` = number of pages (typically 1 with hitsPerPage=1000)
- No bootstrap/cookie required

Key pricing fields on each hit: `price_gram`, `price_each`, `price_half_gram`, `bucket_price`,
`discounted_price_gram`, `discounted_price_each`, `discounted_price_half_gram`,
`special_title`, `special_amount`

Key classification fields: `kind`, `kind_subtype` (e.g. "Live Resin Cartridge", "Distillate Cartridge"),
`root_subtype` (e.g. "Cartridges", "Disposables"), `name`, `brand`, `percent_thc`, `percent_cbd`

### dmerch Bootstrap (still needed for featured rows)

```
GET https://api.iheartjane.com/v1/bootstrap
```

Sets a `jdid` (Jane device ID) cookie required by dmerch calls.

### dmerch Featured Rows (secondary, curated ~50 items)

```
POST https://dmerch.iheartjane.com/v2/multi
  ?jdm_source=ecomm
  &jdm_version=2.14.0
  &jdm_api_key=ce5f15c9-3d09-441d-9bfd-26e87aff5925
```

Request body:
```json
{
  "app_mode": "medical",
  "jane_device_id": "<jdid cookie>",
  "store_id": 2587,
  "num_columns": 4,
  "search_attributes": [],
  "type": "featured",
  "exclude_menu_top_row": false
}
```

Returns `placements[].products[].search_attributes` — only ~50 curated products per store.
`nb_hits` shows the full inventory size but the products list is truncated to featured rows.

### Credentials

All keys are **shared across all Jane-powered operator sites** (sourced from `jane-app-settings`
and `jane-app-secrets` script tags injected into each operator's menu page):

| Key | Value | Source tag |
|-----|-------|-----------|
| `dmSdkApiKey` | `ce5f15c9-3d09-441d-9bfd-26e87aff5925` | `jane-app-secrets` |
| `algoliaApiKey` | `edc5435c65d771cecbd98bbd488aa8d3` | `jane-app-secrets` |
| `algoliaAppId` | `VFM4X0N23A` | `jane-app-settings` |
| `dmSdkVersion` | `2.14.0` | `jane-app-secrets` |

Verified on: Verilife (`verilife.com`), Insa PA (`insa.com`), Vytal Options (`vytaloptions.com`).

## Access Pattern

```python
import requests

ALGOLIA_BASE = "https://search.iheartjane.com"
ALGOLIA_APP_ID = "VFM4X0N23A"
ALGOLIA_API_KEY = "edc5435c65d771cecbd98bbd488aa8d3"
ALGOLIA_INDEX = "menu-products-production"

def fetch_all_products(store_id: int) -> list[dict]:
    r = requests.post(
        f"{ALGOLIA_BASE}/1/indexes/{ALGOLIA_INDEX}/query",
        headers={
            "X-Algolia-Application-Id": ALGOLIA_APP_ID,
            "X-Algolia-API-Key": ALGOLIA_API_KEY,
            "Content-Type": "application/json",
        },
        json={"params": f"filters=store_id%3D{store_id}&hitsPerPage=1000&page=0"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["hits"]
```

## Store Info (unchanged)

```
GET https://api.iheartjane.com/v1/stores/{store_id}
```

Still works with standard headers. No auth required.

## Store ID Discovery Method

For operators whose main site is Cloudflare-protected (e.g. Rise), extract store IDs from
console CORS errors when loading the menu page in a headless browser:

```
[ERROR] Failed to load resource: net::ERR_FAILED
  @ https://api.iheartjane.com/v1/stores/{ID}?
```

The ID appears in the failed fetch URL. This works because the Jane embed JS fires the
API call before CORS blocks it — the URL is visible in the error.

## PA Store Registry

### Verilife (PharmaCann) — 9 stores

Site: `verilife.com` (Magento 2 CMS + iHeartJane embed)

| Location | Verilife slug | Jane store_id |
|---|---|---|
| Chester | chester | 2587 |
| Lancaster | lancaster | 3846 |
| Plymouth Meeting | plymouth-meeting | 3839 |
| Pottstown | pottstown | 3851 |
| Quakertown | quakertown | 3850 |
| Shamokin | shamokin | 2701 |
| South Philadelphia | south-philadelphia | 5835 |
| State College | state-college | 4415 |
| Williamsport | williamsport | 3052 |

### Rise Cannabis (GTI) — 17 stores

Site: `risecannabis.com` — Cloudflare managed challenge (curl + headless blocked).
Store IDs extracted from Wayback Machine CDX URL path enumeration.
URL pattern: `risecannabis.com/dispensaries/pennsylvania/{slug}/{jane_id}/medical-menu/`

| City | slug | Jane store_id |
|---|---|---|
| Carlisle | carlisle | 1547 |
| Chambersburg | chambersburg | 1867 |
| Cranberry | cranberry | 1575 |
| Duncansville | duncansville | 1961 |
| Erie | erie-lake | 392 |
| Grove City | grove-city | 5202 |
| Hermitage | hermitage | 1551 |
| King of Prussia | king-of-prussia | 1552 |
| Latrobe | latrobe | 1549 |
| Meadville | meadville | 2863 |
| Mechanicsburg | mechanicsburg | 1550 |
| Monroeville | monroeville | 2266 |
| New Castle | new-castle | 1545 |
| Philadelphia | philadelphia | 5383 |
| Steelton | steelton | 1544 |
| Warminster | warminster | 3404 |
| York | york | 1548 |

### Insa PA — 1 store

Site: `insa.com/pa/{location}/menu/` (WordPress + Jane Menu Plugin v1.4.7)
Note: `insa.com/stores/` runs Dutchie Plus for FL stores — completely separate.

| City | Jane store_id |
|---|---|
| Newtown Square | 6844 |

### Vytal Options — 6 stores

Site: `vytaloptions.com` (WordPress + Jane Menu Plugin v1.4.8)
Menu URL pattern: `vytaloptions.com/locations/{slug}/menu/`

| City | slug | Jane store_id |
|---|---|---|
| Fogelsville | fogelsville | 6079 |
| Harrisburg | harrisburg | 6078 |
| Kennett Square | kennett-square | 6080 |
| Lancaster | lancaster | 6081 |
| Lansdale | lansdale | 6082 |
| State College | state-college | 6083 |
