"""
Harvest DealerOn (Cosmos) API identifiers for DealerOn dealers.

DealerOn serves inventory from a clean JSON API:
    /api/vhcliaa/vehicle-pages/cosmos/srp/vehicles/{dealerId}/{pageId}?model=X7&...
where {pageId} differs for the New vs Used SRP. dealerId is in the page HTML,
but the pageIds are only revealed by the XHR the SRP fires. This script visits
each DealerOn dealer's new/used SRPs, captures those request URLs, and records
`do_dealer_id`, `do_new_page_id`, `do_used_page_id` onto the master record.

DealerOn sites are not Cloudflare-gated, so headless works fine here.

Usage:
  uv run python harvest_dealeron.py            # DealerOn dealers missing ids
  uv run python harvest_dealeron.py --limit 3
  uv run python harvest_dealeron.py --force
"""

import argparse
import json
import os
import pathlib
import re
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MASTER_FILE = os.path.join(HERE, "master_dealers.json")
PROFILE = str(pathlib.Path.home() / ".bmw_browser_profile2")

SRP_RE = re.compile(r"/api/vhcliaa/vehicle-pages/cosmos/srp/vehicles/(\d+)/(\d+)")


def is_dealeron(rec: dict) -> bool:
    c = rec.get("census") or {}
    return c.get("platform") == "dealeron" or rec.get("platform") == "dealeron"


class DealerOnHarvester:
    def __init__(self, headless=True):
        from playwright.sync_api import sync_playwright
        from playwright_stealth.stealth import Stealth
        pathlib.Path(PROFILE).mkdir(parents=True, exist_ok=True)
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            p = pathlib.Path(PROFILE) / name
            try:
                if p.is_symlink() or p.exists():
                    p.unlink()
            except OSError:
                pass
        self._pw = sync_playwright().start()
        self._ctx = self._pw.chromium.launch_persistent_context(
            PROFILE, channel="chrome", headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1440, "height": 900})
        Stealth().apply_stealth_sync(self._ctx)
        self._page = self._ctx.new_page()
        self._seen: list[tuple[str, str]] = []
        self._page.on("request", self._on_req)

    def _on_req(self, req):
        m = SRP_RE.search(req.url)
        if m:
            self._seen.append((m.group(1), m.group(2)))

    NEW_PATHS = ["/new-vehicles/", "/new-inventory/", "/searchnew.aspx",
                 "/new-cars/", "/inventory/new/"]
    USED_PATHS = ["/used-vehicles/", "/used-inventory/", "/searchused.aspx",
                  "/used-cars/", "/inventory/used/", "/pre-owned-vehicles/"]

    def _capture(self, paths: list[str], origin: str) -> tuple[str, str] | None:
        for path in paths:
            self._seen.clear()
            try:
                self._page.goto(origin + path, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            self._page.wait_for_timeout(4500)
            if self._seen:
                return self._seen[0]
        return None

    def harvest(self, website: str) -> dict:
        origin = website.rstrip("/")
        result: dict = {}
        new_cap = self._capture(self.NEW_PATHS, origin)
        if new_cap:
            result["do_dealer_id"] = new_cap[0]
            result["do_new_page_id"] = new_cap[1]
        used_cap = self._capture(self.USED_PATHS, origin)
        if used_cap:
            result.setdefault("do_dealer_id", used_cap[0])
            result["do_used_page_id"] = used_cap[1]
        return result

    def close(self):
        for fn in (self._page.close, self._ctx.close, self._pw.stop):
            try:
                fn()
            except Exception:
                pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--headful", action="store_true")
    args = ap.parse_args()

    with open(MASTER_FILE) as f:
        records = json.load(f)

    todo = [r for r in records
            if is_dealeron(r) and r.get("website_ok")
            and (args.force or not r.get("do_dealer_id"))]
    if args.limit:
        todo = todo[:args.limit]

    print(f"DealerOn id harvest: {len(todo)} dealers")
    if not todo:
        return

    h = DealerOnHarvester(headless=not args.headful)
    ok = 0
    try:
        for i, rec in enumerate(todo, 1):
            try:
                ids = h.harvest(rec["website"])
            except Exception as e:
                ids = {}
                print(f"  [{i}/{len(todo)}] {rec['name']}: ERROR {str(e)[:50]}")
            if ids.get("do_dealer_id"):
                rec.update(ids)
                ok += 1
                print(f"  [{i}/{len(todo)}] {rec['name']} ({rec['state']}): "
                      f"dealer={ids.get('do_dealer_id')} "
                      f"new={ids.get('do_new_page_id')} used={ids.get('do_used_page_id')}")
            else:
                print(f"  [{i}/{len(todo)}] {rec['name']} ({rec['state']}): no ids")
            if i % 5 == 0:
                with open(MASTER_FILE, "w") as f:
                    json.dump(records, f, indent=2)
            time.sleep(0.3)
    finally:
        h.close()
        with open(MASTER_FILE, "w") as f:
            json.dump(records, f, indent=2)

    total = sum(1 for r in records if is_dealeron(r))
    have = sum(1 for r in records if is_dealeron(r) and r.get("do_dealer_id"))
    print(f"\nHarvested {ok} | DealerOn coverage: {have}/{total}")


if __name__ == "__main__":
    main()
