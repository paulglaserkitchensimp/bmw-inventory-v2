"""
search_inventory.py – Search for a specific vehicle across ALL US BMW dealers.

Covers four dealer-website platforms (per master_dealers.json):
  • Dealer.com    : one call per year to siteId=bmwgroup + 14 standalone sites
  • DealerInspire : one Cars Commerce API call per dealer (~119, via cc_ccid)
  • DealerOn      : one Cosmos SRP API call per dealer (~30, via do_dealer_id)
  • Team Velocity : SSR dataLayer parse per dealer (~22)

After collecting inventory, each dealer VDP is fetched concurrently (8 threads)
to extract the signed CarFax URL that lives in the page HTML.  Use --skip-fetch
to skip this step for faster (CarFax-less) runs.

Usage
-----
python3 search_inventory.py                          # all defaults
python3 search_inventory.py --trim "xDrive40i" --max-miles 20000 --out x7_40i.json
python3 search_inventory.py --skip-fetch             # fast, no CarFax

Defaults: year=2025-2026, model=X7, trim=M60i, miles=60–15000, type=all

Output goes to search_output.json by default. results.json is the persistent
union file: searches refuse to write it — fold sweeps in via merge_results.py
(or the search_*.sh scripts' --sync flag), which only ever adds on top.

Output fields per vehicle:
  vin, stockNumber, year, make, model, trim, type, odometer, internetPrice,
  extColor, certified, daysOnLot, dateInStock,
  nhtsaUrl, carfaxPaywallUrl, carfaxUrl (signed, from VDP),
  dealerName, dealerCity, dealerState, dealerUrl, platform,
  link (API URL), vinLink (VIN slug fallback), resolvedLink (after redirect)
"""

import argparse
import json
import os
import pathlib
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime

import requests
from dotenv import load_dotenv

_CRAWLER_DIR = os.path.dirname(os.path.abspath(__file__))


def _data_path(filename: str) -> str:
    """Resolve a data file relative to the crawler directory (so the script
    works regardless of the caller's working directory)."""
    if os.path.isabs(filename):
        return filename
    return os.path.join(_CRAWLER_DIR, filename)

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


# Optional whitelist of dealer states for the per-dealer platforms (DI,
# DealerOn, Team Velocity). Set by --dealer-states. Empty = no restriction.
# Dealers whose roster entry has no state are always kept — dropping them
# would silently lose ~16 stores whose census never resolved a location.
DEALER_STATES: set[str] = set()


def _load_dealers(dealers_file: str = "master_dealers.json") -> list[dict]:
    """Load master_dealers.json, honouring the DEALER_STATES whitelist."""
    with open(_data_path(dealers_file)) as f:
        dealers: list[dict] = json.load(f)
    if not DEALER_STATES:
        return dealers
    return [d for d in dealers
            if not d.get("state") or (d.get("state") or "").upper() in DEALER_STATES]


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


# ──────────────────────────────────────────────────────────────────────────────
# Option-package detection (330i hunt: "M Sport package or better")
#
# No dealer platform exposes a structured option list in its search API, so the
# only reliable signal is the VDP body text, which lists packages either by
# marketing name ("M Sport Package") or by BMW option code (337 = M Sport
# package on the G20 3 Series; ZMP = M Sport Pro).  We scan the fetched VDP HTML
# for both and record what we saw, so the visualizer can filter on it.
#
# `mSport` is deliberately tri-state:
#   True  – a package marker was found in the page text
#   False – the page was fetched successfully and had no marker
#   None  – the page was never fetched / was blocked (unknown, verify by hand)
# ──────────────────────────────────────────────────────────────────────────────
PACKAGE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("m_sport_pro",   re.compile(r"\bM\s*Sport(?:\s*Package)?\s*Pro\b", re.I)),
    ("m_sport",       re.compile(r"\bM\s*Sport\s*(?:Package|Pkg)\b", re.I)),
    ("m_sport",       re.compile(r"\bPackage\s*337\b|\bOption\s*337\b", re.I)),
    ("shadowline",    re.compile(r"\bShadowline\b", re.I)),
    ("premium",       re.compile(r"\bPremium\s*(?:Package|Pkg)\b", re.I)),
    ("dynamic_handling", re.compile(r"\bDynamic\s*Handling\s*(?:Package|Pkg)\b", re.I)),
    ("m_sport_brakes", re.compile(r"\bM\s*Sport\s*Brakes?\b", re.I)),
    ("driving_assist", re.compile(r"\bDriving\s*Assistance\b", re.I)),
    ("parking_assist", re.compile(r"\bParking\s*Assistance\b", re.I)),
    ("cold_weather",  re.compile(r"\bCold\s*Weather\s*(?:Package|Pkg)\b", re.I)),
]

