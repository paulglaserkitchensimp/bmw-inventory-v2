#!/usr/bin/env bash
# Focused nationwide sweep: BMW XM, model year 2025 only.
#
# Runs all three inventory sources, merges by VIN, and writes xm_2025_results.json.
# Use --sync to merge the focused sweep into results.json and refresh the visualizer.
#
# Usage:
#   ./search_xm_2025.sh
#   ./search_xm_2025.sh --sync
#   ./search_xm_2025.sh --skip-fetch --skip-vdp    # fast listing-only pass
#   ./search_xm_2025.sh --skip-autotrader          # dealers + cars.com only
#
# One-time cars.com setup if VDP fetches block:
#   uv run cars_com.py --warmup --url 'https://www.cars.com/shopping/results/?makes[]=bmw'

set -euo pipefail
cd "$(dirname "$0")"

OUT="xm_2025_results.json"
SYNC=false
SKIP_FETCH=false
SKIP_VDP=false
SKIP_DEALERS=false
SKIP_AUTOTRADER=false
SKIP_CARS_COM=false
AUTOTRADER_HEADLESS=""
EXCLUDE_STATES="CA"
MIN_MILES=60
MAX_MILES=15000

AUTOTRADER_URL='https://www.autotrader.com/cars-for-sale/all-cars/bmw/xm/detroit-mi?mileage=15000&searchRadius=0&startYear=2025&endYear=2025'
CARS_COM_URL='https://www.cars.com/shopping/results/?mileage_min=60&mileage_max=15000&models[]=bmw-xm&zip=48226&maximum_distance=9999&year_min=2025&year_max=2025&makes[]=bmw&sort=best_match_desc'

usage() {
  sed -n '2,14p' "$0" | sed 's/^# \?//'
  echo
  echo "Options:"
  echo "  --sync              Merge focused output into results.json + visualizer sync"
  echo "  --out FILE          Merged output path (default: xm_2025_results.json)"
  echo "  --skip-fetch        Skip VDP/CarFax pass in search_inventory.py"
  echo "  --skip-vdp          Skip cars.com per-listing VDP fetches"
  echo "  --skip-dealers      Skip Dealer.com + DealerInspire (search_inventory.py)"
  echo "  --skip-autotrader   Skip Autotrader crawl"
  echo "  --skip-cars-com     Skip Cars.com crawl"
  echo "  --headless          Run Autotrader browser headless"
  echo "  --exclude-states S  Comma-separated states to drop at merge (default: CA)"
  echo "  --min-miles N       Minimum odometer (default: 60)"
  echo "  --max-miles N       Maximum odometer (default: 15000)"
  echo "  -h, --help          Show this help"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sync) SYNC=true ;;
    --out) OUT="$2"; shift ;;
    --skip-fetch) SKIP_FETCH=true ;;
    --skip-vdp) SKIP_VDP=true ;;
    --skip-dealers) SKIP_DEALERS=true ;;
    --skip-autotrader) SKIP_AUTOTRADER=true ;;
    --skip-cars-com) SKIP_CARS_COM=true ;;
    --headless) AUTOTRADER_HEADLESS="--headless" ;;
    --exclude-states) EXCLUDE_STATES="$2"; shift ;;
    --min-miles) MIN_MILES="$2"; shift ;;
    --max-miles) MAX_MILES="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
  esac
  shift
done

DDC_OUT="xm_2025_ddc_di.json"
AT_OUT="xm_2025_autotrader.json"
CARS_OUT="xm_2025_cars_com.json"
MERGE_INPUTS=()

echo "═══════════════════════════════════════════════════════════════"
echo " BMW XM 2025 — focused multi-platform search"
echo " miles: ${MIN_MILES}–${MAX_MILES}  exclude states: ${EXCLUDE_STATES:-none}"
echo " output: ${OUT}"
echo "═══════════════════════════════════════════════════════════════"

if ! $SKIP_DEALERS; then
  echo
  echo "▶ [1/4] Dealer.com + DealerInspire"
  dealer_cmd=(
    uv run search_inventory.py
    --search XM
    --year 2025
    --min-miles "$MIN_MILES"
    --max-miles "$MAX_MILES"
    --out "$DDC_OUT"
  )
  if $SKIP_FETCH; then dealer_cmd+=(--skip-fetch); fi
  "${dealer_cmd[@]}"
  MERGE_INPUTS+=("$DDC_OUT")
else
  echo
  echo "⊘ [1/4] Dealer.com + DealerInspire (skipped)"
fi

if ! $SKIP_AUTOTRADER; then
  echo
  echo "▶ [2/4] Autotrader"
  at_cmd=(
    uv run crawl_autotrader.py
    --max-pages 30
    --url "$AUTOTRADER_URL"
    --min-miles "$MIN_MILES"
    --max-miles "$MAX_MILES"
    --out "$AT_OUT"
  )
  if [[ -n "$AUTOTRADER_HEADLESS" ]]; then at_cmd+=("$AUTOTRADER_HEADLESS"); fi
  "${at_cmd[@]}"
  MERGE_INPUTS+=("$AT_OUT")
else
  echo
  echo "⊘ [2/4] Autotrader (skipped)"
fi

if ! $SKIP_CARS_COM; then
  echo
  echo "▶ [3/4] Cars.com"
  cars_cmd=(
    uv run cars_com.py
    --url "$CARS_COM_URL"
    --min-miles "$MIN_MILES"
    --max-miles "$MAX_MILES"
    --out "$CARS_OUT"
    --exclude-states "$EXCLUDE_STATES"
  )
  if $SKIP_VDP; then cars_cmd+=(--skip-vdp); fi
  "${cars_cmd[@]}"
  MERGE_INPUTS+=("$CARS_OUT")
else
  echo
  echo "⊘ [3/4] Cars.com (skipped)"
fi

if [[ ${#MERGE_INPUTS[@]} -eq 0 ]]; then
  echo "Nothing to merge — enable at least one source." >&2
  exit 1
fi

echo
echo "▶ [4/4] Merge"
uv run merge_results.py \
  --inputs "${MERGE_INPUTS[@]}" \
  --out "$OUT" \
  --exclude-states "$EXCLUDE_STATES"

if $SYNC; then
  echo
  echo "▶ Merging ${OUT} into results.json (union by VIN — existing inventory kept)"
  if [[ -f results.json ]]; then
    uv run merge_results.py \
      --inputs results.json "$OUT" \
      --out results.json
  else
    cp "$OUT" results.json
    echo "  (no prior results.json — initialized from focused sweep)"
  fi
  (cd ../visualizer && npm run sync)
fi

echo
echo "Done → ${OUT}"
$SYNC && echo "     → merged into results.json (visualizer synced)"
