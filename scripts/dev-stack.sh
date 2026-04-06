#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT_DIR/.logs"
mkdir -p "$LOG_DIR"

LUMIN_LOG="$LOG_DIR/lumin.log"
NANOCLAW_LOG="$LOG_DIR/nanoclaw.log"
DESKTOP_AGENT_LOG="$LOG_DIR/desktop-agent.log"

: >"$LUMIN_LOG"
: >"$NANOCLAW_LOG"
: >"$DESKTOP_AGENT_LOG"

kill_stale_listener() {
  local port="$1"
  local pids
  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    echo "Clearing stale listener on port $port: $pids"
    kill $pids 2>/dev/null || true
    sleep 1
    pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
    if [[ -n "$pids" ]]; then
      echo "Force killing listener on port $port: $pids"
      kill -9 $pids 2>/dev/null || true
    fi
  fi
}

kill_stale_listener 8000

cleanup() {
  local exit_code=$?
  if [[ -n "${LUMIN_PID:-}" ]] && kill -0 "$LUMIN_PID" 2>/dev/null; then
    kill "$LUMIN_PID" 2>/dev/null || true
  fi
  if [[ -n "${NANOCLAW_PID:-}" ]] && kill -0 "$NANOCLAW_PID" 2>/dev/null; then
    kill "$NANOCLAW_PID" 2>/dev/null || true
  fi
  if [[ -n "${DESKTOP_AGENT_PID:-}" ]] && kill -0 "$DESKTOP_AGENT_PID" 2>/dev/null; then
    kill "$DESKTOP_AGENT_PID" 2>/dev/null || true
  fi
  wait "${LUMIN_PID:-}" 2>/dev/null || true
  wait "${NANOCLAW_PID:-}" 2>/dev/null || true
  wait "${DESKTOP_AGENT_PID:-}" 2>/dev/null || true
  exit "$exit_code"
}

trap cleanup EXIT INT TERM

echo "Starting Lumin..."
LUMIN_DEV_RELOAD=false LUMIN_ACCESS_LOG=false bash "$ROOT_DIR/scripts/dev-lumin.sh" >"$LUMIN_LOG" 2>&1 &
LUMIN_PID=$!

sleep 2

echo "Starting NanoClaw..."
bash "$ROOT_DIR/scripts/dev-nanoclaw.sh" >"$NANOCLAW_LOG" 2>&1 &
NANOCLAW_PID=$!

sleep 2

echo "Starting Desktop Agent..."
bash "$ROOT_DIR/scripts/dev-desktop-agent.sh" >"$DESKTOP_AGENT_LOG" 2>&1 &
DESKTOP_AGENT_PID=$!

echo
echo "Lumin stack is starting."
echo "Lumin log:     $LUMIN_LOG"
echo "NanoClaw log:  $NANOCLAW_LOG"
echo "Desktop log:   $DESKTOP_AGENT_LOG"
echo
echo "Open the dashboard at:"
echo "  http://127.0.0.1:8000/dashboard"
echo
echo "Press Ctrl+C to stop all services."
echo

tail -n 0 -F "$LUMIN_LOG" "$NANOCLAW_LOG" "$DESKTOP_AGENT_LOG" &
TAIL_PID=$!
wait "$LUMIN_PID" "$NANOCLAW_PID" "$DESKTOP_AGENT_PID" || true
kill "$TAIL_PID" 2>/dev/null || true