# Strip scripts/styles/tags before pattern matching so we don't get false hits
# from JSON blobs, analytics payloads, or CSS class names.
_TAG_STRIP_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.I | re.S)
_TAGS_RE = re.compile(r"<[^>]+>")


def _visible_text(html: str) -> str:
    txt = _TAG_STRIP_RE.sub(" ", html)
    txt = _TAGS_RE.sub(" ", txt)
    return re.sub(r"\s+", " ", txt)


def extract_packages(html: str) -> dict:
    """Return {'packageSignals': [...], 'mSport': bool} for one VDP page."""
    text = _visible_text(html)
    found: list[str] = []
    for name, pat in PACKAGE_PATTERNS:
        if name not in found and pat.search(text):
            found.append(name)
    return {
        "packageSignals": found,
        "mSport": ("m_sport" in found) or ("m_sport_pro" in found),
    }


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
    out.update(extract_packages(html))
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
                       condition: str, site_id: str = "bmwgroup",
                       endpoint: str = DDC_ENDPOINT,
                       dealer: dict | None = None) -> list[dict]:
    """Single DDC query for one (year, condition) pair.

    `condition` must be 'new' or 'used' — it selects the DDC pageAlias.
    Pass `year=None` to skip the year filter. The returned records have
    their ``type`` field set to `condition` so the downstream merge can
    distinguish them even when the dealer doesn't echo the type back.

    `site_id`/`endpoint`/`dealer` default to the OEM `bmwgroup` aggregate query.
    For a standalone DDC dealer (its own account, not in the OEM feed), pass its
    `site_id`, its own `{website}/api/widget/ws-inv-data/getInventory` endpoint,
    and a `dealer` dict (name/city/state/website) since single-dealer responses
    omit the `accounts` map."""
    page_alias, page_id = DDC_PAGE_ALIAS[condition]

    inv_params: dict = {"make": make, "model": model}
    if year is not None:
        inv_params["year"] = str(year)
    if trim:
        inv_params["trim"] = trim
    inv_params["compositeType"] = condition

    payload = {
        "siteId": site_id,
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
        r = requests.post(endpoint, headers=DDC_HEADERS, json=payload, timeout=45)
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
    if site_id == "bmwgroup":
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
        # Single-dealer sites omit the accounts map — fall back to the known
        # dealer record passed in by the standalone-site caller.
        if not dealer_url and dealer:
            dealer_url = (dealer.get("website") or "").rstrip("/")

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
            "dealerName":   account.get("name") or v.get("accountName") or (dealer or {}).get("name"),
            "dealerCity":   addr.get("city") or v.get("accountCity") or (dealer or {}).get("city"),
            "dealerState":  addr.get("state") or v.get("accountState") or (dealer or {}).get("state"),
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

    all_results.extend(
        _search_ddc_standalone(make, model, year_list, trim, conditions, seen)
    )
    return all_results


def _load_standalone_ddc_dealers() -> list[dict]:
    """DDC dealers running their own site account (not part of the OEM
    `bmwgroup` feed), so the aggregate query never returns them. Identified as
    master_dealers.json DDC records whose `site_id` is absent from the OEM
    Dealer.com sweep (bmw_ddc_dealers.json)."""
    try:
        master = _load_dealers()
    except FileNotFoundError:
        return []
    try:
        with open(_data_path("bmw_ddc_dealers.json")) as f:
            oem_ids = {d.get("site_id") for d in json.load(f)}
    except FileNotFoundError:
        oem_ids = set()

    out = []
    for d in master:
        platform = d.get("platform") or (d.get("census") or {}).get("platform")
        if platform != "dealercom":
            continue
        site_id = d.get("site_id")
        if site_id and site_id not in oem_ids and d.get("website"):
            out.append(d)
    return out


def _search_ddc_standalone(make, model, year_list, trim, conditions,
                           seen: set[str]) -> list[dict]:
    dealers = _load_standalone_ddc_dealers()
    if not dealers:
        return []
    print(f"  DDC: querying {len(dealers)} standalone site(s) outside the OEM feed")

    def query_one(dealer: dict) -> list[dict]:
        website = dealer["website"].rstrip("/")
        endpoint = f"{website}/api/widget/ws-inv-data/getInventory"
        recs: list[dict] = []
        for yr in year_list:
            for cond in conditions:
                try:
                    recs.extend(_search_ddc_single(
                        make, model, yr, trim, cond,
                        site_id=dealer["site_id"], endpoint=endpoint, dealer=dealer))
                except Exception:
                    continue
        return recs

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(query_one, d): d for d in dealers}
        for fut in as_completed(futures):
            try:
                batch = fut.result()
            except Exception:
                batch = []
            for rec in batch:
                key = rec.get("vin") or f"{rec.get('stockNumber')}-{rec.get('dealerName')}"
                if key not in seen:
                    seen.add(key)
                    results.append(rec)
    if results:
        print(f"  DDC standalone: +{len(results)} vehicles")
    return results


