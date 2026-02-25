# Liberty Cannabis / Dutchie Embedded Menu

**Status: PARTIAL** — stores mapped, GraphQL query schema not yet cracked.

## Platform Details

- Operator: Liberty Cannabis (Holistic Industries)
- Site: `libertycannabis.com`
- Menu engine: Dutchie Embedded Menu (iframe embed)
- Parent company: Holistic Industries (also operates in CA, MD, MA, MI, NJ)
- Brands: Strane, Garcia Hand Picked (house brands)

## Architecture

```
libertycannabis.com/shop/{city}/
  └── <iframe src="https://dutchie.com/embedded-menu/liberty-{city}/">
        └── loads https://dutchie.com/api/v2/embedded-menu/{token}.js
              └── app queries https://api.dutchie.com/graphql
```

The embed token is unique per store and acts as the store identifier for GraphQL queries.

## Embed Token Discovery

Navigate to `libertycannabis.com/shop/{city}/` and intercept network requests for
`dutchie.com/api/v2/embedded-menu/*.js` — the filename without `.js` is the token.

## Cracking the GraphQL API (TODO)

```
POST https://api.dutchie.com/graphql
Content-Type: application/json

{
  "query": "...",   ← needs recon
  "variables": {
    "retailerId": "...",   ← derived from embed token
    ...
  }
}
```

To crack: load `dutchie.com/embedded-menu/liberty-norristown/` in a browser,
intercept XHR/fetch to `api.dutchie.com/graphql`, capture query body and variables.

## All 6 PA Liberty Cannabis Locations

| City | Dutchie slug | Embed token |
|---|---|---|
| Aliquippa | liberty-aliquippa | 5e9491bc8c76410099c8fa05 |
| Bensalem | liberty-bensalem | 3TJq5LSGc9j76WKx4 |
| Cranberry Township | liberty-cranberry-township | 60523818a6b5d500e0fb2e31 |
| Norristown | liberty-norristown | Tfr3uEztEfzqLT2xB |
| Philadelphia | liberty-philadelphia | 27eaLRYjHyMPZqigT |
| Pittsburgh | liberty-pittsburgh | 63dab2d8ab202100548dbaf5 |

Shop URL pattern: `libertycannabis.com/shop/{city}/`
Category URL params: `?dtche%5Bcategory%5D={category}` (e.g. `flower`, `vaporizers`, `concentrates`)
