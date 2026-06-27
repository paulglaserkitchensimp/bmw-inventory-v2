"""
cars_com.py – Scrape a Cars.com search-results URL and normalize into our
vehicle schema so results can be merged into results.json.

Pipeline
--------
1. Paginate a Cars.com shopping URL (each page returns up to ~22 cards).
2. Each card exposes JSON in `data-vehicle-details` with VIN, year, trim,
   mileage, price, listingId, seller.zip/customerId, cpoIndicator.
3. For every listing we then fetch the VDP (`/vehicledetail/{listingId}/`)
   to pull dealer name/city/state and a callable phone number. The dealer
   location is parsed out of the `og:description` meta tag which is
   consistently formatted ("…at DEALER in CITY, ST for $…").
4. Records matching --exclude-states (default: CA) are dropped.
5. Output is written as JSON that matches the shape produced by
   search_inventory.py, so merge_results.py can dedupe across sources.

Usage
-----
    uv run cars_com.py --url "https://www.cars.com/shopping/results/?..."
    uv run cars_com.py --url "..." --out cars_com_results.json --max-pages 10
    uv run cars_com.py --url "..." --exclude-states CA,HI
"""

from __future__ import annotations

import argparse
import atexit
import html as html_lib
import json
import os
import pathlib
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

TIMEOUT = 45
VDP_BASE = "https://www.cars.com/vehicledetail/{listing_id}/"

# Dedicated Chrome profile for cars.com — kept separate from the other
# crawlers' ~/.bmw_browser_profile so concurrent runs don't fight over the
# single-instance lock. Accumulates DataDome trust cookies across runs.
BROWSER_PROFILE_DIR = str(pathlib.Path.home() / ".cars_com_browser_profile")

# Cars.com's description is consistently "…at NAME in CITY, ST for $…"
OG_DESC_RE = re.compile(
    r'property="og:description"\s+content="[^"]*?\bat\s+(?P<name>.+?)\s+in\s+(?P<city>[^,]+),\s*(?P<state>[A-Z]{2})\s+for\b',
    re.IGNORECASE,
)
# customer_id is the authoritative dealer id – we use it to pick the *correct*
# /dealers/{id}/{slug}/ URL out of the many dealer-profile links on the page.
CUSTOMER_ID_RE = re.compile(r'"customer_id"\s*:\s*"?(\d+)"?')
# Data blob fields inside the VDP (unquoted raw JSON embedded in script tags)
DEALER_NAME_RE = re.compile(r'"dealer_name"\s*:\s*"([^"]+)"')
STOCK_NUM_RE = re.compile(r'"stockNumber"\s*:\s*"([^"]+)"')
EXT_COLOR_RE = re.compile(r'"extColor"\s*:\s*\["?([^"\]]+)"?\]')
INT_COLOR_RE = re.compile(r'"intColor"\s*:\s*\["?([^"\]]+)"?\]')
# trim can appear as either "trim":"M60i" or "trim":["M60i"]
TRIM_RE = re.compile(r'"trim"\s*:\s*(?:\[\s*"([^"]+)"\s*\]|"([^"]+)")')
# US phone anywhere in page (DNI number routes to dealer)
PHONE_RE = re.compile(r"\(?\b(\d{3})\)?[\s\-\.]?(\d{3})[\s\-\.]?(\d{4})\b")
# Pull every data-vehicle-details blob from a search page
CARD_RE = re.compile(
    r'<fuse-card\s+data-listing-id="([a-f0-9-]+)"\s+data-vehicle-details="([^"]+)"',
    re.IGNORECASE,
)