# ──────────────────────────────────────────────────────────────────────────────
# DI / Cars Commerce search  (per-dealer query against the Cars Commerce API)
# ──────────────────────────────────────────────────────────────────────────────
# DealerInspire retired Algolia (its old app accounts now return
# "Account temporary disabled for security reasons") and moved every DI site to
# Cars Commerce's search service. Each dealer has a numeric `ccid` embedded in
# its homepage (see harvest_di_ccid.py) and shares one public `x-api-key`. We
# POST one query per dealer to /api/v1/listings/{ccid}/search and page through.
CC_SEARCH_URL = "https://websites-search.api.carscommerce.inc/api/v1/listings/{ccid}/search"
CC_API_KEY = "OQa8l7SzMctJyr5bhSG9jYvlGnZUQfgl"
CC_HEADERS = {
    "x-api-key": CC_API_KEY,
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
    ),
}


def _cc_filters(make, model, years, trim) -> dict:
    # Some DI dealers store "X5 M60i" as the *model* (with trim repeated in the
    # trim field), so when a trim is given also accept the combined model name.
    models = [model, f"{model} {trim}"] if trim else [model]
    filters: dict = {"make": [make], "model": models}
    if trim:
        filters["trim"] = [trim]
    if years:
        filters["year"] = list(years)
    return filters


def _cc_query_dealer(dealer: dict, make, model, years, trim,
                     min_miles, max_miles) -> list[dict]:
    """Query one DI dealer's Cars Commerce account, paging until exhausted."""
    ccid = dealer.get("cc_ccid")
    if not ccid:
        return []
    api_key = dealer.get("cc_api_key") or CC_API_KEY
    url = CC_SEARCH_URL.format(ccid=ccid)
    website = (dealer.get("website") or "").rstrip("/")
    headers = {**CC_HEADERS, "x-api-key": api_key}
    if website:
        headers["Origin"] = website
        headers["Referer"] = website + "/"

    body = {"filters": _cc_filters(make, model, years, trim)}
    out: list[dict] = []
    page = 1
    while True:
        payload = {**body, "page": page} if page > 1 else body
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=25)
        except requests.RequestException:
            break
        if r.status_code != 200:
            break
        data = r.json().get("data") or {}
        listings = data.get("listings") or []
        for hit in listings:
            rec = _cc_to_record(hit, dealer, min_miles, max_miles)
            if rec is not None:
                out.append(rec)
        total = data.get("total_vehicle_count") or 0
        # 20 hits/page; stop once we've pulled everything or hit an empty page.
        if not listings or len(out) >= total or page >= 25:
            break
        page += 1
    return out


def _cc_to_record(hit: dict, dealer: dict, min_miles, max_miles) -> dict | None:
    if (hit.get("status") or "publish") not in ("publish", "modified", "pend-sale"):
        return None
    try:
        odometer = int(hit["mileage"]) if hit.get("mileage") is not None else None
    except (ValueError, TypeError):
        odometer = None
    # Mileage pre-filter (the API has no miles filter; we do it client-side).
    if odometer is not None:
        if min_miles is not None and odometer < min_miles:
            return None
        if max_miles is not None and odometer > max_miles:
            return None

    pricing = hit.get("pricing") or {}
    styles = hit.get("styles") or {}
    type_slug = hit.get("type") or "Pre-Owned"
    vtype = "new" if type_slug == "New" else "used"
    website = (dealer.get("website") or "").rstrip("/")
    vdp = hit.get("vdp_url") or ""
    link = vdp if vdp.startswith("http") else (website + "/" + vdp.lstrip("/") if vdp else website)
    date_in_stock = hit.get("date_in_stock")
    vin = hit.get("vin")
    return {
        "vin":           vin,
        "stockNumber":   hit.get("stock"),
        "year":          hit.get("year"),
        "make":          hit.get("make"),
        "model":         hit.get("model"),
        "trim":          hit.get("trim"),
        "odometer":      odometer,
        "internetPrice": _to_int(pricing.get("our_price")) or _to_int(pricing.get("price"))
                         or _to_int(pricing.get("msrp")),
        "extColor":      styles.get("exterior_color"),
        "certified":     bool(hit.get("is_certified")),
        "daysOnLot":     _days_since(date_in_stock),
        "dateInStock":   date_in_stock,
        **_vin_links(vin),
        "dealerName":    dealer.get("name") or dealer.get("dealer_slug"),
        "dealerCity":    dealer.get("city"),
        "dealerState":   dealer.get("state"),
        "dealerUrl":     website,
        "dealerPhone":   None,   # filled in by the VDP pass if available
        "platform":      "dealerinspire",
        "type":          vtype,
        "link":          link,
        "vinLink":       link,   # Cars Commerce vdp_url is already the VIN slug
    }


