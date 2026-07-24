"""
Platform census: detect which website platform every dealer in
master_dealers.json runs on, and harvest DealerInspire Algolia credentials
along the way.

Two-phase sweep:
  Phase A – plain requests (threaded). Detects platforms from homepage HTML.
            Sites behind Cloudflare/DataDome (403 / challenge page) or with no
            recognizable platform are queued for phase B.
  Phase B – Playwright stealth browser (sequential, persistent
            ~/.bmw_browser_profile like the other crawlers). Loads the page,
            lets the JS challenge resolve, then re-runs detection on the
            rendered DOM (which also catches client-side-injected widgets).

Results are written back into master_dealers.json per record:
  census: {platform, signals, method, http_status, blocked, checked_at}
plus harvested DI credentials promoted to the top-level fields
(di_index / app_id / search_key) that search_inventory.py already uses.

The sweep is resumable: records with a census result are skipped unless
--force is given.

Usage:
  uv run python platform_census.py                 # full sweep + report
  uv run python platform_census.py --limit 20      # smoke test
  uv run python platform_census.py --no-browser    # phase A only
  uv run python platform_census.py --report-only   # regenerate coverage_report.md
"""

import argparse
import json
import os
import pathlib
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
MASTER_FILE = os.path.join(HERE, "master_dealers.json")
REPORT_FILE = os.path.join(HERE, "coverage_report.md")
BROWSER_PROFILE_DIR = str(pathlib.Path.home() / ".bmw_browser_profile")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "sec-ch-ua": '"Not:A-Brand";v="99", "Chromium";v="145"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
}

# Signature table, in priority order. The first structural match (dealercom /
# dealerinspire) wins outright; otherwise the highest-priority string signature
# becomes the primary platform and all matches are kept as `signals`.
# Digital-retail add-ons (Roadster, etc.) are recorded but never primary.
SIGNATURES: list[tuple[str, list[str]]] = [
    ("dealercom",      [r"window\.DDC", r"static\.dealer\.com", r"ddc-content"]),
    ("dealerinspire",  [r"sb-algolia-helper", r"dealerinspire\.com", r"di-websites-platform"]),
    ("dealeron",       [r"dealeron\.com", r"\bDealerOn\b", r"dlron\.us"]),
    ("sincro_cdk",     [r"sincrod?\.com", r"assets\.sincro", r"cdk-?global", r"cdkassets"]),
    ("dealereprocess", [r"dealereprocess"]),
    ("teamvelocity",   [r"teamvelocity", r"apolloprogram"]),
    ("foxdealer",      [r"foxdealer"]),
    ("jazel",          [r"jazel"]),
    ("dealerfire",     [r"dealerfire"]),
    ("dealersocket",   [r"dealersocket"]),
    ("sokal",          [r"sokal\.(com|dev)", r"sokalmedia"]),
    ("overfuel",       [r"overfuel"]),
    ("dealervenom",    [r"dealervenom"]),
    ("stream",         [r"streamcompanies"]),
]

ADDON_SIGNATURES: list[tuple[str, list[str]]] = [
    ("roadster",  [r"roadster\.com", r"express\.roadster"]),
    ("gubagoo",   [r"gubagoo"]),
    ("cargurus",  [r"cargurus"]),
]

# The #sb-algolia-helper div only renders on inventory pages, not homepages.
DI_INVENTORY_PATHS = ["/used-vehicles/", "/new-vehicles/", "/inventory/"]

BLOCK_MARKERS = re.compile(
    r"cf-browser-verification|challenge-platform|cf_chl|__cf_chl"
    r"|datadome|geo\.captcha-delivery|Just a moment|Attention Required"
    r"|Access denied|Incapsula|imperva|px-captcha",
    re.IGNORECASE,
)

# A Cloudflare "Sorry, you have been blocked" page names the origin it protects
# ("You are unable to access bmw.websites.dealerinspire.com"), which reliably
# reveals the underlying platform even when we can't render the real site.
_BLOCKED_HOST_RE = re.compile(r"unable to access</span>\s*([a-z0-9.\-]+)", re.IGNORECASE)
_HOST_PLATFORM = [
    ("dealerinspire.com", "dealerinspire"),
    ("dealer.com", "dealercom"),
    ("dealeron.com", "dealeron"),
    ("sincrod", "sincro_cdk"),
    ("dealereprocess", "dealereprocess"),
    ("teamvelocity", "teamvelocity"),
    ("foxdealer", "foxdealer"),
]


