#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -d "venv" ]]; then
  echo "Missing Python virtualenv at $ROOT_DIR/venv" >&2
  exit 1
fi

if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

export LUMIN_BASE_URL="${LUMIN_BASE_URL:-http://127.0.0.1:8000}"

exec "$ROOT_DIR/venv/bin/python" "$ROOT_DIR/scripts/desktop_agent.py"