def search_di(make: str, model: str, years: list[int] | None, trim: str | None,
              min_miles: int | None, max_miles: int | None,
              dealers_file: str = "master_dealers.json") -> list[dict]:
    """Query every DealerInspire dealer via the Cars Commerce search API.

    Reads DI dealers (with a harvested `cc_ccid`) from master_dealers.json and
    fires one threaded request per dealer. Dealers without a ccid are skipped
    (run harvest_di_ccid.py to fill them in).
    """
    dealers = _load_dealers(dealers_file)

    di_dealers = [d for d in dealers if _is_di(d) and d.get("cc_ccid")]
    missing = sum(1 for d in dealers if _is_di(d) and not d.get("cc_ccid"))
    if missing:
        print(f"  DI: {missing} DealerInspire dealers have no ccid yet "
              "(run harvest_di_ccid.py) — skipped")
    if not di_dealers:
        return []

    results: list[dict] = []
    dealers_with_hits = 0
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {
            pool.submit(_cc_query_dealer, d, make, model, years, trim,
                        min_miles, max_miles): d
            for d in di_dealers
        }
        for fut in as_completed(futures):
            try:
                recs = fut.result()
            except Exception:
                recs = []
            if recs:
                dealers_with_hits += 1
                results.extend(recs)

    print(f"  DI (Cars Commerce): {len(di_dealers)} dealers queried → "
          f"{len(results)} hits from {dealers_with_hits} dealers")
    return results


def _is_di(d: dict) -> bool:
    if d.get("platform") == "dealerinspire":
        return True
    census = d.get("census") or {}
    return census.get("platform") == "dealerinspire"


# ──────────────────────────────────────────────────────────────────────────────
# DealerOn search  (per-dealer query against the Cosmos SRP JSON API)
# ──────────────────────────────────────────────────────────────────────────────
# DealerOn exposes inventory at
#   /api/vhcliaa/vehicle-pages/cosmos/srp/vehicles/{dealerId}/{pageId}
# with a base64 `baseFilter` (type='n' for the New SRP, 'u' for Used) plus
# make/model/trim query params. dealerId + the per-SRP pageIds are harvested by
# harvest_dealeron.py into master_dealers.json (do_dealer_id / do_*_page_id).
DEALERON_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "X-Requested-With": "XMLHttpRequest",
}
_DEALERON_BASEFILTER = {"new": "dHlwZT0nbic=", "used": "dHlwZT0ndSc="}  # b64 type='n'/'u'


def _is_dealeron(d: dict) -> bool:
    return (d.get("platform") == "dealeron"
            or (d.get("census") or {}).get("platform") == "dealeron")


def _trim_matches_loose(spec_trim: str | None, value: str | None) -> bool:
    """Case/punctuation-insensitive bidirectional substring match. Dealers
    abbreviate trims inconsistently ('760i xDrive' vs '760i'), so either side
    containing the other counts as a match."""
    if not spec_trim:
        return True
    a = re.sub(r"[^a-z0-9]", "", spec_trim.lower())
    b = re.sub(r"[^a-z0-9]", "", (value or "").lower())
    if not b:
        return False
    return a in b or b in a


def _dealeron_model_matches(card_model: str | None, model: str) -> bool:
    """Word-prefix match: dealers variously store 'X5', 'X5 xDrive40i', or
    'X5 M Competition' as the model. 'X5 M60i' must NOT match target 'X5 M'."""
    if not card_model:
        return False
    return card_model == model or card_model.startswith(model + " ")


