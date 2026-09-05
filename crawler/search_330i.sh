#!/usr/bin/env bash
# Regional sweep — 2025-2026 BMW 330i (RWD), M Sport package, within ~750 mi of 47119.
#
# Target profile:
#   • 2025-2026 330i (2025 leftovers welcome — none seen yet, but they carry
#     the deepest discounts of the model cycle), rear-wheel drive (xDrive is
#     excluded at merge time)
#   • M Sport package or better — flagged post-hoc from the VDP text as
#     `mSport` / `packageSignals`. This is a soft filter, on by default and
#     toggled off in one click in the visualizer's "M Sport Pkg" control —
#     the crawler always records the signal either way, so no re-sweep is
#     needed to try it with the requirement relaxed.
#   • CONDITION MUST BE "NEW" — not used, not CPO. A service loaner that
#     hasn't been retailed yet still qualifies (dealers title it "new" in
#     their own inventory system regardless of odometer), which is exactly
#     what makes it lease-eligible; a car that's already been retailed once
#     and bought back is "used" no matter how few miles it has, and can't be
#     leased as new. 0–5,000 miles (MIN_MILES=0 so a just-arrived unit with
#     2–30 miles isn't hidden by the repo's used-car-era 60-mile floor).
#     Toggle with --any-condition to see used/CPO too.
#   • Market: ~750 miles of Floyds Knobs, IN (Louisville metro)
#   • Deal structure: 36 or 39 month lease, 12,000 mi/year — see the
#     Leasehackr prefill buttons in the visualizer's vehicle detail panel
#
# Sources: Dealer.com + DealerInspire + DealerOn + Team Velocity (via
# search_inventory.py), Autotrader, Cars.com, TrueCar. Merged by VIN into
# 330i_{YEARS}_results.json. --sync UNIONs into results.json and refreshes the
# visualizer.
#
# Model/trim designations per source — VERIFY THESE ONCE on your machine (see
# docs/330I_DEAL_FINDER.md § "Verify the source slugs"); they were transcribed
# from BMW's naming, not confirmed against a live response:
#   search_inventory : "3 Series:330i"      (DDC/DI/DealerOn/TV)
#   Autotrader path  : bmw/3-series/330i
#   Cars.com         : models[]=bmw-3_series   (trim left off on purpose —
#                      pulls 330i/330e/M340i, then trim-filtered locally)
#   TrueCar mmt[]    : bmw_3-series  + client-side --trim 330i
#
# Usage:
#   ./search_330i.sh                       # 2025-2026, new only, 0-5k mi, 750 mi
#   ./search_330i.sh --sync
#   ./search_330i.sh --year 2026 --sync                       # 2026-only pass
#   ./search_330i.sh --any-condition --with-truecar --sync    # widen to used/CPO
#   ./search_330i.sh --skip-fetch --skip-vdp --headless      # fast pass

set -euo pipefail
cd "$(dirname "$0")"

YEAR="2025-2026"
OUT=""
SYNC=false
SKIP_FETCH=false
SKIP_VDP=false
SKIP_DEALERS=false
SKIP_AUTOTRADER=false
SKIP_CARS_COM=false
SKIP_TRUECAR=true   # TrueCar is used-only (see below) — pointless while CONDITION=new
AUTOTRADER_HEADLESS=""
EXCLUDE_STATES=""
MIN_MILES=0
MAX_MILES=5000
# "new" only, so the car can actually be leased as new — a car titled "used"
# can't be, regardless of how few miles it has. Loosen with --any-condition.
CONDITION="new"
ZIP="47119"
RADIUS=750
# Autotrader wants a city slug in the path; the zip= param is what actually
# anchors the radius search.
AT_CITY="louisville-ky"
# Drop the drivetrain we don't want. Trim matching everywhere in this repo is a
# loose substring match, so "330i" also matches "330i xDrive" — this is the
# only thing that keeps the sweep RWD-only.
EXCLUDE_TRIMS="xDrive"
# Dealers within (or straddling) a 750-mile ring around 47119. Used to skip
# ~55% of the per-dealer API calls on DealerInspire / DealerOn / Team Velocity.
# The OEM Dealer.com feed is one nationwide call either way and gets filtered
# after the fact.
DEALER_STATES="KY,IN,TN,IL,OH,WV,MO,AL,VA,NC,SC,GA,MI,AR,MS,PA,IA,MD,DE,DC,WI,NJ,NY,LA,OK,KS,MN,FL,CT,NE,TX"

