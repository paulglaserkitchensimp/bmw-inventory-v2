#!/usr/bin/env bash
# 2025 X5 M60i / X5 M / X6 M60i / X6 M sweep — thin wrapper around search_x56m.sh.
# All flags pass through (--sync, --skip-fetch, --skip-vdp, --headless, …).
exec "$(dirname "$0")/search_x56m.sh" --year 2025 "$@"
