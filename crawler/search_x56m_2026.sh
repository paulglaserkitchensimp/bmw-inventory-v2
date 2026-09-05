#!/usr/bin/env bash
# ── DISABLED for the 2026 330i hunt ───────────────────────────────────────────
# This sweep targets a vehicle this fork is not shopping for (X5/X6/X7/XM/7
# Series). It is kept for reference only; the exec bit has been removed.
# To re-enable:  chmod +x <this file>   (or run it with: bash <this file>)
# See docs/330I_DEAL_FINDER.md § "What was disabled".
# ─────────────────────────────────────────────────────────────────────────────
# 2026 X5 M60i / X5 M / X6 M60i / X6 M sweep — thin wrapper around search_x56m.sh.
# All flags pass through (--sync, --skip-fetch, --skip-vdp, --headless, …).
exec "$(dirname "$0")/search_x56m.sh" --year 2026 "$@"
