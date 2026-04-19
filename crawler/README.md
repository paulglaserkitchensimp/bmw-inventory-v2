# BMW Crawler

Searches BMW inventory across every US dealer in two platform sweeps:

- **Dealer.com (DDC)** — one call per year to the `bmwgroup` OEM site (~275 dealers)
- **DealerInspire (DI)** — one batched Algolia query per app (~44 dealers)

After collecting hits, it optionally fetches each VDP (vehicle detail page) to
extract the signed CarFax URL, owner-count badge, and fully-resolved link, and
can run the full CarFax report through an LLM to assess likely private vs
dealer-only ownership.

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

## Quick start

Default search (`2025–2026 BMW X7 M60i`, `200–15,000 mi`, all conditions):

```bash
uv run search_inventory.py
```

Custom search:

```bash
uv run search_inventory.py --trim xDrive40i --max-miles 20000 --out x7_40i.json
uv run search_inventory.py --model X5 --year 2024 --type used
uv run search_inventory.py --skip-fetch              # fast, no CarFax / VDP pass
```

### With LLM ownership analysis

Full pipeline (search → VDP fetch → CarFax report fetch → LLM analysis):

```bash
uv run search_inventory.py --fetch-carfax --analyze
```

Re-analyze an existing results file without re-searching:

```bash
uv run search_inventory.py --analyze-only results.json --fetch-carfax
```

## Arguments

| Flag | Default | Notes |
| --- | --- | --- |
| `--make` | `BMW` | |
| `--model` | `X7` | |
| `--year` | `2025-2026` | single year (`2025`) or range (`2024-2026`) |
| `--trim` | `M60i` | e.g. `xDrive40i`, `M60i`, `M Competition` |
| `--min-miles` | `200` | |
| `--max-miles` | `15000` | |
| `--type` | `all` | `new`, `used`, or `all` |
| `--out` | `results.json` | |
| `--skip-fetch` | off | skip VDP fetch (no CarFax URLs) |
| `--fetch-carfax` | off | fetch full CarFax reports via real Chrome CDP |
| `--analyze` | off | run LLM ownership assessment (needs `OPENAI_API_KEY`) |
| `--analyze-only FILE` | — | skip search, just re-analyze `FILE` |
| `--llm-model` | `gpt-4o-mini` | any OpenAI chat model |

## Output schema

Each vehicle in `results.json`:

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

## Dealer discovery (one-time / rerun to refresh)

The crawler reads dealer metadata from `dealers.json`. To regenerate it:

```bash
uv run sweep_ddc.py         # discovers Dealer.com BMW/MINI dealers → bmw_ddc_dealers.json
uv run sweep_dealers.py     # discovers DealerInspire BMW dealers via Algolia
uv run discover_dealers.py  # detects platform for arbitrary URLs
uv run discover_dealers.py --from-json   # retry any still-unknown entries
uv run discover_dealers.py --url https://www.foo.com
```

## Files

- `search_inventory.py` — main search + CarFax + LLM pipeline
- `sweep_ddc.py` — Dealer.com dealer discovery
- `sweep_dealers.py` — DealerInspire dealer discovery
- `discover_dealers.py` — platform detection for individual URLs
- `algolia_replay.py` — raw HAR replay for debugging Algolia queries
- `dealers.json` — merged dealer directory (both platforms)
- `bmw_ddc_dealers.json` — raw DDC sweep output
- `bmw_di_slugs.json` — raw DI slug sweep output
- `perplexity_dealers.txt` — fallback dealer→URL lookup (for blank `dealerUrl`s)
