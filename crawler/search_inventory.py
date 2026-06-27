"""
search_inventory.py – Search for a specific vehicle across ALL US BMW dealers.

Covers two platforms in just a handful of API calls:
  • Dealer.com   : one call per year to siteId=bmwgroup (~275 DDC dealers)
  • DealerInspire: one batch call per Algolia app      (~44 DI dealers)

After collecting inventory, each dealer VDP is fetched concurrently (8 threads)
to extract the signed CarFax URL that lives in the page HTML.  Use --skip-fetch
to skip this step for faster (CarFax-less) runs.

Usage
-----
python3 search_inventory.py                          # all defaults
python3 search_inventory.py --trim "xDrive40i" --max-miles 20000 --out x7_40i.json
python3 search_inventory.py --skip-fetch             # fast, no CarFax

Defaults: year=2025-2026, model=X7, trim=M60i, miles=60–15000, type=all

Output fields per vehicle:
  vin, stockNumber, year, make, model, trim, type, odometer, internetPrice,
  extColor, certified, daysOnLot, dateInStock,
  nhtsaUrl, carfaxPaywallUrl, carfaxUrl (signed, from VDP),
  dealerName, dealerCity, dealerState, dealerUrl, platform,
  link (API URL), vinLink (VIN slug fallback), resolvedLink (after redirect)
"""

import argparse
import json
import pathlib
import re
import sys
import time
from collections import defaultdict
from datetime import date, datetime

import requests
from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────────────────────────────────────
# Dealer URL resolution (DuckDuckGo fallback for blank dealerUrl)
# ──────────────────────────────────────────────────────────────────────────────

def _load_perplexity_dealers(path: str = "perplexity_dealers.txt") -> dict[str, str]:
    """Load the perplexity dealer list into a lookup dict.

    Builds two indexes from the CSV (Dealer Name, City, State, Website):
      • exact key  : "{name}|{city}|{state}"   (all lowercase)
      • name-only  : "{name}"                   (lowercase, for looser matches)

    Returns a flat dict keyed by both patterns → URL (https://www.{domain}).
    """
    lookup: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as f:
            next(f)   # skip header
            for line in f:
                parts = line.strip().split(",", 3)
                if len(parts) < 4:
                    continue
                dname, city, state, website = (p.strip() for p in parts)
                url = website if website.startswith("http") else f"https://{website}"
                n, c, s = dname.lower(), city.lower(), state.lower()
                lookup[f"{n}|{c}|{s}"] = url
                lookup[n] = url   # name-only fallback (last one wins — acceptable)
    except FileNotFoundError:
        pass
    return lookup


# Module-level cache — loaded once
_PERPLEXITY_LOOKUP: dict[str, str] = {}


def _get_perplexity_lookup() -> dict[str, str]:
    global _PERPLEXITY_LOOKUP
    if not _PERPLEXITY_LOOKUP:
        _PERPLEXITY_LOOKUP = _load_perplexity_dealers()
    return _PERPLEXITY_LOOKUP


def _resolve_dealer_url(name: str, city: str, state: str) -> str | None:
    """Resolve a missing dealer website URL.

    Priority order (each step is only reached if the previous fails):
      1. perplexity_dealers.txt — exact (name+city+state) then name-only match
      2. Slug-based HEAD check  — instant; works for predictably-named BMW dealers
      3. DuckDuckGo search      — reliable fallback; first non-aggregator result
    """
    from urllib.parse import urlparse

    SKIP_DOMAINS = {"cars.com", "edmunds.com", "autotrader.com", "facebook.com",
                    "yelp.com", "carfax.com", "cargurus.com", "google.com",
                    "drivefivestar.com"}

    n, c, s = name.lower(), city.lower(), state.lower()

    # ── 1. perplexity_dealers.txt ────────────────────────────────────────────
    lkp = _get_perplexity_lookup()
    url = lkp.get(f"{n}|{c}|{s}") or lkp.get(n)
    if url:
        return url

    # ── 2. Slug-based HEAD check ─────────────────────────────────────────────
    slug = re.sub(r"[^a-z0-9]", "", n)
    for candidate in [f"https://www.{slug}.com", f"https://www.{slug}bmw.com"]:
        try:
            r = requests.head(candidate, timeout=6, allow_redirects=True)
            if r.status_code in (200, 301, 302, 403):
                return candidate
        except Exception:
            pass

    # ── 3. DuckDuckGo search ─────────────────────────────────────────────────
    try:
        from ddgs import DDGS
        results = list(DDGS().text(f"{name} {city} {state} BMW dealer", max_results=5))
        for r in results:
            href = r.get("href", "")
            domain = urlparse(href).netloc.lstrip("www.")
            if domain and not any(s in domain for s in SKIP_DOMAINS):
                parsed = urlparse(href)
                return f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        pass

    return None


def resolve_missing_dealer_urls(vehicles: list[dict]) -> list[dict]:
    """For any vehicle with an empty dealerUrl, resolve it and rebuild vinLink."""
    missing = [(i, v) for i, v in enumerate(vehicles) if not v.get("dealerUrl")]
    if not missing:
        return vehicles

    # Deduplicate lookups by dealer name so we don't search the same dealer twice
    name_to_url: dict[str, str | None] = {}

    for i, v in missing:
        name  = v.get("dealerName") or ""
        city  = v.get("dealerCity") or ""
        state = v.get("dealerState") or ""
        key   = f"{name}|{city}|{state}"

        if key not in name_to_url:
            print(f"  Resolving URL for: {name} ({city}, {state})…", end=" ")
            name_to_url[key] = _resolve_dealer_url(name, city, state)
            print(name_to_url[key] or "not found")

        resolved = name_to_url[key]
        if resolved:
            vtype     = v.get("type", "used")
            certified = bool(v.get("certified"))
            vin       = v.get("vin")
            vehicles[i] = {
                **v,
                "dealerUrl": resolved,
                "vinLink":   _vin_slug_url(resolved, vin,
                                 v.get("year"), v.get("make"), v.get("model"),
                                 certified, vtype) or v.get("vinLink"),
            }

    return vehicles


