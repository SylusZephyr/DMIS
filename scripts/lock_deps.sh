#!/usr/bin/env bash
# Regenerate (or --check) the hashed Python lockfiles from pyproject.toml.
#
#   scripts/lock_deps.sh            # rewrite requirements/ci.txt and requirements/prod.txt
#   scripts/lock_deps.sh --upgrade  # also move every pin to the newest allowed version
#   scripts/lock_deps.sh --check    # fail if the lockfiles do not match pyproject.toml (CI)
#
# Needs uv (pip install uv). The locks are universal (any OS/arch) for Python 3.11, with hashes.
#   ci.txt   -> CI and developers who want the exact CI environment
#   prod.txt -> the Docker image (platform, platform-services, ml, observability)
set -euo pipefail
cd "$(dirname "$0")/.."

MODE="${1:-}"
EXTRA_ARGS=()
[ "$MODE" = "--upgrade" ] && EXTRA_ARGS+=(--upgrade)
COMMON=(--universal --python-version 3.11 --generate-hashes --quiet)
CI_EXTRAS=(--extra dev --extra platform --extra platform-services --extra api --extra ml --extra observability)
PROD_EXTRAS=(--extra platform --extra platform-services --extra ml --extra observability)

lock() {  # $1 = output file, rest = extras
  local out="$1"; shift
  uv pip compile pyproject.toml "${COMMON[@]}" "${EXTRA_ARGS[@]}" "$@" -o "$out"
}

if [ "$MODE" = "--check" ]; then
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  cp requirements/ci.txt "$tmp/ci.txt"; cp requirements/prod.txt "$tmp/prod.txt"
  # compile against the committed pins: only a changed pyproject.toml changes the output
  uv pip compile pyproject.toml "${COMMON[@]}" "${CI_EXTRAS[@]}" -o "$tmp/ci.txt"
  uv pip compile pyproject.toml "${COMMON[@]}" "${PROD_EXTRAS[@]}" -o "$tmp/prod.txt"
  strip() { grep -v '^#' "$1"; }
  if ! diff -q <(strip requirements/ci.txt) <(strip "$tmp/ci.txt") >/dev/null \
     || ! diff -q <(strip requirements/prod.txt) <(strip "$tmp/prod.txt") >/dev/null; then
    diff <(strip requirements/ci.txt) <(strip "$tmp/ci.txt") | head -40 || true
    echo "lockfiles are out of date: run scripts/lock_deps.sh and commit requirements/" >&2
    exit 1
  fi
  echo "lockfiles match pyproject.toml"
  exit 0
fi

mkdir -p requirements
lock requirements/ci.txt "${CI_EXTRAS[@]}"
lock requirements/prod.txt "${PROD_EXTRAS[@]}"
echo "wrote requirements/ci.txt and requirements/prod.txt"
