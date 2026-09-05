# 2026 330i Deal Finder — customizing this fork

This repo was built to hunt a low-mile 2025–26 **X7 M60i / XM / 760i**, nationwide,
for a lease. You are hunting a **2025–26 330i RWD with at least the M Sport
package, within ~750 miles of 47119, black strongly preferred, red and white
excluded**, to be leased for **36 or 39 months at 12,000 mi/year** — with a
**2026 loaner** as the ideal find.

Same machinery, different target. This document is the map: what the repo does,
what I changed, what I disabled, what's broken, and what to build next.

> **Note on the radius:** your message said "759 mikes" — I read that as **750 miles**
> and used it everywhere (`ZIP=47119`, `RADIUS=750`, and a `750` default in the UI).
> One number in one place changes it: see [Re-homing the search](#re-homing-the-search).

---

## 1. The target, written down

| Attribute | Value | Where it's enforced |
|---|---|---|
| Year | 2025–2026 (2025 leftover preferred if one shows up — none seen as of this writing) | `search_330i.sh --year` → every source's URL |
| Model / trim | 3 Series, 330i | `--search "3 Series:330i"`, aggregator URL slugs |
| Drivetrain | **RWD only** | `--exclude-trim xDrive` (new flag — see §5) |
| Package | M Sport or better — **on by default, one click to relax** | `mSport` filter, default `true` in `DEFAULT_FILTERS` (see §6) |
| Condition | **Must be titled "new"** (a service loaner not yet retailed still qualifies — see §5 "Condition") | `MIN_MILES=0`, `MAX_MILES=5000`, `--type new`; UI "Condition" filter defaults to New |
| Market | ≤750 mi of 47119 | `--dealer-states` + aggregator `zip`/`radius` + UI radius filter |
| Color | black preferred; **red and white are non-starters** | UI color filter, excluded by default |
| Deal structure | **Lease, 36 or 39 months, 12,000 mi/yr** | Leasehackr prefill buttons in the vehicle detail panel — see "Lease structure" in §5 |
| Horizon | 3–6 months of weekly sweeps | see §11 |

**The one-line version:**

```bash
cd crawler && ./search_330i.sh --sync
cd ../visualizer && npm run dev      # http://localhost:5173
```

---

## 2. Quick start on the MacBook (~15 minutes)

```bash
# Prereqs: Homebrew, Node 20+, uv, Google Chrome installed normally
brew install uv node

git clone <your fork> bmw-inventory-v2 && cd bmw-inventory-v2

# Crawler
cd crawler
uv sync
uv run playwright install chrome        # one-time

# Visualizer
cd ../visualizer && npm install
```

Then a **first, cheap sweep** to prove the plumbing before you commit to a full run:

```bash
cd ../crawler
./search_330i.sh --skip-fetch --skip-vdp --skip-autotrader --skip-truecar
```

That hits only the dealer platforms + Cars.com listings, skips the slow VDP
enrichment, and takes a few minutes. Look at the output: if `3 Series 330i`
returns a plausible count (tens to low hundreds nationwide across 2025–26), the slugs
are right. If it returns 0, go to §7 (**Verify the source slugs**) before anything
else.

Then the real thing:

```bash
./search_330i.sh --sync                 # full: all 7 sources + VDP + merge + sync
cd ../visualizer && npm run dev
```

The **first** Autotrader and Cars.com runs must be **headed** (no `--headless`)
so you can solve the "verify you're human" challenge by hand once. After that
the persistent Chrome profiles keep the trust cookies and `--headless` works.
If a source starts 403ing months from now, that's the fix: run its `--warmup`
once and click through.

---

## 3. How the system actually works

```
                 ┌── Dealer.com (OEM feed, 1 call, ~150 dealers)
                 ├── DealerInspire (Cars Commerce API, 1 call/dealer)
 search_         ├── DealerOn (Cosmos SRP API, 1 call/dealer)
 inventory.py    └── Team Velocity (parse SSR dataLayer, 1 call/dealer)
                        │
 crawl_autotrader.py ───┤   (Playwright + stealth, __NEXT_DATA__ blob)
 cars_com.py ───────────┤   (requests → DataDome fallback to browser)
 truecar.py ────────────┤   (Apollo GraphQL state, USED listings only)
                        ▼
              merge_results.py  ── VIN-dedupe, union, never drops
                        ▼
                  results.json   ← the persistent dataset
                        ▼
             visualizer/public/data/results.json  (npm run sync)
                        ▼
              React + Vite + Leaflet, localhost:5173
```

Three things about this design are worth internalizing, because they drive
every decision below:

1. **`results.json` only ever grows.** Searches are forbidden from writing it
   (`--out results.json` is rejected); only `merge_results.py` writes, and it
   unions. That is great for a 3–6 month hunt — you accumulate a market history
   instead of a snapshot — and it is also the source of the biggest operational
   gap (§9, stale listings).
2. **The crawlers need a real browser and a real human, occasionally.**
   Autotrader, Cars.com, and TrueCar are all behind DataDome/PerimeterX. That
   is the single fact that decides your hosting question (§10): the crawler
   runs on your Mac, period.
3. **Annotations and geocoding are Vite *dev-server* middleware.** They live in
   `visualizer/vite.config.ts` as `configureServer` hooks. They do **not** exist
   in `npm run build` output, and not in `npm run preview` either. A static
   deploy of this app silently loses your notes and your map. Also §10.

---

## 4. Re-homing the search

Everything location-specific now lives in exactly two places:

| File | Constants |
|---|---|
| `visualizer/src/utils/distance.ts` | `ORIGIN_COORDS`, `ORIGIN_LABEL`, `DEFAULT_RADIUS_MILES` |
| `crawler/search_330i.sh` | `ZIP`, `RADIUS`, `AT_CITY`, `DEALER_STATES` |

Both are currently set to **47119 / 750 mi**. `ORIGIN_COORDS` is `[38.3345, -85.8983]`
(Floyds Knobs, IN). If you move the search, change both — the UI radius filter
and the crawler's search radius are independent, and a mismatch means the table
filters out cars the sweep spent 40 minutes finding, or shows you a "750 mile"
list built from a 300-mile crawl.

---

## 5. What I changed

### New: `crawler/search_330i.sh`

The 330i equivalent of the existing `search_*.sh` sweeps. Differences from the
originals that matter:

| Setting | Old scripts | `search_330i.sh` | Why |
|---|---|---|---|
| `MIN_MILES` | 60 | **0** | A brand-new 2026 shows 2–30 miles. The 60-mile floor was there to skip *new* cars; you want them. |
| `MAX_MILES` | 15,000 | **5,000** | Tightened per your spec — anything past 5k on a "new" service loaner starts looking less like a loaner and more like a car someone should have retitled already. |
| `CONDITION` (`--type`) | — (not a flag on the old scripts) | **`new`**, with `--any-condition` to widen | See "Condition" below — this is a real requirement, not a preference, because a used/CPO title can't be leased as new. |
| `EXCLUDE_STATES` | `CA` | *(none)* | CA is 1,900 miles away — the radius filter already handles it, and blanket-excluding a state is the wrong tool here. |
| Radius | `searchRadius=0` (nationwide) | `750` around ZIP `47119` | Your market. |
| Trim exclusion | — | `--exclude-trim xDrive` | See below. |
| Dealer states | all 377 | 31-state whitelist | See below. |

### New flag: `--type` / `--only-type` (condition = new)

You clarified the mileage window (0–5,000) is really a proxy for a firmer
requirement: **the car has to still be titled "new"** so it can be leased as
new. A service loaner that hasn't been retailed yet qualifies regardless of
its odometer — dealers title it "new" in their own inventory system either
way — but a car that was retailed once and bought back is "used" no matter
how few miles it has, and BMW Financial can't write a new-vehicle lease
against it. So the mileage cap narrows the *search*, but `type == "new"` is
what actually enforces the requirement.

That turned up a real gap while wiring it through: `search_inventory.py
--type new` already worked correctly for three of the four dealer platforms
(Dealer.com queries new/used as separate pages; DealerOn and Team Velocity
filter on condition explicitly) — but **DealerInspire's Cars Commerce API has
no condition parameter at all** and always returns new and used together
unfiltered. A `--type new` run was silently letting DI's used inventory
through. Fixed with a global post-filter in `main()` (defensive — it now
catches this regardless of which source misbehaves) plus the analogous
`merge_results.py --only-type {new,used,cpo}` for the three aggregators
(Autotrader/Cars.com/TrueCar), which have no condition filter of their own
either. `search_330i.sh` passes both automatically; `--any-condition` clears
them if you want to see the used/CPO market too (§12's build-next list has a
few reasons you might, e.g. price calibration).

**TrueCar is skipped by default now.** Its entire URL space is
`used-cars-for-sale` — it is structurally incapable of returning a `type:
"new"` record, so running it while hunting new-only wastes a couple of
minutes on a request that will always merge back down to zero. `--with-truecar`
re-enables it (useful with `--any-condition`).

### New flag: `--exclude-trim` (in `search_inventory.py` **and** `merge_results.py`)

This is a real correctness fix, not a convenience. The repo's trim matcher is a
**bidirectional substring match** (`_trim_matches_loose`, `search_inventory.py:1133`):

```python
return a in b or b in a      # "330i" ⊂ "330i xDrive"  →  match
```

So a search for `330i` silently returns `330i xDrive` from every source. For the
X7/XM hunt that never mattered (`M60i` doesn't collide with anything). For you it
matters a lot — AWD is a different car, a different price, and a different lease.

`--exclude-trim xDrive` drops anything whose trim *or* model contains the term.
It exists in two places on purpose:

- `search_inventory.py --exclude-trim` filters the four dealer platforms.
- `merge_results.py --exclude-trim` filters **everything**, including the three
  aggregators, which can't be told to exclude a drivetrain server-side.

`search_330i.sh` passes it to both. `./search_330i.sh --include-xdrive` turns it
off if you decide AWD is acceptable after all.

### New flag: `--dealer-states`

Restricts the *per-dealer* platforms (DealerInspire, DealerOn, Team Velocity,
standalone Dealer.com sites) to a state whitelist. Measured effect on your
31-state list:

| Platform | Nationwide | In-range | Saved |
|---|---|---|---|
| DealerInspire | 119 calls | 90 | 24% |
| DealerOn | 30 | 22 | 27% |
| Team Velocity | 22 | 16 | 27% |

Modest, because your 750-mile ring genuinely covers a lot of the country's BMW
dealers — 277 of 377 roster entries are in or straddling it. The OEM Dealer.com
call is one nationwide request either way; its results get filtered afterwards.
Dealers whose roster entry has no state (16 of them) are always kept rather than
silently dropped.

`./search_330i.sh --nationwide` disables both the whitelist and the radius, if
you ever want to see the whole market for price calibration.

### New: option-package detection

See §6 — it's the hard part.

### Visualizer

| Change | File |
|---|---|
| Origin moved to 47119; haversine + distance extracted to a shared util | `src/utils/distance.ts` (new), `VehicleTable.tsx` |
| **Max distance filter** (750 default, 300/500/750 shortcuts) | `FilterPanel.tsx`, `useVehicles.ts`, `types.ts` |
| **Exterior color filter** — free-text colors bucketed to black/white/red/blue/gray/silver/green/other; **white and red excluded by default** | `src/utils/color.ts` (new), `FilterPanel.tsx` |
| **M Sport filter** + an `M` / `–` / `?` pill in the trim column — **defaults to "Yes + unknown"**, one click to relax to "All" | `FilterPanel.tsx`, `VehicleTable.tsx`, `types.ts` |
| **Condition filter** ("lease eligibility") — All / New / Not New on `v.type`, **defaults to New** | `FilterPanel.tsx`, `useVehicles.ts`, `types.ts` |
| Miles range **defaults to 0–5,000** | `types.ts` |
| Platform filter options now derived from the data instead of a hardcoded pair | `FilterPanel.tsx` |
| Four bug fixes | §9 |

`DEFAULT_FILTERS` in `src/types.ts` is now pre-set to your full spec: 750
miles, red and white excluded, 0–5,000 miles, M Sport required, condition
locked to New. **Every one of those is a single click to relax** — "Reset
all" in the top of the filter panel restores exactly this state, and each
control (Condition, M Sport Pkg, Miles, Exterior Color, Max distance) can be
loosened independently without touching the others or re-running a sweep.
That's the toggle you asked for on M Sport specifically, and it applies the
same way to every other default here.

**The unknown-distance rule matters:** a car whose dealer city hasn't been
geocoded yet has distance `null`, and `null` means *unknown*, not *far*. Those
rows are kept. Otherwise the table would empty out on first load while Nominatim
grinds through a few hundred cities at 1 request/second. The Condition filter
has no such ambiguity — every source always populates `type` — so New/Not New
is a clean split with nothing held back as "unknown".

### Lease structure: 36/39 months, 12,000 mi/yr

You confirmed the deal structure: **lease, 36 or 39 months, 12k mi/year.**
Every vehicle's detail panel (click a row) now has two buttons under
"Leasehackr":

```
[ 36mo / 12k ↗ ]   [ 39mo / 12k ↗ ]
```

Each opens `calculator.leasehackr.com` in a new tab, prefilled with that
listing's `internetPrice` as the selling price plus the term and 12,000 annual
miles. You still fill in MSRP, money factor, and residual from the dealer
worksheet or a rate sheet, run the calculation there, and paste the resulting
URL back into the existing "Paste calculator.leasehackr.com URL…" field to have
the deal (MSRP, % off, MF, term/miles) persist in the table — that round-trip
already existed (`utils/leasehackr.ts :: parseLeasehackrUrl`); the prefill
buttons just remove the retyping.

**Why this doesn't just compute a monthly payment for you:** BMW Financial's
money factor and residual percentage change monthly, vary by term/mileage/
region, and are not published anywhere a crawler can reach. Guessing them would
produce a number that looks authoritative on a car-buying decision and isn't.
`buildLeasehackrPrefillUrl()` in `utils/leasehackr.ts` sets only what the data
actually supports (price, term, mileage) and leaves the rate-dependent inputs
to you and the real calculator. If BMW Financial's current MF/residual for the
330i becomes available in a form worth hardcoding (a monthly-updated constants
file, say), revisit this — see §12 #4.

---

## 6. The M Sport problem (read this one)

**No source in this repo exposes an option list.** Not Dealer.com, not the Cars
Commerce API, not DealerOn, not Team Velocity, not the aggregators' listing
blobs. They give you make/model/trim/color/mileage/price and nothing about how
the car is optioned. And "M Sport" is a *package*, not a trim — a 330i with M
Sport and a 330i without are both `trim: "330i"` in every feed.

That is a genuine hole in the target definition, and there is no clean fix. What
I did:

**`crawler/search_inventory.py :: extract_packages()`** scans the VDP body text
that `fetch_details()` already downloads, and records what it finds:

```json
"packageSignals": ["m_sport", "shadowline", "premium"],
"mSport": true
```

It matches marketing names (`M Sport Package`, `M Sport Pro`) and the BMW option
code (`337` / `Package 337` — M Sport on the G20 3 Series). Scripts and style
blocks are stripped before matching so a JSON analytics payload can't produce a
false positive.

`mSport` is deliberately **tri-state**:

| Value | Meaning | UI |
|---|---|---|
| `true` | marker found on the VDP | green **M** pill |
| `false` | VDP fetched successfully, no marker | gray **–** |
| absent | VDP never fetched, or blocked | gray **?** |

The "M Sport Pkg: **Yes + unknown**" filter keeps `true` *and* unknown, and drops
only confirmed `false`. That is the right default: an unknown is a lead to check
by hand, not a rejection.

### Honest limitations

- **It only works when the VDP was fetched.** `--skip-fetch` / `--skip-vdp` runs
  produce all-unknown. Blocked dealers stay unknown.
- **Dealers lie by omission.** Plenty of VDPs list "M Sport" in the *title* of a
  car that has the M Sport *steering wheel* and nothing else. Treat `mSport: true`
  as "worth 60 seconds of your time", not as verified.
- **The authoritative answer is the window sticker.** For any car you're
  seriously considering, get the VIN and pull the build sheet. That is a manual
  step and it should stay manual.
- **Matching is English-text matching.** If a dealer's site renders its option
  list from JavaScript after page load, plain `requests` won't see it; the
  browser passes will.

If M Sport turns out to be the binding constraint (likely — it's the most common
package on a 330i, but "at least M Sport" also implies checking for M Sport Pro,
Dynamic Handling, and the brakes), the highest-value next build is a **window
sticker / build-sheet lookup by VIN**, cached to disk. See §12.

---

## 7. Verify the source slugs

The X7/XM scripts carry a comment saying "verified 2026-07". The 330i script does
**not** — I wrote its slugs from BMW's naming conventions and could not confirm
them against live responses from this environment (dealer and aggregator domains
are blocked here). **Do this once on your Mac before you trust a zero-result run.**

| Source | What to check | How |
|---|---|---|
| `search_inventory.py` | Is the DDC model `3 Series` or `3-Series`? Is the trim `330i`? | `uv run search_inventory.py --search "3 Series:330i" --year 2026 --min-miles 0 --skip-fetch --out /tmp/t.json` — a nonzero count means the slug is right |
| Autotrader | path `bmw/3-series/330i` | Open `https://www.autotrader.com/cars-for-sale/all-cars/bmw/3-series/330i/louisville-ky?zip=47119&searchRadius=750&startYear=2026&endYear=2026` in a browser. If it redirects or shows the wrong car, copy the URL the site's own filter UI produces and paste it into the script. |
| Cars.com | `models[]=bmw-3_series` | Build the search in cars.com's UI, copy the resulting URL, compare the `models[]` value |
| TrueCar | `mmt[]=bmw_3-series` | Same — build it in TrueCar's UI and copy |

The script deliberately does **not** pass a trim slug to Cars.com or TrueCar. It
pulls the whole 3 Series line (330i / 330e / M340i) and filters the trim locally
(TrueCar via `--trim 330i`, Cars.com at merge time). That's more listings to
page through, but it can't silently return zero because of a wrong trim slug —
and it means an M340i deal shows up in front of you rather than being invisible.

**If a source returns 0, that is almost always a slug problem, not an
inventory problem.** Check before concluding the market is empty.

---

## 8. Your market: 750 miles of 47119

State centroids vs. the origin, with BMW dealers from `master_dealers.json`:

**Fully inside (centroid <600 mi) — 146 dealers**
KY (4), IN (7), TN (6), IL (15), OH (11), WV (1), MO (7), AL (5), VA (10),
NC (11), SC (6), GA (12), MI (9), AR (2), MS (2), PA (19), IA (4), WI (5),
MD (8), DE (2), DC

**Straddling the ring (600–950 mi centroid) — 132 dealers, partially in range**
LA (4), NJ (19), NY (21), OK (2), KS (3), MN (4), FL (27), CT (8), NE (2),
MA (9), RI (2), VT (1), NH (4), SD (1), TX (25)

**Out of range** — ND, ME, CO, NM, WY, MT, UT, AZ, ID, NV, CA, OR, WA, AK, HI
(82 dealers). Not queried by the per-dealer platforms.

`DEALER_STATES` in `search_330i.sh` includes both the core and the straddling
group, and the UI's 750-mile filter does the precise cut per dealer city. That's
the right split: be generous at crawl time (a Nashville-based dealer group's
website may list a car sitting in Louisville), strict at display time.

For a lease, though, distance is softer than it looks. A dealer 700 miles away
can ship, and BMW Financial's money factor and residual don't care where the car
sits. What distance actually costs you is: a test drive, a same-day walk-away,
and leverage in the negotiation. Worth keeping the 750 ring for discovery and a
300-mile ring for "cars I'd actually go see."

---

## 9. Bugs

### Fixed

1. **Annotation data loss.** `vite.config.ts`'s server-side `isEmptyEntry()`
   checked `tag`, `comment`, and `leasehackrUrl` but **not `listingUrl`** — while
   the client's copy in `useAnnotations.ts` checked all four. Saving *only* a
   corrected listing URL (no tag, no note) looked fine in the UI, but the server
   classified the entry as empty and **deleted the VIN**. The override vanished on
   the next 10-second poll or page reload. Both copies now check all four fields;
   there's a comment on each pointing at the other.
2. **Platform filter reached only two of seven platforms.** The chips were
   hardcoded to `Dealer.com` and `DealerInspire`, so DealerOn, Team Velocity,
   Autotrader, Cars.com, and TrueCar records were unreachable through that
   control. Options are now derived from the loaded data.
3. **Four ESLint errors** in `useDebouncedAutoSave.ts` and `carfaxBadge.ts`
   (refs written during render, `let` that should be `const`). `npm run lint` now
   passes clean. Fixing the ref errors unmasked a fifth — React's compiler-based
   rules bail out after the first error in a hook — so the hook was rewritten to
   derive `pending` during render and only keep the "saved" flash in state.
4. **`--min-miles 60` hid every new car.** Correct for the used-X7 hunt, wrong
   for yours. `search_330i.sh` defaults to `0`.
5. **RWD searches silently included xDrive.** §5.
6. **`--type new` didn't actually exclude DealerInspire's used inventory.**
   Dealer.com, DealerOn, and Team Velocity all respected the flag; DealerInspire's
   Cars Commerce API has no condition parameter and always returned new + used
   together regardless. Found while wiring up the new condition requirement
   (§5, "Condition"). Fixed with a global post-filter in `search_inventory.py
   main()` — defensive by design, so it also covers any future source that
   does the same thing — plus the parallel `merge_results.py --only-type` for
   the three aggregators, which never had a condition filter at all.

### Known and not fixed

6. **`results.json` never forgets a sold car.** This is the one that will bite you
   over 3–6 months. `merge_results.py` unions and never drops; `vdpStatus:
   "not_found"` (the 404 → "probably sold" signal) is only set when a VDP is
   fetched, and VDP fetching only runs against the *current sweep's* results. A
   car found in week 2 and sold in week 3 sits in your table looking live until
   the end of time. **This is the #1 thing to build** — see §12.
7. **Platform filter breaks on merged records.** When a VIN is found by two
   sources, `merge_results.py` sets `platform` to a comma-joined string
   (`"dealercom,truecar"`). `applyFilters` compares with `!==`, so such a record
   matches neither. Cross-source hits are exactly the ones you care about most.
   Fix: `v.platform.split(',').includes(filters.platform)`.
8. **Failed geocodes aren't cached client-side.** `useGeocoder` only writes
   successes to `localStorage`, so a city Nominatim can't resolve is re-requested
   on every page load. The dev server caches nulls on disk so it costs nothing
   externally, but the effect re-runs each mount. Cache the null.
9. **Console table mislabels platforms.** `search_inventory.py`'s summary prints
   `DDC` for Dealer.com and `DI ` for *everything else*, including DealerOn and
   Team Velocity. Cosmetic, but misleading when you're debugging which source
   found what.
10. **TrueCar is used-only** — its whole URL space is `used-cars-for-sale`. Now
    that the requirement is a "new" title (§5, "Condition"), TrueCar cannot
    contribute a matching record at all, so `search_330i.sh` skips it by
    default. `--with-truecar` reruns it — useful only alongside
    `--any-condition`, since a used-only source is otherwise pure overhead.
11. **DealerInspire model-name guessing.** `_cc_filters()` sends
    `model: ["3 Series", "3 Series 330i"]` because some DI dealers store the trim
    in the model field. A dealer that stores the model as just `330i` is missed.
    Watch for DI dealers you know have inventory returning zero.

---

## 10. Hosting

### The constraint that settles it

The crawlers drive a real Chrome with persistent profiles and need a human to
solve an anti-bot challenge occasionally. **The crawler runs on your Mac.** There
is no version of this where a Netlify build hook does your sweeps. So the only
question is where the *viewer* runs.

### The trap

`npm run build` produces a static SPA that has **no `/api/annotations` and no
`/api/geocode`**. Those are `configureServer` middleware in `vite.config.ts` —
dev server only. Deploy the build to Netlify as-is and:

- the annotations fetch 404s, the `.catch()` swallows it, and the app loads with
  **zero notes** — every tag and comment you've written is invisible;
- every save POSTs into a 404 and is silently dropped;
- geocoding fails, so **every distance is `—` and the map is empty** (except for
  whatever's in that browser's `localStorage`).

Nothing errors visibly. It just quietly becomes a different, worse app. If you
deploy anywhere, know this first.

### Options, ranked

| Option | Cost | Effort | Verdict |
|---|---|---|---|
| **A. `npm run dev` on the Mac** | $0 | none | **Recommended.** Everything works. Data and notes stay on your disk. The crawler is there anyway. |
| **B. A + Tailscale or Cloudflare Tunnel to `localhost:5173`** | $0 | 15 min | **Recommended addition.** Full app on your phone at a dealership, with annotations and map intact. Tailscale is the simpler of the two for a single-user setup. |
| C. Netlify static, read-only | $0 | 1 hr | Works only if you (a) pre-bake `geocache.json` into `public/data/` and read from it instead of `/api/geocode`, and (b) accept that annotations are read-only from a committed JSON file. Fine as a "show my wife the shortlist" link. Don't triage from it. |
| D. Netlify + Functions + Blobs | $0 at your volume | 3–4 hrs | Port the two middlewares to `netlify/functions/`. Real multi-device annotations. Only worth it if you want to hand this to someone else. |
| E. Fly.io / Render running `vite preview` | ~$0–5/mo | 2 hrs | Doesn't help: `preview` has no middleware either. You'd need a small Express server. More work than D for less. |

**Do A, add B when you get tired of walking to the laptop.** Revisit D only if
this becomes a product rather than a car search — which, given your "$100k/yr
LLC" filter, is a separate conversation worth having (§13).

### Cost of running it

$0. Nominatim is free (rate-limited to 1 rps, respected). OSM tiles are free.
The dealer APIs are the same public client-side keys their own websites use.
The only paid thing in the repo is the optional OpenAI ownership analysis, which
you don't need (§11).

---

## 11. Operating it for 3–6 months

### Cadence

Weekly is the right frequency. Dealer inventory turns over on a roughly weekly
rhythm, a full sweep is ~30–60 minutes with VDP enrichment, and daily crawling
buys you very little while raising your anti-bot profile.

```bash
# Sunday night, say
cd ~/bmw-inventory-v2/crawler && ./search_330i.sh --sync --headless
```

To automate on macOS, `launchd` is more reliable than `cron` for a laptop that
sleeps (`StartCalendarInterval` fires on wake; `cron` just misses). But run the
first few by hand — you want to see the source counts and catch a slug that has
gone stale.

### What to actually look at each week

Sort by **Days on Lot, descending**, with the default filters as shipped:
Condition = New, 0–5,000 miles, M Sport = "Yes + unknown", radius 750,
red/white excluded. A 2025–26 that's been sitting 90+ days is a car the dealer
is paying floorplan interest on and is measured on moving. That's your deal —
far more than any list price.

Then check `daysOnLot` against mileage: a 330i with 3,000–5,000 miles and
30+ days on lot is a service loaner that's coming off duty — and because the
Condition filter is on by default, everything in view is still titled new,
so it's lease-eligible the same way a zero-mile car is. Full new-car
warranty, dealer-service history you can usually get in person, and often
carrying options (M Sport included) a lot-stocking decision put on it rather
than a retail customer's build sheet. Flip Condition to "Not New" only if you
want to see the CPO/used side of the same search — those are real cars too,
just not ones a new lease can be written against.

### The tag workflow

`interesting → shortlisted → contacted → negotiating → purchased`, plus three
explicit pass reasons (`negotiated_pass`, `nonleasable_pass`, `pass`). Passed
cars sink to the bottom of the table but stay in the dataset — so when the same
VIN resurfaces on a different site in month 4, you'll see you already killed it.
Use the pass reasons; over 6 months you'll forget why.

### Things you don't need

- **`--analyze` / `OPENAI_API_KEY`.** The LLM ownership analysis exists to judge
  "was this used car privately owned or a fleet car" on a 3-year-old X7. Your
  cars are new or one-owner-dealer. Skip it; it's off by default.
- **CarFax report fetching (`--fetch-carfax`).** The *badge* on the VDP is worth
  having (it flags an accident on a loaner). The full report fetch is slow and
  mostly redundant for a car with 4,000 miles.
- **`build_master_dealers.py` / `platform_census.py` / `harvest_*.py`.** These
  built the dealer roster. It's built. Re-run `platform_census.py` only if a
  platform's dealer count starts dropping suspiciously.

---

## 12. What to build next, ranked

1. **Stale-listing detection.** (bug #6) A `refresh_stale.py` that walks
   `results.json`, re-fetches the VDP for anything not seen in the last N sweeps,
   and marks 404s as sold with a `soldAt` date. Without it your dataset degrades
   into fiction over 3–6 months, and you lose the thing that makes accumulation
   valuable: *knowing what a car actually sold at and how long it took*. Reuses
   `fetch_details()` verbatim. Half a day.
2. **Price history per VIN.** Right now a price change overwrites. Keep
   `priceHistory: [{date, price}]` and the visualizer can show "$48,900 →
   $47,400 → $46,200 over 74 days." That is the single most useful negotiating
   input in the whole system, and it costs one array and a merge rule.
3. **Window sticker / build sheet by VIN.** (§6) The real answer to the M Sport
   question, and it would also give you MSRP — which is what you need for lease
   math, and which dealers routinely omit from used/loaner listings.
4. **Lease math in the table.** Done halfway: the detail panel now has one-click
   Leasehackr prefill buttons for 36mo/12k and 39mo/12k (§5, "Lease structure"),
   so getting a real monthly quote is a click + a pasted-back URL instead of
   retyping five numbers. What's still missing is doing that automatically for
   *every* row so you could sort the table by *effective monthly cost* instead
   of by sticker price — that needs MSRP (item #3) and a monthly-updated
   MF/residual table for the 330i, and deliberately wasn't guessed (see the
   "why this doesn't just compute a payment" note in §5). If you're willing to
   hand-enter BMW FS's current MF/residual once a month, this becomes a couple
   hours of work; if not, it stays a per-car manual step.
5. **Fix bugs #7 and #8.** Ten minutes each.
6. **Email/push on new matches.** Once cadence is automated, a "3 new cars matched
   your filter this week" summary beats opening the app on a hope.

I'd do 1 and 2 before the next sweep, 3 and 4 only if the hunt runs past month two.

---

## 13. What was disabled

Nothing was deleted. Everything below is in place, marked, and one command from
coming back.

**Sweep scripts for vehicles you're not shopping for** — banner comment at the
top and the exec bit removed:

`search_all_models.sh`, `search_x56m.sh`, `search_x56m_2025.sh`,
`search_x56m_2026.sh`, `search_x7_40i.sh`, `search_760.sh`, `search_xm.sh`,
`search_xm_2025.sh`, `search_xm_x7_m60_25_26.sh`

```bash
chmod +x crawler/search_xm.sh     # to re-enable
bash crawler/search_xm.sh         # or just run it through bash
```

**Legacy discovery scripts** — banner comment only, still fully functional. They
were superseded by `platform_census.py` + `build_master_dealers.py` and nothing
in the 330i pipeline calls them:

`algolia_replay.py`, `discover_dealers.py`, `sweep_ddc.py`, `sweep_dealers.py`

**Already off by default, left alone:** LLM ownership analysis (`--analyze`,
needs `OPENAI_API_KEY`), full CarFax report fetching (`--fetch-carfax`).

**Newly off by default, easy to re-enable:** the TrueCar step in
`search_330i.sh` (`SKIP_TRUECAR=true`) — it's a used-only source and the
sweep now requires `type == "new"`, so it would only ever contribute zero
records. `--with-truecar` turns it back on.

**Deliberately kept, though you might not expect to need it:** the CarFax
badge and owner-count columns. Even under the new "must be titled new"
requirement, a service loaner can accumulate a CarFax report — a fender-bender
during a loan-out, or a service history that starts showing up before the car
is ever retailed — and a low odometer will otherwise talk you right past it.
Worth a glance on any loaner candidate even though it's still new.

---

## 14. Open questions

### Resolved

- **Lease or buy? Term/mileage?** → **Lease, 36 or 39 months, 12,000 mi/yr.**
  Built the Leasehackr prefill buttons (§5, "Lease structure"). Full lease-math
  ranking (§12 #4) is still unbuilt — it needs MSRP and BMW FS's current
  MF/residual, neither of which any crawler source provides — but the manual
  round-trip through the real calculator is one click shorter now.
- **Would you consider a 2025 leftover?** → **Yes, but none seen yet.**
  `search_330i.sh --year` default widened from `2026` to `2025-2026`. If a 2025
  never turns up in a sweep, that's the market telling you something (dealers
  clear 2025s fast when a 2026 is one model-year away) — not a search-slug bug.
- **How hard is "at least M Sport"?** → **Worth trying, but not a hard gate —
  needs an easy off-switch.** The M Sport filter now defaults to "Yes +
  unknown" instead of "All", which is the trial you asked for, and it's one
  click to "All" in the same control if it's filtering out too much (§5,
  "Visualizer" table). Nothing about this needs a re-crawl either way — the
  crawler always records the signal; the filter just decides whether to act
  on it. §6 still applies in full: the underlying signal is VDP text-matching,
  not a real option list, so treat `mSport: true` as "worth a look," not
  "verified."
- **0–5,000 miles, but must be new.** → Built as a firm requirement, not a
  preference, once you connected it to lease eligibility: a car titled
  used/CPO can't be leased as new regardless of mileage, so `type == "new"` is
  what actually gates it, with the 5,000-mile cap narrowing the search on top.
  `search_330i.sh` defaults to `MAX_MILES=5000 --type new`; the UI's new
  "Condition" filter defaults to New for the same reason and is a one-click
  toggle to "All" if you want to see the used/CPO market too (§5, "Condition").
  This also surfaced a real bug — DealerInspire was leaking used inventory
  through `--type new` — now fixed (§9, bug #6).

### Still open

These change what I'd build next, not whether the current setup works.

1. **Budget ceiling / target monthly?** There's a `maxPrice` filter but no
   default set. A 2025–26 330i RWD with M Sport is roughly $48–55k MSRP depending
   on how it's optioned; a new-titled loaner under 5k miles typically lands
   $3–6k under a from-scratch order. A target monthly (now that term/mileage
   are fixed at 36–39mo/12k) would let me flag a listing as "worth calculating"
   before you open the Leasehackr link at all.
2. **How hard is "black"?** Right now red and white are excluded and black is
   *not* prioritized — everything non-red/white shows equally. If black is a
   near-requirement, I'd sort black to the top rather than just filtering. If
   it's a mild preference, leave it.
3. **Is xDrive truly out?** RWD-only is enforced now, but in Indiana in
   February that's a real decision, and it roughly triples your candidate pool.
   `--include-xdrive` flips it.
4. **Where do you want to see this?** If the answer is "on my phone at a
   dealership," I'd set up option B (§10) rather than building anything.
