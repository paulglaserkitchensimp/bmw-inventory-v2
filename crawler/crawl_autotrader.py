"""
crawl_autotrader.py — scrape Autotrader search results and emit records
matching the `results.json` schema used by `search_inventory.py`.

Autotrader embeds the full listing + dealer JSON inside the search results
page's ``<script id="__NEXT_DATA__">`` blob (under
``props.pageProps.__eggsState.{inventory, owners}``). We paginate via the
``firstRecord`` query param and merge all listings client-side, deduping
by VIN. No per-listing detail-page fetch is required.

The browser is driven with Playwright + stealth using the shared
``~/.bmw_browser_profile`` persistent profile so DataDome trust cookies
are preserved across runs.

Usage
-----
  # crawl the default "2025 X7 M60i near Livonia, MI" search
  uv run python crawl_autotrader.py

  # arbitrary search URL — the trim is auto-derived from the URL path
  # (e.g. '.../bmw/x7/xdrive40i/...' implies --trim xdrive40i)
  uv run python crawl_autotrader.py \
      --url "https://www.autotrader.com/cars-for-sale/all-cars/2025-2026/bmw/x7/xdrive40i/livonia-mi?mileage=15000&searchRadius=0"

  # URLs without a trim segment (e.g. XM) get no filter automatically
  uv run python crawl_autotrader.py \
      --url "https://www.autotrader.com/cars-for-sale/all-cars/2025-2026/bmw/xm/livonia-mi?mileage=15000&searchRadius=0"

  # add more excluded states (default is just CA). Pass '' to keep all.
  uv run python crawl_autotrader.py --exclude-states CA,HI

  # run the browser headless (first run should be headed so any DataDome
  # challenge can be solved manually)
  uv run python crawl_autotrader.py --headless
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

# Reuse small helpers from the sibling module so records stay consistent
# with the rest of the pipeline.
from search_inventory import _normalize_phone, _vin_links, _vin_slug_url
from carfax_utils import apply_carfax, badge_log_suffix

PROFILE = str(pathlib.Path.home() / ".bmw_browser_profile")
NEXT_DATA_RE = re.compile(
    r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
)

DEFAULT_URL = (
    "https://www.autotrader.com/cars-for-sale/all-cars/2025/bmw/x7/m60i/"
    "livonia-mi?mileage=15000&searchRadius=0"
)


# ──────────────────────────────────────────────────────────────────────────────
# HTML → structured data
# ──────────────────────────────────────────────────────────────────────────────
def _extract_next_data(html: str) -> dict:
    m = NEXT_DATA_RE.search(html)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}


def _paged_url(base: str, first: int, num: int = 25) -> str:
    """Return *base* with ``firstRecord`` / ``numRecords`` query params set."""
    u = urlparse(base)
    qs = parse_qs(u.query, keep_blank_values=True)
    qs["firstRecord"] = [str(first)]
    qs["numRecords"] = [str(num)]
    return urlunparse(u._replace(query=urlencode(qs, doseq=True)))


def _dealer_domain(owner: dict) -> str:
    """Strip Autotrader tracking params → clean ``https://www.dealer.com``."""
    href = (owner.get("website") or {}).get("href") or ""
    if not href:
        return ""
    p = urlparse(href)
    if not p.netloc:
        return ""
    return f"{p.scheme or 'https'}://{p.netloc}"


def _to_int(val: Any) -> int | None:
    if val is None:
        return None
    try:
        return int(str(val).replace(",", "").replace("$", "").strip())
    except (ValueError, TypeError):
        return None


