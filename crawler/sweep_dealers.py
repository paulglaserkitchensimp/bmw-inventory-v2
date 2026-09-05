# ── NOT USED by the 2026 330i hunt ───────────────────────────────────────────
# Legacy dealer-discovery / debugging script, superseded by platform_census.py
# + build_master_dealers.py. Kept for reference; nothing in the 330i pipeline
# calls it. See docs/330I_DEAL_FINDER.md § "What was disabled".
# ─────────────────────────────────────────────────────────────────────────────
"""
BMW DealerInspire dealer sweeper.

Strategy:
  1. Query ALL known DealerInspire Algolia apps for their full index list.
  2. Find every base inventory index (no sort-variant suffix) whose slug
     contains "bmw" or "mini" — covers both naming conventions:
       - New:  {slug}-sbm{MMYY}_production_inventory
       - Old:  {slug}_production_inventory
  3. Deduplicate across apps (prefer the app that has live inventory data).
  4. For each unique dealer slug, query one inventory hit to get the real
     website URL from the `link` field; fall back to www.{slug}.com.
  5. Scrape the dealer website to confirm it's a live DI site and pull the
     exact Algolia index name from #sb-algolia-helper.
  6. Checkpoint-save to dealers.json every 10 dealers.
     Re-runs skip already-resolved slugs.
"""

import json
import re
import time
import random
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, urlunparse

import requests

# ── Known DealerInspire Algolia apps ─────────────────────────────────────────
# Each app is a separate DealerInspire cluster; BMW dealers are spread across both.
ALGOLIA_APPS = [
    {
        "app_id":  "V3ZOVI2QFZ",
        "api_key": "ec7553dd56e6d4c8bb447a0240e7aab3",
        "host":    "https://v3zovi2qfz-dsn.algolia.net",
        "note":    "Discovered from bmwstore.com HAR",
    },
    {
        "app_id":  "EHWUW84XVK",
        "api_key": "fb58227032e79f03b9b820cbaea7f8fb",
        "host":    "https://ehwuw84xvk-dsn.algolia.net",
        "note":    "Discovered from bmwofbloomfieldhills",
    },
]

# Matches base inventory indexes for BOTH naming conventions:
#   new: {slug}-sbm{MMYY}_production_inventory
#   old: {slug}_production_inventory
# — but NOT sort variants like _defaultSort, _high_to_low, etc.
BASE_INV_RE = re.compile(
    r"^(?P<slug>.+?)(?:-sbm(?P<sbm>\d+))?_production_inventory$"
)

# BMW/MINI brand keywords to match against slugs
BMW_KEYWORDS = {"bmw", "mini"}

# Slugs known to be BMW dealers even without "bmw"/"mini" in the name
KNOWN_BMW_SLUGS = {
    "autohauslebanon",
    "autobahnmotors",
}

OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "dealers.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36 Edg/145.0.0.0"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "max-age=0",
    "DNT": "1",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "sec-ch-ua": '"Not:A-Brand";v="99", "Microsoft Edge";v="145", "Chromium";v="145"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
}

FALLBACK_PATHS = ["/used-vehicles/", "/new-vehicles/", "/inventory/", "/pre-owned/", "/"]


# ── Algolia helpers ───────────────────────────────────────────────────────────

def algolia_list_indexes(app: dict) -> list[dict]:
    """Fetch all index names from one Algolia app with pagination."""
    host, app_id, api_key = app["host"], app["app_id"], app["api_key"]
    hdrs = {"x-algolia-api-key": api_key, "x-algolia-application-id": app_id}

    print(f"  [{app_id}] fetching index list …")
    r = requests.get(f"{host}/1/indexes", headers=hdrs, params={"hitsPerPage": 100, "page": 0}, timeout=15)
    r.raise_for_status()
    data = r.json()
    nb_pages = data.get("nbPages", 1)
    items = list(data.get("items", []))

    for page in range(1, nb_pages):
        batch = requests.get(
            f"{host}/1/indexes", headers=hdrs,
            params={"hitsPerPage": 100, "page": page}, timeout=15
        ).json()
        items.extend(batch.get("items", []))
        if page % 25 == 0:
            print(f"  [{app_id}] … {len(items)} indexes fetched")

    print(f"  [{app_id}] total: {len(items)} indexes")
    return items


