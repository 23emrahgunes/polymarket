#!/usr/bin/env bash
set -euo pipefail
ROOT="$HOME/polymarket"
DIR="$ROOT/research/weather/phase3"
SESSION="weatherpaper_active"
LOG="$DIR/active_forecast_paper.log"

cd "$ROOT"
git pull --ff-only
cd "$DIR"
source ../phase1/.venv/bin/activate
python -m py_compile weather_active_forecast_paper.py
mkdir -p phase3_out

echo "===== ACTIVE FORECAST SMOKE ====="
python -u weather_active_forecast_paper.py --once

echo
echo "===== ACTIVE FORECAST REPORT ====="
python weather_active_forecast_paper.py --report

echo
echo "===== START ACTIVE FORECAST 7/24 PAPER ====="
tmux kill-session -t "$SESSION" 2>/dev/null || true
tmux new-session -d -s "$SESSION" "bash -lc 'cd $DIR && source ../phase1/.venv/bin/activate && python -u weather_active_forecast_paper.py --interval 120 2>&1 | tee -a active_forecast_paper.log'"
sleep 3

echo "===== TMUX ====="
tmux ls | grep "$SESSION" || true

echo
echo "===== LOG ====="
tail -n 80 "$LOG" 2>/dev/null || true

echo
echo "ACTIVE FORECAST PAPER_ONLY daemon started."
echo "Report: cd $DIR && source ../phase1/.venv/bin/activate && python weather_active_forecast_paper.py --report"
echo "Log:    cd $DIR && tail -f active_forecast_paper.log"