def platform_from_blockpage(html: str) -> str | None:
    m = _BLOCKED_HOST_RE.search(html)
    host = m.group(1).lower() if m else ""
    for needle, platform in _HOST_PLATFORM:
        if needle in host:
            return platform
    return None


def is_terminal_block(html: str) -> bool:
    """A static Cloudflare 'Sorry, you have been blocked' page (as opposed to a
    'Just a moment' interstitial) will never resolve in a headless browser, so
    there's no point polling — grab the hint and move on."""
    return "you have been blocked" in html.lower() or "Attention Required" in html


# ── Detection ─────────────────────────────────────────────────────────────────

def detect_signatures(html: str) -> tuple[str | None, list[str]]:
    """Return (primary_platform, all_signals) from page HTML."""
    signals = []
    for name, patterns in SIGNATURES:
        if any(re.search(p, html) for p in patterns):
            signals.append(name)
    for name, patterns in ADDON_SIGNATURES:
        if any(re.search(p, html, re.IGNORECASE) for p in patterns):
            signals.append(f"addon:{name}")
    primary = next((s for s in signals if not s.startswith("addon:")), None)
    return primary, signals


def extract_ddc_site_id(html: str) -> str | None:
    m = re.search(r'["\']?siteId["\']?\s*[:=]\s*["\']([^"\'<>\s]+)["\']', html)
    return m.group(1) if m else None


def extract_di_config(html: str) -> dict | None:
    """Pull Algolia app-id/search-key/index off the #sb-algolia-helper div."""
    m = re.search(r'<div[^>]+id="sb-algolia-helper"[^>]*>', html)
    if not m:
        return None
    tag = m.group(0)

    def attr(name):
        am = re.search(rf'data-{name}="([^"]*)"', tag)
        return am.group(1) if am else None

    index = attr("index")
    slug_m = re.match(r"^(.+?)(?:-sbm\d+)?_production_inventory", index or "")
    return {
        "app_id": attr("app-id"),
        "search_key": attr("search-key"),
        "di_index": index,
        "di_slug": slug_m.group(1) if slug_m else None,
    }


def is_blocked(status: int, html: str) -> bool:
    if status in (403, 429, 503) or (status and len(html) < 6000 and BLOCK_MARKERS.search(html)):
        return True
    return False


# ── Phase A: plain requests ───────────────────────────────────────────────────

def fetch_plain(rec: dict) -> dict:
    """Fetch homepage with requests; return census result dict."""
    url = rec["website"]
    result = {"method": "requests"}
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        html = resp.text
        result["http_status"] = resp.status_code
        if is_blocked(resp.status_code, html):
            result["blocked"] = True
            hint = platform_from_blockpage(html) or detect_signatures(html)[0]
            if hint:
                result["block_hint"] = hint
            return result
        primary, signals = detect_signatures(html)
        result["platform"] = primary or "unknown"
        result["signals"] = signals
        _attach_platform_config(result, html, primary)
        if primary == "dealerinspire" and "di" not in result:
            origin = _origin(resp.url)
            for path in DI_INVENTORY_PATHS:
                try:
                    inv = requests.get(origin + path, headers=HEADERS,
                                       timeout=15, allow_redirects=True)
                    di = extract_di_config(inv.text)
                    if di and di.get("app_id"):
                        result["di"] = di
                        break
                except requests.RequestException:
                    continue
    except requests.RequestException as e:
        result["http_status"] = 0
        result["error"] = str(e)[:200]
    return result


def _origin(url: str) -> str:
    from urllib.parse import urlparse
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _attach_platform_config(result: dict, html: str, primary: str | None) -> None:
    if primary == "dealercom":
        site_id = extract_ddc_site_id(html)
        if site_id:
            result["site_id"] = site_id
    elif primary == "dealerinspire":
        di = extract_di_config(html)
        if di and di.get("app_id"):
            result["di"] = di


# ── Phase B: Playwright stealth browser ───────────────────────────────────────

