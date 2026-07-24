"""
truecar.py – Scrape a TrueCar used-listings search and normalize into our
vehicle schema so results can be merged into results.json.

How it works
------------
TrueCar server-renders every listing into a Next.js Apollo cache embedded in
the page as ``<script type="application/json">…"__APOLLO_STATE__"…</script>``.
Each ``ConsumerSummaryListing`` object carries VIN, year, trim, mileage, price,
color, CPO flag and a reference to a ``ListingDealership`` (name + city/state).
No per-VDP fetch is needed — one page GET yields ~30 fully-detailed listings.

Anti-bot
--------
TrueCar is behind PerimeterX. Clean SEO-path URLs (no query string) usually
pass plain ``requests``; anything with a query string — including the
``?page=N`` pagination and the ``searchRadius``/``mileageHigh`` filter params —
returns a 403 ``px-captcha`` page. So we fall back to a stealthed, persistent
Chromium profile (``~/.truecar_browser_profile``), same approach as
``cars_com.py``. Run ``--warmup`` once to solve the challenge by hand; the
trust cookies persist for later headless runs.

Usage
-----
    uv run truecar.py --url "https://www.truecar.com/used-cars-for-sale/listings/inventory/?mmt[]=bmw_xm&searchRadius=5000&yearLow=2025&mileageHigh=15000"
    uv run truecar.py --warmup --url "https://www.truecar.com/used-cars-for-sale/listings/bmw/xm/"
    uv run truecar.py --url "..." --out truecar_results.json --max-pages 20
    uv run truecar.py --url "..." --exclude-states CA,HI
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

from search_inventory import _vin_links

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 30
BROWSER_PROFILE_DIR = str(pathlib.Path.home() / ".truecar_browser_profile")

_JSON_SCRIPT_RE = re.compile(
    r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', re.S)
_PX_MARKERS = ("px-captcha", "perimeterx", "Access to this page has been denied")


# ── Apollo-state parsing ──────────────────────────────────────────────────────

def _apollo_state(html: str) -> dict | None:
    """Pull __APOLLO_STATE__ out of the largest embedded JSON <script>."""
    best = None
    for blob in _JSON_SCRIPT_RE.findall(html):
        if "__APOLLO_STATE__" not in blob:
            continue
        if best is None or len(blob) > len(best):
            best = blob
    if best is None:
        return None
    try:
        data = json.loads(best)
    except json.JSONDecodeError:
        return None
    try:
        return data["props"]["pageProps"]["__APOLLO_STATE__"]
    except (KeyError, TypeError):
        return None


def _total_count(apollo: dict) -> int | None:
    rq = apollo.get("ROOT_QUERY", {})
    for key, val in rq.items():
        if key.startswith("marketplaceListingSearch") and isinstance(val, dict):
            tc = val.get("totalCount")
            if isinstance(tc, int):
                return tc
    return None


def _listings_from_apollo(apollo: dict) -> list[dict]:
    dealerships = {k: v for k, v in apollo.items()
                   if k.startswith("ListingDealership")}
    out = []
    for key, obj in apollo.items():
        if not key.startswith("ConsumerSummaryListing"):
            continue
        veh = obj.get("vehicle") or {}
        vin = veh.get("vin")
        if not vin:
            continue
        style = veh.get("style") or {}
        details = veh.get("details") or {}
        pricing = obj.get("pricing") or {}
        dref = (obj.get("dealership") or {}).get("__ref")
        dealer = dealerships.get(dref, {}) if dref else {}
        geo = ((dealer.get("location") or {}).get("geolocation") or {})
        out.append({
            "vin": vin,
            "year": veh.get("year"),
            "make": (veh.get("make") or {}).get("name"),
            "model": (veh.get("model") or {}).get("name"),
            "model_slug": (veh.get("model") or {}).get("slug"),
            "trim": style.get("trimName") or style.get("name"),
            "mileage": veh.get("mileage"),
            "price": pricing.get("listPrice"),
            "ext_color": veh.get("exteriorColor"),
            "certified": bool(veh.get("certifiedPreOwned")),
            "condition": (veh.get("condition") or "USED").lower(),
            "stock": details.get("stockNumber"),
            "listed_at": details.get("listedAt"),
            "dealer_name": dealer.get("name"),
            "dealer_city": geo.get("city"),
            "dealer_state": geo.get("state"),
        })
    return out


# ── Record mapping ────────────────────────────────────────────────────────────

def _days_since(iso: str | None) -> int | None:
    if not iso:
        return None
    from datetime import datetime, timezone
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).days
    except ValueError:
        return None


def _to_record(item: dict) -> dict:
    vin = item["vin"]
    year = item.get("year")
    model_slug = item.get("model_slug") or (item.get("model") or "").lower().replace(" ", "-")
    make_slug = (item.get("make") or "bmw").lower()
    # TrueCar VDP path, e.g. /used-cars-for-sale/listing/{VIN}/{year}-bmw-{model}/
    link = (f"https://www.truecar.com/used-cars-for-sale/listing/{vin}/"
            f"{year}-{make_slug}-{model_slug}/" if year else
            f"https://www.truecar.com/used-cars-for-sale/listing/{vin}/")
    cert = item.get("certified")
    return {
        "vin":           vin,
        "stockNumber":   item.get("stock"),
        "year":          year,
        "make":          item.get("make"),
        "model":         item.get("model"),
        "trim":          item.get("trim"),
        "odometer":      item.get("mileage"),
        "internetPrice": item.get("price"),
        "extColor":      item.get("ext_color"),
        "certified":     cert,
        "daysOnLot":     _days_since(item.get("listed_at")),
        "dateInStock":   item.get("listed_at"),
        **_vin_links(vin),
        "dealerName":    item.get("dealer_name"),
        "dealerCity":    item.get("dealer_city"),
        "dealerState":   item.get("dealer_state"),
        # TrueCar never exposes the dealer's own website — leave blank so
        # search_inventory.resolve_missing_dealer_urls / merge_results backfill
        # the real dealer domain from another source.
        "dealerUrl":     "",
        "dealerPhone":   None,
        "platform":      "truecar",
        "type":          "cpo" if cert else item.get("condition") or "used",
        "link":          link,
        "vinLink":       link,
    }


# ── Fetching (requests + PerimeterX browser fallback) ─────────────────────────

_PW = None
_PW_CTX = None
_PW_PAGE = None
_PW_UNAVAILABLE = False
_PW_HEADLESS = True


def _looks_blocked(status: int, html: str) -> bool:
    if status == 403:
        return True
    head = html[:4000].lower()
    return any(m.lower() in head for m in _PX_MARKERS)


def _clear_singleton_locks() -> None:
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        p = pathlib.Path(BROWSER_PROFILE_DIR) / name
        try:
            if p.is_symlink() or p.exists():
                p.unlink()
        except OSError:
            pass


def _ensure_browser():
    global _PW, _PW_CTX, _PW_PAGE, _PW_UNAVAILABLE
    if _PW_UNAVAILABLE:
        return None
    if _PW_PAGE is not None:
        return _PW_PAGE
    try:
        from playwright.sync_api import sync_playwright
        from playwright_stealth.stealth import Stealth
    except Exception as e:
        print(f"  ! playwright unavailable ({e}) — run: uv run playwright install chrome",
              file=sys.stderr)
        _PW_UNAVAILABLE = True
        return None
    pathlib.Path(BROWSER_PROFILE_DIR).mkdir(parents=True, exist_ok=True)
    _clear_singleton_locks()
    try:
        _PW = sync_playwright().start()
        _PW_CTX = _PW.chromium.launch_persistent_context(
            BROWSER_PROFILE_DIR, channel="chrome", headless=_PW_HEADLESS,
            args=["--disable-blink-features=AutomationControlled"],
            user_agent=HEADERS["User-Agent"],
            viewport={"width": 1440, "height": 900})
        Stealth().apply_stealth_sync(_PW_CTX)
        _PW_PAGE = _PW_CTX.new_page()
        print("  · launched stealth browser for TrueCar (PerimeterX fallback)",
              file=sys.stderr)
        return _PW_PAGE
    except Exception as e:
        print(f"  ! failed to launch browser ({e})", file=sys.stderr)
        _PW_UNAVAILABLE = True
        return None


def _shutdown_browser():
    global _PW, _PW_CTX, _PW_PAGE
    for fn in (lambda: _PW_PAGE and _PW_PAGE.close(),
               lambda: _PW_CTX and _PW_CTX.close(),
               lambda: _PW and _PW.stop()):
        try:
            fn()
        except Exception:
            pass
    _PW = _PW_CTX = _PW_PAGE = None


def _browser_get(url: str) -> str | None:
    page = _ensure_browser()
    if page is None:
        return None
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT * 1000)
    except Exception:
        pass
    # Give any PerimeterX interstitial a moment; retry content read on races.
    for _ in range(3):
        page.wait_for_timeout(2500)
        try:
            html = page.content()
        except Exception:
            continue
        if not _looks_blocked(200, html) and "__APOLLO_STATE__" in html:
            return html
    try:
        return page.content()
    except Exception:
        return None


def fetch(url: str, session: requests.Session) -> str | None:
    """Return page HTML, using plain requests when possible and the stealth
    browser when PerimeterX blocks the request."""
    try:
        r = session.get(url, timeout=TIMEOUT)
        if not _looks_blocked(r.status_code, r.text):
            return r.text
    except requests.RequestException:
        pass
    return _browser_get(url)


# ── URL normalization ─────────────────────────────────────────────────────────
# The ``/listings/inventory/?searchRadius=…&mmt[]=bmw_xm&yearLow=…`` query form
# (what the TrueCar UI produces) is PerimeterX-gated on plain requests. The
# equivalent SEO path — ``/listings/bmw/xm/location-nationwide/year-2025-max-2026/
# mileage-15000/`` — usually passes without a browser, so we translate to it and
# avoid needing a warmup for the common case.

def normalize_truecar_url(url: str) -> tuple[str, dict]:
    """Return (url_to_fetch, hints). Translates the query-param
    ``/listings/inventory/?…`` form to the plain-requests-friendly SEO path and
    surfaces model/trim/year hints for client-side filtering — TrueCar pads
    scarce results with "similar" fallback listings of other models/years, so
    we must re-check every listing ourselves."""
    parts = urlparse(url)
    if "/listings/inventory" not in parts.path:
        return url, {}  # already an SEO-path or VDP URL
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    q = dict(pairs)
    mmts = [v for k, v in pairs if k in ("mmt[]", "mmt")]
    if not mmts:
        return url, {}
    bits = mmts[0].split("_")  # e.g. "bmw_xm" / "bmw_x7_m60i"
    make = bits[0]
    model = bits[1] if len(bits) > 1 else ""
    trim = " ".join(bits[2:]) if len(bits) > 2 else None
    hints = {"model_slug": model or None, "trim": trim}
    yl_h, yh_h = q.get("yearLow"), q.get("yearHigh")
    if yl_h and yl_h.isdigit():
        hints["year_min"] = int(yl_h)
    if (yh_h or yl_h) and (yh_h or yl_h).isdigit():
        hints["year_max"] = int(yh_h or yl_h)
    if not model:
        return url, hints

    segs = []
    radius = q.get("searchRadius")
    city, state = q.get("city"), q.get("state")
    nationwide = (radius and radius.isdigit() and int(radius) >= 500) or not (city and state)
    segs.append("location-nationwide" if nationwide
                else f"location-{city.lower().replace(' ', '-')}-{state.lower()}")
    yl, yh = q.get("yearLow"), q.get("yearHigh")
    if yl:
        segs.append(f"year-{yl}-max-{yh or yl}")
    if q.get("mileageHigh"):
        segs.append(f"mileage-{q['mileageHigh']}")

    seo = f"/used-cars-for-sale/listings/{make}/{model}/" + "".join(s + "/" for s in segs)
    return urlunparse(parts._replace(path=seo, query="")), hints


# ── Pagination ────────────────────────────────────────────────────────────────

def _with_page(url: str, page: int) -> str:
    parts = urlparse(url)
    q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
         if k != "page"]
    if page > 1:
        q.append(("page", str(page)))
    return urlunparse(parts._replace(query=urlencode(q, doseq=True)))


# ── Crawl ─────────────────────────────────────────────────────────────────────

def crawl(url: str, max_pages: int, min_miles: int | None, max_miles: int | None,
          drop_states: set[str], hints: dict | None = None) -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)
    hints = hints or {}
    trim_l = (hints.get("trim") or "").lower() or None
    model_slug = (hints.get("model_slug") or "").lower() or None
    year_min, year_max = hints.get("year_min"), hints.get("year_max")

    records: list[dict] = []
    seen_vins: set[str] = set()
    total: int | None = None

    for page_num in range(1, max_pages + 1):
        page_url = _with_page(url, page_num)
        html = fetch(page_url, session)
        if not html:
            print(f"  page {page_num}: no HTML (blocked?) — stopping", flush=True)
            break
        apollo = _apollo_state(html)
        if apollo is None:
            if _looks_blocked(200, html):
                print(f"  page {page_num}: blocked by PerimeterX. Solve it once with:\n"
                      f"      uv run truecar.py --warmup --url '{page_url}'\n"
                      "    then re-run. (Clean SEO-path URLs without query params "
                      "usually work without a warmup.)", flush=True)
            else:
                print(f"  page {page_num}: no listing data — stopping", flush=True)
            break
        if total is None:
            total = _total_count(apollo)
        items = _listings_from_apollo(apollo)
        new_on_page = 0
        kept = 0
        for it in items:
            vin = it["vin"]
            if vin in seen_vins:
                continue
            seen_vins.add(vin)
            new_on_page += 1
            # TrueCar pads scarce searches with "similar" fallback listings and
            # its trim URL segment doesn't reliably filter — enforce model,
            # trim, and year client-side.
            if model_slug and (it.get("model_slug") or "").lower() != model_slug:
                continue
            if trim_l and trim_l not in (it.get("trim") or "").lower():
                continue
            yr = it.get("year")
            if yr is not None:
                if year_min is not None and yr < year_min:
                    continue
                if year_max is not None and yr > year_max:
                    continue
            odo = it.get("mileage")
            if odo is not None:
                if min_miles is not None and odo < min_miles:
                    continue
                if max_miles is not None and odo > max_miles:
                    continue
            st = (it.get("dealer_state") or "").upper()
            if st and st in drop_states:
                continue
            records.append(_to_record(it))
            kept += 1
        print(f"  page {page_num}: {len(items)} listings "
              f"({new_on_page} new, {kept} kept)"
              + (f"  [total={total}]" if total else ""), flush=True)
        if new_on_page == 0:
            break
        if total is not None and len(seen_vins) >= total:
            break
        time.sleep(1.0)

    return records


def _warmup(url: str) -> int:
    global _PW_HEADLESS
    _PW_HEADLESS = False
    print("Opening a visible Chrome window on TrueCar.", flush=True)
    print("→ Solve the 'press & hold' / CAPTCHA challenge, wait for the real "
          "listings page to load, then press Enter here.", flush=True)
    page = _ensure_browser()
    if page is None:
        print("Could not launch a browser — is Chrome installed? "
              "(uv run playwright install chrome)", file=sys.stderr)
        return 1
    try:
        page.goto(url, timeout=TIMEOUT * 1000, wait_until="domcontentloaded")
    except Exception as e:
        print(f"  (navigation warning: {e.__class__.__name__})", file=sys.stderr)
    try:
        input("Press Enter once the page is fully loaded and unblocked… ")
    except EOFError:
        page.wait_for_timeout(30000)
    try:
        html = page.content()
    except Exception:
        html = ""
    ok = "__APOLLO_STATE__" in html and not _looks_blocked(200, html)
    print("Trust cookies saved." if ok else
          "Page still looks blocked — interact more before pressing Enter.",
          flush=True)
    _shutdown_browser()
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True, help="TrueCar used-listings URL to crawl")
    ap.add_argument("--out", default="truecar_results.json")
    ap.add_argument("--max-pages", type=int, default=34,
                    help="Max pages (~30 listings each; default 34)")
    ap.add_argument("--min-miles", type=int, default=60,
                    help="Minimum odometer (default: 60)")
    ap.add_argument("--max-miles", type=int, default=15000,
                    help="Maximum odometer (default: 15000)")
    ap.add_argument("--exclude-states", default="CA",
                    help="Comma-separated state codes to drop (default: CA)")
    ap.add_argument("--trim", default=None,
                    help="Client-side trim filter (substring match). Auto-derived "
                         "from the URL's mmt[] param when omitted, since TrueCar's "
                         "trim URL segment doesn't reliably filter.")
    ap.add_argument("--warmup", action="store_true",
                    help="Open a visible browser to clear PerimeterX once; trust "
                         "cookies persist for later headless runs.")
    args = ap.parse_args()

    # results.json is the persistent union file — only merge_results.py writes it.
    if os.path.basename(args.out) == "results.json":
        ap.error("refusing to write results.json — use another output file and "
                 "merge it in via merge_results.py")

    if args.warmup:
        return _warmup(normalize_truecar_url(args.url)[0])

    drop_states = {s.strip().upper() for s in args.exclude_states.split(",") if s.strip()}

    url, hints = normalize_truecar_url(args.url)
    if args.trim:
        hints["trim"] = args.trim
    if url != args.url:
        print(f"  translated to SEO path: {url}", flush=True)
    args.url = url

    filters = [f"{k}={v}" for k, v in hints.items() if v is not None]
    print(f"TrueCar crawl: {args.url}", flush=True)
    print(f"  miles {args.min_miles}–{args.max_miles} | exclude "
          f"{sorted(drop_states) or 'none'}"
          + (f" | client filters: {', '.join(filters)}" if filters else ""), flush=True)
    try:
        records = crawl(args.url, args.max_pages, args.min_miles, args.max_miles,
                        drop_states, hints)
    finally:
        _shutdown_browser()

    with open(args.out, "w") as f:
        json.dump(records, f, indent=2)
    print(f"\nWrote {len(records)} vehicles → {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
