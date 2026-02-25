# Trulieve REST API

**Status: FULLY CRACKED**
Trulieve wraps a Dutchie Plus backend but exposes their own REST API — no auth required.

## Menu Endpoint

```
GET https://api.trulieve.com/api/v2/menu/{dutchie_plus_id}/{category}/DEFAULT
  ?page=1
  &sort_by=default
  &search=
  &weights=
  &brand=
  &strain_type=
  &subcategory=
  &cbd_max=&cbd_min=&thc_max=&thc_min=
  &tags_menu=&collections=&special=
```

- No auth required
- 50 products per page; check `last_page` for pagination
- `dutchie_plus_id` = numeric store ID (see store table below)

### Category Slugs
`flower`, `vapes`, `concentrates`, `edibles`, `tinctures`, `topicals`, `accessories`, `pre-rolls`

### Pricing Type
Append `/DEFAULT`, `/MED`, or `/REC` to the URL path.

## Product Schema

Key fields returned per product:

```
id, store_id, product_id (Dutchie UUID), name, description,
category, image_url, quantity,
unit_price, med_unit_price, rec_unit_price,
strain, strain_type (INDICA/SATIVA/HYBRID),
brand, source ("dutchie"),
thc_content, cbd_content, thc_content_unit, cbd_content_unit,
subcategory, staff_pick, menu_type,
effects, slug, sku,
variants[], specials[], store{}, images[]
```

- `variants[]`: `[{option: "3.5g", unit_price: 26, sale_unit_price: null}]`
- `specials[]`: active deals attached to the product
- `unit_price` is in **USD dollars** (not cents)

## Store ID Discovery

From the Next.js page data:
```
GET https://www.trulieve.com/_next/data/{buildId}/en/dispensaries/pennsylvania/{slug}.json
→ pageProps.stores[0].dutchie_embed_script_url  →  contains "dutchieplus=N"
```

## All 21 PA Trulieve Locations

| Name | City | Zip | dutchie_plus_id | dutchie_retailer_id |
|---|---|---|---|---|
| Camp Hill | Camp Hill | 17011 | 88 | 5fcd803c0d0a250106ded129 |
| Coatesville | Coatesville | 19320 | 92 | 628414ba169240008cb1b334 |
| Cranberry Township | Cranberry Township | 16066 | 87 | 5fcd8074fa3cf100cb2b7098 |
| Harrisburg | Harrisburg | 17110 | 89 | 5fcd8098bdf7ed00ebdb7320 |
| Johnstown | Johnstown | 15901 | 74 | 5fcd80bede6523010411865e |
| King of Prussia | King of Prussia | 19406 | 106 | 6274290112704c0237179a91 |
| Lancaster | Lancaster | 17601 | 76 | 678fe516c772da375168e3ad |
| Limerick | Limerick | 19468 | 109 | 6453cc9ec5742b0055cb3531 |
| Philadelphia (Packer Ave) | Philadelphia | 19148 | 107 | 627428c500f2d04cdad66a81 |
| Philadelphia (Center City) | Philadelphia | 19107 | 104 | 6169ba6f97abf100ab976dd6 |
| Philadelphia (Washington Square) | Philadelphia | 19147 | 85 | 60d24e689655f600b5ca1bc2 |
| Pittsburgh (North Shore) | Pittsburgh | 15212 | 90 | 6090306bd43b6e00c28cf0e5 |
| Pittsburgh (Squirrel Hill) | Pittsburgh | 15217 | 86 | 627429962262fd6c7c3dbbb2 |
| Reading (5th Street) | Reading | 19605 | 84 | 5fcd8116cf099200b819fd12 |
| Reading (Lancaster Ave) | Reading | 19611 | 80 | 5fcd815e51b1c000ec2f25a3 |
| Scranton | Scranton | 18505 | 108 | 5fcd8181656f8f00bc07dc8b |
| Washington | Washington | 15301 | 71 | 6272e32719bbdc008a71bb98 |
| Whitehall | Whitehall | 18052 | 79 | 609456326bf69800d1533fc2 |
| Wilkes-Barre | Wilkes-Barre Township | 18702 | 97 | 667581d71356d84114cfcc05 |
| York | York | 17402 | 81 | 5ed197666ccfd20132a5d574 |
| Zelienople | Zelienople | 16063 | 103 | 62742963973a624cc8dad488 |