def _dealeron_query(dealer: dict, make, model, years, trim,
                    min_miles, max_miles) -> list[dict]:
    dealer_id = dealer.get("do_dealer_id")
    if not dealer_id:
        return []
    website = (dealer.get("website") or "").rstrip("/")
    host = website.replace("https://", "").replace("http://", "")
    year_set = set(years) if years else None

    pages = [("new", dealer.get("do_new_page_id")),
             ("used", dealer.get("do_used_page_id"))]
    out: list[dict] = []
    for condition, page_id in pages:
        if not page_id:
            continue
        base = (f"{website}/api/vhcliaa/vehicle-pages/cosmos/srp/vehicles/"
                f"{dealer_id}/{page_id}")

        def run(params: dict, client_filter: bool) -> list[dict]:
            got: list[dict] = []
            page_num = 1
            while True:
                q = dict(params)
                if page_num > 1:
                    q["pt"] = str(page_num)
                try:
                    r = requests.get(base,
                                     headers={**DEALERON_HEADERS, "Referer": website + "/"},
                                     params=q, timeout=25)
                except requests.RequestException:
                    break
                if r.status_code != 200:
                    break
                try:
                    data = r.json()
                except ValueError:
                    break
                cards = [c.get("VehicleCard") for c in data.get("DisplayCards", [])
                         if c.get("VehicleCard")]
                for vc in cards:
                    if client_filter:
                        if not _dealeron_model_matches(vc.get("VehicleModel"), model):
                            continue
                        if not _trim_matches_loose(trim, vc.get("VehicleTrim")):
                            continue
                    rec = _dealeron_to_record(vc, dealer, condition, year_set,
                                              min_miles, max_miles)
                    if rec is not None:
                        got.append(rec)
                paging = (data.get("Paging") or {}).get("PaginationDataModel") or {}
                total_pages = paging.get("TotalPages") or 1
                if page_num >= total_pages or page_num >= 15 or not cards:
                    break
                page_num += 1
            return got

        common = {"host": host, "baseFilter": _DEALERON_BASEFILTER[condition]}
        server = {**common, "make": make, "model": model}
        if trim:
            server["trim"] = trim
        recs = run(server, client_filter=False)
        if not recs:
            # Some dealers store 'X5 xDrive40i' as the model, so the exact
            # server-side model filter finds nothing — refetch the whole make
            # and filter client-side instead.
            recs = run({**common, "make": make}, client_filter=True)
        out.extend(recs)
    return out


def _dealeron_to_record(vc: dict, dealer: dict, condition: str,
                        year_set, min_miles, max_miles) -> dict | None:
    # Server filter is by make/model/trim only; enforce year/miles client-side.
    year = vc.get("VehicleYear")
    if year_set and year not in year_set:
        return None
    odometer = _to_int(vc.get("VehicleMileage"))
    if odometer is not None:
        if min_miles is not None and odometer < min_miles:
            return None
        if max_miles is not None and odometer > max_miles:
            return None

    vtype = "new" if (vc.get("VehicleType") or condition) == "new" else "used"
    website = (dealer.get("website") or "").rstrip("/")
    link = vc.get("VehicleDetailUrl") or website
    vin = vc.get("VehicleVin")
    price = _to_int(vc.get("VehicleInternetPrice"))
    if not price:
        price = _to_int(vc.get("VehicleMsrp"))
    return {
        "vin":           vin,
        "stockNumber":   vc.get("VehicleStockNumber"),
        "year":          year,
        "make":          vc.get("VehicleMake"),
        "model":         vc.get("VehicleModel"),
        "trim":          vc.get("VehicleTrim"),
        "odometer":      odometer,
        "internetPrice": price,
        "extColor":      vc.get("ExteriorColorLabel"),
        "certified":     bool(vc.get("VehicleCpo")),
        "daysOnLot":     _days_since(vc.get("VehicleTaggingInventoryDate")),
        "dateInStock":   vc.get("VehicleTaggingInventoryDate"),
        **_vin_links(vin),
        "dealerName":    vc.get("DealerName") or dealer.get("name"),
        "dealerCity":    vc.get("DealerLocatedAtCity") or dealer.get("city"),
        "dealerState":   vc.get("DealerLocatedAtState") or dealer.get("state"),
        "dealerUrl":     website,
        "dealerPhone":   None,
        "platform":      "dealeron",
        "type":          vtype,
        "link":          link,
        "vinLink":       link,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Team Velocity search  (parse the SSR dataLayer embedded in each SRP page)
# ──────────────────────────────────────────────────────────────────────────────
# Team Velocity / Apollo sites server-render their inventory and embed a
# per-vehicle dataLayer object in the SRP HTML:
#   {"item_category":..,"item_color":..,"item_condition":"new","item_id":<VIN>,
#    "item_inventory_date":..,"item_make":"BMW","item_model":"X7",
#    "item_number":<stock>,"item_variant":<trim>,"item_year":..,"item_price":..}
# We fetch /inventory/{condition}/bmw/{model-slug} (paginated) with plain
# requests — no browser, no per-dealer harvest needed (just the website).
TV_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
}
_TV_ITEM_RE = re.compile(r'\{"item_category":.*?"item_price":\d+\}')


def _is_teamvelocity(d: dict) -> bool:
    return (d.get("platform") == "teamvelocity"
            or (d.get("census") or {}).get("platform") == "teamvelocity")


