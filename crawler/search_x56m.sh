#!/usr/bin/env bash
# Targeted nationwide sweep — X5/X6 M family, <15k miles, year-parameterized:
#   • BMW X5 M60i   (trim search)
#   • BMW X5 M      (any trim — Competition etc.)
#   • BMW X6 M60i   (trim search)
#   • BMW X6 M      (any trim)
#
# Sources: Dealer.com + DealerInspire + DealerOn + Team Velocity (via
# search_inventory.py), Autotrader, Cars.com, and TrueCar. Everything is merged
# by VIN into x56m_{YEAR}_results.json. Use --sync to UNION into results.json
# (never replaces existing inventory) and refresh the visualizer.
#
# Model/trim designations per source (verified 2026-07):
#   search_inventory : X5:M60i | "X5 M" | X6:M60i | "X6 M"
#   Autotrader paths : bmw/x5/m60i | bmw/x5-m | bmw/x6/m60i | bmw/x6-m
#   Cars.com         : models[]=bmw-x5&trims[]=bmw-x5-m60i | models[]=bmw-x5_m
#                      models[]=bmw-x6&trims[]=bmw-x6-m60i | models[]=bmw-x6_m
#   TrueCar mmt[]    : bmw_x5_m60i | bmw_x5-m | bmw_x6_m60i | bmw_x6-m
#
# Usage:
#   ./search_x56m.sh --year 2026
#   ./search_x56m.sh --year 2025 --sync
#   ./search_x56m.sh --year 2026 --skip-fetch --skip-vdp --headless
# (or use the year wrappers: ./search_x56m_2025.sh / ./search_x56m_2026.sh)

set -euo pipefail
cd "$(dirname "$0")"

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
YEAR="2025"