def _normalize_phone(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"


# ── Browser fallback ──────────────────────────────────────────────────────
# cars.com fronts pages with DataDome, which 403s the plain `requests` client
# (no real TLS/JS fingerprint). When that happens we fetch the page through a
# stealthed, persistent-profile Chromium instead — same approach the other
# crawlers use. The browser is created lazily on first 403 and reused for the
# rest of the run, then torn down at exit.

_PW = None            # sync_playwright() context manager handle
_PW_CTX = None        # persistent browser context
_PW_UNAVAILABLE = False  # set True if playwright import/launch fails — stop retrying
_PW_HEADLESS = True   # flipped to False by --warmup so DataDome can be solved by hand
# Playwright's sync API is not thread-safe; VDP fetches run multi-threaded, so
# all browser navigation is serialized through this lock.
_PW_LOCK = threading.Lock()


def _browser_settle(page, full: bool = False) -> None:
    """Let DataDome's JS challenge resolve. `full=True` waits for networkidle
    (needed for the SPA search results to render their cards); VDP pages expose
    everything we parse in the initial HTML/meta, so they use the light path."""
    if full:
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(1200)
    else:
        page.wait_for_timeout(400)


def _clear_stale_singleton_locks() -> None:
    """Remove leftover Singleton* lock files from a previous crashed/aborted run.

    A persistent Chrome profile refuses to launch if these exist (it assumes
    another live instance owns the profile). Since we use a dedicated profile
    that nothing else should be using, any lock here is stale and safe to clear.
    """
    profile = pathlib.Path(BROWSER_PROFILE_DIR)
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        p = profile / name
        try:
            if p.is_symlink() or p.exists():
                p.unlink()
        except OSError:
            pass


def _ensure_browser():
    """Lazily launch a stealthed persistent Chromium context. Returns it or None."""
    global _PW, _PW_CTX, _PW_UNAVAILABLE
    if _PW_UNAVAILABLE:
        return None
    if _PW_CTX is not None:
        return _PW_CTX
    try:
        from playwright.sync_api import sync_playwright
        from playwright_stealth.stealth import Stealth
    except Exception as e:
        print(f"  ! playwright unavailable ({e}) — cannot bypass 403. "
              "Run: uv run playwright install chrome", file=sys.stderr)
        _PW_UNAVAILABLE = True
        return None
    pathlib.Path(BROWSER_PROFILE_DIR).mkdir(parents=True, exist_ok=True)
    _clear_stale_singleton_locks()
    try:
        _PW = sync_playwright().start()
        _PW_CTX = _PW.chromium.launch_persistent_context(
            BROWSER_PROFILE_DIR,
            channel="chrome",
            headless=_PW_HEADLESS,
            args=["--disable-blink-features=AutomationControlled"],
            user_agent=HEADERS["User-Agent"],
            viewport={"width": 1440, "height": 900},
        )
        Stealth().apply_stealth_sync(_PW_CTX)
        print("  · launched stealth browser for anti-bot fallback", file=sys.stderr)
        return _PW_CTX
    except Exception as e:
        print(f"  ! failed to launch browser ({e}) — cannot bypass 403.",
              file=sys.stderr)
        _PW_UNAVAILABLE = True
        return None


def _shutdown_browser() -> None:
    global _PW, _PW_CTX
    try:
        if _PW_CTX is not None:
            _PW_CTX.close()
    except Exception:
        pass
    try:
        if _PW is not None:
            _PW.stop()
    except Exception:
        pass
    _PW_CTX = None
    _PW = None


def _shutdown_browser_atexit() -> None:
    """atexit hook. Playwright's sync API can't be driven from the interpreter
    shutdown greenlet, so we just hard-exit if a browser is still up — the data
    file is already written by then and a clean teardown isn't worth a hang."""
    if _PW_CTX is not None or _PW is not None:
        os._exit(0)


atexit.register(_shutdown_browser_atexit)


class _FakeResponse:
    """Minimal requests.Response stand-in so callers can keep using .text /
    .status_code regardless of whether HTML came from requests or the browser."""
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code


def _browser_get(url: str, full: bool = False) -> _FakeResponse | None:
    """Fetch a page through the stealth browser. Returns a _FakeResponse with the
    rendered HTML, or None if the browser is unavailable / navigation failed.

    Serialized via _PW_LOCK since the sync Playwright API is not thread-safe and
    VDP fetches run concurrently."""
    with _PW_LOCK:
        ctx = _ensure_browser()
        if ctx is None:
            return None
        page = None
        try:
            page = ctx.new_page()
            resp = page.goto(url, timeout=TIMEOUT * 1000, wait_until="domcontentloaded")
            _browser_settle(page, full=full)
            status = resp.status if resp is not None else 200
            html = page.content()
            # DataDome interstitial sometimes returns 200 with a challenge body
            # and no listing cards; treat a still-empty body as a soft failure.
            if status == 404:
                return _FakeResponse(html, 404)
            return _FakeResponse(html, 200 if html else status)
        except Exception as e:
            print(f"  ! browser fetch {url} → {e.__class__.__name__}", file=sys.stderr)
            return None
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass


# Once cars.com 403s us, the whole session is IP-blocked for the plain client.
# Flip this flag so subsequent requests skip the (pointless) requests retries
# and go straight to the browser — avoids ~6s of wasted backoff per URL across
# potentially dozens of VDP fetches.
_SESSION_BLOCKED = False


def _get(url: str, session: requests.Session, tries: int = 3, full: bool = False):
    """GET with retry/backoff. Falls back to a stealth browser when cars.com's
    anti-bot returns 403 (or otherwise refuses the plain requests client).

    `full=True` is for SPA search pages that need a networkidle render; VDPs
    parse from initial HTML so they use the fast browser settle.

    After the first block we mark the session blocked and route directly to the
    browser, since the plain client won't recover within the run."""
    global _SESSION_BLOCKED

    # Fast path: session already known-blocked → go straight to the browser.
    if _SESSION_BLOCKED:
        br = _browser_get(url, full=full)
        if br is not None and br.status_code in (200, 404) and br.text:
            return br
        return None

    backoff = 1.0
    saw_block = False
    for attempt in range(tries):
        try:
            r = session.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200:
                return r
            if r.status_code == 404:
                return r  # caller decides
            if r.status_code in (403, 429):
                saw_block = True
                break  # don't waste retries — escalate to the browser now
            print(f"  ! {url} → HTTP {r.status_code} (attempt {attempt + 1})", file=sys.stderr)
        except requests.RequestException as e:
            print(f"  ! {url} → {e.__class__.__name__} (attempt {attempt + 1})", file=sys.stderr)
        time.sleep(backoff)
        backoff *= 2

    # Blocked → mark the session and try the browser fallback.
    if saw_block:
        if not _SESSION_BLOCKED:
            print("  → cars.com anti-bot detected — switching to stealth browser "
                  "for the rest of this run.", file=sys.stderr, flush=True)
            _SESSION_BLOCKED = True
        br = _browser_get(url, full=full)
        if br is not None and br.status_code in (200, 404) and br.text:
            return br
    return None


def _with_page(url: str, page: int) -> str:
    """Replace/insert `page=` query param without clobbering the rest."""
    parsed = urlparse(url)
    qs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k != "page"]
    qs.append(("page", str(page)))
    return urlunparse(parsed._replace(query=urlencode(qs, doseq=True)))


