#!/usr/bin/env bash
# Smoke test of the production compose stack (infra/docker-compose.prod.yml).
#
#   scripts/smoke_docker.sh            # build, start, test, tear down (volumes removed)
#   KEEP=1 scripts/smoke_docker.sh     # leave the stack running for inspection
#   SKIP_BUILD=1 scripts/smoke_docker.sh  # use an existing dmis-platform image
#
# Builds the platform image, starts postgres + redis + neo4j + qdrant + api (2 uvicorn workers) + 1 queue
# worker, creates an admin token, then runs scripts/smoke_api.py: health, upload a synthetic dataset, wait
# for the queued job, check the market endpoints. The web/caddy/scheduler services are not started.
# Needs: docker with the compose plugin, python3 (standard library only). Port: SMOKE_PORT (default 8105).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export SMOKE_PORT="${SMOKE_PORT:-8105}"
PROJECT="${SMOKE_PROJECT:-dmis-smoke}"
ENV_FILE="$(mktemp)"
cat > "$ENV_FILE" <<EOF
DMIS_DOMAIN=localhost
POSTGRES_PASSWORD=smoke-$(date +%s)
NEO4J_PASSWORD=smoke-neo4j-password
DIP_API_WORKERS=2
EOF
DC=(docker compose -p "$PROJECT" -f infra/docker-compose.prod.yml -f infra/docker-compose.smoke.yml --env-file "$ENV_FILE")
SERVICES=(postgres redis neo4j qdrant api worker)

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    echo "---- logs (api, worker) ----"
    "${DC[@]}" logs --no-color --tail 120 api worker || true
  fi
  if [ -z "${KEEP:-}" ]; then
    "${DC[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  fi
  rm -f "$ENV_FILE"
  exit "$status"
}
trap cleanup EXIT

echo "== docker compose config"
"${DC[@]}" config -q

if [ -z "${SKIP_BUILD:-}" ]; then
  echo "== build"
  "${DC[@]}" build api
fi

echo "== up: ${SERVICES[*]}"
"${DC[@]}" up -d --no-build "${SERVICES[@]}"

echo "== wait for the API"
for _ in $(seq 1 100); do
  if curl -fsS "http://127.0.0.1:${SMOKE_PORT}/api/v2/health" >/dev/null 2>&1; then break; fi
  sleep 3
done

echo "== create an admin token (DIP_AUTH=on in this stack)"
TOKEN="$("${DC[@]}" exec -T api python scripts/dmis.py create-user smoke@example.com Smoke --role admin \
  | sed -n 's/^API token (shown once): //p')"
[ -n "$TOKEN" ] || { echo "no token"; exit 1; }

echo "== smoke flow"
python3 scripts/smoke_api.py --base "http://127.0.0.1:${SMOKE_PORT}" --token "$TOKEN" --timeout 900

echo "== worker processed the job"
"${DC[@]}" logs --no-color worker | grep -q "queued job .* done" && echo "ok   worker log shows the job"

echo "== backup (Qdrant server snapshots included)"
"${DC[@]}" exec -T api python scripts/dmis.py backup --out /tmp/smoke-backup
"${DC[@]}" exec -T api python - <<'PY'
import glob, json, tarfile
m = json.load(tarfile.open(sorted(glob.glob("/tmp/smoke-backup/*.tar.gz"))[-1]).extractfile("manifest.json"))
print("ok   backup manifest:", {k: m.get(k) if isinstance(m.get(k), str) else sorted(m.get(k) or {}) for k in ("postgres", "qdrant", "neo4j")})
assert isinstance(m.get("qdrant"), dict) and m["qdrant"], m.get("qdrant")
PY
echo "SMOKE (docker) PASSED"