# ──────────────────────────────────────────────────────────────────────────────
# Credentials (from HAR / prior discovery)
# ──────────────────────────────────────────────────────────────────────────────
def _vin_links(vin: str | None) -> dict:
    """Return VIN-based links.

    carfaxUrl   – signed free CarFax report, filled by --fetch-details from
                  the dealer VDP HTML (null until then).
    carfaxPaywallUrl – always-constructable CarFax link; leads to full report
                       behind a paywall / upsell if the user has no account.
    nhtsaUrl    – NHTSA VIN decode (free, official recall/spec data).
    """
    if not vin:
        return {"nhtsaUrl": None, "carfaxPaywallUrl": None, "carfaxUrl": None}
    return {
        "nhtsaUrl":          f"https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{vin}?format=json",
        "carfaxPaywallUrl":  f"https://www.carfax.com/VehicleHistory/p/Report.cfx?partner=ADV_0&vin={vin}",
        "carfaxUrl":         None,   # filled by fetch_details() if the VDP is accessible
    }


def _vin_slug_url(dealer_url: str, vin: str | None, year, make, model,
                  certified: bool, vehicle_type: str) -> str | None:
    """Construct a VIN-based slug URL as a fallback for dealers that don't
    use the DDC UUID format.

    Pattern used by both DI and many DDC dealers:
      {domain}/inventory/{condition}-{year}-{make}-{model}-{vin}/
    """
    if not (dealer_url and vin and year and model):
        return None
    if certified:
        condition = "certified-used"
    elif vehicle_type == "new":
        condition = "new"
    else:
        condition = "used"
    make_slug  = (make or "bmw").lower().replace(" ", "-")
    model_slug = model.lower().replace(" ", "-")
    return f"{dealer_url.rstrip('/')}/inventory/{condition}-{year}-{make_slug}-{model_slug}-{vin.lower()}/"


# ─── CarFax extraction via VDP HTML ───────────────────────────────────────────

