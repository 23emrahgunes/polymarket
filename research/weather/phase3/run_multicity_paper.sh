#!/usr/bin/env bash
set -euo pipefail
ROOT="$HOME/polymarket"
DIR="$ROOT/research/weather/phase3"
SESSION="weatherpaper_multi"
LOG="$DIR/multicity_paper.log"

cd "$ROOT"
git pull --ff-only
cd "$DIR"
source ../phase1/.venv/bin/activate
python -m py_compile weather_multicity_paper.py multicity_status.py
mkdir -p phase3_out

echo "===== MULTI-CITY SMOKE ====="
python -u weather_multicity_paper.py --once

echo
echo "===== START MULTI-CITY 7/24 PAPER ====="
tmux kill-session -t "$SESSION" 2>/dev/null || true
tmux new-session -d -s "$SESSION" "bash -lc 'cd $DIR && source ../phase1/.venv/bin/activate && python -u weather_multicity_paper.py --interval 90 2>&1 | tee -a multicity_paper.log'"
sleep 5

echo "===== STATUS ====="
python multicity_status.py

echo
echo "===== TMUX ====="
tmux ls | grep "$SESSION" || true

echo
echo "===== LOG ====="
tail -n 80 "$LOG" 2>/dev/null || true

echo
echo "MULTI-CITY PAPER_ONLY daemon started."
echo "Status: cd $DIR && source ../phase1/.venv/bin/activate && python multicity_status.py"
echo "Report: cd $DIR && source ../phase1/.venv/bin/activate && python weather_multicity_paper.py --report"
echo "Log:    cd $DIR && tail -f multicity_paper.log"
