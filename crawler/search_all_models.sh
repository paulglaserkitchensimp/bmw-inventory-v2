#!/usr/bin/env bash
# ── DISABLED for the 2026 330i hunt ───────────────────────────────────────────
# This sweep targets a vehicle this fork is not shopping for (X5/X6/X7/XM/7
# Series). It is kept for reference only; the exec bit has been removed.
# To re-enable:  chmod +x <this file>   (or run it with: bash <this file>)
# See docs/330I_DEAL_FINDER.md § "What was disabled".
# ─────────────────────────────────────────────────────────────────────────────
# Full M-lineup nationwide sweep — all five target models, <15k miles:
#   • BMW X5 M60i
#   • BMW X6 M60i
#   • BMW X7 M60i
#   • BMW XM           (any trim)
#   • BMW 760i xDrive
#
# Sources: Dealer.com + DealerInspire + DealerOn + Team Velocity (via
# search_inventory.py), Autotrader, Cars.com, and TrueCar. Everything is merged
# by VIN into allm_{YEARS}_results.json. Use --sync to UNION into results.json
# (never replaces existing inventory) and refresh the visualizer.
#
# Model/trim designations per source (verified 2026-07):
#   search_inventory : X5:M60i | X6:M60i | X7:M60i | XM | "7 Series:760i xDrive"
#   Autotrader paths : bmw/x5/m60i | bmw/x6/m60i | bmw/x7/m60i | bmw/xm | bmw/760i-xdrive
#   Cars.com         : trims[]=bmw-{x5,x6,x7}-m60i | models[]=bmw-xm | models[]=bmw-760
#   TrueCar mmt[]    : bmw_x5_m60i | bmw_x6_m60i | bmw_x7_m60i | bmw_xm | bmw_7-series_760i
#
# Usage:
#   ./search_all_models.sh                    # 2025-2026 (default)
#   ./search_all_models.sh --sync
#   ./search_all_models.sh --year 2025 --skip-fetch --skip-vdp --headless

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
  sed -n '9,31p' "$0" | sed 's/^# \?//'
  echo
  echo "Options:"
  echo "  --year Y[-Y]        Model year or range (default: 2025-2026)"
  echo "  --sync              Union focused output into results.json + visualizer sync"
  echo "  --out FILE          Merged output path (default: allm_{YEARS}_results.json)"
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
PREFIX="allm_$(echo "$YEAR" | tr - _)"
[[ -z "$OUT" ]] && OUT="${PREFIX}_results.json"

AT_BASE='https://www.autotrader.com/cars-for-sale/all-cars/bmw'
AT_QS="mileage=${MAX_MILES}&searchRadius=0&startYear=${YEAR_LOW}&endYear=${YEAR_HIGH}"
CARS_BASE="https://www.cars.com/shopping/results/?mileage_min=${MIN_MILES}&mileage_max=${MAX_MILES}&zip=48226&maximum_distance=9999&year_min=${YEAR_LOW}&year_max=${YEAR_HIGH}&makes[]=bmw&sort=best_match_desc"
TC_BASE="https://www.truecar.com/used-cars-for-sale/listings/inventory/?searchRadius=5000&yearLow=${YEAR_LOW}&yearHigh=${YEAR_HIGH}&mileageHigh=${MAX_MILES}"

DDC_OUT="${PREFIX}_dealers.json"
MERGE_INPUTS=()

echo "═══════════════════════════════════════════════════════════════"
echo " Full sweep: X5 M60i + X6 M60i + X7 M60i + XM + 760i xDrive (${YEAR})"
echo " miles: ${MIN_MILES}–${MAX_MILES}  exclude states: ${EXCLUDE_STATES:-none}"
echo " output: ${OUT}"
echo "═══════════════════════════════════════════════════════════════"

if ! $SKIP_DEALERS; then
  echo
  echo "▶ [1/4] Dealer platforms (DDC + DealerInspire + DealerOn + Team Velocity)"
  dealer_cmd=(
    uv run search_inventory.py
    --search "X5:M60i"
    --search "X6:M60i"
    --search "X7:M60i"
    --search "XM"
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

# name → autotrader path
AT_SEARCHES=(
  "x5_m60i:x5/m60i"
  "x6_m60i:x6/m60i"
  "x7_m60i:x7/m60i"
  "xm:xm"
  "760:760i-xdrive"
)
if ! $SKIP_AUTOTRADER; then
  echo
  echo "▶ [2/4] Autotrader (5 searches)"
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
  echo; echo "⊘ [2/4] Autotrader (skipped)"
fi

# name → cars.com model/trim query
CARS_SEARCHES=(
  "x5_m60i:models[]=bmw-x5&trims[]=bmw-x5-m60i"
  "x6_m60i:models[]=bmw-x6&trims[]=bmw-x6-m60i"
  "x7_m60i:models[]=bmw-x7&trims[]=bmw-x7-m60i"
  "xm:models[]=bmw-xm"
  "760:models[]=bmw-760"
)
if ! $SKIP_CARS_COM; then
  echo
  echo "▶ [3/4] Cars.com (5 searches)"
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
  echo; echo "⊘ [3/4] Cars.com (skipped)"
fi

# name → truecar mmt value
TC_SEARCHES=(
  "x5_m60i:bmw_x5_m60i"
  "x6_m60i:bmw_x6_m60i"
  "x7_m60i:bmw_x7_m60i"
  "xm:bmw_xm"
  "760:bmw_7-series_760i"
)
if ! $SKIP_TRUECAR; then
  echo
  echo "▶ [4/4] TrueCar (5 searches)"
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