usage() {
  sed -n '2,44p' "$0" | sed 's/^# \?//'
  echo
  echo "Options:"
  echo "  --year Y[-Y]        Model year or range (default: 2025-2026)"
  echo "  --sync              Union focused output into results.json + visualizer sync"
  echo "  --out FILE          Merged output path (default: 330i_{YEARS}_results.json)"
  echo "  --zip ZIP           Search origin (default: 47119)"
  echo "  --radius MI         Aggregator search radius (default: 750)"
  echo "  --include-xdrive    Keep 330i xDrive too (default: RWD only)"
  echo "  --any-condition     Include used/CPO too (default: new only, for lease eligibility)"
  echo "  --with-truecar      Run TrueCar anyway (default: skipped — it is used-only)"
  echo "  --nationwide        Ignore the dealer-state whitelist + radius"
  echo "  --skip-fetch / --skip-vdp / --skip-dealers / --skip-autotrader"
  echo "  --skip-cars-com / --skip-truecar / --headless"
  echo "  --exclude-states S  (default: none)  --min-miles N / --max-miles N (default: 0-5000)"
  echo "  -h, --help          Show this help"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --year) YEAR="$2"; shift ;;
    --sync) SYNC=true ;;
    --out) OUT="$2"; shift ;;
    --zip) ZIP="$2"; shift ;;
    --radius) RADIUS="$2"; shift ;;
    --include-xdrive) EXCLUDE_TRIMS="" ;;
    --any-condition) CONDITION="all" ;;
    --with-truecar) SKIP_TRUECAR=false ;;
    --nationwide) DEALER_STATES=""; RADIUS=0 ;;
    --skip-fetch) SKIP_FETCH=true ;;
    --skip-vdp) SKIP_VDP=true ;;
    --skip-dealers) SKIP_DEALERS=true ;;
    --skip-autotrader) SKIP_AUTOTRADER=true ;;
    --skip-cars-com) SKIP_CARS_COM=true ;;
    --skip-truecar) SKIP_TRUECAR=true ;;
    --headless) AUTOTRADER_HEADLESS="--headless" ;;
    --exclude-states) EXCLUDE_STATES="$2"; shift ;;
    --min-miles) MIN_MILES="$2"; shift ;;
    --max-miles) MAX_MILES="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
  esac
  shift
done

YEAR_LOW="${YEAR%%-*}"
YEAR_HIGH="${YEAR##*-}"
PREFIX="330i_$(echo "$YEAR" | tr - _)"
[[ -z "$OUT" ]] && OUT="${PREFIX}_results.json"

DDC_OUT="${PREFIX}_dealers.json"
AT_OUT="${PREFIX}_autotrader.json"
CARS_OUT="${PREFIX}_cars_com.json"
TC_OUT="${PREFIX}_truecar.json"
MERGE_INPUTS=()

echo "═══════════════════════════════════════════════════════════════"
echo " Targeted search: 3 Series 330i (${YEAR})   condition: ${CONDITION}   lease target: 36/39mo, 12k mi/yr"
echo " miles: ${MIN_MILES}–${MAX_MILES}   origin: ${ZIP}   radius: ${RADIUS:-nationwide} mi"
echo " exclude trims: ${EXCLUDE_TRIMS:-none}   exclude states: ${EXCLUDE_STATES:-none}"
echo " output: ${OUT}"
echo "═══════════════════════════════════════════════════════════════"

if ! $SKIP_DEALERS; then
  echo
  echo "▶ [1/4] Dealer platforms (DDC + DealerInspire + DealerOn + Team Velocity)"
  dealer_cmd=(
    uv run search_inventory.py
    --search "3 Series:330i"
    --year "$YEAR"
    --min-miles "$MIN_MILES"
    --max-miles "$MAX_MILES"
    --type "$CONDITION"
    --out "$DDC_OUT"
  )
  if [[ -n "$EXCLUDE_TRIMS" ]]; then dealer_cmd+=(--exclude-trim "$EXCLUDE_TRIMS"); fi
  if [[ -n "$DEALER_STATES" ]]; then dealer_cmd+=(--dealer-states "$DEALER_STATES"); fi
  if $SKIP_FETCH; then dealer_cmd+=(--skip-fetch); fi
  "${dealer_cmd[@]}"
  MERGE_INPUTS+=("$DDC_OUT")
else
  echo; echo "⊘ [1/4] Dealer platforms (skipped)"
fi

