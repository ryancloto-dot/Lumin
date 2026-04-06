#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$ROOT_DIR/lumin.pid"
PORT="${LUMIN_PORT:-}"

if [ -z "$PORT" ] && [ -f "$ROOT_DIR/.env" ]; then
  PORT="$(grep '^LUMIN_PORT=' "$ROOT_DIR/.env" | cut -d'=' -f2 || true)"
fi
PORT="${PORT:-8000}"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "✅ Lumin running (PID $(cat "$PID_FILE"))"
  if command -v curl >/dev/null 2>&1; then
    if curl -fsS "http://127.0.0.1:${PORT}/health" | python3 -m json.tool; then
      exit 0
    fi
    echo "⚠️  Process exists but healthcheck failed on port ${PORT}"
  fi
else
  echo "❌ Lumin not running"
fi