def algolia_query(app: dict, index_name: str, params: str) -> dict:
    """POST a search query to a specific index."""
    r = requests.post(
        f"{app['host']}/1/indexes/{index_name}/query",
        headers={
            "x-algolia-api-key":        app["api_key"],
            "x-algolia-application-id": app["app_id"],
            "Content-Type": "application/json",
        },
        json={"params": params},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def get_website_from_index(app: dict, index_name: str) -> str | None:
    """Pull the dealer's website origin from the first inventory record's link field."""
    try:
        data = algolia_query(app, index_name, "hitsPerPage=1")
        hits = data.get("hits", [])
        if hits:
            link = hits[0].get("link", "")
            if link:
                parsed = urlparse(link)
                if parsed.scheme and parsed.netloc:
                    return f"{parsed.scheme}://{parsed.netloc}"
    except Exception as e:
        print(f"  [warn] {index_name}: {e}")
    return None


def check_makes_in_index(app: dict, index_name: str) -> list[str]:
    """Return the makes facet from an inventory index."""
    try:
        data = algolia_query(app, index_name, "hitsPerPage=0&facets=%5B%22make%22%5D")
        return list(data.get("facets", {}).get("make", {}).keys())
    except Exception:
        return []


# ── DI website scraper ────────────────────────────────────────────────────────

def detect_platform(website_url: str) -> dict:
    """
    Scrape the dealer website and detect its platform.
    Imports from discover_dealers.py to avoid duplication.
    """
    try:
        from discover_dealers import detect_platform as _detect
        return _detect(website_url)
    except Exception as e:
        return {"platform": "unknown", "error": str(e)}


# ── Dealer resolution ─────────────────────────────────────────────────────────

def is_bmw_slug(slug: str) -> bool:
    return any(kw in slug for kw in BMW_KEYWORDS) or slug in KNOWN_BMW_SLUGS


def process_dealer(slug: str, index_base: str, sbm_code: str | None,
                   app: dict, existing: dict) -> dict:
    """Fully resolve a dealer: website URL → DI check."""
    if slug in existing and existing[slug].get("resolved"):
        return existing[slug]

    print(f"  [work] {slug} ({app['app_id']})")
    record: dict = {
        "dealer_slug": slug,
        "algolia_app":  app["app_id"],
        "index_base":   index_base,
        "resolved":     False,
    }
    if sbm_code:
        record["sbm_code"] = sbm_code

    # 1. Get website from inventory; fall back to slug-derived URL
    website = get_website_from_index(app, index_base)
    if not website:
        website = f"https://www.{slug}.com"
        record["website_guessed"] = True
    record["website"] = website
    record["is_bmw"] = True

    # 2. Detect platform (DealerInspire, Dealer.com, or unknown)
    time.sleep(random.uniform(0.2, 0.7))
    platform_info = detect_platform(website)
    record.update(platform_info)
    # Legacy compat: mark is_di for DI dealers
    record["is_di"] = platform_info.get("platform") == "dealerinspire"
    record["resolved"] = True
    return record


# ── I/O ───────────────────────────────────────────────────────────────────────

def load_existing() -> dict:
    if not os.path.exists(OUTPUT_FILE):
        return {}
    try:
        with open(OUTPUT_FILE) as f:
            data = json.load(f)
        return {d["dealer_slug"]: d for d in data if "dealer_slug" in d}
    except Exception:
        return {}


def save_dealers(dealers_by_slug: dict):
    records = sorted(dealers_by_slug.values(), key=lambda d: d["dealer_slug"])
    with open(OUTPUT_FILE, "w") as f:
        json.dump(records, f, indent=2)
    print(f"  → Saved {len(records)} dealers to {OUTPUT_FILE}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    existing = load_existing()
    print(f"Loaded {len(existing)} existing dealers\n")

    # Step 1: Collect candidates from all apps
    # candidates: {slug -> (index_base, sbm_code, app)}
    # If slug appears in multiple apps, prefer the one with live data (resolved later)
    candidates: dict[str, tuple[str, str | None, dict]] = {}

    for app in ALGOLIA_APPS:
        indexes = algolia_list_indexes(app)
        new_for_app = 0
        for idx in indexes:
            m = BASE_INV_RE.match(idx["name"])
            if not m:
                continue
            slug = m.group("slug")
            sbm  = m.group("sbm")  # None for old-style
            if not is_bmw_slug(slug):
                continue
            # Don't overwrite if we already have this slug from a previous app
            # unless the current app has sbm-style (newer) and existing doesn't
            if slug not in candidates or (sbm and not candidates[slug][1]):
                candidates[slug] = (idx["name"], sbm, app)
                new_for_app += 1
        print(f"  [{app['app_id']}] new BMW/MINI slugs: {new_for_app}")

    print(f"\nTotal unique BMW/MINI dealer slugs: {len(candidates)}")

    # Step 2: Filter out already-resolved
    todo = [
        (slug, idx, sbm, app)
        for slug, (idx, sbm, app) in candidates.items()
        if not (slug in existing and existing[slug].get("resolved"))
    ]
    print(f"Already resolved: {len(candidates) - len(todo)}")
    print(f"To process: {len(todo)}\n")

    # Step 3: Process in parallel with checkpointing
    dealers_by_slug = dict(existing)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(process_dealer, slug, idx, sbm, app, existing): slug
            for slug, idx, sbm, app in todo
        }
        for i, future in enumerate(as_completed(futures), 1):
            slug = futures[future]
            try:
                result = future.result()
                dealers_by_slug[slug] = result
            except Exception as e:
                print(f"  [error] {slug}: {e}")
                dealers_by_slug[slug] = {"dealer_slug": slug, "error": str(e), "resolved": False}

            if i % 10 == 0:
                save_dealers(dealers_by_slug)

    save_dealers(dealers_by_slug)

    # Step 4: Summary
    all_resolved = [d for d in dealers_by_slug.values() if d.get("resolved")]
    by_platform: dict[str, list] = {}
    for d in all_resolved:
        p = d.get("platform", "unknown")
        by_platform.setdefault(p, []).append(d)

    print(f"\n{'='*60}")
    print(f"Total BMW/MINI slugs processed: {len(all_resolved)}")
    for platform, dealers in sorted(by_platform.items()):
        print(f"  {platform:20s}: {len(dealers)}")

    if "dealerinspire" in by_platform:
        print(f"\nDealerInspire dealers:")
        for d in sorted(by_platform["dealerinspire"], key=lambda x: x["dealer_slug"]):
            print(f"  [{d.get('algolia_app','?'):12s}]  {d['dealer_slug']:42s}  {d.get('website','')}")

    if "dealercom" in by_platform:
        print(f"\nDealer.com dealers:")
        for d in sorted(by_platform["dealercom"], key=lambda x: x["dealer_slug"]):
            print(f"  siteId={d.get('site_id','?'):30s}  {d.get('website','')}")

    unknown = by_platform.get("unknown", [])
    if unknown:
        print(f"\nUnknown/failed (run 'discover_dealers.py --from-json' to retry):")
        for d in sorted(unknown, key=lambda x: x["dealer_slug"]):
            print(f"  {d['dealer_slug']:45s}  {d.get('error', '')}")


if __name__ == "__main__":
    main()
