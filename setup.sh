#!/bin/bash
set -euo pipefail

LUMIN_VERSION="0.1.0"
LUMIN_PORT="${LUMIN_PORT:-8000}"
DRY_RUN=false

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    *)
      echo "Unknown option: $arg"
      echo "Usage: bash setup.sh [--dry-run]"
      exit 1
      ;;
  esac
done

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

say() { printf '%b\n' "$1"; }
run_cmd() {
  if [ "$DRY_RUN" = true ]; then
    say "${YELLOW}DRY RUN${NC} $*"
    return 0
  fi
  "$@"
}

write_file() {
  local target="$1"
  local content="$2"
  if [ "$DRY_RUN" = true ]; then
    say "${YELLOW}DRY RUN${NC} write $target"
    return 0
  fi
  printf '%s' "$content" > "$target"
}

backup_file_if_exists() {
  local target="$1"
  if [ -f "$target" ]; then
    run_cmd cp "$target" "${target}.bak"
  fi
}

replace_or_append_line() {
  local target="$1"
  local key="$2"
  local value="$3"
  if [ "$DRY_RUN" = true ]; then
    say "${YELLOW}DRY RUN${NC} set ${key}=... in $target"
    return 0
  fi
  mkdir -p "$(dirname "$target")"
  touch "$target"
  python3 - "$target" "$key" "$value" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]
lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
prefix = f"{key}="
updated = []
replaced = False
for line in lines:
    if line.startswith(prefix):
        if not replaced:
            updated.append(f"{key}={value}")
            replaced = True
    else:
        updated.append(line)
if not replaced:
    updated.append(f"{key}={value}")
path.write_text("\n".join(updated).rstrip() + "\n", encoding="utf-8")
PY
}

prompt_value() {
  local env_name="$1"
  local prompt_text="$2"
  local current_value="${!env_name:-}"
  if [ -n "$current_value" ]; then
    printf '%s' "$current_value"
    return 0
  fi
  if [ "$DRY_RUN" = true ] || [ ! -t 0 ]; then
    printf ''
    return 0
  fi
  read -r -p "$prompt_text" current_value
  printf '%s' "$current_value"
}

generate_hex() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 8
    return 0
  fi
  python3 - <<'PY'
import secrets
print(secrets.token_hex(8))
PY
}

find_first_existing() {
  local candidate
  for candidate in "$@"; do
    if [ -d "$candidate" ]; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  return 1
}

say ""
say "  🟢 LUMIN — AI Cost Optimization"
say "  Version $LUMIN_VERSION"
say ""

if ! command -v python3 >/dev/null 2>&1; then
  say "❌ Python 3 required. Install from python.org"
  exit 1
fi

PYTHON_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PYTHON_MINOR="$(python3 -c 'import sys; print(sys.version_info.minor)')"
if [ "$PYTHON_MINOR" -lt 10 ]; then
  say "❌ Python 3.10+ required"
  exit 1
fi
say "✅ Python $PYTHON_VERSION found"

if [ ! -d "venv" ]; then
  say "📦 Creating virtual environment..."
  run_cmd python3 -m venv venv
fi
if [ "$DRY_RUN" = false ]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
fi
say "✅ Virtual environment ready"

say "📦 Installing dependencies..."
if [ "$DRY_RUN" = true ]; then
  say "${YELLOW}DRY RUN${NC} venv/bin/python -m pip install -r requirements.txt"
else
  ./venv/bin/python -m pip install -r requirements.txt -q
fi
say "✅ Dependencies installed"

say ""
say "Provider API Keys (press Enter to skip any)"
OPENAI_KEY="$(prompt_value "OPENAI_API_KEY" "OpenAI API Key: ")"
ANTHROPIC_KEY="$(prompt_value "ANTHROPIC_API_KEY" "Anthropic API Key: ")"
GOOGLE_KEY="$(prompt_value "GOOGLE_API_KEY" "Google API Key (optional): ")"

NANOCLAW_PATH=""
OPENCLAW_PATH=""
NANOCLAW_FOUND=false
OPENCLAW_FOUND=false

say ""
say "🔍 Scanning for AI agents..."

if NANOCLAW_PATH="$(find_first_existing \
  "$ROOT_DIR/nanoclaw" \
  "$HOME/.nanoclaw" \
  "$HOME/nanoclaw" \
  "/opt/nanoclaw"
)"; then
  if [ -f "$NANOCLAW_PATH/package.json" ] || [ -f "$NANOCLAW_PATH/src/container-runner.ts" ]; then
    NANOCLAW_FOUND=true
    say "✅ NanoClaw found at $NANOCLAW_PATH"
  fi
fi

if OPENCLAW_PATH="$(find_first_existing \
  "$HOME/.openclaw" \
  "$HOME/openclaw" \
  "/opt/openclaw" \
  "$HOME/Library/Application Support/openclaw"
)"; then
  OPENCLAW_FOUND=true
  say "✅ OpenClaw found at $OPENCLAW_PATH"
fi

DASHBOARD_KEY="lumin_$(generate_hex)"
DESKTOP_SECRET="desktop_$(generate_hex)"
STATE_DB_PATH="$ROOT_DIR/data/lumin_state.db"
mkdir -p "$ROOT_DIR/data"

ENV_CONTENT="$(cat <<EOF
# Lumin Configuration
LUMIN_DASHBOARD_KEY=${DASHBOARD_KEY}
LUMIN_DESKTOP_SECRET=${DESKTOP_SECRET}
LUMIN_PORT=${LUMIN_PORT}