def _clear_stale_singleton_locks() -> None:
    profile = pathlib.Path(BROWSER_PROFILE_DIR)
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        p = profile / name
        try:
            if p.is_symlink() or p.exists():
                p.unlink()
        except OSError:
            pass


class BrowserFetcher:
    """Lazily-launched stealth Chromium with the shared persistent profile."""

    def __init__(self):
        self._pw = None
        self._ctx = None
        self.unavailable = False

    def _ensure(self):
        if self.unavailable or self._ctx is not None:
            return self._ctx
        try:
            from playwright.sync_api import sync_playwright
            from playwright_stealth.stealth import Stealth
        except Exception as e:
            print(f"  ! playwright unavailable ({e}) — skipping browser phase",
                  file=sys.stderr)
            self.unavailable = True
            return None
        pathlib.Path(BROWSER_PROFILE_DIR).mkdir(parents=True, exist_ok=True)
        _clear_stale_singleton_locks()
        try:
            self._pw = sync_playwright().start()
            self._ctx = self._pw.chromium.launch_persistent_context(
                BROWSER_PROFILE_DIR,
                channel="chrome",
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
                user_agent=HEADERS["User-Agent"],
                viewport={"width": 1440, "height": 900},
            )
            Stealth().apply_stealth_sync(self._ctx)
            print("  · launched stealth browser for blocked sites", file=sys.stderr)
        except Exception as e:
            print(f"  ! failed to launch browser ({e})", file=sys.stderr)
            self.unavailable = True
        return self._ctx

    def fetch(self, url: str) -> dict:
        ctx = self._ensure()
        result = {"method": "browser"}
        if ctx is None:
            result["error"] = "browser unavailable"
            return result
        page = None
        # DealerInspire pages fire Algolia XHRs on load; capturing them off the
        # wire is far more reliable than scraping the helper div (which many
        # newer DI themes no longer render server-side).
        sniffed = {"app_id": None, "search_key": None, "indices": set()}

        def on_request(req):
            u = req.url
            if "algolia.net" not in u and "algolianet.com" not in u:
                return
            h = req.headers
            if h.get("x-algolia-application-id"):
                sniffed["app_id"] = h["x-algolia-application-id"]
            elif not sniffed["app_id"]:
                m = re.match(r"https://([a-z0-9]+)-dsn", u, re.IGNORECASE)
                if m:
                    sniffed["app_id"] = m.group(1).upper()
            if h.get("x-algolia-api-key"):
                sniffed["search_key"] = h["x-algolia-api-key"]
            for idx in re.findall(r"([a-z0-9_]+_production_inventory)", req.post_data or ""):
                sniffed["indices"].add(idx)

        try:
            page = ctx.new_page()
            page.on("request", on_request)
            # wait_until="commit" resolves as soon as the response is committed,
            # so a Cloudflare 403 challenge body doesn't raise — we then poll for
            # the challenge JS to swap in the real page.
            status = 0
            try:
                resp = page.goto(url, wait_until="commit", timeout=20000)
                status = resp.status if resp else 0
            except Exception:
                page.wait_for_timeout(2500)
            html = self._content(page)
            # A static block page won't clear — record the hint and return now.
            if is_terminal_block(html):
                result["http_status"] = status or 403
                result["blocked"] = True
                hint = platform_from_blockpage(html) or detect_signatures(html)[0]
                if hint:
                    result["block_hint"] = hint
                return result
            for _ in range(2):
                if not is_blocked(status, html) and "Just a moment" not in html \
                        and len(html) > 8000:
                    status = 200
                    break
                page.wait_for_timeout(3000)
                html = self._content(page)
            result["http_status"] = status
            # Bail out immediately on a hard block — headless Chrome won't clear
            # it, and further waits/navigation just burn time.
            if is_blocked(status, html):
                result["blocked"] = True
                hint = platform_from_blockpage(html) or detect_signatures(html)[0]
                if hint:
                    result["block_hint"] = hint
                return result
            try:
                page.wait_for_load_state("networkidle", timeout=6000)
            except Exception:
                pass
            page.wait_for_timeout(600)
            html = self._content(page)
            primary, signals = detect_signatures(html)
            result["platform"] = primary or "unknown"
            result["signals"] = signals
            _attach_platform_config(result, html, primary)
            # For DI (or an as-yet-unknown page), browse into inventory so the
            # Algolia XHRs fire, then read credentials off the sniffer.
            if (primary == "dealerinspire" or primary is None) and "di" not in result:
                origin = _origin(page.url if not page.url.startswith("chrome-error") else url)
                for path in DI_INVENTORY_PATHS:
                    if sniffed["app_id"] and sniffed["indices"]:
                        break
                    try:
                        page.goto(origin + path, wait_until="commit", timeout=25000)
                        page.wait_for_timeout(6000)
                        self._content(page)
                    except Exception:
                        continue
                if not signals and sniffed["app_id"]:
                    result["platform"] = "dealerinspire"
                    result["signals"] = ["dealerinspire"]
                di = self._sniffed_to_di(sniffed)
                if di:
                    result["di"] = di
        except Exception as e:
            result["error"] = str(e)[:200]
            result.setdefault("http_status", 0)
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass
        return result

    @staticmethod
    def _content(page) -> str:
        """page.content() can race with an in-flight navigation; retry briefly."""
        for _ in range(4):
            try:
                return page.content()
            except Exception:
                page.wait_for_timeout(1200)
        try:
            return page.content()
        except Exception:
            return ""

    @staticmethod
    def _sniffed_to_di(sniffed: dict) -> dict | None:
        if not sniffed.get("app_id"):
            return None
        di = {"app_id": sniffed["app_id"], "search_key": sniffed.get("search_key")}
        indices = sniffed.get("indices") or set()
        prod = [i for i in indices if i.endswith("_production_inventory")]
        if prod:
            index = sorted(prod)[0]
            di["di_index"] = index
            slug_m = re.match(r"^(.+?)(?:-sbm\d+)?_production_inventory", index)
            di["di_slug"] = slug_m.group(1) if slug_m else None
        return di

    def close(self):
        for closer in (lambda: self._ctx.close(), lambda: self._pw.stop()):
            try:
                closer()
            except Exception:
                pass
        self._ctx = None
        self._pw = None