if ! $SKIP_AUTOTRADER; then
  echo
  echo "▶ [2/4] Autotrader — 3-series/330i"
  at_cmd=(
    uv run crawl_autotrader.py
    --max-pages 30
    --url "https://www.autotrader.com/cars-for-sale/all-cars/bmw/3-series/330i/${AT_CITY}?zip=${ZIP}&searchRadius=${RADIUS}&mileage=${MAX_MILES}&startYear=${YEAR_LOW}&endYear=${YEAR_HIGH}"
    --min-miles "$MIN_MILES"
    --max-miles "$MAX_MILES"
    --out "$AT_OUT"
  )
  if [[ -n "$EXCLUDE_STATES" ]]; then at_cmd+=(--exclude-states "$EXCLUDE_STATES"); fi
  if [[ -n "$AUTOTRADER_HEADLESS" ]]; then at_cmd+=("$AUTOTRADER_HEADLESS"); fi
  "${at_cmd[@]}"
  MERGE_INPUTS+=("$AT_OUT")
else
  echo; echo "⊘ [2/4] Autotrader (skipped)"
fi

if ! $SKIP_CARS_COM; then
  echo
  echo "▶ [3/4] Cars.com — models[]=bmw-3_series (trim filtered at merge)"
  cars_cmd=(
    uv run cars_com.py
    --url "https://www.cars.com/shopping/results/?mileage_min=${MIN_MILES}&mileage_max=${MAX_MILES}&zip=${ZIP}&maximum_distance=${RADIUS}&year_min=${YEAR_LOW}&year_max=${YEAR_HIGH}&makes[]=bmw&models[]=bmw-3_series&sort=best_match_desc"
    --min-miles "$MIN_MILES"
    --max-miles "$MAX_MILES"
    --out "$CARS_OUT"
    --exclude-states "$EXCLUDE_STATES"
  )
  if $SKIP_VDP; then cars_cmd+=(--skip-vdp); fi
  "${cars_cmd[@]}"
  MERGE_INPUTS+=("$CARS_OUT")
else
  echo; echo "⊘ [3/4] Cars.com (skipped)"
fi

if ! $SKIP_TRUECAR; then
  echo
  echo "▶ [4/4] TrueCar — mmt[]=bmw_3-series --trim 330i"
  # TrueCar's whole URL path is used-cars-for-sale — it structurally cannot
  # return a "new" record, so this step is skipped by default while
  # CONDITION=new (see SKIP_TRUECAR above). Runs anyway with --with-truecar,
  # e.g. when combined with --any-condition to see the used/CPO market too.
  uv run truecar.py \
    --url "https://www.truecar.com/used-cars-for-sale/listings/inventory/?zip=${ZIP}&searchRadius=${RADIUS}&yearLow=${YEAR_LOW}&yearHigh=${YEAR_HIGH}&mileageHigh=${MAX_MILES}&mmt[]=bmw_3-series" \
    --trim 330i \
    --min-miles "$MIN_MILES" \
    --max-miles "$MAX_MILES" \
    --out "$TC_OUT" \
    --exclude-states "$EXCLUDE_STATES"
  MERGE_INPUTS+=("$TC_OUT")
else
  echo; echo "⊘ [4/4] TrueCar (skipped)"
fi

if [[ ${#MERGE_INPUTS[@]} -eq 0 ]]; then
  echo "Nothing to merge — enable at least one source." >&2
  exit 1
fi

echo
echo "▶ Merge focused sweep"
merge_cmd=(uv run merge_results.py --inputs "${MERGE_INPUTS[@]}" --out "$OUT")
if [[ -n "$EXCLUDE_STATES" ]]; then merge_cmd+=(--exclude-states "$EXCLUDE_STATES"); fi
if [[ -n "$EXCLUDE_TRIMS" ]]; then merge_cmd+=(--exclude-trim "$EXCLUDE_TRIMS"); fi
# search_inventory.py's --type covers the four dealer platforms; the
# aggregators (Autotrader/Cars.com/TrueCar) have no condition filter of their
# own, so this is what actually keeps used/CPO listings out of the merged
# output. merge_results.py doesn't accept "all" here (only new/used/cpo).
if [[ "$CONDITION" != "all" ]]; then merge_cmd+=(--only-type "$CONDITION"); fi
"${merge_cmd[@]}"

if $SYNC; then
  echo
  echo "▶ Union ${OUT} → results.json (existing VINs preserved)"
  if [[ -f results.json ]]; then
    uv run merge_results.py --inputs results.json "$OUT" --out results.json
  else
    cp "$OUT" results.json
    echo "  (no prior results.json — initialized from focused sweep)"
  fi
  (cd ../visualizer && npm run sync)
fi

echo
echo "Done → ${OUT}"
if $SYNC; then echo "     → unioned into results.json (visualizer synced)"; fi