# Provider Keys
OPENAI_API_KEY=${OPENAI_KEY}
ANTHROPIC_API_KEY=${ANTHROPIC_KEY}
GOOGLE_API_KEY=${GOOGLE_KEY}
OLLAMA_BASE_URL=http://localhost:11434

# Defaults
LUMIN_DAILY_BUDGET=10.00
LUMIN_MONTHLY_BUDGET=100.00
LUMIN_ALERT_THRESHOLD=0.80
LUMIN_COMPRESSION_TIER=free
LUMIN_STATE_DB_PATH=${STATE_DB_PATH}
LUMIN_NANOCLAW_ROOT=${NANOCLAW_FOUND:+$NANOCLAW_PATH}
LUMIN_NANOCLAW_PROXY_URL=http://host.docker.internal:${LUMIN_PORT}
EOF
)"

say ""
say "📝 Writing local .env..."
backup_file_if_exists "$ROOT_DIR/.env"
write_file "$ROOT_DIR/.env" "$ENV_CONTENT"$'\n'
say "✅ Configuration saved"

CONFIGURED_AGENTS=0
if [ "$NANOCLAW_FOUND" = true ]; then
  say ""
  say "⚙️  Configuring NanoClaw..."
  backup_file_if_exists "$NANOCLAW_PATH/.env"
  replace_or_append_line "$NANOCLAW_PATH/.env" "LUMIN_PROXY_URL" "http://localhost:${LUMIN_PORT}"
  say "✅ NanoClaw configured to route through Lumin"
  CONFIGURED_AGENTS=$((CONFIGURED_AGENTS + 1))
fi

if [ "$OPENCLAW_FOUND" = true ]; then
  say ""
  say "⚙️  Configuring OpenClaw..."
  backup_file_if_exists "$OPENCLAW_PATH/.env"
  replace_or_append_line "$OPENCLAW_PATH/.env" "OPENAI_BASE_URL" "http://localhost:${LUMIN_PORT}/v1"
  replace_or_append_line "$OPENCLAW_PATH/.env" "ANTHROPIC_BASE_URL" "http://localhost:${LUMIN_PORT}/anthropic/main"
  say "✅ OpenClaw configured to route through Lumin"
  CONFIGURED_AGENTS=$((CONFIGURED_AGENTS + 1))
fi

if [ "$CONFIGURED_AGENTS" -eq 0 ]; then
  say "ℹ️  No agents detected. Manual config:"
  say ""
  say "  For NanoClaw:"
  say "  LUMIN_PROXY_URL=http://localhost:${LUMIN_PORT}"
  say ""
  say "  For OpenClaw / Anthropic-compatible agents:"
  say "  ANTHROPIC_BASE_URL=http://localhost:${LUMIN_PORT}/anthropic/main"
  say ""
  say "  For OpenAI-compatible agents:"
  say "  OPENAI_BASE_URL=http://localhost:${LUMIN_PORT}/v1"
fi

say ""
say "🚀 Starting Lumin..."
if [ "$DRY_RUN" = true ]; then
  say "${YELLOW}DRY RUN${NC} would stop anything on port ${LUMIN_PORT}"
  say "${YELLOW}DRY RUN${NC} would start uvicorn main:app --host 0.0.0.0 --port ${LUMIN_PORT}"
  say ""
  say "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  say ""
  say "  DRY RUN COMPLETE"
  say ""
  say "  Proxy:     http://localhost:${LUMIN_PORT}"
  say "  Dashboard: http://localhost:${LUMIN_PORT}/dashboard?key=${DASHBOARD_KEY}"
  say ""
  say "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  exit 0
fi

set -a
# shellcheck disable=SC1091
source "$ROOT_DIR/.env"
set +a

if command -v fuser >/dev/null 2>&1; then
  fuser -k "${LUMIN_PORT}/tcp" 2>/dev/null || true
elif command -v lsof >/dev/null 2>&1; then
  pids="$(lsof -ti tcp:${LUMIN_PORT} || true)"
  if [ -n "$pids" ]; then
    kill $pids 2>/dev/null || true
  fi
fi

nohup "$ROOT_DIR/venv/bin/uvicorn" main:app \
  --host 127.0.0.1 \
  --port "${LUMIN_PORT}" \
  --log-level warning \
  > "$ROOT_DIR/lumin.log" 2>&1 &

LUMIN_PID=$!
printf '%s\n' "$LUMIN_PID" > "$ROOT_DIR/lumin.pid"
sleep 2

if curl -fsS "http://127.0.0.1:${LUMIN_PORT}/health" >/dev/null; then
  say "✅ Lumin running on port ${LUMIN_PORT}"
else
  say "❌ Lumin failed to start. Check lumin.log"
  exit 1
fi

say ""
say "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
say ""
say "  🟢 LUMIN IS RUNNING"
say ""
say "  Proxy:     http://localhost:${LUMIN_PORT}"
say "  Dashboard: http://localhost:${LUMIN_PORT}/dashboard?key=${DASHBOARD_KEY}"
say "  Settings:  http://localhost:${LUMIN_PORT}/settings?key=${DASHBOARD_KEY}"
say ""
if [ "$CONFIGURED_AGENTS" -gt 0 ]; then
  say "  $CONFIGURED_AGENTS agent(s) configured."
  say "  Restart your agent to start saving."
  say ""
fi
say "  To stop:  ./stop.sh"
say "  Logs:     tail -f lumin.log"
say ""
say "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