def _infer_default_trim(url: str) -> str | None:
    """Pull the trim slug from ?trims[]=bmw-x7-m60i and return it title-cased."""
    for k, v in parse_qsl(urlparse(url).query, keep_blank_values=True):
        if k in ("trims[]", "trims") and v:
            tail = v.rsplit("-", 1)[-1]
            # canonicalise "m60i" → "M60i"
            return tail[0].upper() + tail[1:] if tail else None
    return None


def _parse_cards(html: str) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for m in CARD_RE.finditer(html):
        listing_id = m.group(1)
        try:
            blob = json.loads(html_lib.unescape(m.group(2)))
        except json.JSONDecodeError:
            continue
        blob["_listingId"] = listing_id
        cards.append(blob)
    return cards


def _scrape_search(url: str, max_pages: int, session: requests.Session) -> list[dict[str, Any]]:
    """Walk paginated search URL until no new listings come back."""
    seen: set[str] = set()
    all_cards: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        page_url = _with_page(url, page)
        print(f"[search] page {page}: {page_url}", flush=True)
        r = _get(page_url, session, full=True)
        if r is None:
            print(f"  ! page {page} unreachable – stopping")
            break
        cards = _parse_cards(r.text)
        if not cards:
            print(f"  · no cards on page {page} – done")
            break
        new = [c for c in cards if c.get("vin") and c["vin"] not in seen]
        for c in new:
            seen.add(c["vin"])
        all_cards.extend(new)
        print(f"  + {len(cards)} cards ({len(new)} new, {len(seen)} total)")
        if not new:
            # likely reached the end (cars.com clamps large page numbers to the last page)
            break
        time.sleep(1.0)
    return all_cards