# ── Record update / persistence ──────────────────────────────────────────────

def apply_census(rec: dict, result: dict) -> None:
    result["checked_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rec["census"] = result
    platform = result.get("platform")
    if platform and platform != "unknown":
        rec["platform"] = platform
    if result.get("site_id"):
        rec["site_id"] = result["site_id"]
    di = result.get("di")
    if di:
        for key in ("app_id", "search_key", "di_index", "di_slug"):
            if di.get(key):
                rec[key] = di[key]


def save(records: list[dict]) -> None:
    with open(MASTER_FILE, "w") as f:
        json.dump(records, f, indent=2)


def _label(rec: dict) -> str:
    return f"{rec.get('name', '?')} ({rec.get('state', '?')})"


# ── Report ────────────────────────────────────────────────────────────────────

def generate_report(records: list[dict]) -> None:
    total = len(records)
    reachable = [r for r in records if r.get("website_ok")]
    censused = [r for r in records if r.get("census")]

    def final_platform(r):
        c = r.get("census") or {}
        p = c.get("platform")
        if p and p != "unknown":
            return p
        if c.get("blocked"):
            return f"blocked ({c.get('block_hint', 'no hint')})"
        if not r.get("website_ok"):
            return "site dead/unreachable"
        if p == "unknown":
            return "unknown"
        return "not yet censused"

    by_platform: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_platform[final_platform(r)].append(r)

    # Queryability per platform, matching what search_inventory.py supports.
    di_cc_ready = [r for r in records if r.get("cc_ccid")]
    do_ready = [r for r in records if r.get("do_dealer_id")]

    lines = []
    lines.append("# BMW Dealer Coverage Report")
    lines.append("")
    lines.append(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")
    lines.append(f"- **Master directory:** {total} dealers "
                 f"({len(reachable)} reachable websites)")
    lines.append(f"- **Platform census completed for:** {len(censused)} dealers")
    lines.append("")

    lines.append("## Platform breakdown")
    lines.append("")
    lines.append("| Platform | Dealers | % of total | Crawler support today |")
    lines.append("|---|---|---|---|")
    SUPPORT = {
        "dealercom": "Yes — OEM `bmwgroup` query + standalone-site queries in search_inventory.py",
        "dealerinspire": "Yes — Cars Commerce search API (per-dealer `cc_ccid`)",
        "dealeron": "Yes — Cosmos SRP JSON API (per-dealer `do_dealer_id`/page ids)",
        "teamvelocity": "Yes — SRP dataLayer parser",
    }
    for platform, recs in sorted(by_platform.items(), key=lambda kv: -len(kv[1])):
        pct = 100 * len(recs) / total
        support = SUPPORT.get(platform, "No — covered via aggregators (Autotrader / Cars.com / TrueCar)")
        lines.append(f"| {platform} | {len(recs)} | {pct:.0f}% | {support} |")
    lines.append("")

    lines.append("## Harvested API credentials")
    lines.append("")
    lines.append(f"- DealerInspire with Cars Commerce `cc_ccid`: {len(di_cc_ready)} dealers")
    lines.append(f"- DealerOn with `do_dealer_id` + page ids: {len(do_ready)} dealers")
    lines.append("")

    lines.append("## Dealers by platform")
    for platform, recs in sorted(by_platform.items(), key=lambda kv: -len(kv[1])):
        lines.append("")
        lines.append(f"### {platform} ({len(recs)})")
        lines.append("")
        states = Counter(r.get("state", "?") for r in recs)
        lines.append("States: " + ", ".join(f"{s}({n})" for s, n in states.most_common()))
        lines.append("")
        for r in sorted(recs, key=lambda x: (x.get("state", ""), x.get("name", ""))):
            extra = ""
            if r.get("census", {}).get("signals"):
                addons = [s for s in r["census"]["signals"] if s.startswith("addon:")]
                if addons:
                    extra = "  _" + ", ".join(addons) + "_"
            lines.append(f"- {r.get('state', '?')} — {r.get('name', '?')} — "
                         f"{r.get('website', '')}{extra}")

    lines.append("")
    lines.append("## Coverage summary")
    lines.append("")
    ddc_n = len(by_platform.get("dealercom", []))
    di_n = len(by_platform.get("dealerinspire", []))
    do_n = len(by_platform.get("dealeron", []))
    tv_n = len(by_platform.get("teamvelocity", []))
    covered_now = ddc_n + len(di_cc_ready) + len(do_ready) + tv_n
    lines.append(f"- **Directly crawlable:** {covered_now} dealers — "
                 f"{ddc_n} Dealer.com + {len(di_cc_ready)} DealerInspire (Cars Commerce) + "
                 f"{len(do_ready)} DealerOn + {tv_n} Team Velocity, "
                 f"~{100 * covered_now / total:.0f}% of the {total}-dealer roster.")
    if len(di_cc_ready) < di_n:
        lines.append(f"- **DealerInspire credential gap:** {di_n - len(di_cc_ready)} of "
                     f"{di_n} DI dealers still missing a `cc_ccid` — rerun "
                     "`harvest_di_ccid.py` to close it.")
    if len(do_ready) < do_n:
        lines.append(f"- **DealerOn credential gap:** {do_n - len(do_ready)} of "
                     f"{do_n} DealerOn dealers still missing ids — rerun "
                     "`harvest_dealeron.py`.")
    other = sum(len(rs) for p, rs in by_platform.items()
                if p not in ("dealercom", "dealerinspire", "dealeron", "teamvelocity")
                and not p.startswith(("blocked", "site dead", "unknown", "not yet")))
    lines.append(f"- **On platforms with no adapter:** {other} dealers "
                 "(Dealer eProcess, DealerSocket, …) — reachable via the "
                 "Autotrader / Cars.com / TrueCar aggregators.")
    lines.append("")

    lines.append("## Remaining gaps")
    lines.append("")
    ranked = [(p, len(rs)) for p, rs in by_platform.items()
              if p not in ("dealercom", "dealerinspire", "dealeron", "teamvelocity")
              and not p.startswith(("blocked", "site dead", "unknown", "not yet"))]
    ranked.sort(key=lambda kv: -kv[1])
    APPROACH = {
        "dealereprocess": "Dealer eProcess sites; small count, evaluate an API probe "
                          "before committing to an adapter.",
        "dealersocket": "DealerSocket/DealerFire; small count, aggregator coverage "
                        "suffices near-term.",
    }
    for i, (p, n) in enumerate(ranked, 1):
        lines.append(f"{i}. **{p}** ({n} dealers). " + APPROACH.get(p, ""))
    lines.append("")
    lines.append("The remaining `unknown` / dead-site records are mostly roster entries "
                 "whose canonical URL could not be resolved cleanly; they are a data-"
                 "cleanup task, not a platform to support.")
    lines.append("")

    with open(REPORT_FILE, "w") as f:
        f.write("\n".join(lines))
    print(f"\nWrote report → {REPORT_FILE}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="BMW dealer platform census")
    parser.add_argument("--limit", type=int, help="Max records to process")
    parser.add_argument("--force", action="store_true",
                        help="Re-census records that already have results")
    parser.add_argument("--retry-errors", action="store_true",
                        help="Re-census only records whose last census errored")
    parser.add_argument("--no-browser", action="store_true",
                        help="Skip the Playwright fallback phase")
    parser.add_argument("--report-only", action="store_true",
                        help="Just regenerate coverage_report.md")
    parser.add_argument("--workers", type=int, default=10)
    args = parser.parse_args()

    with open(MASTER_FILE) as f:
        records = json.load(f)

    if args.report_only:
        generate_report(records)
        return

    def needs_census(r: dict) -> bool:
        if not r.get("website_ok"):
            return False
        if args.retry_errors:
            return "error" in (r.get("census") or {})
        return args.force or "census" not in r

    todo = [r for r in records if needs_census(r)]
    # Uncovered dealers first — they're the point of this exercise.
    todo.sort(key=lambda r: (r.get("coverage") != "uncovered", r.get("state", "")))
    if args.limit:
        todo = todo[:args.limit]

    print(f"Census: {len(todo)} dealers to check "
          f"({sum(1 for r in todo if r.get('coverage') == 'uncovered')} uncovered)")

    # ── Phase A ──
    needs_browser: list[dict] = []
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_plain, r): r for r in todo}
        for future in as_completed(futures):
            rec = futures[future]
            result = future.result()
            done += 1
            if result.get("blocked") or result.get("platform") in (None, "unknown") \
                    or "error" in result:
                needs_browser.append(rec)
                tag = "blocked" if result.get("blocked") else \
                      result.get("error", "unknown")[:40] or "unknown"
                print(f"  [A {done}/{len(todo)}] {_label(rec)}: {tag} → browser queue")
                rec["_pending"] = result
            else:
                apply_census(rec, result)
                print(f"  [A {done}/{len(todo)}] {_label(rec)}: {result['platform']}")
            if done % 25 == 0:
                save(records)
    save(records)

    # ── Phase B ──
    if args.no_browser:
        for rec in needs_browser:
            apply_census(rec, rec.pop("_pending"))
        save(records)
    elif needs_browser:
        print(f"\nBrowser phase: {len(needs_browser)} sites")
        fetcher = BrowserFetcher()
        try:
            for i, rec in enumerate(needs_browser, 1):
                plain = rec.pop("_pending", {})
                result = fetcher.fetch(rec["website"])
                if fetcher.unavailable:
                    apply_census(rec, plain)
                    continue
                # Keep the better of the two attempts.
                if result.get("platform") in (None, "unknown") and not result.get("blocked"):
                    if plain.get("block_hint") and "platform" not in result:
                        result["block_hint"] = plain["block_hint"]
                if result.get("blocked") and plain.get("block_hint") \
                        and not result.get("block_hint"):
                    result["block_hint"] = plain["block_hint"]
                apply_census(rec, result)
                status = result.get("platform") or \
                    ("blocked:" + result.get("block_hint", "?") if result.get("blocked")
                     else result.get("error", "?")[:40])
                print(f"  [B {i}/{len(needs_browser)}] {_label(rec)}: {status}")
                if i % 10 == 0:
                    save(records)
                time.sleep(0.5)
        finally:
            fetcher.close()
        save(records)

    generate_report(records)


if __name__ == "__main__":
    main()
