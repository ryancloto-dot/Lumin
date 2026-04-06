#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -d "venv" ]]; then
  echo "Missing Python virtualenv at $ROOT_DIR/venv" >&2
  echo "Create it first, then install requirements." >&2
  exit 1
fi

if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

UVICORN_ARGS=(main:app --host 0.0.0.0 --port 8000)

if [[ "${LUMIN_DEV_RELOAD:-true}" == "true" ]]; then
  UVICORN_ARGS+=(--reload)
fi

if [[ "${LUMIN_ACCESS_LOG:-true}" == "false" ]]; then
  UVICORN_ARGS+=(--no-access-log)
fi

exec "$ROOT_DIR/venv/bin/python" -m uvicorn "${UVICORN_ARGS[@]}"