def _tv_query(dealer: dict, make, model, years, trim,
              min_miles, max_miles, vehicle_type,
              blocked_out: list | None = None) -> list[dict]:
    website = (dealer.get("website") or "").rstrip("/")
    if not website:
        return []
    model_slug = model.lower().replace(" ", "-")
    trim_l = trim.lower() if trim else None
    year_set = set(years) if years else None
    conditions = ["new", "used", "cpo"] if vehicle_type == "all" else \
                 (["new"] if vehicle_type == "new" else ["used", "cpo"])

    seen_vins: set[str] = set()
    out: list[dict] = []
    for condition in conditions:
        page = 1
        while page <= 15:
            url = f"{website}/inventory/{condition}/bmw/{model_slug}"
            try:
                r = requests.get(url, headers={**TV_HEADERS, "Referer": website + "/"},
                                 params={"page": page} if page > 1 else None, timeout=25)
            except requests.RequestException:
                break
            # Some TV dealers sit behind Akamai and 403 plain requests; queue
            # them for the sequential browser fallback.
            if r.status_code in (403, 429) and page == 1 and blocked_out is not None:
                if dealer not in blocked_out:
                    blocked_out.append(dealer)
                break
            if r.status_code != 200:
                break
            items = []
            for raw in _TV_ITEM_RE.findall(r.text):
                try:
                    items.append(json.loads(raw))
                except ValueError:
                    continue
            if not items:
                break
            new_this_page = 0
            for it in items:
                vin = it.get("item_id")
                if not vin or vin in seen_vins:
                    continue
                if (it.get("item_make") or "").upper() != make.upper():
                    continue
                if not _trim_matches_loose(trim_l, it.get("item_variant")):
                    continue
                if year_set and it.get("item_year") not in year_set:
                    continue
                seen_vins.add(vin)
                new_this_page += 1
                out.append(_tv_to_record(it, dealer, website))
            # dataLayer shows one page (~11 items) at a time; stop when a page
            # yields no rows we haven't already seen.
            if new_this_page == 0 and page > 1:
                break
            if len(items) < 8:
                break
            page += 1
    return out


def _tv_to_record(it: dict, dealer: dict, website: str) -> dict:
    cond = (it.get("item_condition") or "").lower()
    vtype = "new" if cond == "new" else "used"
    vin = it.get("item_id")
    model_slug = (it.get("item_model") or "").lower().replace(" ", "-")
    link = f"{website}/inventory/{cond or 'used'}/bmw/{model_slug}"
    return {
        "vin":           vin,
        "stockNumber":   it.get("item_number"),
        "year":          it.get("item_year"),
        "make":          it.get("item_make"),
        "model":         it.get("item_model"),
        "trim":          it.get("item_variant"),
        "odometer":      None,   # not present in the dataLayer
        "internetPrice": _to_int(it.get("item_price")) or None,
        "extColor":      it.get("item_color"),
        "certified":     cond in ("cpo", "certified"),
        "daysOnLot":     _days_since(it.get("item_inventory_date")),
        "dateInStock":   it.get("item_inventory_date"),
        **_vin_links(vin),
        "dealerName":    dealer.get("name"),
        "dealerCity":    dealer.get("city"),
        "dealerState":   dealer.get("state"),
        "dealerUrl":     website,
        "dealerPhone":   None,
        "platform":      "teamvelocity",
        "type":          vtype,
        "link":          link,
        "vinLink":       link,
    }


def _tv_parse_html(html: str, dealer: dict, website: str, make, trim,
                   year_set, seen_vins: set[str]) -> list[dict]:
    trim_l = trim.lower() if trim else None
    out = []
    for raw in _TV_ITEM_RE.findall(html):
        try:
            it = json.loads(raw)
        except ValueError:
            continue
        vin = it.get("item_id")
        if not vin or vin in seen_vins:
            continue
        if (it.get("item_make") or "").upper() != make.upper():
            continue
        if not _trim_matches_loose(trim_l, it.get("item_variant")):
            continue
        if year_set and it.get("item_year") not in year_set:
            continue
        seen_vins.add(vin)
        out.append(_tv_to_record(it, dealer, website))
    return out


