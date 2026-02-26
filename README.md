# PA Dispensary Menu Scraper

Research project to scrape live menu data (prices, THC%, inventory) from Pennsylvania
medical marijuana dispensaries. Each operator uses one of a handful of backend platforms —
crack the platform once, scrape all operators on it.

## Platform Map

| Platform | Operators | PA Stores | Status | Doc |
|---|---|---|---|---|
| Trulieve REST | Trulieve | 21 | ✅ Cracked | [docs/trulieve.md](docs/trulieve.md) |
| SweedPOS SSR | Apothecarium (TerrAscend) | 6 | ✅ Cracked | [docs/sweedpos.md](docs/sweedpos.md) |
| Cresco Labs API | Sunnyside | 18 | ✅ Cracked | [docs/sunnyside.md](docs/sunnyside.md) |
| iHeartJane | Verilife, Rise/GTI, Insa, Vytal Options | 33 | ✅ Cracked | [docs/iheartjane.md](docs/iheartjane.md) |
| Dutchie Embedded | Liberty Cannabis | 6 | ⚠️ Partial (GraphQL TBD) | [docs/liberty-dutchie.md](docs/liberty-dutchie.md) |
| Unknown | Cannabist, Beyond Hello, Ethos, Maitri, Terrapin, Harvest | ? | 🔲 Pending recon | — |

**Total cracked: ~84 PA stores across 5 platforms.**

## Store Registry

All known stores with platform IDs: [`data/stores.json`](data/stores.json)

## Quick API Reference

### Trulieve
```
GET https://api.trulieve.com/api/v2/menu/{dutchie_plus_id}/{category}/DEFAULT?page=1
```
No auth. Categories: `flower`, `vapes`, `concentrates`, `edibles`, `tinctures`, `topicals`, `pre-rolls`.

### Sunnyside (Cresco Labs)
```
GET https://api.crescolabs.com/p/inventory/op/fifo-inventory?category=flower&limit=50&offset=0
Headers: x-api-key, store_id (header!), ordering_app_id
```
⚠️ `store_id` is a **request header**, not a query param.

### iHeartJane (Verilife / Rise / Insa / Vytal)
```
GET https://api.iheartjane.com/v1/stores/{store_id}/menu?page=1&per_page=50
```
No auth. Cloudflare-protected — use Python `requests` with Firefox UA, not headless browser.

### SweedPOS (Apothecarium)
```
GET https://shop.apothecarium.com/{basePath}/menu?filters={"category":[id]}&page=1
```
Parse `window.__sw_qc` from SSR HTML. No auth, no API calls needed.

### Liberty Cannabis (Dutchie Embedded)
```
iframe: https://dutchie.com/embedded-menu/liberty-{city}/
token:  GET https://dutchie.com/api/v2/embedded-menu/{token}.js
api:    POST https://api.dutchie.com/graphql  ← GraphQL, schema TBD
```

## Remaining Work

- [ ] Crack Dutchie GraphQL query schema for Liberty menus
- [ ] Confirm iHeartJane menu response schema from live call
- [ ] Playwright recon: Cannabist, Beyond Hello, Ethos, Maitri, Terrapin, Harvest
- [ ] Find other PA SweedPOS operators
- [ ] Build Python adapters (see `adapters/`)
- [ ] Normalization layer (weight → grams, dose → mg, price → USD, THC → %)
- [ ] Scheduler + storage

## MCP Server (for Agents)

This repo now includes an MCP server at [`mcp_server.py`](mcp_server.py) that exposes:

- Local resources: project README, registry summary, full `data/stores.json`, and platform docs in `docs/`
- Buddy agent resource: `pa://buddy/profile` for persona + safety + keystone-green alignment
- Registry tools: list/search stores, get store by `registry_index`, platform capability metadata
- Scraper tools: dispatch to the existing adapters (`trulieve`, `sunnyside`, `iheartjane`, `sweedpos`) using a store selected from the local registry
- Dutchie recon helper: fetch Liberty embedded bootstrap JS (GraphQL menu scraping is still not cracked)
- Buddy guidelines tool: `get_buddy_guidelines(mode?)` for structured persona/mode/safety/tooling rules

### Install

```bash
pip install -r requirements.txt
```

### Run (stdio transport)

```bash
python mcp_server.py
```

This is the typical mode for MCP agent clients that spawn the server as a subprocess.

### Optional HTTP transport

```bash
python mcp_server.py --transport streamable-http
```

### Typical Agent Flow

1. Call `list_stores` to find a store and capture its `registry_index`
2. Call `get_store` to inspect required IDs/fields
3. Call `scrape_store_menu` for the platform
4. For SweedPOS, call `sweedpos_get_category_ids` first to get category IDs

## Buddy Agent Profile (Prompt + Skill Scaffold)

This repo includes a first-pass Buddy profile for a cannabis-savvy, stoner-friendly agent that is also technically aligned with the PA scraper workflow:

- Prompt: [`prompts/buddy_system.md`](prompts/buddy_system.md)
- Profile doc: [`docs/buddy-profile.md`](docs/buddy-profile.md)
- Local agent skill (ignored by git in many setups): `.agents/skills/buddy-persona/SKILL.md`

Buddy is designed to:
- answer cannabis education/product questions (flower, carts, edibles, concentrates, extraction, terpenes, cannabinoids)
- use harm-reduction framing and avoid medical claims
- follow `keystone-green` scraping rules when asked to fetch live PA menu data

## Daily Deal Watcher

`dispo_watch.py` is a local CLI for scheduled PA dispensary price snapshots and a terminal deal digest.

Features:
- Scrapes supported stores (iHeartJane, Trulieve, Sunnyside/Cresco, SweedPOS; skips Liberty/Dutchie)
- Normalizes product rows into a local SQLite database (`prices.db`)
- Prints a Rich terminal digest of current deals and price drops
- Supports filtering by:
  - all stores
  - explicit `registry_index` list
  - radius around a ZIP code (with cached geocoding in `data/zip_cache.json`)

Usage:

```bash
python dispo_watch.py            # scrape + digest
python dispo_watch.py --digest   # digest only
python dispo_watch.py --scrape   # scrape only
python dispo_watch.py --list     # list stores matching current config
```

Config:
- Optional local `watch_config.json` (ignored by git) for `all`, `stores`, or `radius` filter mode.
- See inline example in `dispo_watch.py`.

### 🧬 iHeartJane Hybrid Parsing
The scraper uses a hybrid chain to maximize terpene coverage for iHeartJane stores:
1. **Structured Lab Results (Priority):** If the store exposes raw lab data in the Algolia payload (`lab_results`), the watcher parses terpene compounds from that structured source.
2. **Text Fallback (Optional):** If structured data is missing, the watcher can parse description text for explicit `Terpene: %` patterns.
   - Enable in `watch_config.json`: `"iheartjane_text_fallback": true`

Systemd user timer:
- Templates included: [`dispo_watch.service`](dispo_watch.service), [`dispo_watch.timer`](dispo_watch.timer)
- Default timer runs daily at `07:00` with randomized delay.