def _extract_vdp(html: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "dealerName": None,
        "dealerCity": None,
        "dealerState": None,
        "dealerUrl": None,
        "dealerPhone": None,
        "stockNumber": None,
        "extColor": None,
        "interiorColor": None,
        "trim": None,
    }

    # og:description → dealer name + city + state (authoritative)
    m = OG_DESC_RE.search(html)
    if m:
        out["dealerName"] = html_lib.unescape(m.group("name").strip())
        out["dealerCity"] = html_lib.unescape(m.group("city").strip())
        out["dealerState"] = m.group("state").strip().upper()

    # Fallback: dealer_name data blob field
    if not out["dealerName"]:
        m = DEALER_NAME_RE.search(html)
        if m:
            out["dealerName"] = html_lib.unescape(m.group(1).strip())

    # Cars.com dealer profile URL – key off customer_id so we don't accidentally
    # pick up a "similar dealers" recommendation link.
    m = CUSTOMER_ID_RE.search(html)
    if m:
        cid = m.group(1)
        pm = re.search(
            rf'href="(https?://www\.cars\.com/dealers/{re.escape(cid)}/[^/"#]+/)"',
            html,
        )
        if pm:
            out["dealerUrl"] = pm.group(1)
        else:
            # Any bare /dealers/{cid}/slug/ link on the page (href may be relative)
            pm = re.search(rf'/dealers/{re.escape(cid)}/([^/"#]+)/', html)
            if pm:
                out["dealerUrl"] = f"https://www.cars.com/dealers/{cid}/{pm.group(1)}/"

    m = STOCK_NUM_RE.search(html)
    if m:
        out["stockNumber"] = m.group(1).strip()

    m = EXT_COLOR_RE.search(html)
    if m:
        out["extColor"] = m.group(1).strip()
    m = INT_COLOR_RE.search(html)
    if m:
        out["interiorColor"] = m.group(1).strip()
    m = TRIM_RE.search(html)
    if m:
        out["trim"] = (m.group(1) or m.group(2)).strip()

    # Phone: first plausible US phone number in the page (cars.com injects the
    # dealer-routed DNI number near the "Call now" button)
    m = PHONE_RE.search(html)
    if m:
        out["dealerPhone"] = _normalize_phone("".join(m.groups()))

    return out


def _fetch_vdp(listing_id: str, session: requests.Session) -> tuple[dict[str, Any], str]:
    """Return (extracted_fields, vdpStatus)."""
    url = VDP_BASE.format(listing_id=listing_id)
    r = _get(url, session)
    if r is None:
        return {}, "blocked"
    if r.status_code == 404:
        return {}, "not_found"
    if r.status_code != 200:
        return {}, "blocked"
    return _extract_vdp(r.text), "ok"


def _to_record(card: dict[str, Any], vdp: dict[str, Any], vdp_status: str, default_trim: str | None = None) -> dict[str, Any]:
    vin = card.get("vin")
    listing_id = card["_listingId"]
    url = VDP_BASE.format(listing_id=listing_id)

    def _maybe_int(v: Any) -> int | None:
        try:
            return int(str(v).replace(",", ""))
        except (TypeError, ValueError):
            return None

    year = _maybe_int(card.get("year"))
    price = _maybe_int(card.get("price"))
    mileage = _maybe_int(card.get("mileage"))

    return {
        "vin": vin,
        "stockNumber": vdp.get("stockNumber"),
        "year": year,
        "make": card.get("make"),
        "model": card.get("model"),
        "trim": card.get("trim") or vdp.get("trim") or default_trim,
        "odometer": mileage,
        "internetPrice": price,
        "extColor": vdp.get("extColor"),
        "interiorColor": vdp.get("interiorColor"),
        "certified": bool(card.get("cpoIndicator")),
        "daysOnLot": None,
        "dateInStock": None,
        "nhtsaUrl": f"https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{vin}?format=json" if vin else None,
        "carfaxPaywallUrl": f"https://www.carfax.com/VehicleHistory/p/Report.cfx?partner=ADV_0&vin={vin}" if vin else None,
        "carfaxUrl": None,
        "dealerName": vdp.get("dealerName"),
        "dealerCity": vdp.get("dealerCity"),
        "dealerState": vdp.get("dealerState"),
        "dealerUrl": vdp.get("dealerUrl"),
        "dealerPhone": vdp.get("dealerPhone"),
        "platform": "cars.com",
        "type": "cpo" if card.get("cpoIndicator") else (str(card.get("stockType", "used")).lower() or "used"),
        "link": url,
        "vinLink": url,
        "resolvedLink": url,
        "vdpStatus": vdp_status,
    }


