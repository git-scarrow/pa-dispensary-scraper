"""
Dutchie Embedded Menu adapter (Liberty Cannabis).
Docs: ../docs/liberty-dutchie.md

STATUS: INCOMPLETE — GraphQL query schema not yet cracked.
The embed token per store is known; the GraphQL query body is not.
"""
import requests

GRAPHQL_URL = "https://api.dutchie.com/graphql"
EMBED_BASE = "https://dutchie.com/embedded-menu"

# Known embed tokens per Liberty PA store
LIBERTY_PA_TOKENS = {
    "aliquippa":          "5e9491bc8c76410099c8fa05",
    "bensalem":           "3TJq5LSGc9j76WKx4",
    "cranberry-township": "60523818a6b5d500e0fb2e31",
    "norristown":         "Tfr3uEztEfzqLT2xB",
    "philadelphia":       "27eaLRYjHyMPZqigT",
    "pittsburgh":         "63dab2d8ab202100548dbaf5",
}

def fetch_embed_bootstrap(token: str) -> str:
    """Fetch the embed bootstrap JS to inspect what retailerId it sets."""
    r = requests.get(
        f"https://dutchie.com/api/v2/embedded-menu/{token}.js",
        headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"},
        timeout=15,
    )
    r.raise_for_status()
    return r.text

def graphql_query(query: str, variables: dict, retailer_token: str) -> dict:
    """
    Execute a Dutchie GraphQL query.
    TODO: capture query body + variables from browser devtools on the Liberty embed.
    """
    r = requests.post(
        GRAPHQL_URL,
        json={"query": query, "variables": variables},
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
            # Additional headers (auth tokens etc.) likely needed — TBD from recon
        },
        timeout=15,
    )
    r.raise_for_status()
    return r.json()

# TODO: implement fetch_menu() once GraphQL schema is known
