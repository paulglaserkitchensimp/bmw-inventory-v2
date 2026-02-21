#!/usr/bin/env bash
# Copy the latest crawler results into the visualizer's public folder
SRC="../crawler/results.json"
DST="public/data/results.json"
mkdir -p public/data
if [ -f "$SRC" ]; then
  cp "$SRC" "$DST"
  echo "✓ Synced $SRC → $DST"
else
  echo "✗ $SRC not found — run the crawler first"
  exit 1
fi