def _warmup(url: str) -> int:
    """Headed browser so the user can clear DataDome once; cookies persist in the
    dedicated profile for subsequent headless runs."""
    global _PW_HEADLESS
    _PW_HEADLESS = False
    print("Opening a visible Chrome window on cars.com.", flush=True)
    print("→ Solve any CAPTCHA / 'verify you are human' challenge, wait for the "
          "real page to load, then press Enter here.", flush=True)
    ctx = _ensure_browser()
    if ctx is None:
        print("Could not launch a browser — is Chrome installed? "
              "(uv run playwright install chrome)", file=sys.stderr)
        return 1
    page = ctx.new_page()
    try:
        page.goto(url, timeout=TIMEOUT * 1000, wait_until="domcontentloaded")
    except Exception as e:
        print(f"  (navigation warning: {e.__class__.__name__})", file=sys.stderr)
    try:
        input("Press Enter once the page is fully loaded and unblocked… ")
    except EOFError:
        page.wait_for_timeout(30000)
    html = page.content()
    ok = "og:description" in html or '<fuse-card' in html or len(html) > 40000
    print("Trust cookies saved." if ok else
          "Page still looks blocked — you may need to interact more before pressing Enter.",
          flush=True)
    page.close()
    _shutdown_browser()
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True, help="Cars.com shopping-results URL to crawl")
    ap.add_argument("--max-pages", type=int, default=15)
    ap.add_argument("--out", default="cars_com_results.json")
    ap.add_argument("--exclude-states", default="CA", help="Comma-separated state codes to drop (default: CA)")
    ap.add_argument("--workers", type=int, default=6, help="Concurrent VDP fetches")
    ap.add_argument("--skip-vdp", action="store_true",
                    help="Skip per-listing VDP fetches (no dealer name/phone/colors, "
                         "and no state filtering since state comes from the VDP). "
                         "Much faster when cars.com is anti-botting the run.")
    ap.add_argument("--warmup", action="store_true",
                    help="Open cars.com in a visible browser so you can solve the "
                         "DataDome anti-bot challenge once by hand. The dedicated "
                         "profile banks the trust cookies, after which normal "
                         "headless runs sail through. Run this first when blocked.")
    args = ap.parse_args()

    if args.warmup:
        return _warmup(args.url)

    exclude = {s.strip().upper() for s in args.exclude_states.split(",") if s.strip()}
    print(f"Excluding dealer states: {sorted(exclude) or '(none)'}", flush=True)

    session = requests.Session()
    default_trim = _infer_default_trim(args.url)
    if default_trim:
        print(f"Default trim (from URL filter): {default_trim}", flush=True)
    cards = _scrape_search(args.url, args.max_pages, session)
    print(f"\n[search] collected {len(cards)} unique listings", flush=True)
    if not cards:
        return 0

    if args.skip_vdp:
        print("\n[vdp] skipped (--skip-vdp) — dealer fields will be empty", flush=True)
        records = [_to_record(c, {}, "skipped", default_trim=default_trim) for c in cards]
    else:
        # Once the session is browser-bound, VDP fetches are serialized through a
        # single browser anyway, so extra threads just contend on the lock. Drop
        # to 1 worker in that case to keep output orderly.
        workers = 1 if _SESSION_BLOCKED else args.workers
        print(f"\n[vdp] fetching dealer info for {len(cards)} listings ({workers} worker"
              f"{'s' if workers != 1 else ''})…", flush=True)
        records = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_fetch_vdp, c["_listingId"], session): c
                for c in cards
            }
            for i, fut in enumerate(as_completed(futures), 1):
                card = futures[fut]
                try:
                    vdp, status = fut.result()
                except Exception as e:
                    print(f"  ! VDP {card['_listingId']} failed: {e}", file=sys.stderr)
                    vdp, status = {}, "blocked"
                rec = _to_record(card, vdp, status, default_trim=default_trim)
                dealer = f"{rec.get('dealerName') or '?'} ({rec.get('dealerCity') or '?'}, {rec.get('dealerState') or '?'})"
                print(f"  [{i:>3}/{len(cards)}] {rec['vin']} {rec['year']} {rec['trim']} · {dealer} · {status}", flush=True)
                records.append(rec)

    before = len(records)
    filtered = [r for r in records if (r.get("dealerState") or "").upper() not in exclude]
    print(f"\n[filter] dropped {before - len(filtered)} records in {sorted(exclude)}", flush=True)

    # Also dedupe by VIN within the scrape itself, preferring records we actually hit
    by_vin: dict[str, dict[str, Any]] = {}
    for r in filtered:
        vin = r.get("vin")
        if not vin:
            continue
        existing = by_vin.get(vin)
        if not existing or (existing.get("vdpStatus") != "ok" and r.get("vdpStatus") == "ok"):
            by_vin[vin] = r
    final = list(by_vin.values())

    out_path = pathlib.Path(args.out)
    out_path.write_text(json.dumps(final, indent=2))
    print(f"\nWrote {len(final)} records → {out_path}", flush=True)
    _shutdown_browser()
    return 0


if __name__ == "__main__":
    sys.exit(main())
