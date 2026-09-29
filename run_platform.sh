#!/usr/bin/env bash
# One-command: DMIS Intelligence Platform (macOS/Linux). Safe to re-run.
# Starts the API (:8000), the new interface v2 (:3001) and the classic interface (:3000).
# Optional keys (AI, live Amazon data, Chinese marketplaces): copy .env.example to .env and fill in what you have.
set -euo pipefail
cd "$(dirname "$0")"
command -v npm >/dev/null || { echo "[ERROR] Install Node.js 20+ from https://nodejs.org"; exit 1; }
PYTHON=$(command -v python3 || command -v python) || { echo "[ERROR] Install Python 3.11-3.12"; exit 1; }
if [ -f .env ]; then set -a; . ./.env; set +a; fi          # your keys, never committed
[ -x .venv/bin/python ] || "$PYTHON" -m venv .venv
PY=.venv/bin/python
"$PY" -m pip install -q -e ".[platform]"
[ -f data/platform/business.db ] || "$PY" scripts/dmis.py bootstrap
COMMIT=$(git rev-parse HEAD 2>/dev/null || echo unknown)

build() {   # install + build a web app when its dependencies or the code changed since the last build
  ( cd "$1"
    [ node_modules/.package-lock.json -nt package-lock.json ] || npm install
    [ -f .next/BUILD_ID ] && [ "$(cat .next/.dmis_commit 2>/dev/null)" = "$COMMIT" ] || { npm run build && echo "$COMMIT" > .next/.dmis_commit; } )
}
build frontend-v2
build frontend

"$PY" scripts/dmis.py serve --port 8000 &
PIDS=$!
trap 'kill $PIDS 2>/dev/null' EXIT
(cd frontend && npm run start -- -p 3000) &
PIDS="$PIDS $!"
echo
echo "DMIS is running:"
echo "  new interface (v2): http://localhost:3001"
echo "  classic interface:  http://localhost:3000"
echo "  API docs:           http://localhost:8000/docs"
echo "Press Ctrl+C to stop."
cd frontend-v2 && npm run start -- -p 3001
