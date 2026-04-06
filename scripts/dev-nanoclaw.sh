#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NANOCLAW_DIR="$ROOT_DIR/nanoclaw"

cd "$ROOT_DIR"

if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

cd "$NANOCLAW_DIR"

if [[ ! -d "node_modules" ]]; then
  echo "NanoClaw dependencies are missing in $NANOCLAW_DIR/node_modules" >&2
  echo "Run: cd $NANOCLAW_DIR && npm install" >&2
  exit 1
fi

export LUMIN_PROXY_URL="${LUMIN_PROXY_URL:-http://localhost:8000}"
export NANOCLAW_HEADLESS="${NANOCLAW_HEADLESS:-true}"

exec npm run dev
