"""
Discovers BMW dealer platforms and Algolia/inventory config.

Detected platforms:
  dealerinspire  – #sb-algolia-helper div; Algolia app + search key + index name
  dealercom      – window.DDC + siteId; Cox Automotive /api/widget/ inventory API
  unknown        – neither detected

Usage:
  python discover_dealers.py                    # process DEALER_URLS list
  python discover_dealers.py --from-json        # re-check unresolved in dealers.json
  python discover_dealers.py --url https://...  # single URL
"""

import argparse
import json
import os
import re
import sys
import time
import random
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from urllib.parse import urlparse, urlunparse

OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "dealers.json")

DEALER_URLS: list[str] = [
    "https://www.bmwstore.com/retired-courtesy-vehicles/",
    "https://www.bmwofdallas.com/",
    # Add more dealers here
]

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


# ── Platform parsers ──────────────────────────────────────────────────────────

class DealerInspireParser(HTMLParser):
    """Looks for <div id="sb-algolia-helper" data-app-id=... data-index=...>"""
    def __init__(self):
        super().__init__()
        self.result: dict = {}

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        if attr.get("id") == "sb-algolia-helper":
            self.result = {
                "app_id":     attr.get("data-app-id"),
                "search_key": attr.get("data-search-key"),
                "index":      attr.get("data-index"),
            }


def _detect_di(html: str, origin: str) -> dict | None:
    parser = DealerInspireParser()
    parser.feed(html)
    if not parser.result:
        return None
    idx = parser.result.get("index", "")
    slug_m = re.match(r"^(.+?)(?:-sbm\d+)?_production_inventory", idx)
    return {
        "platform":   "dealerinspire",
        "di_index":   idx,
        "di_slug":    slug_m.group(1) if slug_m else None,
        "app_id":     parser.result.get("app_id"),
        "search_key": parser.result.get("search_key"),
    }


def _detect_dealercom(html: str, origin: str) -> dict | None:
    if "window.DDC" not in html:
        return None
    # Extract siteId — appears multiple times; grab first
    m = re.search(r'["\']?siteId["\']?\s*[:=]\s*["\']([^"\'<>\s]+)["\']', html)
    if not m:
        return None
    site_id = m.group(1)
    # Confirm inventory API is reachable
    inventory_url = f"{origin}/api/widget/ws-inv-data/getInventory"
    return {
        "platform":      "dealercom",
        "site_id":       site_id,
        "inventory_url": inventory_url,
    }


def detect_platform(url: str, retries: int = 3) -> dict:
    """
    Fetch the page and detect which dealer platform it runs on.
    Returns a dict with at minimum: website, platform, checked_url.
    """
    parsed = urlparse(url)
    origin = urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
    candidates = [url] + [
        origin + path for path in FALLBACK_PATHS if origin + path != url
    ]

    session = requests.Session()
    session.headers.update(HEADERS)

    for candidate in candidates:
        for attempt in range(retries):
            try:
                resp = session.get(candidate, timeout=15, allow_redirects=True)
                if resp.status_code in (403, 404):
                    break
                if resp.status_code == 429:
                    wait = (2 ** attempt) + random.uniform(0, 1)
                    print(f"  [rate-limit] {candidate} — waiting {wait:.1f}s")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()

                html = resp.text

                # DealerInspire check
                di = _detect_di(html, origin)
                if di:
                    return {"website": origin, "checked_url": candidate, **di}

                # Dealer.com check
                ddc = _detect_dealercom(html, origin)
                if ddc:
                    return {"website": origin, "checked_url": candidate, **ddc}

                break  # page loaded fine — no known platform widget, try next path

            except requests.HTTPError:
                break
            except (requests.ConnectionError, requests.Timeout) as e:
                if attempt == retries - 1:
                    return {"website": origin, "checked_url": candidate,
                            "platform": "unknown", "error": str(e)}
                time.sleep(2 ** attempt)
            except Exception as e:
                return {"website": origin, "checked_url": candidate,
                        "platform": "unknown", "error": str(e)}

    return {"website": origin, "platform": "unknown"}


# ── I/O ───────────────────────────────────────────────────────────────────────

def load_existing() -> dict:
    """Return existing dealers keyed by website origin."""
    if not os.path.exists(OUTPUT_FILE):
        return {}
    try:
        with open(OUTPUT_FILE) as f:
            data = json.load(f)
        result = {}
        for d in data:
            key = d.get("website") or d.get("url", "")
            if key:
                result[key] = d
        return result
    except Exception:
        return {}


def save_results(existing: dict, new_results: list[dict]):
    for r in new_results:
        key = r.get("website") or r.get("url", "")
        if not key:
            continue
        prev = existing.get(key, {})
        # Only update if we have a better result (known platform > unknown)
        if r.get("platform", "unknown") != "unknown" or "platform" not in prev:
            existing[key] = {**prev, **r}

    records = sorted(
        existing.values(),
        key=lambda d: d.get("dealer_slug") or d.get("site_id") or d.get("website") or ""
    )
    with open(OUTPUT_FILE, "w") as f:
        json.dump(records, f, indent=2)
    print(f"\nSaved {len(records)} total dealers → {OUTPUT_FILE}")


def urls_from_dealers_json(existing: dict) -> list[str]:
    """Return URLs that haven't been platform-detected yet."""
    return [
        key for key, d in existing.items()
        if "platform" not in d or d.get("platform") == "unknown" or "error" in d
    ]


# ── Runner ────────────────────────────────────────────────────────────────────

def run(urls: list[str], workers: int = 5):
    existing = load_existing()
    todo = [
        u for u in urls
        if u not in existing
        or existing[u].get("platform") in (None, "unknown")
        or "error" in existing[u]
    ]
    skip = len(urls) - len(todo)
    if skip:
        print(f"Skipping {skip} already-resolved URLs.")
    print(f"Processing {len(todo)} URLs …\n")

    results = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(detect_platform, url): url for url in todo}
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            platform = result.get("platform", "unknown")
            website  = result.get("website", futures[future])

            if platform == "dealerinspire":
                print(f"[DI]         {website}")
                print(f"             index: {result.get('di_index')}")
            elif platform == "dealercom":
                print(f"[Dealer.com] {website}")
                print(f"             siteId: {result.get('site_id')}")
            elif "error" in result:
                print(f"[ERROR]      {website}: {result['error']}")
            else:
                print(f"[unknown]    {website}")

    save_results(existing, results)

    di  = [r for r in results if r.get("platform") == "dealerinspire"]
    ddc = [r for r in results if r.get("platform") == "dealercom"]
    unk = [r for r in results if r.get("platform") == "unknown"]
    print(f"\nThis run: {len(di)} DealerInspire  |  {len(ddc)} Dealer.com  |  {len(unk)} unknown")


def main():
    parser = argparse.ArgumentParser(description="Detect BMW dealer platform")
    parser.add_argument("--from-json", action="store_true",
                        help="Process unresolved websites in dealers.json")
    parser.add_argument("--url", metavar="URL",
                        help="Process a single website URL")
    parser.add_argument("--workers", type=int, default=5)
    args = parser.parse_args()

    if args.url:
        urls = [args.url]
    elif args.from_json:
        existing = load_existing()
        urls = urls_from_dealers_json(existing)
        if not urls:
            print("No unresolved URLs in dealers.json.")
            return
        print(f"Found {len(urls)} unresolved URLs.")
    else:
        urls = DEALER_URLS

    run(urls, workers=args.workers)


if __name__ == "__main__":
    main()
