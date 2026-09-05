# ── NOT USED by the 2026 330i hunt ───────────────────────────────────────────
# Legacy dealer-discovery / debugging script, superseded by platform_census.py
# + build_master_dealers.py. Kept for reference; nothing in the 330i pipeline
# calls it. See docs/330I_DEAL_FINDER.md § "What was disabled".
# ─────────────────────────────────────────────────────────────────────────────
"""
sweep_ddc.py – Discover all BMW/MINI dealers on the Dealer.com (Cox Automotive) platform.

Strategy
--------
BMW and MINI each have an OEM-level "group" siteId on Dealer.com:
  • bmwgroup  – aggregates all BMW dealer inventory
  • minigroup – aggregates all MINI dealer inventory

Querying /api/widget/ws-inv-data/getInventory with one of those siteIds and
filtering by accountState returns the `accounts` dict for the dealers whose
vehicles appear on that page.  Iterating over every US state with a 100-item
page yields all dealers in one pass (~50 API calls total).

The script saves results to bmw_ddc_dealers.json and merges them into
dealers.json (same format used by sweep_dealers.py / discover_dealers.py).
"""

import json
import time
from collections import Counter

import requests

# ──────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────
DDC_ENDPOINT = "https://www.bmwofdallas.com/api/widget/ws-inv-data/getInventory"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/json",
    "Referer": "https://www.bmwofdallas.com/",
}

OEM_GROUPS = ["bmwgroup", "minigroup"]

US_STATES = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
]

DDC_OUT   = "bmw_ddc_dealers.json"
DEALERS_OUT = "dealers.json"

# ──────────────────────────────────────────────────────────────
# Core API helper
# ──────────────────────────────────────────────────────────────

def query_group(group_id: str, state: str, page_size: int = 100) -> dict[str, dict]:
    """Return the `accounts` dict from a single OEM-group + state query."""
    payload = {
        "siteId": group_id,
        "locale": "en_US",
        "device": "DESKTOP",
        "pageAlias": "INVENTORY_LISTING_DEFAULT_AUTO_ALL",
        "widgetName": "ws-inv-data",
        "inventoryParameters": {"accountState": state},
        "preferences": {"pageSize": str(page_size)},
    }
    try:
        r = requests.post(DDC_ENDPOINT, headers=HEADERS, json=payload, timeout=20)
        r.raise_for_status()
        data = r.json()
        return data.get("accounts", {})
    except Exception as exc:
        print(f"  WARN {group_id}/{state}: {exc}")
        return {}


# ──────────────────────────────────────────────────────────────
# Discovery
# ──────────────────────────────────────────────────────────────

def discover_all() -> list[dict]:
    all_raw: dict[str, dict] = {}

    for group in OEM_GROUPS:
        for state in US_STATES:
            accts = query_group(group, state)
            new = {k: v for k, v in accts.items() if k not in all_raw}
            if new:
                all_raw.update(new)
                total_vehicles = sum(1 for _ in new)
                print(f"  {group} {state}: +{len(new)} new → {len(all_raw)} total")
            time.sleep(0.1)

    # Normalise into a flat list
    dealers = []
    for site_id, info in all_raw.items():
        raw_url = info.get("url") or ""
        website = ("https://" + raw_url) if raw_url and not raw_url.startswith("http") else raw_url
        address = info.get("address", {})
        codes   = info.get("dealerCodes", [])

        def code(t):
            return next((d["code"] for d in codes if d["codeType"] == t), None)

        dealers.append({
            "site_id":    site_id,
            "name":       info.get("name"),
            "city":       address.get("city"),
            "state":      address.get("state"),
            "url":        raw_url or None,
            "website":    website or None,
            "bmw_code":   code("bmw"),
            "mini_code":  code("mini"),
            "bmw_region": code("bmw_region"),
        })

    dealers.sort(key=lambda d: (d.get("state") or "", d["site_id"]))
    return dealers


# ──────────────────────────────────────────────────────────────
# Merge into dealers.json
# ──────────────────────────────────────────────────────────────

def merge_into_main(ddc_dealers: list[dict]) -> None:
    try:
        with open(DEALERS_OUT) as f:
            existing: list[dict] = json.load(f)
    except FileNotFoundError:
        existing = []

    # Build lookups
    by_site_id: dict[str, int] = {}
    by_url:     dict[str, int] = {}
    for i, d in enumerate(existing):
        if d.get("site_id"):
            by_site_id[d["site_id"]] = i
        if d.get("website"):
            key = _url_key(d["website"])
            by_url[key] = i

    added = updated = skipped = 0
    for ddc in ddc_dealers:
        sid  = ddc["site_id"]
        url  = ddc.get("website")

        if "pmdemo" in sid or not url:
            skipped += 1
            continue

        record = {
            "platform":      "dealercom",
            "site_id":       sid,
            "name":          ddc.get("name"),
            "city":          ddc.get("city"),
            "state":         ddc.get("state"),
            "website":       url,
            "inventory_url": f"{url}/api/widget/ws-inv-data/getInventory",
            "bmw_code":      ddc.get("bmw_code"),
            "mini_code":     ddc.get("mini_code"),
            "bmw_region":    ddc.get("bmw_region"),
            "resolved":      True,
            "is_di":         False,
            "is_bmw":        bool(ddc.get("bmw_code")),
        }

        key = _url_key(url)
        if sid in by_site_id:
            idx = by_site_id[sid]
            existing[idx].update({k: v for k, v in record.items() if v is not None})
            updated += 1
        elif key in by_url:
            idx = by_url[key]
            existing[idx].update({k: v for k, v in record.items() if v is not None})
            updated += 1
        else:
            existing.append(record)
            by_site_id[sid] = len(existing) - 1
            by_url[key]     = len(existing) - 1
            added += 1

    with open(DEALERS_OUT, "w") as f:
        json.dump(existing, f, indent=2)

    platforms = Counter(d.get("platform", "?") for d in existing)
    print(f"\nMerged into {DEALERS_OUT}")
    print(f"  Added: {added}, Updated: {updated}, Skipped: {skipped}")
    print(f"  Total: {len(existing)}")
    for p, n in platforms.most_common():
        print(f"    {p:<20}: {n}")


def _url_key(url: str) -> str:
    return url.lower().rstrip("/").replace("https://", "").replace("http://", "").replace("www.", "")


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Sweeping BMW/MINI DDC dealers via OEM group accounts…\n")

    dealers = discover_all()

    print(f"\nDiscovered {len(dealers)} unique DDC accounts")
    print(f"  BMW-only : {sum(1 for d in dealers if d.get('bmw_code') and not d.get('mini_code'))}")
    print(f"  MINI-only: {sum(1 for d in dealers if d.get('mini_code') and not d.get('bmw_code'))}")
    print(f"  Both     : {sum(1 for d in dealers if d.get('bmw_code') and d.get('mini_code'))}")

    with open(DDC_OUT, "w") as f:
        json.dump(dealers, f, indent=2)
    print(f"Saved → {DDC_OUT}")

    merge_into_main(dealers)
