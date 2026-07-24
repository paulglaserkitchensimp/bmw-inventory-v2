# BMW Crawler

Searches BMW inventory nationwide by querying dealer website platforms
directly, plus three marketplace aggregators for everything else.

**Dealer platforms** (via `search_inventory.py`, driven by `master_dealers.json`):

- **Dealer.com (DDC)** — one JSON query per year to the OEM `bmwgroup`
  account (~150 dealers), plus per-site queries for standalone DDC accounts
- **DealerInspire (DI)** — Cars Commerce search API, one call per dealer
  (~120 dealers)
- **DealerOn** — Cosmos SRP JSON API (~30 dealers)
- **Team Velocity** — vehicle dataLayer parsed from server-rendered SRP
  HTML (~22 dealers)

**Aggregators** (fill in Dealer eProcess, DealerSocket, and other platforms):

- **Autotrader** — `crawl_autotrader.py`
- **Cars.com** — `cars_com.py`
- **TrueCar** — `truecar.py`

After collecting hits, the pipeline optionally fetches each VDP (vehicle
detail page) to extract the signed CarFax URL, owner-count badge, and
fully-resolved link, and can run the full CarFax report through an LLM to
assess likely private vs dealer-only ownership.

## Setup

Uses [uv](https://github.com/astral-sh/uv) for dependency management.

```bash
cd crawler
uv sync                           # creates .venv and installs deps
uv run playwright install chrome  # one-time: install Chrome for Playwright
```

Create `.env` with your OpenAI key (only needed for `--analyze` / `--analyze-only`):

```
OPENAI_API_KEY=sk-...
```

## Sweep scripts (recommended entry point)

Each target vehicle has a shell script that fans out across **all seven
sources**, VIN-merges the outputs, and (with `--sync`) unions the result into
`results.json` and refreshes the visualizer:

```bash
./search_all_models.sh --sync   # X5/X6/X7 M60i + XM + 760i xDrive, 2025-26
./search_x56m.sh --year 2026    # X5 M60i / X5 M / X6 M60i / X6 M, one year
./search_x7_40i.sh --sync       # X7 xDrive40i
./search_760.sh                 # 7 Series 760i xDrive
./search_xm.sh --year 2026      # all XM trims (min-miles defaults to 0)
```

Common flags: `--sync`, `--year Y[-Y]`, `--min-miles` / `--max-miles`,
`--exclude-states`, `--skip-fetch --skip-vdp` (fast pass, no VDP/CarFax
enrichment), `--headless`, and per-source `--skip-autotrader` /
`--skip-cars` / `--skip-truecar` / `--skip-platforms`.

The script headers document the exact model/trim designation each source
expects (they differ: `X5:M60i` vs `bmw/x5/m60i` vs `trims[]=bmw-x5-m60i` vs
`mmt[]=bmw_x5_m60i`).

## Dealer-platform search (`search_inventory.py`)

Default search (`2025–2026 BMW X7 M60i`, `60–15,000 mi`, all conditions):

```bash
uv run search_inventory.py
```

Custom searches:

```bash
uv run search_inventory.py --trim xDrive40i --max-miles 20000 --out x7_40i.json
uv run search_inventory.py --model X5 --year 2024 --type used
uv run search_inventory.py --skip-fetch              # fast, no CarFax / VDP pass
```

### Sweeping multiple model/trim combos in one run

Pass `--search MODEL[:TRIM]` once per pair to sweep them all together. Each
spec hits every supported platform independently and results are VIN-deduped
at the end. Omit `:TRIM` (or leave it blank) to pull any trim of that model.

```bash
uv run search_inventory.py \
  --search X7:M60i \
  --search X7:xDrive40i \
  --search X5:M60i \
  --search XM
```

When `--search` is given, `--model` / `--trim` are ignored; `--year`,
`--min-miles`, `--max-miles`, and `--type` still apply globally to every spec.

### With LLM ownership analysis

Full pipeline (search → VDP fetch → CarFax report fetch → LLM analysis):

```bash
uv run search_inventory.py --fetch-carfax --analyze
```

Re-analyze an existing results file without re-searching:

```bash
uv run search_inventory.py --analyze-only some_results.json --fetch-carfax
```

### Arguments

| Flag | Default | Notes |
| --- | --- | --- |
| `--make` | `BMW` | |
| `--model` | `X7` | ignored if any `--search` is given |
| `--year` | `2025-2026` | single year (`2025`) or range (`2024-2026`) |
| `--trim` | `M60i` | e.g. `xDrive40i`, `M60i`, `M Competition`; ignored if `--search` is given |
| `--search MODEL[:TRIM]` | — | repeatable; sweep multiple model/trim pairs in one run |
| `--min-miles` | `60` | |
| `--max-miles` | `15000` | |
| `--type` | `all` | `new`, `used`, or `all` |
| `--out` | `search_output.json` | **cannot be `results.json`** — see below |
| `--skip-fetch` | off | skip VDP fetch (no CarFax URLs) |
| `--fetch-carfax` | off | fetch full CarFax reports via real Chrome CDP |
| `--analyze` | off | run LLM ownership assessment (needs `OPENAI_API_KEY`) |
| `--analyze-only FILE` | — | skip search, just re-analyze `FILE` |
| `--llm-model` | `gpt-4o-mini` | any OpenAI chat model |

### `results.json` is protected

`results.json` is the persistent, ever-growing dataset. Every crawler
**refuses** `--out results.json` and writes standalone files instead. The only
writer is `merge_results.py`, which unions by VIN and never drops existing
vehicles.

## Output schema

Each vehicle record:

```
vin, stockNumber, year, make, model, trim, type, odometer,
internetPrice, extColor, interiorColor, certified,
daysOnLot, dateInStock,
nhtsaUrl, carfaxPaywallUrl, carfaxUrl,     # carfaxUrl set after VDP fetch
carfaxBadge, ownerCount,                    # from VDP CarFax badge
carfaxHistory,                              # full report text (--fetch-carfax)
ownershipAssessment, ownershipConfidence, ownershipReasoning,  # --analyze
dealerName, dealerCity, dealerState, dealerUrl, platform,
link, vinLink, resolvedLink
```

## How VDP fetching works

`fetch_details()` tries three passes in order, each one more heavyweight:

1. **`requests`** — plain HTTP, parallel (8 workers). Works for open sites.
2. **Real Chrome via CDP** — connects to your running Chrome on
   `--remote-debugging-port=9222` (launched automatically using
   `~/.chrome_debug_session` as a dedicated profile). Carries real
   Cloudflare / DataDome trust cookies and fingerprint.
3. **Headless persistent-profile** — Playwright stealth fallback using
   `~/.bmw_browser_profile` for dealers Chrome can't reach.

Any page still blocked after all passes keeps its `vinLink` +
`carfaxPaywallUrl` so you can open it manually.

## Autotrader crawler

`crawl_autotrader.py` drives Chrome (via Playwright + stealth, persistent
profile `~/.bmw_browser_profile`) through an Autotrader search URL and
pulls the structured listing data embedded in
`<script id="__NEXT_DATA__">` on every SRP page (no per-listing detail
fetch needed).

```bash
# Defaults: 2025 BMW X7 M60i ≤ 15k mi nationwide, CA dropped.
uv run crawl_autotrader.py

# Arbitrary search URL — note: encode year ranges as startYear/endYear
# query params, NOT as a path slug like /2025-2026/. Autotrader treats
# range slugs as a wildcard and serves spotlight ads from other makes.
uv run crawl_autotrader.py \
  --url 'https://www.autotrader.com/cars-for-sale/all-cars/bmw/x7/m60i/detroit-mi?mileage=15000&searchRadius=0&startYear=2025&endYear=2026' \
  --out autotrader_x7_m60i.json

# Exclude more states than just CA
uv run crawl_autotrader.py --exclude-states CA,HI

# Bump page depth for popular trims (default 12 pages × 25 ≈ 300 hits)
uv run crawl_autotrader.py --max-pages 30 --url '…'
```

The first run should be headed (`--headless` off, the default) so any
DataDome challenge can be solved manually. Subsequent runs can pass
`--headless` because the persistent profile retains trust cookies.

The crawler client-side filters by make + model + trim, all
auto-derived from the URL path, to drop spotlight/sponsored ads that
Autotrader mixes into the SRP `inventory` blob. Pass `--trim ''` to
keep everything regardless of trim.

## Cars.com crawler

`cars_com.py` scrapes a `cars.com/shopping/results/` URL (whatever filters you
build in the UI) and produces records in the same schema as
`search_inventory.py`. It paginates forward from page 1 until no new VINs come
back, then fetches every VDP (6 workers by default) to pull dealer
name/city/state, a routable phone number, stock number, colors, and canonical
trim.

```bash
# Build the URL on cars.com, then hand it to the crawler
uv run cars_com.py \
  --url 'https://www.cars.com/shopping/results/?mileage_min=60&mileage_max=15000&stock_type=cpo&trims[]=bmw-x7-m60i&models[]=bmw-x7&zip=48226&maximum_distance=9999&year_min=2025&year_max=2026&makes[]=bmw&sort=best_match_desc' \
  --out cars_com_x7_m60i.json

# Exclude extra states (CA is dropped by default)
uv run cars_com.py --url '…' --exclude-states CA,HI
```

California is dropped by default (`--exclude-states CA`); pass an empty string
to keep everything. Records blocked by cars.com (timeout / 403) are still
written with `vdpStatus: "blocked"`, 404s as `"not_found"`.

### Anti-bot (DataDome) handling

cars.com fronts its pages with DataDome, which returns **HTTP 403** to the
plain `requests` client. The crawler detects this and automatically falls
back to a stealthed, persistent-profile Chromium (dedicated profile at
`~/.cars_com_browser_profile`, separate from the other crawlers so concurrent
runs don't fight over Chrome's single-instance lock). Stale `Singleton*` locks
from a crashed run are cleared automatically on launch.

Search-results pages render fine through the headless browser, but **VDP pages
require trust cookies** — a fresh profile gets 403'd on every VDP. To bank those
cookies, solve the challenge once in a visible browser:

```bash
# 1. One-time: open cars.com headed, solve the "verify you're human" challenge,
#    then press Enter. Cookies persist in the dedicated profile.
uv run cars_com.py --warmup --url 'https://www.cars.com/shopping/results/?makes[]=bmw'

# 2. Subsequent headless runs reuse those cookies and can fetch VDPs.
uv run cars_com.py --url '…' --out cars_com_x7.json
```

For a fast, **listings-only** crawl that never touches VDPs (so it never blocks
on the per-listing anti-bot), use `--skip-vdp`. You lose dealer
name/city/state/phone/colors and state filtering (state comes from the VDP), but
keep VIN, year, model, trim, mileage, price, and CPO flag:

```bash
uv run cars_com.py --url '…' --skip-vdp --out cars_com_x7.json
```

## TrueCar crawler

`truecar.py` crawls a TrueCar used-listings URL by parsing the Apollo GraphQL
state embedded in each server-rendered page. Query-param URLs are normalized
into SEO path segments (e.g. `…/location-nationwide/year-2025-max-2025/mileage-15000/`)
because those pass TrueCar's PerimeterX protection on plain requests;
pagination beyond page 1 falls back to the stealth browser.

```bash
uv run truecar.py \
  --url 'https://www.truecar.com/used-cars-for-sale/listings/inventory/?mmt[]=bmw_xm&yearLow=2025&mileageHigh=15000&searchRadius=5000' \
  --out truecar_xm.json

# One-time if PerimeterX starts blocking the browser fallback:
uv run truecar.py --warmup --url 'https://www.truecar.com/used-cars-for-sale/listings/'
```

Model, trim, year, and mileage are also enforced client-side (`--trim`)
because TrueCar's URL trim filter is unreliable. Defaults: 60–15,000 mi,
CA excluded, `--max-pages 34`.

## Merging sources

`merge_results.py` unions any number of result files into one, deduping by
VIN. When the same VIN appears in multiple sources it keeps the record with
more populated authoritative fields (`daysOnLot`, `dateInStock`, `carfaxUrl`,
`dealerUrl`, `dealerPhone`, `extColor`, `interiorColor`, `stockNumber`,
`internetPrice`). Ties go to the earlier `--inputs` entry.

```bash
# Platform results are richer than aggregators, so list results.json first.
uv run merge_results.py \
  --inputs results.json new_sweep.json \
  --out results.json

# Drop unwanted states during the merge too
uv run merge_results.py \
  --inputs results.json autotrader_x.json cars_com_x.json \
  --out results.json \
  --exclude-states CA
```

Besides picking a winning base record, the merger also backfills any
still-empty authoritative fields from the other sources (so you keep a
CarFax URL from source A plus a price from source B), and swaps
aggregator dealer URLs (`cars.com`, `edmunds.com`, …) for a real dealer
domain when one of the other sources has it.

## Dealer directory (one-time / rerun to refresh)

`master_dealers.json` is the 377-dealer roster with each store's platform and
harvested API identifiers. To rebuild it from scratch:

```bash
uv run build_master_dealers.py     # merge dealer lists + verify URLs → master_dealers.json
uv run platform_census.py          # detect each dealer's website platform
uv run platform_census.py --report-only   # regenerate coverage_report.md
uv run harvest_di_ccid.py          # harvest Cars Commerce ccid/apiKey (DealerInspire)
uv run harvest_dealeron.py         # harvest DealerOn dealerId/pageId
```

Legacy discovery scripts (still functional, superseded by the census):
`sweep_ddc.py`, `sweep_dealers.py`, `discover_dealers.py`.

## Files

- `search_inventory.py` — dealer-platform search engine (DDC / DI / DealerOn / Team Velocity) + CarFax + LLM pipeline
- `crawl_autotrader.py` — Autotrader SRP crawler (Playwright + stealth)
- `cars_com.py` — Cars.com search scraper
- `truecar.py` — TrueCar crawler (Apollo-state parser)
- `merge_results.py` — VIN-dedupes N result files into one
- `search_*.sh` — per-vehicle sweep scripts (all sources + merge)
- `master_dealers.json` — dealer roster w/ platform + harvested API ids
- `build_master_dealers.py` — roster builder (merge + URL verification)
- `platform_census.py` — platform detection + `coverage_report.md` generator
- `harvest_di_ccid.py` / `harvest_dealeron.py` — per-platform credential harvesters
- `coverage_report.md` — nationwide platform census results
- `dealers.json` / `bmw_ddc_dealers.json` / `bmw_di_slugs.json` — legacy dealer directories
- `perplexity_dealers.txt` — dealer name/city/state/URL seed list
- `carfax_utils.py` — CarFax URL/report helpers
- `algolia_replay.py` — raw HAR replay for debugging (legacy)