def _tv_browser_fallback(dealers: list[dict], make, model, years, trim,
                         vehicle_type) -> list[dict]:
    """Fetch Akamai-blocked TV dealers through a stealth browser (sequential)."""
    try:
        from playwright.sync_api import sync_playwright
        from playwright_stealth.stealth import Stealth
    except Exception:
        print(f"  Team Velocity: {len(dealers)} blocked dealers need a browser "
              "(playwright unavailable) — skipped")
        return []

    profile = str(pathlib.Path.home() / ".bmw_browser_profile")
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        p = pathlib.Path(profile) / name
        try:
            if p.is_symlink() or p.exists():
                p.unlink()
        except OSError:
            pass

    model_slug = model.lower().replace(" ", "-")
    year_set = set(years) if years else None
    conditions = ["new", "used", "cpo"] if vehicle_type == "all" else \
                 (["new"] if vehicle_type == "new" else ["used", "cpo"])
    results: list[dict] = []
    pw = ctx = None
    try:
        pw = sync_playwright().start()
        ctx = pw.chromium.launch_persistent_context(
            profile, channel="chrome", headless=True,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1440, "height": 900})
        Stealth().apply_stealth_sync(ctx)
        page = ctx.new_page()
        for dealer in dealers:
            website = dealer["website"].rstrip("/")
            seen_vins: set[str] = set()
            for condition in conditions:
                try:
                    page.goto(f"{website}/inventory/{condition}/bmw/{model_slug}",
                              wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(2500)
                    html = page.content()
                except Exception:
                    continue
                results.extend(_tv_parse_html(html, dealer, website, make, trim,
                                              year_set, seen_vins))
    except Exception as e:
        print(f"  Team Velocity browser fallback error: {str(e)[:60]}")
    finally:
        for fn in (lambda: ctx and ctx.close(), lambda: pw and pw.stop()):
            try:
                fn()
            except Exception:
                pass
    return results


def search_teamvelocity(make: str, model: str, years: list[int] | None,
                        trim: str | None, min_miles: int | None,
                        max_miles: int | None, vehicle_type: str = "all",
                        dealers_file: str = "master_dealers.json",
                        use_browser: bool = False) -> list[dict]:
    """Query every Team Velocity dealer by parsing their SSR SRP dataLayer.

    Plain requests handles most dealers. Some sit behind Akamai and 403 both
    plain requests and headless Chrome; those need the shared browser profile to
    have earned trust cookies first (see cars_com.py --warmup). Pass
    use_browser=True once the profile is warmed to pick them up."""
    dealers = _load_dealers(dealers_file)
    tv_dealers = [d for d in dealers if _is_teamvelocity(d) and d.get("website")]
    if not tv_dealers:
        return []

    results: list[dict] = []
    dealers_with_hits = 0
    blocked: list[dict] = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {
            pool.submit(_tv_query, d, make, model, years, trim,
                        min_miles, max_miles, vehicle_type, blocked): d
            for d in tv_dealers
        }
        for fut in as_completed(futures):
            try:
                recs = fut.result()
            except Exception:
                recs = []
            if recs:
                dealers_with_hits += 1
                results.extend(recs)

    if blocked and use_browser:
        print(f"  Team Velocity: {len(blocked)} dealer(s) blocked plain requests "
              "— retrying via browser")
        fb = _tv_browser_fallback(blocked, make, model, years, trim, vehicle_type)
        if fb:
            dealers_with_hits += len({r["dealerName"] for r in fb})
            results.extend(fb)

    print(f"  Team Velocity: {len(tv_dealers)} dealers queried → "
          f"{len(results)} hits from {dealers_with_hits} dealers")
    return results


def search_dealeron(make: str, model: str, years: list[int] | None, trim: str | None,
                    min_miles: int | None, max_miles: int | None,
                    vehicle_type: str = "all",
                    dealers_file: str = "master_dealers.json") -> list[dict]:
    """Query every DealerOn dealer via the Cosmos SRP JSON API (threaded)."""
    dealers = _load_dealers(dealers_file)

    do_dealers = [d for d in dealers if _is_dealeron(d) and d.get("do_dealer_id")]
    missing = sum(1 for d in dealers if _is_dealeron(d) and not d.get("do_dealer_id"))
    if missing:
        print(f"  DealerOn: {missing} dealers have no ids yet "
              "(run harvest_dealeron.py) — skipped")
    if not do_dealers:
        return []

    results: list[dict] = []
    dealers_with_hits = 0
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {
            pool.submit(_dealeron_query, d, make, model, years, trim,
                        min_miles, max_miles): d
            for d in do_dealers
        }
        for fut in as_completed(futures):
            try:
                recs = fut.result()
            except Exception:
                recs = []
            if vehicle_type != "all":
                recs = [r for r in recs if r["type"] == vehicle_type]
            if recs:
                dealers_with_hits += 1
                results.extend(recs)

    print(f"  DealerOn: {len(do_dealers)} dealers queried → "
          f"{len(results)} hits from {dealers_with_hits} dealers")
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
    parser.add_argument("--exclude-trim",  action="append", default=[], metavar="TERM",
                        help="Drop records whose trim or model contains TERM "
                             "(case/punctuation-insensitive). Comma-separated "
                             "and/or repeatable. Needed because trim matching is "
                             "a loose substring match, so --search '3 Series:330i' "
                             "also returns '330i xDrive'; pass --exclude-trim xDrive "
                             "to keep RWD only.")
    parser.add_argument("--dealer-states", default="", metavar="ST[,ST...]",
                        help="Restrict the per-dealer platforms (DealerInspire, "
                             "DealerOn, Team Velocity, standalone Dealer.com) to "
                             "dealers in these states. Cuts run time roughly in "
                             "half for a regional search. The OEM Dealer.com "
                             "aggregate query is nationwide either way — its "
                             "results are filtered afterwards. Dealers whose "
                             "roster entry has no state are always kept.")
    parser.add_argument("--out",         default="search_output.json",
                        help="Output JSON file (default: search_output.json). "
                             "results.json is reserved for the merge/union flow "
                             "and cannot be written by a search.")
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

    # results.json is the persistent, accumulated inventory — only
    # merge_results.py (the union flow) may write it. A search run overwriting
    # it once wiped ~400 vehicles down to a single sweep's worth.
    if os.path.basename(args.out) == "results.json":
        parser.error(
            "refusing to write results.json — it is the persistent union file. "
            "Write to another file (e.g. the default search_output.json) and fold "
            "it in with: uv run merge_results.py --inputs results.json <file> "
            "--out results.json")

    # ── Shortcut: just analyze an existing file, skip search entirely ─────────
    if args.analyze_only:
        src = args.analyze_only
        dst = args.out if args.out != "search_output.json" else src
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

    # Restrict per-dealer platform queries to a set of states (regional search).
    global DEALER_STATES
    DEALER_STATES = {s.strip().upper()
                     for s in args.dealer_states.split(",") if s.strip()}
    if DEALER_STATES:
        print(f"Dealer-state whitelist: {','.join(sorted(DEALER_STATES))} "
              "(per-dealer platforms only)")

    exclude_trims = [t.strip().lower() for entry in args.exclude_trim
                     for t in entry.split(",") if t.strip()]

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

        print(f"\n[ DealerInspire / Cars Commerce — {label} ]")
        try:
            di = search_di(args.make, model, years, trim,
                           args.min_miles, args.max_miles)
            all_results.extend(di)
        except Exception as e:
            print(f"  DI error: {e}")

        print(f"\n[ DealerOn — {label} ]")
        try:
            do = search_dealeron(args.make, model, years, trim,
                                 args.min_miles, args.max_miles, ddc_type)
            all_results.extend(do)
        except Exception as e:
            print(f"  DealerOn error: {e}")

        print(f"\n[ Team Velocity — {label} ]")
        try:
            tv = search_teamvelocity(args.make, model, years, trim,
                                     args.min_miles, args.max_miles, ddc_type)
            all_results.extend(tv)
        except Exception as e:
            print(f"  Team Velocity error: {e}")
        print()

    # Post-filter excluded trim terms. The platform trim match is a loose
    # bidirectional substring ("330i" matches "330i xDrive"), so drivetrain
    # exclusions have to happen here.
    if exclude_trims:
        before = len(all_results)
        all_results = [
            v for v in all_results
            if not any(term in f"{v.get('trim') or ''} {v.get('model') or ''}".lower()
                       for term in exclude_trims)
        ]
        if before != len(all_results):
            print(f"\nExcluded trims {exclude_trims}: {before} → {len(all_results)} vehicles")

    # The OEM Dealer.com feed is nationwide, so apply the state whitelist to
    # every source once the results are in.
    if DEALER_STATES:
        before = len(all_results)
        all_results = [v for v in all_results
                       if (v.get("dealerState") or "").upper() in DEALER_STATES
                       or not v.get("dealerState")]
        if before != len(all_results):
            print(f"\nDealer-state filter: {before} → {len(all_results)} vehicles")

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

    # Post-filter vehicle condition. DealerOn and Team Velocity already
    # query/filter by condition, and DDC queries new/used as separate pages, so
    # this is a no-op for them. DealerInspire's Cars Commerce API has no
    # condition parameter and always returns both new and used together — this
    # is the only thing that actually enforces --type new/used against DI
    # results, and it's cheap insurance against any other source doing the
    # same. `type` is always populated by every source's record builder (never
    # None), so an exact match is safe.
    if args.type != "all":
        before = len(all_results)
        all_results = [v for v in all_results if (v.get("type") or "").lower() == args.type]
        if before != len(all_results):
            print(f"\nCondition filter ({args.type}): {before} → {len(all_results)} vehicles")

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
