"""
Harvest Cars Commerce credentials for DealerInspire dealers.

DealerInspire retired its Algolia search (the old app accounts now return
"Account temporary disabled") and moved to Cars Commerce's search API. Each DI
site embeds its config in the homepage as:

    var SEARCH_SERVICE = {"apiUrl":"...","ccid":"22142","apiKey":"OQa8...", ...}

The apiKey is shared across DI dealers; the `ccid` (numeric account id) is what
we need per dealer to query /api/v1/listings/{ccid}/search. This script visits
each DealerInspire dealer in master_dealers.json, extracts ccid + apiKey, and
writes them back onto the record (`cc_ccid`, `cc_api_key`).

Many DI sites sit behind Cloudflare that blocks headless Chrome, so this runs a
*headful* stealth browser (headless=False) which clears the challenge reliably.
It's a one-time-ish, resumable pass (records with cc_ccid are skipped).

Usage:
  uv run python harvest_di_ccid.py                 # all DI dealers missing ccid
  uv run python harvest_di_ccid.py --limit 5       # smoke test
  uv run python harvest_di_ccid.py --force         # re-harvest everything
  uv run python harvest_di_ccid.py --headless      # try headless (faster, blocked more)
"""

import argparse
import json
import os
import pathlib
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MASTER_FILE = os.path.join(HERE, "master_dealers.json")
BROWSER_PROFILE_DIR = str(pathlib.Path.home() / ".bmw_browser_profile")

SEARCH_SERVICE_RE = re.compile(r"var SEARCH_SERVICE\s*=\s*(\{.*?\});", re.S)
INVENTORY_PATHS = ["", "/used-inventory/index.htm", "/used-vehicles/", "/inventory/"]


def is_di(rec: dict) -> bool:
    c = rec.get("census") or {}
    return c.get("platform") == "dealerinspire" or rec.get("platform") == "dealerinspire"


def _clear_locks():
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        p = pathlib.Path(BROWSER_PROFILE_DIR) / name
        try:
            if p.is_symlink() or p.exists():
                p.unlink()
        except OSError:
            pass


def extract_config(html: str) -> dict | None:
    m = SEARCH_SERVICE_RE.search(html)
    if not m:
        return None
    try:
        cfg = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    if not cfg.get("ccid"):
        return None
    return {"cc_ccid": str(cfg["ccid"]), "cc_api_key": cfg.get("apiKey")}


class Harvester:
    def __init__(self, headless: bool):
        self._pw = None
        self._ctx = None
        self._page = None
        self.headless = headless

    def _ensure(self):
        if self._ctx is not None:
            return self._ctx
        from playwright.sync_api import sync_playwright
        from playwright_stealth.stealth import Stealth
        pathlib.Path(BROWSER_PROFILE_DIR).mkdir(parents=True, exist_ok=True)
        _clear_locks()
        self._pw = sync_playwright().start()
        self._ctx = self._pw.chromium.launch_persistent_context(
            BROWSER_PROFILE_DIR,
            channel="chrome",
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1440, "height": 900},
        )
        Stealth().apply_stealth_sync(self._ctx)
        self._page = self._ctx.new_page()
        return self._ctx

    def _content(self) -> str:
        for _ in range(4):
            try:
                return self._page.content()
            except Exception:
                self._page.wait_for_timeout(1000)
        return ""

    def harvest(self, website: str) -> dict | None:
        self._ensure()
        origin = website.rstrip("/")
        for path in INVENTORY_PATHS:
            url = origin + path
            try:
                self._page.goto(url, wait_until="commit", timeout=35000)
            except Exception:
                pass
            # Let a Cloudflare challenge clear.
            cfg = None
            for _ in range(5):
                self._page.wait_for_timeout(2500)
                html = self._content()
                if "you have been blocked" in html.lower():
                    break  # hard block — try next path/site
                cfg = extract_config(html)
                if cfg:
                    return cfg
                if len(html) > 40000 and "Just a moment" not in html:
                    # Page rendered but no config on this path; try next path.
                    break
        return None

    def close(self):
        for fn in (lambda: self._page and self._page.close(),
                   lambda: self._ctx and self._ctx.close(),
                   lambda: self._pw and self._pw.stop()):
            try:
                fn()
            except Exception:
                pass


def main():
    ap = argparse.ArgumentParser(description="Harvest DealerInspire Cars Commerce ccid")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--headless", action="store_true",
                    help="Run headless (faster but Cloudflare blocks more sites)")
    args = ap.parse_args()

    with open(MASTER_FILE) as f:
        records = json.load(f)

    todo = [r for r in records
            if is_di(r) and r.get("website_ok")
            and (args.force or not r.get("cc_ccid"))]
    if args.limit:
        todo = todo[:args.limit]

    print(f"DealerInspire ccid harvest: {len(todo)} dealers "
          f"({'headless' if args.headless else 'headful'})")
    if not todo:
        return

    h = Harvester(headless=args.headless)
    ok = fail = 0
    try:
        for i, rec in enumerate(todo, 1):
            website = rec.get("website")
            try:
                cfg = h.harvest(website)
            except Exception as e:
                cfg = None
                print(f"  [{i}/{len(todo)}] {rec['name']}: ERROR {str(e)[:60]}")
            if cfg:
                rec.update(cfg)
                ok += 1
                print(f"  [{i}/{len(todo)}] {rec['name']} ({rec['state']}): "
                      f"ccid={cfg['cc_ccid']}")
            else:
                fail += 1
                print(f"  [{i}/{len(todo)}] {rec['name']} ({rec['state']}): no ccid")
            if i % 5 == 0:
                with open(MASTER_FILE, "w") as f:
                    json.dump(records, f, indent=2)
            time.sleep(0.4)
    finally:
        h.close()
        with open(MASTER_FILE, "w") as f:
            json.dump(records, f, indent=2)

    print(f"\nHarvested {ok} ccids | {fail} failed")
    total_di = sum(1 for r in records if is_di(r))
    with_ccid = sum(1 for r in records if is_di(r) and r.get("cc_ccid"))
    print(f"DI coverage: {with_ccid}/{total_di} dealers now have a ccid")


if __name__ == "__main__":
    main()
