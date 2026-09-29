#!/usr/bin/env bash
# One-command local setup for the DMIS platform (macOS/Linux).
# Usage: bash setup_local.sh      (safe to re-run)
# Same steps as setup_local.bat: branch -> .venv -> deps -> engine data -> smoke tests -> platform.
set -euo pipefail
BRANCH=main
REPO=https://github.com/SylusZephyr/DMIS-.git
HERE="$(cd "$(dirname "$0")" && pwd)"

command -v git >/dev/null || { echo "[ERROR] git is not installed"; exit 1; }
PYTHON=$(command -v python3 || command -v python) || { echo "[ERROR] Python is not installed"; exit 1; }

if [ -d "$HERE/.git" ]; then
  cd "$HERE"
else
  [ -d "$HERE/DMIS/.git" ] || git clone "$REPO" "$HERE/DMIS"
  cd "$HERE/DMIS"
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "[STOP] You have uncommitted changes. Commit or stash them, then re-run."
  git status --short
  exit 1
fi
git fetch origin
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

[ -x .venv/bin/python ] || "$PYTHON" -m venv .venv
PY=.venv/bin/python
"$PY" -m pip install --upgrade pip >/dev/null
echo "Installing dependencies (first run takes a few minutes)..."
"$PY" -m pip install -e ".[dev,platform]"

echo "Building engine data from data/raw ..."
"$PY" scripts/run_engine.py --all-raw

echo "Running engine smoke tests..."
"$PY" -m pytest tests/engine -q || echo "[WARN] Some engine tests failed - see output above."

echo; echo "Setup complete. Starting the platform - press Ctrl+C to stop."
echo "Starting the platform (API + web app)..."
exec ./run_platform.sh
