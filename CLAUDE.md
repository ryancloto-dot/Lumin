# CLAUDE.md

This repository is **Lumin**: a local-first AI cost optimization proxy with a small dashboard, a mobile/desktop control chat, and an OpenAI-compatible API surface.

## Core Product Path

The one path that matters for MVP is:

1. Connect one provider
2. Send chat through Lumin
3. See savings in the dashboard

Everything else is secondary.

## What Is Core

Core user-facing routes:
- `GET /health`
- `GET /dashboard`
- `GET /settings`
- `GET /api/stats`
- `GET /api/requests`
- `GET /api/budget`
- `POST /api/chat`
- `WS /ws/live`
- `POST /v1/chat/completions`
- `POST /v1/predict`
- `GET|POST /api/settings/providers`
- `DELETE /api/settings/providers/{provider_type}`
- `POST /api/pairing/code`
- `POST /api/pairing/claim`
- `POST /api/desktop/register`
- `POST /api/desktop/heartbeat`
- `POST /api/desktop/tasks/claim`
- `POST /api/desktop/tasks/{task_id}/started`
- `POST /api/desktop/tasks/{task_id}/result`

Advanced/admin routes are still present under `/api/advanced/*`.

## Real Provider List

Only these providers are real in the proxy send path:
- `openai`
- `openrouter`
- `anthropic`
- `google`
- `ollama`

Not real providers today:
- `glm`
- `codex-subscription`

## Layer Status

Built and in code:
- Free prompt compression
- TOON conversion for profitable structured JSON arrays
- NanoClaw context distillation
- Intelligent chunking
- Python transpilation
- Semantic cache
- Cost oracle
- OpenAI-compatible proxy
- Basic SSE streaming for OpenAI-compatible upstreams

Still partial:
- Anthropic streaming
- NanoClaw latency / warm runtime reuse
- Broad connector execution

## How Requests Flow

### Proxy
`POST /v1/chat/completions`

1. Validate model/pricing
2. Optionally compress NanoClaw context blocks
3. Compress prompt
4. Check semantic cache
5. Route to the resolved upstream provider
6. Return OpenAI-compatible response with savings headers

### Control Chat
`POST /api/chat`

Fast path only:
1. Prefer NanoClaw if bridge is available
2. Else use the desktop agent if one is online
3. Else fall back directly to Lumin

Each agent path is capped with a short timeout. Chat typing events are pushed over `/ws/live`.

## Commands

```bash
# Install Python deps
pip install -r requirements.txt

# Run the app
uvicorn main:app --reload --port 8000

# Compile-check key code
python3 -m compileall main.py
python3 -m compileall engine/ proxy/

# Run tests
python3 -m unittest discover -s tests -p 'test_*.py'
```

The repo currently has `88` Python unit tests discoverable under `tests/`.

Important note:
- some API tests are skipped if `fastapi` test dependencies are unavailable
- the full suite can still hang in this environment, so do not assume full green without checking output

## Environment / Important Config

Common env vars:
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `GOOGLE_API_KEY`
- `OPENAI_BASE_URL`
- `ANTHROPIC_BASE_URL`
- `OLLAMA_BASE_URL`
- `LUMIN_DASHBOARD_KEY`
- `LUMIN_DESKTOP_SECRET`
- `LUMIN_NANOCLAW_ROOT`
- `LUMIN_PROXY_URL`
- `LUMIN_TOON_ENABLED`
- `LUMIN_TOON_MIN_SAVINGS`

Pricing exists for:
- OpenAI GPT models
- Anthropic Claude models
- Google Gemini models
- `ollama/*` as zero-cost local models

## Current UI Shape

### Dashboard
Keep it focused on:
- silent savings hero
- saved / requests / cache / avg save
- sparkline
- live request feed
- model routing
- budget
- chat

### Settings
Settings moved out of the dashboard into `/settings`.

## Working Rules

- Never claim a provider works unless the proxy has a real send path for it.
- Never show non-core complexity in the main dashboard if it hides the chat/savings loop.
- Never log dashboard/settings polling as if it were a real model request.
- Never break OpenAI-compatible response format.
- Fail safe to original prompt if compression is uncertain.