usage() {
  sed -n '2,25p' "$0" | sed 's/^# \?//'
  echo
  echo "Options:"
  echo "  --year YYYY         Model year to search (default: 2025)"
  echo "  --sync              Union focused output into results.json + visualizer sync"
  echo "  --out FILE          Merged output path (default: x56m_{YEAR}_results.json)"
  echo "  --skip-fetch        Skip VDP/CarFax pass in search_inventory.py"
  echo "  --skip-vdp          Skip cars.com per-listing VDP fetches"
  echo "  --skip-dealers      Skip dealer platforms (DDC/DI/DealerOn/TeamVelocity)"
  echo "  --skip-autotrader   Skip Autotrader crawls"
  echo "  --skip-cars-com     Skip Cars.com crawls"
  echo "  --skip-truecar      Skip TrueCar crawls"
  echo "  --headless          Run Autotrader browser headless"
  echo "  --exclude-states S  States to drop in focused merge only (default: CA)"
  echo "  --min-miles N       Minimum odometer (default: 60)"
  echo "  --max-miles N       Maximum odometer (default: 15000)"
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

PREFIX="x56m_${YEAR}"
[[ -z "$OUT" ]] && OUT="${PREFIX}_results.json"

AT_BASE='https://www.autotrader.com/cars-for-sale/all-cars/bmw'
AT_QS="mileage=15000&searchRadius=0&startYear=${YEAR}&endYear=${YEAR}"
CARS_BASE="https://www.cars.com/shopping/results/?mileage_min=${MIN_MILES}&mileage_max=${MAX_MILES}&zip=48226&maximum_distance=9999&year_min=${YEAR}&year_max=${YEAR}&makes[]=bmw&sort=best_match_desc"
TC_BASE="https://www.truecar.com/used-cars-for-sale/listings/inventory/?searchRadius=5000&yearLow=${YEAR}&yearHigh=${YEAR}&mileageHigh=${MAX_MILES}"

DDC_OUT="${PREFIX}_dealers.json"
MERGE_INPUTS=()

echo "═══════════════════════════════════════════════════════════════"
echo " Targeted search: X5 M60i + X5 M + X6 M60i + X6 M (${YEAR})"
echo " miles: ${MIN_MILES}–${MAX_MILES}  exclude states: ${EXCLUDE_STATES:-none}"
echo " output: ${OUT}"
echo "═══════════════════════════════════════════════════════════════"

if ! $SKIP_DEALERS; then
  echo
  echo "▶ [1/4] Dealer platforms (DDC + DealerInspire + DealerOn + Team Velocity)"
  dealer_cmd=(
    uv run search_inventory.py
    --search "X5:M60i"
    --search "X5 M"
    --search "X6:M60i"
    --search "X6 M"
    --year "$YEAR"
    --min-miles "$MIN_MILES"
    --max-miles "$MAX_MILES"
    --out "$DDC_OUT"
  )
  if $SKIP_FETCH; then dealer_cmd+=(--skip-fetch); fi
  "${dealer_cmd[@]}"
  MERGE_INPUTS+=("$DDC_OUT")
else
  echo
  echo "⊘ [1/4] Dealer platforms (skipped)"
fi

# name → autotrader path
AT_SEARCHES=(
  "x5_m60i:x5/m60i"
  "x5m:x5-m"
  "x6_m60i:x6/m60i"
  "x6m:x6-m"
)
if ! $SKIP_AUTOTRADER; then
  echo
  echo "▶ [2/4] Autotrader (4 searches)"
  for spec in "${AT_SEARCHES[@]}"; do
    name="${spec%%:*}"; path="${spec#*:}"
    at_out="${PREFIX}_autotrader_${name}.json"
    echo
    echo "  — Autotrader ${path}"
    at_cmd=(
      uv run crawl_autotrader.py
      --max-pages 30
      --url "${AT_BASE}/${path}/detroit-mi?${AT_QS}"
      --min-miles "$MIN_MILES"
      --max-miles "$MAX_MILES"
      --out "$at_out"
    )
    if [[ -n "$AUTOTRADER_HEADLESS" ]]; then at_cmd+=("$AUTOTRADER_HEADLESS"); fi
    "${at_cmd[@]}"
    MERGE_INPUTS+=("$at_out")
  done
else
  echo
  echo "⊘ [2/4] Autotrader (skipped)"
fi

# name → cars.com model/trim query
CARS_SEARCHES=(
  "x5_m60i:models[]=bmw-x5&trims[]=bmw-x5-m60i"
  "x5m:models[]=bmw-x5_m"
  "x6_m60i:models[]=bmw-x6&trims[]=bmw-x6-m60i"
  "x6m:models[]=bmw-x6_m"
)
if ! $SKIP_CARS_COM; then
  echo
  echo "▶ [3/4] Cars.com (4 searches)"
  for spec in "${CARS_SEARCHES[@]}"; do
    name="${spec%%:*}"; qs="${spec#*:}"
    cars_out="${PREFIX}_cars_com_${name}.json"
    echo
    echo "  — Cars.com ${qs}"
    cars_cmd=(
      uv run cars_com.py
      --url "${CARS_BASE}&${qs}"
      --min-miles "$MIN_MILES"
      --max-miles "$MAX_MILES"
      --out "$cars_out"
      --exclude-states "$EXCLUDE_STATES"
    )
    if $SKIP_VDP; then cars_cmd+=(--skip-vdp); fi
    "${cars_cmd[@]}"
    MERGE_INPUTS+=("$cars_out")
  done
else
  echo
  echo "⊘ [3/4] Cars.com (skipped)"
fi

# name → truecar mmt value
TC_SEARCHES=(
  "x5_m60i:bmw_x5_m60i"
  "x5m:bmw_x5-m"
  "x6_m60i:bmw_x6_m60i"
  "x6m:bmw_x6-m"
)
if ! $SKIP_TRUECAR; then
  echo
  echo "▶ [4/4] TrueCar (4 searches)"
  for spec in "${TC_SEARCHES[@]}"; do
    name="${spec%%:*}"; mmt="${spec#*:}"
    tc_out="${PREFIX}_truecar_${name}.json"
    echo
    echo "  — TrueCar mmt[]=${mmt}"
    uv run truecar.py \
      --url "${TC_BASE}&mmt[]=${mmt}" \
      --min-miles "$MIN_MILES" \
      --max-miles "$MAX_MILES" \
      --out "$tc_out" \
      --exclude-states "$EXCLUDE_STATES"
    MERGE_INPUTS+=("$tc_out")
  done
else
  echo
  echo "⊘ [4/4] TrueCar (skipped)"
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
if $SYNC; then echo "     → unioned into results.json (visualizer synced)"; fi
