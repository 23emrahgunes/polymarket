#!/usr/bin/env bash
set -euo pipefail
START="${1:-2026-03-01}"
END="${2:-2026-08-08}"
OUT="${3:-phase1_full_out}"
python3 phase1_full_runner.py \
  --start "$START" \
  --end "$END" \
  --out "$OUT" \
  --horizons 24,12,6,3,1 \
  --fidelity 5 \
  --max-staleness-min 90 \
  --bootstrap 600
