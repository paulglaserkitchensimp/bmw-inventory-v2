#!/usr/bin/env bash
# Targeted nationwide sweep:
#   • BMW XM           2025–2026  (any trim)
#   • BMW X7 M60i      2025–2026
#
# Runs Dealer.com + DealerInspire, Autotrader, and Cars.com, merges by VIN into
# xm_x7_m60_25_26_results.json.  Use --sync to UNION into results.json (never
# replaces existing inventory) and refresh the visualizer.
#
# Usage:
#   ./search_xm_x7_m60_25_26.sh
#   ./search_xm_x7_m60_25_26.sh --sync
#   ./search_xm_x7_m60_25_26.sh --skip-fetch --skip-vdp
#
# One-time cars.com warmup if VDP fetches stay partial:
#   uv run cars_com.py --warmup --url 'https://www.cars.com/shopping/results/?makes[]=bmw'

set -euo pipefail
cd "$(dirname "$0")"

OUT="xm_x7_m60_25_26_results.json"
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
YEAR_RANGE="2025-2026"

AT_BASE='https://www.autotrader.com/cars-for-sale/all-cars/bmw'
AT_QS="mileage=15000&searchRadius=0&startYear=2025&endYear=2026"
CARS_BASE='https://www.cars.com/shopping/results/?mileage_min=60&mileage_max=15000&zip=48335&maximum_distance=9999&year_min=2025&year_max=2026&makes[]=bmw&sort=best_match_desc'

usage() {
  sed -n '2,16p' "$0" | sed 's/^# \?//'
  echo
  echo "Options:"
  echo "  --sync              Union focused output into results.json + visualizer sync"
  echo "  --out FILE          Merged output path (default: ${OUT})"
  echo "  --skip-fetch        Skip VDP/CarFax pass in search_inventory.py"
  echo "  --skip-vdp          Skip cars.com per-listing VDP fetches"
  echo "  --skip-dealers      Skip Dealer.com + DealerInspire"
  echo "  --skip-autotrader   Skip Autotrader crawls"
  echo "  --skip-cars-com     Skip Cars.com crawls"
  echo "  --headless          Run Autotrader browser headless"
  echo "  --exclude-states S  States to drop in focused merge only (default: CA)"
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

DDC_OUT="xm_x7_m60_25_26_ddc_di.json"
AT_XM_OUT="xm_x7_m60_25_26_autotrader_xm.json"
AT_X7_OUT="xm_x7_m60_25_26_autotrader_x7_m60i.json"
CARS_XM_OUT="xm_x7_m60_25_26_cars_com_xm.json"
CARS_X7_OUT="xm_x7_m60_25_26_cars_com_x7_m60i.json"
MERGE_INPUTS=()

echo "═══════════════════════════════════════════════════════════════"
echo " Targeted search: XM + X7 M60i (${YEAR_RANGE})"
echo " miles: ${MIN_MILES}–${MAX_MILES}  exclude states: ${EXCLUDE_STATES:-none}"
echo " output: ${OUT}"
echo "═══════════════════════════════════════════════════════════════"

if ! $SKIP_DEALERS; then
  echo
  echo "▶ [1/6] Dealer.com + DealerInspire (XM + X7 M60i)"
  dealer_cmd=(
    uv run search_inventory.py
    --search XM
    --search "X7:M60i"
    --year "$YEAR_RANGE"
    --min-miles "$MIN_MILES"
    --max-miles "$MAX_MILES"
    --out "$DDC_OUT"
  )
  if $SKIP_FETCH; then dealer_cmd+=(--skip-fetch); fi
  "${dealer_cmd[@]}"
  MERGE_INPUTS+=("$DDC_OUT")
else
  echo
  echo "⊘ [1/6] Dealer.com + DealerInspire (skipped)"
fi

if ! $SKIP_AUTOTRADER; then
  echo
  echo "▶ [2/6] Autotrader — XM"
  at_xm_cmd=(
    uv run crawl_autotrader.py
    --max-pages 30
    --url "${AT_BASE}/xm/livonia-mi?${AT_QS}"
    --min-miles "$MIN_MILES"
    --max-miles "$MAX_MILES"
    --out "$AT_XM_OUT"
  )
  if [[ -n "$AUTOTRADER_HEADLESS" ]]; then at_xm_cmd+=("$AUTOTRADER_HEADLESS"); fi
  "${at_xm_cmd[@]}"
  MERGE_INPUTS+=("$AT_XM_OUT")

  echo
  echo "▶ [3/6] Autotrader — X7 M60i"
  at_x7_cmd=(
    uv run crawl_autotrader.py
    --max-pages 30
    --url "${AT_BASE}/x7/m60i/livonia-mi?${AT_QS}"
    --min-miles "$MIN_MILES"
    --max-miles "$MAX_MILES"
    --out "$AT_X7_OUT"
  )
  if [[ -n "$AUTOTRADER_HEADLESS" ]]; then at_x7_cmd+=("$AUTOTRADER_HEADLESS"); fi
  "${at_x7_cmd[@]}"
  MERGE_INPUTS+=("$AT_X7_OUT")
else
  echo
  echo "⊘ [2–3/6] Autotrader (skipped)"
fi

if ! $SKIP_CARS_COM; then
  echo
  echo "▶ [4/6] Cars.com — XM"
  cars_xm_cmd=(
    uv run cars_com.py
    --url "${CARS_BASE}&models[]=bmw-xm"
    --min-miles "$MIN_MILES"
    --max-miles "$MAX_MILES"
    --out "$CARS_XM_OUT"
    --exclude-states "$EXCLUDE_STATES"
  )
  if $SKIP_VDP; then cars_xm_cmd+=(--skip-vdp); fi
  "${cars_xm_cmd[@]}"
  MERGE_INPUTS+=("$CARS_XM_OUT")

  echo
  echo "▶ [5/6] Cars.com — X7 M60i"
  cars_x7_cmd=(
    uv run cars_com.py
    --url "${CARS_BASE}&stock_type=cpo&trims[]=bmw-x7-m60i&models[]=bmw-x7"
    --min-miles "$MIN_MILES"
    --max-miles "$MAX_MILES"
    --out "$CARS_X7_OUT"
    --exclude-states "$EXCLUDE_STATES"
  )
  if $SKIP_VDP; then cars_x7_cmd+=(--skip-vdp); fi
  "${cars_x7_cmd[@]}"
  MERGE_INPUTS+=("$CARS_X7_OUT")
else
  echo
  echo "⊘ [4–5/6] Cars.com (skipped)"
fi

if [[ ${#MERGE_INPUTS[@]} -eq 0 ]]; then
  echo "Nothing to merge — enable at least one source." >&2
  exit 1
fi

echo
echo "▶ [6/6] Merge focused sweep"
uv run merge_results.py \
  --inputs "${MERGE_INPUTS[@]}" \
  --out "$OUT" \
  --exclude-states "$EXCLUDE_STATES"

if $SYNC; then
  echo
  echo "▶ Union ${OUT} → results.json (existing VINs preserved)"
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
$SYNC && echo "     → unioned into results.json (visualizer synced)"