VDP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,*/*;q=0.9",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}

from carfax_utils import (
    badge_log_suffix,
    extract_from_html,
    merge_carfax,
)

TEL_RE = re.compile(r'href=["\']tel:([^"\']+)["\']', re.IGNORECASE)


def _normalize_phone(raw: str | None) -> str | None:
    """Return a phone as '###-###-####', or None if unparseable."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return f"{digits[0:3]}-{digits[3:6]}-{digits[6:10]}"


def _extract_vdp_signals(html: str) -> dict:
    """Parse CarFax + contact signals (signed URL, owner count, phone) from raw VDP HTML."""
    out: dict = extract_from_html(html)
    # First tel: link on the page — typically the dealer's main sales line.
    for raw in TEL_RE.findall(html):
        phone = _normalize_phone(raw)
        if phone:
            out["dealerPhone"] = phone
            break
    return out


def _best_urls(v: dict) -> list[str]:
    """Return candidate VDP URLs to try, deduped, most specific first."""
    seen: set[str] = set()
    urls: list[str] = []
    for u in [v.get("link"), v.get("vinLink")]:
        if u and u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


def _extract_cfx_requests(v: dict) -> dict | None:
    """Pass 1: fast requests-based fetch.

    Returns:
      - record with signals + ``vdpStatus='ok'`` on HTTP 200
      - record with ``vdpStatus='not_found'`` when every attempted URL returned
        404 (listing almost certainly removed — skip browser retries)
      - None on any other failure (timeout / 403 / Cloudflare challenge /
        etc.); caller will retry via the browser passes
    """
    urls = _best_urls(v)
    only_404 = bool(urls)   # stays True only if every tried URL returns 404
    for url in urls:
        try:
            r = requests.get(url, headers=VDP_HEADERS, timeout=10, allow_redirects=True)
            if r.status_code == 200:
                signals = _extract_vdp_signals(r.text)
                if v.get("dealerPhone"):
                    signals.pop("dealerPhone", None)   # prefer authoritative source (DDC)
                return {**v, "resolvedLink": r.url, "vdpStatus": "ok", **signals}
            if r.status_code != 404:
                only_404 = False
        except Exception:
            only_404 = False
    if only_404:
        return {**v, "vdpStatus": "not_found"}
    return None


BROWSER_PROFILE_DIR = str(pathlib.Path.home() / ".bmw_browser_profile")
CHROME_DEBUG_PORT   = 9222
CHROME_DEBUG_URL    = f"http://localhost:{CHROME_DEBUG_PORT}"

CHROME_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]

# Separate user-data-dir so we can run alongside the user's normal Chrome,
# but still pre-populate it with cookies from the real Default profile.
CHROME_DEBUG_PROFILE = str(pathlib.Path.home() / ".chrome_debug_session")

_chrome_proc = None   # subprocess.Popen handle if we launched Chrome ourselves


def ensure_chrome_debug() -> bool:
    """Ensure Chrome is running with --remote-debugging-port=9222.

    Uses a dedicated user-data-dir (~/.chrome_debug_session) so it runs as a
    separate instance alongside any already-open Chrome. On first launch the dir
    is empty (fresh profile); subsequent runs accumulate trust cookies.

    Called once at script startup so Chrome is warm by VDP-fetch time.
    Returns True if the debug endpoint is reachable.
    """
    import subprocess, time, urllib.request
    global _chrome_proc

    def _reachable() -> bool:
        try:
            urllib.request.urlopen(f"{CHROME_DEBUG_URL}/json/version", timeout=1)
            return True
        except Exception:
            return False

    if _reachable():
        print("Chrome debug port already open — connecting to existing instance.", flush=True)
        return True

    chrome_bin = next((p for p in CHROME_PATHS if pathlib.Path(p).exists()), None)
    if not chrome_bin:
        print("Chrome not found — browser pass will use headless fallback.", flush=True)
        return False

    pathlib.Path(CHROME_DEBUG_PROFILE).mkdir(parents=True, exist_ok=True)

    print("Starting Chrome with --remote-debugging-port=9222 …", flush=True)
    _chrome_proc = subprocess.Popen(
        [
            chrome_bin,
            f"--remote-debugging-port={CHROME_DEBUG_PORT}",
            f"--user-data-dir={CHROME_DEBUG_PROFILE}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=Translate",
            "--disable-extensions",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    for _ in range(20):
        time.sleep(1)
        if _reachable():
            print("Chrome ready.", flush=True)
            return True

    print("Chrome did not start in time — browser pass will use headless fallback.", flush=True)
    return False


def shutdown_chrome_if_owned() -> None:
    """Terminate the Chrome process we launched (if any)."""
    global _chrome_proc
    if _chrome_proc and _chrome_proc.poll() is None:
        _chrome_proc.terminate()
        _chrome_proc = None


def _connect_real_chrome(pw):
    """Connect to Chrome's CDP debug endpoint (assumed already started by ensure_chrome_debug).

    Returns (browser, False) on success, (None, False) if unreachable.
    owned is always False — lifecycle is managed by ensure_chrome_debug /
    shutdown_chrome_if_owned at the script level.
    """
    try:
        # no_defaults=True avoids Browser.setDownloadBehavior, which Chrome
        # rejects when attaching over CDP (Playwright <1.60 always failed here).
        browser = pw.chromium.connect_over_cdp(
            CHROME_DEBUG_URL, timeout=10000, no_defaults=True
        )
        return browser, False
    except TypeError:
        # Older Playwright without no_defaults — best-effort connect.
        try:
            browser = pw.chromium.connect_over_cdp(CHROME_DEBUG_URL, timeout=10000)
            return browser, False
        except Exception as e:
            print(f"  CDP connect failed ({e.__class__.__name__}) — "
                  "falling back to Playwright profile", flush=True)
            return None, False
    except Exception as e:
        print(f"  CDP connect failed ({e.__class__.__name__}) — "
              "falling back to Playwright profile", flush=True)
        return None, False


def _page_result(page, v: dict) -> dict | None:
    """Extract CarFax signals from an already-navigated browser page."""
    page.wait_for_timeout(_PW_SETTLE)
    title = page.evaluate("() => document.title") or ""
    if not title:
        return None   # encrypted / challenge not solved

    html = page.content()
    signals = extract_from_html(html)

    # Dynamic DOM links may not appear in static HTML — merge if JS finds them.
    cfx_links: list[str] = page.evaluate("""
        () => Array.from(
                document.querySelectorAll('a[href*="carfax.com/vehiclehistory/ar20"]')
              ).map(a => a.href)
    """)
    if cfx_links:
        signals = merge_carfax(signals, {"carfaxUrl": cfx_links[0]})

    phone = None
    for raw in page.evaluate("""
        () => Array.from(
                document.querySelectorAll('a[href^="tel:"]')
              ).map(a => a.getAttribute('href').replace(/^tel:/i, ''))
    """):
        phone = _normalize_phone(raw)
        if phone:
            break
    if not phone:
        for raw in TEL_RE.findall(html):
            phone = _normalize_phone(raw)
            if phone:
                break

    out = {**v, "resolvedLink": page.url, "vdpStatus": "ok", **signals}
    if phone and not v.get("dealerPhone"):
        out["dealerPhone"] = phone
    return out


_PW_TIMEOUT  = 15000   # ms — per page.goto in browser passes
_PW_SETTLE   = 2500    # ms — settle time after page load


def _extract_cfx_playwright(pw_page, v: dict) -> dict | None:
    """Pass 2 — playwright + stealth + real Chrome (persistent context).

    Handles dealers that 403 plain HTTP but pass Cloudflare JS challenges.
    navigator.webdriver is patched to False via playwright-stealth.
    """
    from playwright.sync_api import Error as PWError
    for url in _best_urls(v):
        try:
            resp = pw_page.goto(url, wait_until="domcontentloaded", timeout=_PW_TIMEOUT)
            status = resp.status if resp else 0
            if status not in (200, 304):
                continue
            result = _page_result(pw_page, v)
            if result is not None:
                return result
        except PWError:
            continue
        except Exception:
            continue
    return None


def _extract_cfx_camoufox(cfx_page, v: dict) -> dict | None:
    """Pass 3 — camoufox (Firefox, fresh fingerprint each run).

    Final fallback for pages that Chrome still can't bypass.
    """
    for url in _best_urls(v):
        try:
            resp = cfx_page.goto(url, wait_until="domcontentloaded", timeout=_PW_TIMEOUT)
            status = resp.status if resp else 0
            if status not in (200, 304):
                continue
            result = _page_result(cfx_page, v)
            if result is not None:
                return result
        except Exception:
            continue
    return None


def _run_browser_pass(label: str, n_total: int, idxs: list[int],
                      vehicles: list[dict], results: list,
                      page) -> tuple[list[int], int, int]:
    """Iterate idxs, navigate each VDP in page, collect results.

    Returns (still_blocked, ok_count, cfx_count).
    """
    still_blocked: list[int] = []
    ok = cfx = 0
    for n, idx in enumerate(idxs, 1):
        v = vehicles[idx]
        dealer = (v.get("dealerName") or "")[:30]
        print(f"    [{n}/{len(idxs)}] {v.get('vin')} {dealer}…", end=" ", flush=True)
        result = _extract_cfx_playwright(page, v)
        if result is not None:
            results[idx] = result
            ok += 1
            flag = "✓cfx" if result.get("carfaxUrl") else "ok"
            print(flag + badge_log_suffix(result), flush=True)
            if result.get("carfaxUrl"):
                cfx += 1
        else:
            still_blocked.append(idx)
            print("blocked", flush=True)
    print(f"  {label}: {ok}/{len(idxs)} rendered, {cfx} CarFax URLs")
    return still_blocked, ok, cfx


def fetch_details(vehicles: list[dict], workers: int = 8) -> list[dict]:
    """Two-pass VDP fetcher to extract CarFax signals and resolve links.

    Pass 1  requests (parallel)    – fast HTTP; works for open sites.
    Pass 2  real Chrome via CDP    – connects to the user's running Chrome
                                     (or launches one) so all Cloudflare /
                                     DataDome trust cookies & fingerprints
                                     are real. Falls back to a headless
                                     persistent-profile context if Chrome
                                     cannot be reached.

    Vehicles whose pages remain inaccessible retain vinLink + carfaxPaywallUrl.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # ── Pass 1: requests (parallel) ──────────────────────────────────────────
    results: list[dict | None] = [None] * len(vehicles)
    needs_browser: list[int] = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_extract_cfx_requests, v): i
                   for i, v in enumerate(vehicles)}
        for fut in as_completed(futures):
            idx = futures[fut]
            result = fut.result()
            if result is not None:
                results[idx] = result
            else:
                needs_browser.append(idx)

    pass1_ok    = sum(1 for r in results if r and r.get("vdpStatus") == "ok")
    pass1_404   = sum(1 for r in results if r and r.get("vdpStatus") == "not_found")
    pass1_cfx   = sum(1 for r in results if r and r.get("carfaxUrl"))
    print(f"  Pass 1 (requests):   {pass1_ok}/{len(vehicles)} reached, "
          f"{pass1_404} not-found (likely sold), "
          f"{pass1_cfx} CarFax URLs")

    if not needs_browser:
        total_cfx = sum(1 for r in results if r and r.get("carfaxUrl"))
        print(f"  Total CarFax URLs: {total_cfx}/{len(vehicles)}")
        return [r if r is not None else vehicles[i] for i, r in enumerate(results)]

    # ── Pass 2: real Chrome via CDP (primary) → headless fallback ────────────
    print(f"  Pass 2 (browser): trying {len(needs_browser)} pages…")
    still_blocked: list[int] = needs_browser

    try:
        from playwright.sync_api import sync_playwright
        from playwright_stealth.stealth import Stealth

        with sync_playwright() as p:
            browser, owned = _connect_real_chrome(p)

            if browser:
                # Use a fresh incognito-style context so we don't pollute the
                # user's tabs, but inherit the real Chrome's fingerprint.
                ctx      = browser.new_context()
                pw_page  = ctx.new_page()
                still_blocked, _, _ = _run_browser_pass(
                    "Pass 2 (real Chrome CDP)", len(vehicles),
                    needs_browser, vehicles, results, pw_page,
                )
                pw_page.close()
                ctx.close()
                if owned:
                    browser.close()
            else:
                print("  real Chrome unavailable — trying headless persistent profile…")

            # Fallback: headless persistent-profile context
            if still_blocked:
                stealth = Stealth()
                ctx2 = p.chromium.launch_persistent_context(
                    BROWSER_PROFILE_DIR,
                    channel="chrome", headless=True,
                    args=["--disable-blink-features=AutomationControlled"],
                    ignore_default_args=["--enable-automation"],
                )
                stealth.apply_stealth_sync(ctx2)
                fb_page = ctx2.new_page()
                still_blocked, _, _ = _run_browser_pass(
                    "Pass 2 fallback (headless Chrome)", len(vehicles),
                    still_blocked, vehicles, results, fb_page,
                )
                fb_page.close()
                ctx2.close()

    except ImportError:
        print("  playwright not installed — skipping browser pass "
              "(pip install playwright playwright-stealth && "
              "python -m playwright install chrome)")

    # Fill any remaining blocked vehicles with their original records, marked
    # so the UI can flag them as "couldn't verify — needs manual research".
    for idx in still_blocked:
        if results[idx] is None:
            results[idx] = {**vehicles[idx], "vdpStatus": "blocked"}

    total_cfx = sum(1 for r in results if r and r.get("carfaxUrl"))
    total_404 = sum(1 for r in results if r and r.get("vdpStatus") == "not_found")
    total_blk = sum(1 for r in results if r and r.get("vdpStatus") == "blocked")
    print(f"  Total CarFax URLs: {total_cfx}/{len(vehicles)}  "
          f"({total_404} likely-sold, {total_blk} unreachable)")
    return [r if r is not None else vehicles[i] for i, r in enumerate(results)]


def _days_since(date_str: str | None) -> int | None:
    """Compute days since a date string.  Handles 'Jan 28, 2026' (DDC) and
    'MM/DD/YYYY' (DI) formats."""
    if not date_str:
        return None
    for fmt in ("%b %d, %Y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            d = datetime.strptime(date_str.strip(), fmt).date()
            return (date.today() - d).days
        except ValueError:
            continue
    return None


def _to_int(val) -> int | None:
    """Safely convert a price value (int, float, or string like '$119,255') to int."""
    if val is None:
        return None
    try:
        return int(str(val).replace("$", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return None


ALGOLIA_APPS = {
    "V3ZOVI2QFZ": "ec7553dd56e6d4c8bb447a0240e7aab3",
    "EHWUW84XVK": "fb58227032e79f03b9b820cbaea7f8fb",
}

DDC_ENDPOINT = "https://www.bmwofdallas.com/api/widget/ws-inv-data/getInventory"

DDC_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/json",
    "Referer": "https://www.bmwofdallas.com/",
}


# ──────────────────────────────────────────────────────────────────────────────
# DDC search  (single call via bmwgroup OEM account)
# ──────────────────────────────────────────────────────────────────────────────
# DDC's `pageAlias` is the single biggest knob: each page corresponds to a
# different inventory partition on the bmwgroup site. AUTO_USED returns used
# vehicles only; DEFAULT_AUTO_NEW returns new vehicles only. There is no
# combined "ALL" page that returns both, so when the caller wants both we
# fire one call per page and union the results.
DDC_PAGE_ALIAS = {
    "used": (
        "INVENTORY_LISTING_TARGETED_RESULTS_AUTO_USED",
        "v9_INVENTORY_LISTING_TARGETED_RESULTS_AUTO_USED_V1_1",
    ),
    "new": (
        "INVENTORY_LISTING_DEFAULT_AUTO_NEW",
        "v9_INVENTORY_LISTING_DEFAULT_AUTO_NEW_V1_1",
    ),
}


def _search_ddc_single(make: str, model: str, year: int | None, trim: str | None,
                       condition: str) -> list[dict]:
    """Single DDC query for one (year, condition) pair.

    `condition` must be 'new' or 'used' — it selects the DDC pageAlias.
    Pass `year=None` to skip the year filter. The returned records have
    their ``type`` field set to `condition` so the downstream merge can
    distinguish them even when the dealer doesn't echo the type back."""
    page_alias, page_id = DDC_PAGE_ALIAS[condition]

    inv_params: dict = {"make": make, "model": model}
    if year is not None:
        inv_params["year"] = str(year)
    if trim:
        inv_params["trim"] = trim
    inv_params["compositeType"] = condition

    payload = {
        "siteId": "bmwgroup",
        "locale": "en_US",
        "device": "DESKTOP",
        "pageAlias": page_alias,
        "pageId": page_id,
        "windowId": "inventory-data-bus2",
        "widgetName": "ws-inv-data",
        "inventoryParameters": inv_params,
        "preferences": {
            "pageSize": "500",
            "listing.config.id": f"auto-{condition}",
            "removeEmptyFacets": "true",
            "removeEmptyConstraints": "true",
            "required.display.sets": "TITLE,IMAGE_ALT,IMAGE_TITLE,PRICE,FEATURED_ITEMS,CALLOUT,LISTING,HIGHLIGHTED_ATTRIBUTES",
            "required.display.attributes": (
                "accountCity,accountName,accountState,accountZipcode,"
                "accountId,odometer,internetPrice,vin,trim,gvTrim,"
                "year,make,model,certified,type,classification,"
                "extColor,interiorColor,mileage,stockNumber,daysOnLot,"
                "inventoryDate,location"
            ),
            "showFranchiseVehiclesOnly": "true",
            "sorts": "odometer,internetPrice",
        },
    }

    # bmwofdallas.com's DDC proxy occasionally 504s on specific OEM queries
    # (notably XM / year=2026 / new) even though retries usually succeed.
    data = None
    for attempt in range(3):
        r = requests.post(DDC_ENDPOINT, headers=DDC_HEADERS, json=payload, timeout=45)
        if r.status_code in (502, 503, 504):
            if attempt < 2:
                wait = 2 ** attempt
                print(f"  DDC [{condition}] HTTP {r.status_code} for year={year} "
                      f"— retrying in {wait}s…", flush=True)
                time.sleep(wait)
                continue
        r.raise_for_status()
        data = r.json()
        break
    if data is None:
        raise RuntimeError(f"DDC [{condition}] failed for year={year}")

    raw_accounts: dict = data.get("accounts", {})
    vehicles = data.get("inventory", [])
    total = data.get("pageInfo", {}).get("totalCount", 0)
    print(f"  DDC [{condition}]: {total} vehicles from bmwgroup ({len(raw_accounts)} dealers)")

    results = []
    for v in vehicles:
        # Detailed fields live inside trackingAttributes at OEM-level queries
        tracking = {t["name"]: t.get("value") for t in v.get("trackingAttributes", [])}
        odometer_raw = tracking.get("odometer") or v.get("odometer")
        try:
            odometer = int(odometer_raw) if odometer_raw is not None else None
        except (ValueError, TypeError):
            odometer = None

        account_id = v.get("accountId") or ""
        account    = raw_accounts.get(account_id, {})
        addr       = account.get("address", {})
        raw_url    = account.get("url") or ""
        dealer_url = ("https://" + raw_url) if raw_url and not raw_url.startswith("http") else raw_url

        # Build absolute link – DDC API returns /{type}/BMW/{year}-...-{uuid}.htm
        # Some dealers also support /inventory/{condition}-{year}-{make}-{model}-{vin}/
        rel_link  = v.get("link") or ""
        full_link = rel_link if rel_link.startswith("http") else (dealer_url.rstrip("/") + rel_link) if dealer_url and rel_link else rel_link

        inventory_date = v.get("inventoryDate")
        vin       = v.get("vin")
        certified = bool(v.get("certified"))
        vtype     = v.get("type") or condition
        results.append({
            "vin":          vin,
            "stockNumber":  v.get("stockNumber"),
            "year":         v.get("year"),
            "make":         v.get("make"),
            "model":        v.get("model"),
            "trim":         v.get("trim"),
            "odometer":     odometer,
            "internetPrice": v.get("internetPrice"),   # null at OEM level; see link/dealerUrl for price
            "extColor":     tracking.get("exteriorColor") or v.get("extColor"),
            "interiorColor": tracking.get("interiorColor"),
            "certified":    certified,
            "daysOnLot":    _days_since(inventory_date),
            "dateInStock":  inventory_date,
            **_vin_links(vin),
            "dealerName":   account.get("name") or v.get("accountName"),
            "dealerCity":   addr.get("city") or v.get("accountCity"),
            "dealerState":  addr.get("state") or v.get("accountState"),
            "dealerUrl":    dealer_url,
            "dealerPhone":  _normalize_phone(account.get("phone")),
            "platform":     "dealercom",
            "type":         vtype,
            "link":         full_link,
            "vinLink":      _vin_slug_url(dealer_url, vin,
                                v.get("year"), v.get("make"), v.get("model"),
                                certified, vtype),
        })

    return results


def search_ddc(make: str, model: str, years: list[int] | None, trim: str | None,
               vehicle_type: str) -> list[dict]:
    """Multi-year, multi-condition wrapper: one DDC call per (year, condition).

    DDC's bmwgroup site exposes ``AUTO_USED`` and ``AUTO_NEW`` as separate
    pages — there's no combined endpoint — so when the caller wants both
    we have to fire one call per page. This matters especially for models
    like the XM where the vast majority of inventory is new: querying only
    the USED page returned ~21 vehicles, while NEW + USED returns ~240.
    """
    conditions = ["new", "used"] if vehicle_type == "all" else [vehicle_type]
    year_list = years if years else [None]
    all_results: list[dict] = []
    seen: set[str] = set()
    for yr in year_list:
        for cond in conditions:
            try:
                batch = _search_ddc_single(make, model, yr, trim, cond)
            except Exception as e:
                print(f"  DDC [{cond}] error for year={yr}: {e}")
                continue
            for rec in batch:
                key = rec.get("vin") or f"{rec.get('stockNumber')}-{rec.get('dealerName')}"
                if key not in seen:
                    seen.add(key)
                    all_results.append(rec)
    return all_results


# ──────────────────────────────────────────────────────────────────────────────
# DI / Algolia search  (batch query across all known DI dealer indexes)
# ──────────────────────────────────────────────────────────────────────────────
def _algolia_filter(make, model, years: list[int] | None, trim,
                    min_miles: int | None, max_miles: int | None) -> str:
    parts = [f'make:"{make}"', f'model:"{model}"']
    if years:
        if len(years) == 1:
            parts.append(f"year:{years[0]}")
        else:
            yr_min, yr_max = min(years), max(years)
            parts.append(f"year >= {yr_min} AND year <= {yr_max}")
    if trim:
        parts.append(f'trim:"{trim}"')
    if min_miles is not None:
        parts.append(f"miles > {min_miles}")
    if max_miles is not None:
        parts.append(f"miles < {max_miles}")
    return " AND ".join(parts)


def search_di(make: str, model: str, years: list[int] | None, trim: str | None,
              min_miles: int | None, max_miles: int | None,
              dealers_file: str = "dealers.json") -> list[dict]:
    """
    Batch-query all DealerInspire Algolia indexes in a single request per app.
    Algolia supports up to 1000 sub-queries per batch call.
    """
    with open(dealers_file) as f:
        dealers: list[dict] = json.load(f)

    di_dealers = [d for d in dealers if d.get("platform") == "dealerinspire" and d.get("di_index")]

    # Group by Algolia app
    by_app: dict[str, list[dict]] = defaultdict(list)
    for d in di_dealers:
        by_app[d["app_id"]].append(d)

    alg_filter = _algolia_filter(make, model, years, trim, min_miles, max_miles)
    params_str = (
        f"filters={requests.utils.quote(alg_filter)}"
        "&hitsPerPage=200"
        "&attributesToRetrieve=vin,stock,year,make,model,trim,miles,our_price,msrp,ext_color,certified,type,location,title,link,days_in_stock,date_in_stock"
    )

    results = []
    for app_id, app_dealers in by_app.items():
        if app_id not in ALGOLIA_APPS:
            print(f"  DI app {app_id}: skipped (unknown API key — {len(app_dealers)} dealers)")
            continue
        api_key = ALGOLIA_APPS[app_id]
        host    = f"https://{app_id.lower()}-dsn.algolia.net"

        requests_payload = [
            {"indexName": d["di_index"], "params": params_str}
            for d in app_dealers
        ]

        r = requests.post(
            f"{host}/1/indexes/*/queries",
            headers={
                "x-algolia-application-id": app_id,
                "x-algolia-api-key": api_key,
                "Content-Type": "application/json",
            },
            json={"requests": requests_payload},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()

        # Each element of "results" corresponds to one index query
        index_map = {d["di_index"]: d for d in app_dealers}
        hit_count = 0
        for i, res in enumerate(data.get("results", [])):
            dealer_info = app_dealers[i]
            for hit in res.get("hits", []):
                odometer_raw = hit.get("miles")
                try:
                    odometer = int(odometer_raw) if odometer_raw is not None else None
                except (ValueError, TypeError):
                    odometer = None

                # location is a string of option codes in DI, not a dict — use dealer_info
                dealer_website = dealer_info.get("website", "")
                link = hit.get("link") or ""
                # DI link from Algolia is already the VIN slug path e.g.
                # /inventory/used-2025-bmw-x7-m60i-awd-sport-utility-{vin}/
                # No secondary vinLink needed — it IS the VIN slug.
                full_link = link if link.startswith("http") else (dealer_website.rstrip("/") + "/" + link.lstrip("/")) if link else dealer_website
                date_in_stock = hit.get("date_in_stock")
                vin = hit.get("vin")
                results.append({
                    "vin":          vin,
                    "stockNumber":  hit.get("stock"),
                    "year":         hit.get("year"),
                    "make":         hit.get("make"),
                    "model":        hit.get("model"),
                    "trim":         hit.get("trim"),
                    "odometer":     odometer,
                    "internetPrice": _to_int(hit.get("our_price")) or _to_int(hit.get("msrp")),
                    "extColor":     hit.get("ext_color"),
                    "certified":    hit.get("certified"),
                    "daysOnLot":    hit.get("days_in_stock") or _days_since(date_in_stock),
                    "dateInStock":  date_in_stock,
                    **_vin_links(vin),
                    "dealerName":   dealer_info.get("name") or dealer_info.get("dealer_slug"),
                    "dealerCity":   dealer_info.get("city"),
                    "dealerState":  dealer_info.get("state"),
                    "dealerUrl":    dealer_website,
                    "dealerPhone":  None,   # filled in by VDP pass if available
                    "platform":     "dealerinspire",
                    "type":         hit.get("type") or "used",
                    "link":         full_link,
                    "vinLink":      full_link,   # DI links are already VIN slugs
                })
                hit_count += 1

        print(f"  DI app {app_id}: {len(app_dealers)} indexes → {hit_count} hits")

    return results


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────
def _parse_years(year_str: str | None) -> list[int] | None:
    """Parse --year argument: '2025' → [2025], '2025-2026' → [2025, 2026], None → None."""
    if not year_str:
        return None
    if "-" in year_str:
        parts = year_str.split("-", 1)
        try:
            lo, hi = int(parts[0]), int(parts[1])
            return list(range(lo, hi + 1))
        except ValueError:
            pass
    try:
        return [int(year_str)]
    except ValueError:
        return None


