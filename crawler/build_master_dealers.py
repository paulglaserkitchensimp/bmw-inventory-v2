"""
Builds the authoritative nationwide BMW dealer directory: master_dealers.json.

Sources (merged by normalized host, then fuzzy name+state):
  perplexity_dealers.txt  – 359-dealer nationwide CSV (primary roster)
  dealers.json            – platform-detected dealers (dealercom / dealerinspire)
  bmw_ddc_dealers.json    – Dealer.com OEM-group account sweep (siteId, dealer codes)

Every record's website URL is verified (redirects followed, canonical host
recorded); dead/missing URLs fall back to slug guessing and DuckDuckGo — the
same strategy as search_inventory._resolve_dealer_url.

Output record schema:
  name, city, state          – identity
  website                    – verified canonical origin (https://www...)
  website_ok                 – bool: site responded (any HTTP status incl. 403)
  http_status                – last observed status code (0 = unreachable)
  coverage                   – "ddc" | "di" | "uncovered"
  platform / site_id / di_index / app_id / search_key – carried over when known
  sources                    – which inputs mentioned this dealer

Usage:
  uv run python build_master_dealers.py             # full build + URL verification
  uv run python build_master_dealers.py --no-verify # skip network checks
"""

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
PERPLEXITY_FILE = os.path.join(HERE, "perplexity_dealers.txt")
DEALERS_FILE = os.path.join(HERE, "dealers.json")
DDC_SWEEP_FILE = os.path.join(HERE, "bmw_ddc_dealers.json")
OUTPUT_FILE = os.path.join(HERE, "master_dealers.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

AGGREGATOR_DOMAINS = {
    "cars.com", "edmunds.com", "autotrader.com", "facebook.com", "yelp.com",
    "carfax.com", "cargurus.com", "google.com", "drivefivestar.com",
    "bmwusa.com", "wikipedia.org", "instagram.com", "yellowpages.com",
    "dealerrater.com", "mapquest.com", "kbb.com", "carmax.com", "truecar.com",
    "autolist.com", "youtube.com", "dailymotion.com", "tiendeo.us",
    "ibegin.com", "top10place.com", "bmwgroup.com", "bmwgroup-werke.com",
    "linkedin.com", "twitter.com", "x.com", "tripadvisor.com", "foursquare.com",
}

# BMW/MINI corporate sites (bmw.com, bmw.co.uk, bmw-me.com, bmw-berlin.de, …)
# that DuckDuckGo loves to return instead of the actual dealership.
_CORPORATE_RE = re.compile(r"^(bmw|mini)(-[a-z0-9]+)?\.(com|net|org|de|co\.[a-z]{2}|[a-z]{2})$")


# ── Normalization helpers ─────────────────────────────────────────────────────

def norm_host(url: str | None) -> str:
    if not url:
        return ""
    url = url.strip()
    if not url.startswith("http"):
        url = "https://" + url
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def norm_name(name: str) -> str:
    """Collapse a dealer name to its distinctive tokens for fuzzy matching."""
    n = name.lower()
    n = re.sub(r"\([^)]*\)", "", n)          # drop parentheticals like "(IL)"
    n = re.sub(r"[^a-z0-9 ]", " ", n)
    n = re.sub(r"\b(bmw|of|the|inc|llc|motors?|autos?|group|cars?)\b", "", n)
    return re.sub(r"\s+", " ", n).strip()


def to_origin(url: str) -> str:
    if not url.startswith("http"):
        url = "https://" + url
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


# ── Source loading ────────────────────────────────────────────────────────────

def load_perplexity() -> list[dict]:
    rows = []
    with open(PERPLEXITY_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.lower().startswith("dealer name"):
                continue
            parts = line.split(",")
            if len(parts) < 4:
                continue
            rows.append({
                "name": parts[0].strip(),
                "city": parts[1].strip(),
                "state": parts[2].strip().upper(),
                "website": parts[-1].strip(),
            })
    return rows


def load_dealers_json() -> list[dict]:
    try:
        with open(DEALERS_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def load_ddc_sweep() -> list[dict]:
    try:
        with open(DDC_SWEEP_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return []


# ── Merge ─────────────────────────────────────────────────────────────────────

def build_known_index() -> tuple[dict, dict]:
    """Index everything we already know, keyed by host and by (name, state)."""
    by_host: dict[str, dict] = {}
    by_name: dict[tuple[str, str], dict] = {}

    def register(host: str, name: str, state: str, info: dict):
        if host:
            info = {**info, "website": f"https://www.{host}"}
            merged = {**by_host.get(host, {}), **{k: v for k, v in info.items() if v}}
            by_host[host] = merged
        nn = norm_name(name) if name else ""
        if nn:
            key = (nn, state.upper())
            merged = {**by_name.get(key, {}), **{k: v for k, v in info.items() if v}}
            by_name[key] = merged

    # DDC sweep: authoritative for site_id + dealer identity; bmw_code implies
    # the store sells BMWs (mini_code-only accounts are MINI stores).
    for d in load_ddc_sweep():
        if not d.get("bmw_code"):
            continue
        info = {
            "platform": "dealercom",
            "site_id": d.get("site_id"),
            "name": d.get("name"),
            "city": d.get("city"),
            "state": (d.get("state") or "").upper(),
        }
        register(norm_host(d.get("url")), d.get("name") or "", info["state"], info)

    # dealers.json: platform detections incl. DealerInspire Algolia config.
    for d in load_dealers_json():
        platform = d.get("platform")
        if platform == "dealerinspire" and d.get("is_bmw") is False:
            continue
        info = {
            "platform": platform if platform in ("dealercom", "dealerinspire") else None,
            "site_id": d.get("site_id"),
            "di_index": d.get("di_index") or d.get("index_base"),
            "di_slug": d.get("di_slug") or d.get("dealer_slug"),
            "app_id": d.get("app_id") or d.get("algolia_app"),
            "search_key": d.get("search_key"),
            "name": d.get("name"),
            "city": d.get("city"),
            "state": (d.get("state") or "").upper(),
        }
        host = norm_host(d.get("website") or d.get("url"))
        register(host, d.get("name") or "", info["state"], info)

    return by_host, by_name


def mini_only_hosts() -> set[str]:
    """Hosts of DDC-sweep accounts with a MINI dealer code but no BMW code."""
    return {norm_host(d.get("url")) for d in load_ddc_sweep() if not d.get("bmw_code")}


def _looks_mini_only(host: str, name: str, mini_hosts: set[str]) -> bool:
    """The is_bmw flags in dealers.json are unreliable (MINI stores flagged True,
    real BMW stores flagged False), so MINI filtering uses the OEM sweep's dealer
    codes plus a host/name heuristic."""
    if host in mini_hosts:
        return True
    text = f"{host} {name.lower()}"
    return "mini" in text and "bmw" not in text


def merge_sources() -> list[dict]:
    by_host, by_name = build_known_index()
    perplexity = load_perplexity()
    mini_hosts = mini_only_hosts()

    records: list[dict] = []
    matched_hosts: set[str] = set()
    matched_names: set[tuple[str, str]] = set()

    for row in perplexity:
        host = norm_host(row["website"])
        name_key = (norm_name(row["name"]), row["state"])
        known = by_host.get(host) or by_name.get(name_key) or {}
        if host in by_host:
            matched_hosts.add(host)
        if name_key in by_name:
            matched_names.add(name_key)
        # Also mark the known record's own host as matched (perplexity URL may
        # differ from the platform-detected canonical one).
        known_host = norm_host(known.get("website", "")) if known else ""
        if known_host:
            matched_hosts.add(known_host)

        rec = {
            "name": row["name"],
            "city": row["city"],
            "state": row["state"],
            "website": to_origin(row["website"]),
            "sources": ["perplexity"],
        }
        _apply_known(rec, known)
        records.append(rec)

    # Known dealers absent from the perplexity roster (superset guarantee).
    seen_name_keys = {(norm_name(r["name"]), r["state"]) for r in records}
    for host, known in by_host.items():
        if host in matched_hosts:
            continue
        name = known.get("name") or host
        state = (known.get("state") or "").upper()
        if (norm_name(name), state) in seen_name_keys:
            continue
        if _looks_mini_only(host, name, mini_hosts):
            continue
        rec = {
            "name": name,
            "city": known.get("city") or "",
            "state": state,
            "website": to_origin(host),
            "sources": ["dealers_json"],
        }
        _apply_known(rec, known)
        records.append(rec)

    return records


def _apply_known(rec: dict, known: dict) -> None:
    platform = known.get("platform")
    if platform == "dealercom":
        rec["coverage"] = "ddc"
    elif platform == "dealerinspire":
        rec["coverage"] = "di"
    else:
        rec["coverage"] = "uncovered"
    for field in ("platform", "site_id", "di_index", "di_slug", "app_id", "search_key"):
        if known.get(field):
            rec[field] = known[field]
    if known:
        # The platform-detected canonical domain beats the perplexity guess.
        if known.get("website"):
            rec["website"] = known["website"]
        rec["sources"] = rec.get("sources", []) + ["dealers_json"]


# ── URL verification ──────────────────────────────────────────────────────────

def verify_url(rec: dict) -> dict:
    """Check the record's website; on failure try slug guesses, then DuckDuckGo."""
    url = rec["website"]
    status, final = _probe(url)
    if status:
        rec["website"] = final
        rec["website_ok"] = True
        rec["http_status"] = status
        return rec

    # A dealer with platform config has a known-good domain — a transient
    # connection failure must not let the DDG fallback swap in a wrong site.
    if rec.get("site_id") or rec.get("di_index"):
        rec["website_ok"] = False
        rec["http_status"] = 0
        return rec

    slug = re.sub(r"[^a-z0-9]", "", rec["name"].lower())
    for candidate in (f"https://www.{slug}.com", f"https://www.{slug}bmw.com"):
        if norm_host(candidate) == norm_host(url):
            continue
        status, final = _probe(candidate)
        if status:
            rec["website"] = final
            rec["website_ok"] = True
            rec["http_status"] = status
            rec["url_resolved_via"] = "slug_guess"
            return rec

    resolved = _ddg_lookup(rec["name"], rec["city"], rec["state"])
    if resolved:
        status, final = _probe(resolved)
        rec["website"] = final if status else resolved
        rec["website_ok"] = bool(status)
        rec["http_status"] = status
        rec["url_resolved_via"] = "duckduckgo"
        return rec

    rec["website_ok"] = False
    rec["http_status"] = 0
    return rec


def _probe(url: str) -> tuple[int, str]:
    """Return (status, final_origin). status=0 means connection-level failure.
    Any HTTP response — including 403 anti-bot — proves the site exists."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=12, allow_redirects=True,
                            stream=True)
        final = to_origin(resp.url)
        resp.close()
        return resp.status_code, final
    except requests.RequestException:
        return 0, url


def _plausible_dealer_domain(domain: str, name: str) -> bool:
    """Reject aggregators, BMW corporate sites, and domains with no relation to
    the dealer (DuckDuckGo happily returns youtube.com or the city government)."""
    if not domain or any(a in domain for a in AGGREGATOR_DOMAINS):
        return False
    if _CORPORATE_RE.match(domain):
        return False
    if not re.search(r"\.(com|net|org|us)$", domain):
        return False
    if "bmw" in domain:
        return True
    tokens = [t for t in re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()
              if len(t) >= 4 and t not in ("bmw", "mini", "auto", "cars", "motors")]
    return any(t in domain for t in tokens)


def _ddg_lookup(name: str, city: str, state: str) -> str | None:
    try:
        from ddgs import DDGS
        results = list(DDGS().text(f"{name} {city} {state} BMW dealer official site",
                                   max_results=8))
        for r in results:
            href = r.get("href", "")
            domain = norm_host(href)
            if _plausible_dealer_domain(domain, name):
                return to_origin(href)
    except Exception:
        pass
    return None


def dedupe_by_host(records: list[dict]) -> list[dict]:
    """Collapse records that resolved to the same canonical host (e.g. a
    perplexity URL that redirects to the platform-detected domain). The record
    with platform info wins; identity fields are backfilled from the loser."""
    by_host: dict[str, dict] = {}
    out: list[dict] = []
    for rec in records:
        host = norm_host(rec.get("website"))
        if not host:
            out.append(rec)
            continue
        prev = by_host.get(host)
        if prev is None:
            by_host[host] = rec
            out.append(rec)
            continue
        winner, loser = (rec, prev) if (
            rec.get("platform") and not prev.get("platform")) else (prev, rec)
        for field in ("name", "city", "state"):
            # Prefer a human-readable name over a host-as-name placeholder.
            if not winner.get(field) or "." in str(winner.get(field, "")):
                if loser.get(field) and "." not in str(loser[field]):
                    winner[field] = loser[field]
        winner["sources"] = sorted(set(winner.get("sources", []) + loser.get("sources", [])))
        if winner is rec:
            out[out.index(prev)] = rec
            by_host[host] = rec
    return out


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Build master_dealers.json")
    parser.add_argument("--no-verify", action="store_true",
                        help="Skip URL verification (offline merge only)")
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()

    records = merge_sources()
    from collections import Counter
    cov = Counter(r["coverage"] for r in records)
    print(f"Merged {len(records)} dealers "
          f"(ddc={cov.get('ddc', 0)}, di={cov.get('di', 0)}, "
          f"uncovered={cov.get('uncovered', 0)})")

    if not args.no_verify:
        print(f"Verifying {len(records)} website URLs ({args.workers} workers)…")
        done = 0
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(verify_url, r): r for r in records}
            for future in as_completed(futures):
                rec = future.result()
                done += 1
                if not rec.get("website_ok"):
                    print(f"  [dead] {rec['name']} ({rec['state']}) — {rec['website']}")
                elif rec.get("url_resolved_via"):
                    print(f"  [fixed via {rec['url_resolved_via']}] {rec['name']} "
                          f"→ {rec['website']}")
                if done % 50 == 0:
                    print(f"  … {done}/{len(records)}")

        before = len(records)
        records = dedupe_by_host(records)
        if len(records) != before:
            print(f"Deduped {before - len(records)} records that share a canonical host")

    records.sort(key=lambda r: (r["state"], r["name"]))
    with open(OUTPUT_FILE, "w") as f:
        json.dump(records, f, indent=2)

    ok = sum(1 for r in records if r.get("website_ok"))
    print(f"\nWrote {len(records)} dealers → {OUTPUT_FILE}")
    if not args.no_verify:
        print(f"  reachable: {ok} | dead: {len(records) - ok}")


if __name__ == "__main__":
    main()
