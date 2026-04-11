# Lumin — AI Cost Optimization

![Lumin mark](assets/branding/lumin-mark.svg)

![License](https://img.shields.io/badge/license-MIT-green) ![Python](https://img.shields.io/badge/python-3.10%2B-blue) ![Tests](https://img.shields.io/badge/tests-88-green) ![Version](https://img.shields.io/badge/version-0.1.0-black)

> Cut your AI agent costs locally.  
> One environment variable. No code changes.

## What it does

- Runs an OpenAI-compatible proxy in front of your agent so prompts can be compressed, cached, and routed before they hit the model.
- Detects profitable structured JSON arrays and can rewrite them into token-efficient TOON format before sending upstream.
- Shows `Would have spent $X -> Spent $Y` live, with request history, budget tracking, and cache stats.
- Works locally with real provider paths for OpenAI, Anthropic, Google Gemini, OpenRouter, and Ollama.

## Quick start (60 seconds)

### For OpenClaw / NanoClaw users

Tell your agent:

```text
Install Lumin from github.com/ryancloto-dot/Lumin.
Run bash setup.sh.
If you want a preview first, run bash setup.sh --dry-run.
Then point my agent at the local Lumin proxy.
```

Your agent should be able to handle the rest.

OpenClaw note:

- The current OpenClaw release expects `Node 22.14+`.
- We verified a local OpenClaw profile routed through Lumin using a custom OpenAI-compatible provider pointed at `http://127.0.0.1:8000/v1`.
- If the published npm bundle is flaky on your machine, try a clean local install or source checkout before assuming the Lumin proxy is the problem.

### Manual install

```bash
git clone https://github.com/ryancloto-dot/Lumin
cd lumin
./setup.sh
```

### One-line config

For OpenAI-compatible agents:

```bash
OPENAI_BASE_URL=http://localhost:8000/v1
```

For OpenClaw / Anthropic-compatible shim traffic:

```bash
ANTHROPIC_BASE_URL=http://localhost:8000/anthropic/main
```

For NanoClaw:

```bash
LUMIN_PROXY_URL=http://localhost:8000
```

## How it works

```text
Your Agent
    |
    v
  Lumin  ------------------>  OpenAI / Anthropic / Google / OpenRouter / Ollama
    |
    +--> silent compression
    +--> model routing
    +--> semantic caching
    +--> budget tracking
    |
    v
  "Saved $0.43"
```

## Benchmarks

Local savings vary a lot by workflow. The strongest wins today come from repeated context, cache hits, and route selection rather than one flat guaranteed percentage.

Current quick local compression pass on the public repo:

- `agentic_debug`: `97.2%`
- `code_review`: `44.0%`
- `repeated_context_loop`: `94.7%`
- `structured_export`: `76.7%` via formatting normalization + TOON on larger uniform JSON arrays
  - `399` tokens saved from formatting normalization
  - `261` tokens saved from TOON conversion
- `rag_research`: `95.2%`
- simple average across that pass: `81.5%`

That benchmark is intentionally small and workload-shaped. The honest read is:

- simple one-shot prompts still often have little to compress
- repeated-context loops are where Lumin is currently strongest
- you should treat benchmark numbers as workflow-dependent, not universal guarantees

## What you get

- Local self-hosted proxy
- Cost Oracle at `/v1/predict`
- Prompt compression with verification, chunking, TOON conversion, and static-context pruning
- Model routing
- Savings dashboard
- Works with OpenAI-compatible agents

## Dashboard

The dashboard is intentionally simple:

- Silent Savings hero
- 4 key stats
- Sparkline
- Live request feed
- Model routing breakdown
- Budget tracker
- Chat dock

Open it here after setup:

- `http://localhost:8000/dashboard?key=<your dashboard key>`

## Supported agents

Confirmed local integration paths:

- `OpenClaw`
- `NanoClaw`
- `Any OpenAI-compatible agent`

Notes:

- `OpenClaw` was verified locally through a custom OpenAI-compatible provider profile backed by Lumin.
- `NanoClaw` routing is wired, but a full live run still depends on NanoClaw channel setup plus Anthropic/OneCLI credential injection.

Likely to work via the OpenAI-compatible endpoint, but not explicitly verified here:

- `LangChain`
- `LlamaIndex`
- `AutoGen`
- `Paperclip`

## Supported providers

- `OpenAI`
- `Anthropic`
- `Google Gemini`
- `Ollama`
- `OpenRouter`

These are the providers with real proxy send paths today.

## Self-hosting

The fastest path is local:

```bash
./setup.sh
```

What `setup.sh` does:

- creates `venv`
- installs Python dependencies
- writes `.env`
- best-effort detects OpenClaw and NanoClaw
- configures local proxy env vars
- starts Lumin on port `8000`

Useful scripts:

```bash
./setup.sh --dry-run
./status.sh
./restart.sh
./stop.sh
```

## Configuration

Start from [`.env.example`](.env.example).

Most important settings:

```env
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
GOOGLE_API_KEY=
OLLAMA_BASE_URL=http://localhost:11434

LUMIN_TOON_ENABLED=true
LUMIN_DASHBOARD_KEY=lumin123
LUMIN_DAILY_BUDGET=10.00
LUMIN_MONTHLY_BUDGET=100.00
```

## Core API

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

## Testing

```bash
python3 -m compileall main.py
python3 -m compileall engine/ proxy/
python3 -m unittest discover -s tests -p 'test_*.py'
```

Current repo status:

- `88` Python unit tests discovered under `tests/`
- compression, cache, predictor, context compression, transpilation, proxy shim, and state-store coverage exist
- full-suite runtime can still be slow depending on your machine and installed test deps

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
