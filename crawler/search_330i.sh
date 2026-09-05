#!/usr/bin/env bash
# Regional sweep — 2026 BMW 330i (RWD), M Sport package, within ~750 mi of 47119.
#
# Target profile:
#   • 2026 330i, rear-wheel drive (xDrive is excluded at merge time)
#   • M Sport package or better — flagged post-hoc from the VDP text as
#     `mSport` / `packageSignals`; filter on it in the visualizer
#   • New OR loaner/demo/CPO, so MIN_MILES defaults to 0 (a brand-new car
#     shows 2–30 miles and the repo's usual 60-mile floor would hide it)
#   • Market: ~750 miles of Floyds Knobs, IN (Louisville metro)
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
#   ./search_330i.sh                       # 2026, 0-15k mi, 750 mi radius
#   ./search_330i.sh --sync
#   ./search_330i.sh --year 2025-2026 --max-miles 8000 --sync
#   ./search_330i.sh --skip-fetch --skip-vdp --headless      # fast pass

set -euo pipefail
cd "$(dirname "$0")"

YEAR="2026"
OUT=""
SYNC=false
SKIP_FETCH=false
SKIP_VDP=false
SKIP_DEALERS=false
SKIP_AUTOTRADER=false
SKIP_CARS_COM=false
SKIP_TRUECAR=false
AUTOTRADER_HEADLESS=""
EXCLUDE_STATES=""
MIN_MILES=0
MAX_MILES=15000
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
  sed -n '2,31p' "$0" | sed 's/^# \?//'
  echo
  echo "Options:"
  echo "  --year Y[-Y]        Model year or range (default: 2026)"
  echo "  --sync              Union focused output into results.json + visualizer sync"
  echo "  --out FILE          Merged output path (default: 330i_{YEARS}_results.json)"
  echo "  --zip ZIP           Search origin (default: 47119)"
  echo "  --radius MI         Aggregator search radius (default: 750)"
  echo "  --include-xdrive    Keep 330i xDrive too (default: RWD only)"
  echo "  --nationwide        Ignore the dealer-state whitelist + radius"
  echo "  --skip-fetch / --skip-vdp / --skip-dealers / --skip-autotrader"
  echo "  --skip-cars-com / --skip-truecar / --headless"
  echo "  --exclude-states S  (default: none)  --min-miles N / --max-miles N"
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
echo " Targeted search: 3 Series 330i (${YEAR})"
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
  # TrueCar only lists used inventory, so this source finds loaners/demos and
  # never brand-new cars. That is fine — loaners are the target here.
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
