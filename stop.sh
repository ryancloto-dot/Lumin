#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$ROOT_DIR/lumin.pid"

if [ -f "$PID_FILE" ]; then
  PID="$(cat "$PID_FILE")"
  if kill "$PID" 2>/dev/null; then
    rm -f "$PID_FILE"
    echo "✅ Lumin stopped"
  else
    rm -f "$PID_FILE"
    echo "Lumin was not running"
  fi
else
  echo "Lumin not running"
fi