def _parse_searches(search_specs: list[str] | None,
                    fallback_model: str,
                    fallback_trim: str | None) -> list[tuple[str, str | None]]:
    """Return list of (model, trim_or_None) pairs to sweep.

    Each --search spec is ``MODEL`` or ``MODEL:TRIM``; an empty trim (or no
    colon) means "any trim of that model". Whitespace is stripped so quoting
    is forgiving (e.g. ``--search "X7: M60i"``).

    Falls back to a single (--model, --trim) pair when --search is absent so
    the legacy single-search invocation keeps working unchanged.
    """
    if not search_specs:
        return [(fallback_model, fallback_trim or None)]

    pairs: list[tuple[str, str | None]] = []
    for spec in search_specs:
        spec = spec.strip()
        if not spec:
            continue
        model, sep, trim = spec.partition(":")
        model = model.strip()
        if not model:
            continue
        # Treat both "X7" (no colon) and "X7:" (empty trim) as "any trim".
        trim_val: str | None = trim.strip() if sep else ""
        pairs.append((model, trim_val or None))
    return pairs or [(fallback_model, fallback_trim or None)]


def fetch_carfax_history(vehicles: list[dict]) -> list[dict]:
    """Fetch the full CarFax report text for every vehicle that has a carfaxUrl,
    using the user's real Chrome via CDP (so DataDome trust cookies are present).

    Adds per-vehicle field:
      carfaxHistory – plain text of the CarFax report page (null if unavailable)

    Requires Chrome to be running with --remote-debugging-port=9222, OR will
    attempt to launch it automatically.
    """
    candidates = [v for v in vehicles if v.get("carfaxUrl")]
    if not candidates:
        print("  No signed CarFax URLs to fetch.")
        return vehicles

    print(f"  Fetching CarFax reports for {len(candidates)} vehicles via real Chrome…")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  playwright not installed — skipping (pip install playwright)")
        return vehicles

    history_map: dict[str, str] = {}   # vin → report text

    with sync_playwright() as p:
        browser, owned = _connect_real_chrome(p)
        if not browser:
            print("  Could not connect to Chrome — skipping CarFax fetch.")
            return vehicles

        ctx    = browser.new_context(locale="en-US", timezone_id="America/New_York")
        page   = ctx.new_page()

        for i, v in enumerate(candidates, 1):
            vin = v.get("vin")
            url = v["carfaxUrl"]
            print(f"    [{i}/{len(candidates)}] {vin}… ", end="", flush=True)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=_PW_TIMEOUT)
                page.wait_for_timeout(_PW_SETTLE)
                final_url = page.url
                if "carfax.eu" in final_url or "carfax.com" not in final_url:
                    print(f"redirected → {final_url[:60]}", flush=True)
                    continue
                body = page.evaluate("() => document.body.innerText") or ""
                if len(body) < 200:
                    print("empty page", flush=True)
                    continue
                history_map[vin] = body
                print(f"✓ {len(body):,} chars", flush=True)
            except Exception as e:
                print(f"error: {e}", flush=True)

        page.close()
        ctx.close()
        if owned:
            browser.close()

    # Merge history into vehicle records
    updated = []
    for v in vehicles:
        vin = v.get("vin")
        updated.append({**v, "carfaxHistory": history_map.get(vin)})

    fetched = sum(1 for v in updated if v.get("carfaxHistory"))
    print(f"  CarFax history fetched: {fetched}/{len(candidates)}")
    return updated