def _normalize_trim(s: str | None) -> str:
    """Lowercase + strip non-alphanumerics so "M60i", "m60i", and
    "M60i xDrive Sports Activity Vehicle" all reduce to comparable forms."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _trim_matches(listing_trim: str | None, filter_trim: str | None) -> bool:
    """Return True if the listing's atTrim contains the filter trim.

    Spotlight/featured ads can slip into the inventory dict even when they
    don't match the query trim, so we filter them out client-side. Autotrader
    returns ``atTrim`` either as the short form (``"M60i"``) or a long
    marketing string (``"M60i xDrive Sports Activity Vehicle"``), so an exact
    equality check would drop most legitimate hits. Substring on the
    normalized form accepts both shapes.
    """
    if not filter_trim:
        return True
    return _normalize_trim(filter_trim) in _normalize_trim(listing_trim)


_MAKES = ("bmw", "mini")


def _extract_make_model_trim_from_url(url: str) -> tuple[str | None, str | None, str | None]:
    """Pull (make, model, trim) slugs from a /cars-for-sale URL.

    Autotrader's search-URL convention is::

        /cars-for-sale/all-cars/{year}/{make}/{model}/{trim}/{city-state}?...

    The trailing city segment always exists; ``trim`` is optional. ``year``
    may or may not be present in the path — when absent the year is passed
    via startYear/endYear query params instead. We locate the make segment
    by name and read the model + optional trim from positions just after.

    Returns slugs as written in the URL (lowercase). Callers normalize for
    comparison against listing fields.

    Why this matters: Autotrader's SRP ``inventory`` blob now mixes the
    actual results with sponsored/spotlight ads from *other* makes and
    models, so we need to filter all three dimensions client-side rather
    than just trim.
    """
    path = urlparse(url).path.lower()
    parts = [p for p in path.split("/") if p]
    for i, p in enumerate(parts):
        if p in _MAKES:
            after_make = parts[i + 1 :]
            # after_make = [model, ...trim?, city-state]. The last segment is
            # always the city, so anything in between is model + optional trim.
            if len(after_make) < 2:
                return p, None, None
            model = after_make[0]
            # If there are 3+ segments after the make, parts[i+2] is the trim;
            # otherwise the URL has no trim filter (e.g. /bmw/xm/livonia-mi/).
            trim = after_make[1] if len(after_make) >= 3 else None
            return p, model, trim
    return None, None, None


def _extract_trim_from_url(url: str) -> str | None:
    """Compatibility shim: just the trim slug, or None."""
    return _extract_make_model_trim_from_url(url)[2]


def _to_record(
    listing: dict, owner: dict, source_url: str, autotrader_id: str
) -> dict:
    """Map Autotrader JSON shapes → our `results.json` schema."""
    vin = listing.get("vin")
    year = listing.get("year")
    make = (listing.get("make") or {}).get("name") or "BMW"
    model = (listing.get("model") or {}).get("name")
    trim = listing.get("atTrim") or (listing.get("trim") or {}).get("name")

    vtype_raw = (listing.get("listingType") or listing.get("type") or "").lower()
    if "new" in vtype_raw:
        vtype = "new"
    elif "certified" in vtype_raw:
        vtype = "used"  # CPO → used + certified flag
    else:
        vtype = "used"
    certified = "certified" in vtype_raw

    odometer = _to_int((listing.get("mileage") or {}).get("value"))

    color = listing.get("color") or {}
    ext_color = color.get("exteriorColor")
    int_color = color.get("interiorColor")

    pricing = listing.get("pricingDetail") or {}
    internet_price = _to_int(
        pricing.get("salePrice") or pricing.get("incentive") or pricing.get("msrp")
    )

    days_on_lot = listing.get("daysOnSite")

    addr = (owner.get("location") or {}).get("address") or {}
    dealer_url = _dealer_domain(owner)
    phone = _normalize_phone((owner.get("phone") or {}).get("value"))

    return apply_carfax({
        "vin": vin,
        "stockNumber": listing.get("stockNumber"),
        "year": year,
        "make": make,
        "model": model,
        "trim": trim,
        "odometer": odometer,
        "internetPrice": internet_price,
        "extColor": ext_color,
        "interiorColor": int_color,
        "certified": certified,
        "daysOnLot": days_on_lot,
        "dateInStock": None,
        **_vin_links(vin),
        "dealerName": owner.get("name"),
        "dealerCity": addr.get("city"),
        "dealerState": addr.get("state"),
        "dealerUrl": dealer_url,
        "dealerPhone": phone,
        "platform": "autotrader",
        "type": vtype,
        "link": f"https://www.autotrader.com/cars-for-sale/vehicle/{autotrader_id}",
        "vinLink": _vin_slug_url(
            dealer_url, vin, year, make, model, certified, vtype
        ),
        "resolvedLink": None,
        "autotraderId": autotrader_id,
        "autotraderSearchUrl": source_url,
    }, listing)


def _is_autotrader_blocked(html: str, title: str) -> str | None:
    """Return a human-readable block reason, or None if the page looks OK."""
    low = html.lower()
    if "site is currently unavailable" in low or "page unavailable" in title.lower():
        m = re.search(r"Incident Number:\s*([^\s<]+)", html, re.I)
        incident = f" (incident {m.group(1)})" if m else ""
        return (
            "Autotrader outage / anti-bot block page"
            + incident
        )
    if "captcha" in low or "datadome" in low:
        return "Autotrader CAPTCHA / DataDome challenge"
    return None


def _load_srp_page(page, url: str, *, max_wait_s: float = 20.0) -> tuple[dict, dict, dict]:
    """Navigate to an Autotrader SRP and wait for ``__eggsState.inventory``.

    DataDome often serves a shell page first; poll until the inventory blob
    appears or we time out.
    """
    page.goto(url, wait_until="domcontentloaded", timeout=90000)
    deadline = time.time() + max_wait_s
    last_title = ""
    while time.time() < deadline:
        html = page.content()
        last_title = page.title()
        data = _extract_next_data(html)
        eggs = (
            data.get("props", {})
            .get("pageProps", {})
            .get("__eggsState", {})
        )
        inventory: dict = eggs.get("inventory") or {}
        if inventory:
            return data, inventory, eggs.get("owners") or {}
        block = _is_autotrader_blocked(html, last_title)
        if block:
            print(f"  blocked: {block}", flush=True)
            page.wait_for_timeout(1500)
            continue
        # Blocked/challenge pages have no __NEXT_DATA__ at all.
        if not data and ("unavailable" in last_title.lower() or "captcha" in html.lower()):
            page.wait_for_timeout(1500)
            continue
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        page.wait_for_timeout(1000)
    print(
        f"  no inventory blob (title={last_title!r}) — "
        "Autotrader may be blocking this browser session.\n"
        "  Fix: run `uv run crawl_autotrader.py --warmup --url '<search url>'` "
        "in a visible Chrome window, or use --skip-autotrader.",
        flush=True,
    )
    return {}, {}, {}


def _open_browser(p, *, headless: bool):
    """Return (page, cleanup) using real Chrome CDP when available.

    ``search_inventory.py`` warms Chrome on :9222 with trust cookies; prefer
    that over Playwright's isolated persistent profile, which Autotrader's
    DataDome often blocks outright.
    """
    from search_inventory import _connect_real_chrome, ensure_chrome_debug

    ensure_chrome_debug()
    browser, _ = _connect_real_chrome(p)
    if browser:
        # Reuse the debug Chrome's default context (has trust cookies).
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()

        def cleanup() -> None:
            page.close()
            if not browser.contexts or ctx not in browser.contexts:
                ctx.close()

        print("  using real Chrome via CDP (~/.chrome_debug_session)", flush=True)
        return page, cleanup

    from playwright_stealth.stealth import Stealth

    stealth = Stealth()
    ctx = p.chromium.launch_persistent_context(
        PROFILE,
        channel="chrome",
        headless=headless,
        args=["--disable-blink-features=AutomationControlled"],
        ignore_default_args=["--enable-automation"],
        viewport={"width": 1400, "height": 1000},
    )
    stealth.apply_stealth_sync(ctx)
    page = ctx.new_page()
    print(
        "  using Playwright persistent profile (~/.bmw_browser_profile). "
        "If blocked, run: uv run crawl_autotrader.py --warmup --url '…'",
        flush=True,
    )

    def cleanup() -> None:
        ctx.close()

    return page, cleanup


def _warmup(search_url: str) -> int:
    """Open Autotrader in visible Chrome so the user can clear blocks once."""
    from playwright.sync_api import sync_playwright

    print("Opening Autotrader in Chrome (~/.chrome_debug_session).", flush=True)
    print("→ If you see 'site is currently unavailable', wait or try again later.", flush=True)
    print("→ Once search results load normally, press Enter here.", flush=True)
    with sync_playwright() as p:
        page, cleanup = _open_browser(p, headless=False)
        try:
            page.goto(search_url, wait_until="domcontentloaded", timeout=90000)
            print(f"  title: {page.title()!r}", flush=True)
        except Exception as e:
            print(f"  navigation warning: {e.__class__.__name__}", file=sys.stderr)
        try:
            input("Press Enter once Autotrader shows real search results… ")
        except EOFError:
            page.wait_for_timeout(30000)
        _, inv, _ = _load_srp_page(page, search_url, max_wait_s=5.0)
        ok = len(inv) > 0
        print("Trust cookies saved — crawl should work now." if ok else
              "Still no inventory blob — try again or use --skip-autotrader.", flush=True)
        cleanup()
    return 0 if ok else 1


# ──────────────────────────────────────────────────────────────────────────────
# Crawl loop
# ──────────────────────────────────────────────────────────────────────────────
def crawl(
    search_url: str,
    *,
    drop_states: set[str],
    make_filter: str | None,
    model_filter: str | None,
    trim_filter: str | None,
    headless: bool,
    max_pages: int = 12,
    min_miles: int | None = 60,
    max_miles: int | None = 15000,
) -> list[dict]:
    """Paginate the Autotrader search results and return a flat list of
    records (one per unique VIN).

    The ``make_filter``/``model_filter``/``trim_filter`` triple is applied
    client-side to weed out spotlight/sponsored ads — Autotrader includes
    them in the ``inventory`` blob even though they don't match the user's
    query.
    """
    from playwright.sync_api import sync_playwright

    seen_vins: dict[str, dict] = {}
    dropped_state: set[str] = set()   # VINs dropped, deduped
    dropped_mileage: set[str] = set()
    dropped_make_model: set[str] = set()
    dropped_trim: set[str] = set()
    badge_count = 0

    with sync_playwright() as p:
        page, cleanup = _open_browser(p, headless=headless)
        try:
            for page_idx in range(max_pages):
                first = page_idx * 25
                url = _paged_url(search_url, first, 25)
                print(f"→ page {page_idx + 1}  (firstRecord={first})", flush=True)
                try:
                    _, inventory, owners = _load_srp_page(page, url)
                except Exception as e:
                    print(f"  goto failed: {e}", flush=True)
                    break

                if not inventory:
                    break

                new_on_page = 0
                for at_id, listing in inventory.items():
                    vin = listing.get("vin")
                    if not vin:
                        continue
                    # Skip VINs we've already decided about on an earlier page
                    # (kept, or dropped for any reason).
                    if (
                        vin in seen_vins
                        or vin in dropped_make_model
                        or vin in dropped_trim
                        or vin in dropped_state
                    ):
                        continue

                    # Drop spotlight ads from other makes/models. Autotrader's
                    # SRP `inventory` blob mixes results with sponsored ads of
                    # nearby/popular vehicles, so URL-derived make+model is the
                    # only safe way to keep just real query matches.
                    listing_make = (listing.get("make") or {}).get("name")
                    listing_model = (listing.get("model") or {}).get("name")
                    if make_filter and _normalize_trim(listing_make) != _normalize_trim(make_filter):
                        dropped_make_model.add(vin)
                        continue
                    if model_filter and _normalize_trim(listing_model) != _normalize_trim(model_filter):
                        dropped_make_model.add(vin)
                        continue

                    if not _trim_matches(listing.get("atTrim"), trim_filter):
                        dropped_trim.add(vin)
                        continue

                    odo = _to_int((listing.get("mileage") or {}).get("value"))
                    if min_miles is not None and odo is not None and odo < min_miles:
                        dropped_mileage.add(vin)
                        continue
                    if max_miles is not None and odo is not None and odo > max_miles:
                        dropped_mileage.add(vin)
                        continue

                    owner_id = str(listing.get("ownerId") or listing.get("owner") or "")
                    owner = owners.get(owner_id) or {}
                    # Fall back to `ownerName` on the listing if the owners
                    # dict doesn't have this dealer (shouldn't happen on SRP
                    # but defensive).
                    if not owner.get("name") and listing.get("ownerName"):
                        owner = {**owner, "name": listing["ownerName"]}
                    rec = _to_record(listing, owner, search_url, at_id)

                    if (rec.get("dealerState") or "").upper() in drop_states:
                        dropped_state.add(vin)
                        continue

                    seen_vins[vin] = rec
                    new_on_page += 1
                    if rec.get("carfaxBadge"):
                        badge_count += 1
                        print(f"    {vin}{badge_log_suffix(rec)}", flush=True)

                print(
                    f"  inv={len(inventory)}  new={new_on_page}  "
                    f"cum={len(seen_vins)}",
                    flush=True,
                )

                # Stop when a page produces nothing new (end of results) — but
                # also require at least one page of data first.
                if new_on_page == 0 and page_idx > 0:
                    break
        finally:
            cleanup()

    print(
        f"\ncollected: {len(seen_vins)} VINs  "
        f"({badge_count} with CarFax badges)  "
        f"(dropped {len(dropped_make_model)} off-make/model, "
        f"{len(dropped_trim)} off-trim, "
        f"{len(dropped_mileage)} off-mileage, "
        f"{len(dropped_state)} in-state)"
    )
    return list(seen_vins.values())


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--url", default=DEFAULT_URL, help="Autotrader search URL")
    ap.add_argument(
        "--out",
        default="autotrader_results.json",
        help="Output JSON path (default: autotrader_results.json)",
    )
    ap.add_argument(
        "--trim",
        default=None,
        help="Filter listings by atTrim using a case- and "
        "punctuation-insensitive substring match. When omitted, the trim is "
        "auto-derived from the URL path (e.g. '.../bmw/x7/m60i/...' → 'm60i'); "
        "URLs without a trim segment (e.g. '.../bmw/xm/livonia-mi/...') get no "
        "filter. Pass --trim '' to explicitly disable filtering.",
    )
    ap.add_argument(
        "--exclude-states",
        action="append",
        default=[],
        help="Drop listings from these states (comma-separated two-letter "
        "codes, also repeatable). Defaults to 'CA'. Pass an empty string "
        "to keep all states.",
    )
    ap.add_argument("--headless", action="store_true", help="Run Chrome headless")
    ap.add_argument("--warmup", action="store_true",
                    help="Open Autotrader in visible Chrome to clear anti-bot blocks")
    ap.add_argument("--max-pages", type=int, default=12)
    ap.add_argument("--min-miles", type=int, default=60,
                    help="Minimum odometer (default: 60)")
    ap.add_argument("--max-miles", type=int, default=15000,
                    help="Maximum odometer (default: 15000)")

    args = ap.parse_args()

    if args.warmup:
        sys.exit(_warmup(args.url))

    # Parse --exclude-states (repeatable + comma-separated). Default CA.
    if args.exclude_states:
        drop_states: set[str] = set()
        for entry in args.exclude_states:
            for s in entry.split(","):
                s = s.strip().upper()
                if s:
                    drop_states.add(s)
    else:
        drop_states = {"CA"}

    # Always derive make+model from the URL path — Autotrader's SRP inventory
    # blob mixes the real query results with spotlight ads from other makes
    # and models, so we always need both filters. There's no CLI override
    # because the URL is the source of truth for what was queried.
    make_filter, model_filter, url_trim = _extract_make_model_trim_from_url(args.url)
    print(
        f"derived from URL: make={make_filter!r}  model={model_filter!r}  "
        f"trim={url_trim!r}",
        flush=True,
    )

    # Explicit --trim wins over URL-derived. `args.trim is None` means the
    # user didn't pass --trim at all → auto-derive from URL. An empty string
    # ("--trim ''") is the documented way to disable filtering entirely.
    if args.trim is None:
        trim_filter = url_trim
        if trim_filter:
            print(f"trim filter (auto): {trim_filter}", flush=True)
        else:
            print("trim filter disabled (URL has no trim segment)", flush=True)
    else:
        trim_filter = args.trim.strip() or None
        print(f"trim filter (explicit): {trim_filter or '(disabled)'}", flush=True)

    results = crawl(
        args.url,
        drop_states=drop_states,
        make_filter=make_filter,
        model_filter=model_filter,
        trim_filter=trim_filter,
        headless=args.headless,
        max_pages=args.max_pages,
        min_miles=args.min_miles,
        max_miles=args.max_miles,
    )

    out_path = pathlib.Path(args.out)
    out_path.write_text(json.dumps(results, indent=2))
    print(f"wrote {len(results)} records → {out_path}")


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    main()
