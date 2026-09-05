#!/usr/bin/env bash
# ── DISABLED for the 2026 330i hunt ───────────────────────────────────────────
# This sweep targets a vehicle this fork is not shopping for (X5/X6/X7/XM/7
# Series). It is kept for reference only; the exec bit has been removed.
# To re-enable:  chmod +x <this file>   (or run it with: bash <this file>)
# See docs/330I_DEAL_FINDER.md § "What was disabled".
# ─────────────────────────────────────────────────────────────────────────────
# Targeted nationwide sweep — BMW 7 Series 760i xDrive, <15k miles.
#
# Sources: Dealer.com + DealerInspire + DealerOn + Team Velocity (via
# search_inventory.py), Autotrader, Cars.com, and TrueCar. Merged by VIN into
# b760_{YEARS}_results.json. Use --sync to UNION into results.json (never
# replaces existing inventory) and refresh the visualizer.
#
# Model/trim designations per source (verified 2026-07):
#   search_inventory : "7 Series:760i xDrive"  (DDC/DI store trim as "760i xDrive";
#                      TV/DealerOn abbreviate — matched via loose trim matching)
#   Autotrader path  : bmw/760i-xdrive         (760i xDrive is its own model there)
#   Cars.com         : models[]=bmw-760        ("760" is its own model there)
#   TrueCar mmt[]    : bmw_7-series_760i       (model 7-series, trim "760i")
#
# Usage:
#   ./search_760.sh                     # 2025-2026 (default)
#   ./search_760.sh --year 2023-2026 --sync
#   ./search_760.sh --skip-fetch --skip-vdp --headless

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
SKIP_TRUECAR=false
AUTOTRADER_HEADLESS=""
EXCLUDE_STATES="CA"
MIN_MILES=60
MAX_MILES=15000

usage() {
  sed -n '9,27p' "$0" | sed 's/^# \?//'
  echo
  echo "Options:"
  echo "  --year Y[-Y]        Model year or range (default: 2025-2026)"
  echo "  --sync              Union focused output into results.json + visualizer sync"
  echo "  --out FILE          Merged output path (default: b760_{YEARS}_results.json)"
  echo "  --skip-fetch / --skip-vdp / --skip-dealers / --skip-autotrader"
  echo "  --skip-cars-com / --skip-truecar / --headless"
  echo "  --exclude-states S  (default: CA)   --min-miles N / --max-miles N"
  echo "  -h, --help          Show this help"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --year) YEAR="$2"; shift ;;
    --sync) SYNC=true ;;
    --out) OUT="$2"; shift ;;
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
PREFIX="b760_$(echo "$YEAR" | tr - _)"
[[ -z "$OUT" ]] && OUT="${PREFIX}_results.json"

DDC_OUT="${PREFIX}_dealers.json"
AT_OUT="${PREFIX}_autotrader.json"
CARS_OUT="${PREFIX}_cars_com.json"
TC_OUT="${PREFIX}_truecar.json"
MERGE_INPUTS=()

echo "═══════════════════════════════════════════════════════════════"
echo " Targeted search: 7 Series 760i xDrive (${YEAR})"
echo " miles: ${MIN_MILES}–${MAX_MILES}  exclude states: ${EXCLUDE_STATES:-none}"
echo " output: ${OUT}"
echo "═══════════════════════════════════════════════════════════════"

if ! $SKIP_DEALERS; then
  echo
  echo "▶ [1/4] Dealer platforms (DDC + DealerInspire + DealerOn + Team Velocity)"
  dealer_cmd=(
    uv run search_inventory.py
    --search "7 Series:760i xDrive"
    --year "$YEAR"
    --min-miles "$MIN_MILES"
    --max-miles "$MAX_MILES"
    --out "$DDC_OUT"
  )
  if $SKIP_FETCH; then dealer_cmd+=(--skip-fetch); fi
  "${dealer_cmd[@]}"
  MERGE_INPUTS+=("$DDC_OUT")
else
  echo; echo "⊘ [1/4] Dealer platforms (skipped)"
fi

if ! $SKIP_AUTOTRADER; then
  echo
  echo "▶ [2/4] Autotrader — 760i-xdrive"
  at_cmd=(
    uv run crawl_autotrader.py
    --max-pages 30
    --url "https://www.autotrader.com/cars-for-sale/all-cars/bmw/760i-xdrive/detroit-mi?mileage=${MAX_MILES}&searchRadius=0&startYear=${YEAR_LOW}&endYear=${YEAR_HIGH}"
    --min-miles "$MIN_MILES"
    --max-miles "$MAX_MILES"
    --out "$AT_OUT"
  )
  if [[ -n "$AUTOTRADER_HEADLESS" ]]; then at_cmd+=("$AUTOTRADER_HEADLESS"); fi
  "${at_cmd[@]}"
  MERGE_INPUTS+=("$AT_OUT")
else
  echo; echo "⊘ [2/4] Autotrader (skipped)"
fi

if ! $SKIP_CARS_COM; then
  echo
  echo "▶ [3/4] Cars.com — bmw-760"
  cars_cmd=(
    uv run cars_com.py
    --url "https://www.cars.com/shopping/results/?mileage_min=${MIN_MILES}&mileage_max=${MAX_MILES}&zip=48226&maximum_distance=9999&year_min=${YEAR_LOW}&year_max=${YEAR_HIGH}&makes[]=bmw&models[]=bmw-760&sort=best_match_desc"
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
  echo "▶ [4/4] TrueCar — mmt[]=bmw_7-series_760i"
  uv run truecar.py \
    --url "https://www.truecar.com/used-cars-for-sale/listings/inventory/?searchRadius=5000&yearLow=${YEAR_LOW}&yearHigh=${YEAR_HIGH}&mileageHigh=${MAX_MILES}&mmt[]=bmw_7-series_760i" \
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
uv run merge_results.py \
  --inputs "${MERGE_INPUTS[@]}" \
  --out "$OUT" \
  --exclude-states "$EXCLUDE_STATES"

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
