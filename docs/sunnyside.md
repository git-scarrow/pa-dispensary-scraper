# Sunnyside / Cresco Labs API

**Status: FULLY CRACKED**
Sunnyside (Cresco Labs retail) uses a proprietary REST API with header-based auth.

## Auth Headers (ALL Requests)

```
x-api-key: hE1gQuwYcO54382jYNH0c9W0w4fEC3dJ8ljnwVau
store_id: <numeric_store_id>           ← REQUEST HEADER, not query param!
ordering_app_id: 9ha3c289-1260-4he2-nm62-4598bca34naa
x-client-version: 4.31.0
Origin: https://www.sunnyside.shop
Referer: https://www.sunnyside.shop/
User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36
```

> **CRITICAL:** `store_id` goes in the HTTP **request header**, NOT the query string.
> Without it, the API returns `{rows: 0, data: []}`.

> **CRITICAL (updated 2026-02):** `Origin`, `Referer`, and `User-Agent` headers are now
> required. Omitting them returns **401 Unauthorized**.

## Inventory Endpoint

```
GET https://api.crescolabs.com/p/inventory/op/fifo-inventory
  ?category={cat}
  &inventory_type=retail
  &require_sellable_quantity=true
  &include_specials=true
  &sellable=true
  &order_by=brand
  &limit=50
  &offset=0
  &usage_type=medical
  &hob_first=true
  &include_filters=true
  &include_facets=true
```

- Categories: `flower`, `vapes`, `concentrates`, `edibles`, `tinctures`, `topicals`, `capsules`, `accessories`
- Paginate by incrementing `offset` by `limit`
- Response: `{rows, total_rows, data: [...]}`
- `usage_type`: `medical` or `recreational`

## Key Product Schema Fields

```
id, name, brand, product_id, strain_id, ecomm_display_name,
bt_potency_thc, bt_potency_thca, bt_potency_cbd, bt_potency_cbda, bt_potency_terps,
remaining_qty_med, remaining_qty_rec,
sellable_quantity_med, sellable_quantity_rec,
price, post_tax_price, discounted_price, discounted_post_tax_price, price_med,
usage_type, store_id,
sku: {
  id, name,
  product: {id, name, category, strain_type, sub_category, weight, weight_in_g,
            description, image_urls, brand: {id, name, company}},
  strain: {id, name, strain_type, effects, terpenes}
},
potency: {thc, thca, cbd, cbda, total_terps, b_myrcene, limonene, b_caryophyllene, ...},
specials: [...], applied_special: {...}, batch_count
```

## Store Lookup

```
GET https://api.crescolabs.com/p/stores?ids={comma_list}&order_by=city
```
No `store_id` header needed (public endpoint).
Returns: `{id, name, city, state, zip, address, store_slug, lat, lng, is_med_menu, is_rec_menu, ...}`

## All 18 PA Sunnyside Locations

| City | Zip | store_id | store_slug |
|---|---|---|---|
| Altoona | 16602 | 816 | altoona-pa |
| Ambler | 19002 | 650 | ambler-pa |
| Beaver Falls | 15010 | 964 | beaver-falls-pa |
| Butler | 16001 | 202 | butler-pa |
| Erie | 16509 | 785 | erie-pa |
| Gettysburg | 17325 | 814 | gettysburg-pa |
| Greensburg | 15601 | 898 | greensburg-pa |
| Lancaster | 17601 | 633 | lancaster-pa |
| Montgomeryville | 18936 | 636 | montgomeryville-pa |
| New Kensington | 15068 | 229 | new-kensington-pa |
| Philadelphia (Chestnut) | 19107 | 619 | philadelphia-pa |
| Philadelphia (City Ave) | 19131 | 634 | philadelphia-city-ave-pa |
| Phoenixville | 19460 | 635 | phoenixville-pa |
| Pittsburgh (Penn Ave) | 15222 | 203 | pittsburgh-pa |
| Pittsburgh (Lawrenceville) | 15201 | 899 | pittsburgh-lawrenceville-pa |
| Somerset | 15501 | 815 | somerset-pa |
| Washington | 15301 | 813 | washington-pa |
| Wyomissing | 19610 | 624 | wyomissing-pa |
