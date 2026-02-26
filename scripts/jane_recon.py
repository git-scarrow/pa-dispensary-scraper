#!/usr/bin/env python3
"""
jane_recon.py — Assessment tool for iHeartJane data quality.
"""
import json
import re
import time
from pathlib import Path

from rich.console import Console
from rich.table import Table

from adapters import iheartjane

ROOT = Path(__file__).resolve().parent.parent
STORES_PATH = ROOT / "data" / "stores.json"
console = Console()


def analyze_store(store: dict) -> dict | None:
    store_id = int(store["jane_store_id"])
    operator = store["operator"]
    city = store["city"]

    console.print(f"[dim]Probing {operator} {city} ({store_id})...[/dim]", end=" ")

    try:
        products = iheartjane.fetch_all_products(store_id)
        sample = []
        for i, p in enumerate(products):
            if i >= 50:
                break
            sample.append(p)

        if not sample:
            console.print("[red]EMPTY[/red]")
            return None

        stats = {
            "count": len(sample),
            "structured": 0,  # non-empty 'terpenes' list
            "desc_hits": 0,   # terp names in description
            "potency_obj": 0  # rare schema variant
        }

        terp_pattern = re.compile(
            r"(myrcene|caryophyllene|limonene|terpinolene|linalool)",
            re.IGNORECASE,
        )

        for p in sample:
            if p.get("terpenes") and len(p.get("terpenes")) > 0:
                stats["structured"] += 1

            desc = p.get("description") or ""
            if terp_pattern.search(desc):
                stats["desc_hits"] += 1

            if isinstance(p.get("potency"), dict) and len(p.get("potency")) > 2:
                stats["potency_obj"] += 1

        console.print(f"[green]OK ({len(sample)} items)[/green]")
        return stats

    except Exception as e:
        console.print(f"[red]FAIL: {e}[/red]")
        return None


def main() -> None:
    stores = json.loads(STORES_PATH.read_text())["stores"]
    jane_stores = [s for s in stores if s["platform"] == "iheartjane"]

    console.print(f"[bold]Starting iHeartJane Recon on {len(jane_stores)} stores...[/bold]")

    results = []

    for s in jane_stores:
        data = analyze_store(s)
        if data:
            results.append({
                "store": f"{s['operator']} {s['city']}",
                "total": data["count"],
                "struct_pct": (data["structured"] / data["count"]) * 100,
                "desc_pct": (data["desc_hits"] / data["count"]) * 100,
                "potency_pct": (data["potency_obj"] / data["count"]) * 100,
            })
        time.sleep(1.0)  # Be polite

    table = Table(title="iHeartJane Terpene Source Analysis", title_style="bold magenta")
    table.add_column("Store")
    table.add_column("Sample", justify="right")
    table.add_column("Structured %", justify="right", style="cyan")
    table.add_column("Desc Hints %", justify="right", style="yellow")
    table.add_column("Potency Obj %", justify="right", style="dim")
    table.add_column("Verdict", style="bold")

    for r in results:
        verdict = "[red]BLIND[/red]"
        if r["struct_pct"] > 50:
            verdict = "[green]STRUCTURED[/green]"
        elif r["desc_pct"] > 50:
            verdict = "[yellow]TEXT PARSE[/yellow]"
        elif r["struct_pct"] > 0 or r["desc_pct"] > 0:
            verdict = "[dim]MIXED[/dim]"

        table.add_row(
            r["store"],
            str(r["total"]),
            f"{r['struct_pct']:.1f}%",
            f"{r['desc_pct']:.1f}%",
            f"{r['potency_pct']:.1f}%",
            verdict,
        )

    console.print(table)


if __name__ == "__main__":
    main()