def analyze_ownership(vehicles: list[dict], model: str = "gpt-4o-mini") -> list[dict]:
    """Use an LLM to assess whether each vehicle was likely owned by a private buyer.

    Uses all available signals:
      carfaxHistory – full CarFax report text (if fetched via --fetch-carfax)
      ownerCount    – from CarFax badge on dealer VDP (1, 2, … or None)
      carfaxBadge   – badge quality slug (1own_great_black, 1own_fair_black, …)
      certified     – BMW CPO (requires clean single-owner history)
      type          – used / certified / Pre-Owned / new
      daysOnLot     – time at current dealer
      odometer      – mileage

    Adds per-vehicle fields:
      ownershipAssessment   – "dealer_only" | "likely_private" | "unknown"
      ownershipConfidence   – "high" | "medium" | "low"
      ownershipReasoning    – one-sentence explanation

    Requires OPENAI_API_KEY env var.  Falls back gracefully if unavailable.
    """
    import os
    try:
        from openai import OpenAI
    except ImportError:
        print("  openai package not installed (pip install openai) — skipping LLM analysis")
        return vehicles

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("  OPENAI_API_KEY not set — skipping LLM analysis")
        return vehicles

    client = OpenAI(api_key=api_key)
    has_history = any(v.get("carfaxHistory") for v in vehicles)

    rows = []
    for i, v in enumerate(vehicles):
        row: dict = {
            "idx":         i,
            "vin":         v.get("vin"),
            "year":        v.get("year"),
            "trim":        v.get("trim"),
            "odometer":    v.get("odometer"),
            "type":        v.get("type"),
            "certified":   v.get("certified"),
            "ownerCount":  v.get("ownerCount"),
            "carfaxBadge": v.get("carfaxBadge"),
            "daysOnLot":   v.get("daysOnLot"),
            "dealerName":  v.get("dealerName"),
        }
        if has_history:
            row["carfaxHistory"] = (v.get("carfaxHistory") or "")[:3000] or None
        rows.append(row)

    history_instruction = """
- carfaxHistory: raw text of the CarFax report page. Look for:
    • "Number of Owners" / "1-Owner" / "2-Owner"
    • "Personal Use" / "Lease" / "Fleet" / "Rental" / "Corporate Fleet"
    • Accident or damage records
    • Service records at independent shops (suggests private ownership)
  Use this as the primary signal when available.
""" if has_history else ""

    prompt = f"""You are analyzing BMW pre-owned vehicle inventory data.
For each vehicle, determine whether it was ever owned by a private buyer (non-dealer).

Field meanings:
- ownerCount: owners per CarFax badge (null = unknown)
- carfaxBadge: quality encoded in slug (e.g. "1own_great_black", "1own_fair_black")
- certified: BMW CPO — clean title, typically ≤1 previous owner
- type: condition (used, certified, Pre-Owned, new)
- odometer: current mileage{history_instruction}

For each vehicle return a JSON array with objects:
{{
  "idx": <integer>,
  "ownershipAssessment": "dealer_only" | "likely_private" | "unknown",
  "ownershipConfidence": "high" | "medium" | "low",
  "ownershipReasoning": "<one concise sentence>"
}}

Rules (when carfaxHistory is absent):
- ownerCount > 1 → likely_private (high)
- ownerCount = 1 → likely_private (medium); confidence=high if certified or odometer 3000–30000
- "fair" in carfaxBadge → suggests accident/damage, likely real-world use
- ownerCount = null AND certified = false → unknown (low)

Vehicles:
{json.dumps(rows, indent=2)}

Return ONLY the JSON array, no explanation.
"""

    print(f"  Sending {len(vehicles)} vehicles to {model} for ownership analysis…")
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content
        # The model might wrap in {"results": [...]} — handle both
        parsed = json.loads(raw)
        assessments = parsed if isinstance(parsed, list) else next(
            (v for v in parsed.values() if isinstance(v, list)), []
        )
        by_idx = {a["idx"]: a for a in assessments}
        updated = []
        for i, v in enumerate(vehicles):
            a = by_idx.get(i, {})
            updated.append({
                **v,
                "ownershipAssessment": a.get("ownershipAssessment"),
                "ownershipConfidence": a.get("ownershipConfidence"),
                "ownershipReasoning":  a.get("ownershipReasoning"),
            })
        private_count = sum(1 for v in updated if v.get("ownershipAssessment") == "likely_private")
        dealer_count  = sum(1 for v in updated if v.get("ownershipAssessment") == "dealer_only")
        print(f"  Assessment: {dealer_count} dealer-only, {private_count} likely-private, "
              f"{len(updated)-dealer_count-private_count} unknown")
        return updated
    except Exception as e:
        print(f"  LLM analysis failed: {e}")
        return vehicles


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Search BMW inventory nationwide (defaults: 2025-2026 X7 M60i, 60–15000 mi, new+used)"
    )
    parser.add_argument("--make",          default="BMW")
    parser.add_argument("--model",         default="X7",
                        help="Model name (default: X7). Ignored if --search is given.")
    parser.add_argument("--year",          default="2025-2026",
                        help="Single year or range: 2025 | 2025-2026 (default: 2025-2026)")
    parser.add_argument("--trim",          default="M60i",
                        help="Trim level (default: M60i). Ignored if --search is given.")
    parser.add_argument("--search",        action="append", metavar="MODEL[:TRIM]",
                        help="Sweep a (model, trim) pair. Repeatable; e.g. "
                             "--search X7:M60i --search X7:xDrive40i --search X5:M60i "
                             "--search XM. Omit ':TRIM' (or leave blank) to match any "
                             "trim of that model. When set, --model/--trim are ignored.")
    parser.add_argument("--min-miles",     type=int, default=60,
                        help="Minimum odometer (default: 60)")
    parser.add_argument("--max-miles",     type=int, default=15000,
                        help="Maximum odometer (default: 15000)")
    parser.add_argument("--type",          choices=["new", "used", "all"], default="all",
                        help="Vehicle condition (default: all)")
    parser.add_argument("--out",         default="results.json",
                        help="Output JSON file (default: results.json)")
    parser.add_argument("--skip-fetch", action="store_true",
                        help="Skip VDP page fetching (faster, but no CarFax URLs or resolved links)")
    parser.add_argument("--fetch-carfax", action="store_true",
                        help="After VDP fetch, load each signed CarFax report via real Chrome CDP "
                             "and store full report text in carfaxHistory for richer LLM analysis")
    parser.add_argument("--analyze", action="store_true",
                        help="Use LLM (gpt-4o-mini) to assess private vs dealer ownership "
                             "(requires OPENAI_API_KEY)")
    parser.add_argument("--analyze-only", metavar="FILE",
                        help="Skip search: load FILE, run LLM ownership analysis, re-save. "
                             "Requires OPENAI_API_KEY.")
    parser.add_argument("--llm-model", default="gpt-4o-mini",
                        help="OpenAI model for --analyze / --analyze-only (default: gpt-4o-mini)")
    args = parser.parse_args()

    # ── Shortcut: just analyze an existing file, skip search entirely ─────────
    if args.analyze_only:
        src = args.analyze_only
        dst = args.out if args.out != "results.json" else src
        print(f"\n[ Ownership Analysis Only: {src} ]")
        with open(src) as f:
            vehicles = json.load(f)
        print(f"  Loaded {len(vehicles)} vehicles")
        if args.fetch_carfax:
            print("\n[ Fetching CarFax Reports (real Chrome CDP) ]")
            vehicles = fetch_carfax_history(vehicles)
        updated = analyze_ownership(vehicles, model=args.llm_model)
        with open(dst, "w") as f:
            json.dump(updated, f, indent=2)
        print(f"  Saved → {dst}")
        return

    # Start Chrome early so it's warmed up before VDP fetching
    if not args.skip_fetch:
        ensure_chrome_debug()

    years = _parse_years(args.year)
    searches = _parse_searches(args.search, args.model, args.trim)

    year_label = args.year or "any year"
    miles_label = f"{args.min_miles:,}–{args.max_miles:,} mi" if (args.min_miles or args.max_miles) else "any miles"
    sweep_label = ", ".join(f"{m}{' ' + t if t else ''}" for m, t in searches)
    print(f"\nSearching: {year_label} {args.make} [{sweep_label}]  "
          f"{miles_label}  type={args.type}\n")

    all_results: list[dict] = []
    ddc_type = "all" if args.type == "all" else args.type

    for model, trim in searches:
        label = f"{model}{' ' + trim if trim else ''}"

        print(f"[ Dealer.com — {label} ]")
        try:
            ddc = search_ddc(args.make, model, years, trim, ddc_type)
            all_results.extend(ddc)
        except Exception as e:
            print(f"  DDC error: {e}")

        print(f"\n[ DealerInspire / Algolia — {label} ]")
        try:
            di = search_di(args.make, model, years, trim,
                           args.min_miles, args.max_miles)
            all_results.extend(di)
        except Exception as e:
            print(f"  DI error: {e}")
        print()

    # Post-filter miles (DDC odometer comes from trackingAttributes, filtered here)
    before = len(all_results)
    filtered = []
    for v in all_results:
        odo = v.get("odometer")
        if odo is None:
            filtered.append(v)
            continue
        if args.min_miles is not None and odo < args.min_miles:
            continue
        if args.max_miles is not None and odo > args.max_miles:
            continue
        filtered.append(v)
    all_results = filtered
    if before != len(all_results):
        print(f"\nMileage filter {args.min_miles:,}–{args.max_miles:,}: {before} → {len(all_results)} vehicles")

    # Deduplicate by VIN
    seen_vins: set[str] = set()
    deduped = []
    for v in all_results:
        key = v.get("vin") or f"{v.get('stockNumber')}-{v.get('dealerName')}"
        if key not in seen_vins:
            seen_vins.add(key)
            deduped.append(v)

    # ── Resolve any blank dealerUrls before VDP fetching ─────────────────────
    missing_url_count = sum(1 for v in deduped if not v.get("dealerUrl"))
    if missing_url_count:
        print(f"\n[ Resolving {missing_url_count} blank dealer URL(s) ]")
        deduped = resolve_missing_dealer_urls(deduped)

    # ── Fetch VDP pages to resolve canonical links + extract signed CarFax URLs ──
    if not args.skip_fetch:
        print("\n[ Fetching VDP pages for CarFax + canonical links ]")
        deduped = fetch_details(deduped)

    print(f"\nTotal unique vehicles: {len(deduped)}")
    print(f"{'VIN':<20} {'Miles':>7}  {'Days':>5}  {'Price':>10}  {'Type':<10} {'Color':<25} {'Dealer':<35} {'State'}")
    print("-" * 145)

    def sort_price(v):
        p = v.get("internetPrice")
        if p is None:
            return 0
        try:
            return int(str(p).replace("$", "").replace(",", ""))
        except (ValueError, TypeError):
            return 0

    for v in sorted(deduped, key=lambda x: (x.get("odometer") or 9_999_999, sort_price(x))):
        vin      = v.get("vin") or "—"
        miles    = v.get("odometer")
        days     = v.get("daysOnLot")
        price    = v.get("internetPrice")
        vtype    = (v.get("type") or "")[:9]
        color    = (v.get("extColor") or "")[:24]
        dealer   = (v.get("dealerName") or "")[:34]
        state    = v.get("dealerState") or ""
        plat     = "DDC" if v.get("platform") == "dealercom" else "DI "

        miles_str = f"{miles:,}" if miles is not None else "new"
        days_str  = f"{days}d" if days is not None else "—"
        price_str = f"${price:,}" if price else "—"
        print(f"[{plat}] {vin:<19} {miles_str:>7}  {days_str:>5}  {price_str:>10}  {vtype:<10} {color:<25} {dealer:<35} {state}")

    # ── Optional CarFax history fetch via real Chrome ────────────────────────
    if args.fetch_carfax:
        print("\n[ Fetching CarFax Reports (real Chrome CDP) ]")
        deduped = fetch_carfax_history(deduped)

    # ── Optional LLM ownership analysis ──────────────────────────────────────
    if args.analyze:
        print("\n[ LLM Ownership Analysis ]")
        deduped = analyze_ownership(deduped, model=args.llm_model)

    if args.out:
        with open(args.out, "w") as f:
            json.dump(deduped, f, indent=2)
        print(f"\nSaved {len(deduped)} results → {args.out}")

    shutdown_chrome_if_owned()


if __name__ == "__main__":
    main()
