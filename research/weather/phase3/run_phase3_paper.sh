#!/usr/bin/env bash
set -euo pipefail

ROOT="$HOME/polymarket"
DIR="$ROOT/research/weather/phase3"
SESSION="weatherpaper"
LOG="$DIR/phase3_paper.log"

cd "$ROOT"
git pull --ff-only
cd "$DIR"
source ../phase1/.venv/bin/activate
python -m py_compile weather_forward_paper.py

mkdir -p phase3_out

echo "===== PHASE 3 SMOKE ====="
python weather_forward_paper.py --once

echo
echo "===== STARTING 7/24 PAPER DAEMON ====="
tmux kill-session -t "$SESSION" 2>/dev/null || true
tmux new-session -d -s "$SESSION" "bash -lc 'cd $DIR && source ../phase1/.venv/bin/activate && python weather_forward_paper.py --interval 60 2>&1 | tee -a phase3_paper.log'"
sleep 3

echo "===== TMUX ====="
tmux ls | grep "$SESSION" || true

echo "===== LOG ====="
tail -n 60 "$LOG" 2>/dev/null || true

echo
echo "PAPER_ONLY daemon started."
echo "Live log: cd $DIR && tail -f phase3_paper.log"
echo "Report:   cd $DIR && source ../phase1/.venv/bin/activate && python weather_forward_paper.py --report"
