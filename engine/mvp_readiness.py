"""MVP readiness checks, provider/connector preflight, and OpenClaw scans."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from config import get_settings
from engine.agent_presets import get_agent_preset_manager
from engine.connectors import get_connector_definition
from engine.providers import get_provider_definition
from engine.state_store import get_state_store


def _required_missing(fields: list[dict[str, Any]], config: dict[str, str]) -> list[str]:
    return [
        str(field.get("key") or "")
        for field in fields
        if field.get("required") and not str(config.get(str(field.get("key") or ""), "")).strip()
    ]


def _probe_url(url: str, timeout_seconds: float = 1.5) -> tuple[bool, str]:
    try:
        with urlopen(url, timeout=timeout_seconds) as response:  # noqa: S310 - operator-supplied health probe
            return True, f"reachable ({getattr(response, 'status', 200)})"
    except URLError as exc:
        return False, str(exc.reason or exc)
    except Exception as exc:  # pragma: no cover - defensive
        return False, str(exc)


def check_provider(provider_type: str, config: dict[str, str]) -> dict[str, Any]:
    """Return a lightweight readiness verdict for a provider config."""

    definition = get_provider_definition(provider_type)
    if definition is None:
        raise ValueError(f"Unsupported provider type: {provider_type}")
    missing = _required_missing(list(definition.get("fields") or []), config)
    if missing:
        return {
            "status": "incomplete",
            "summary": f"Missing required fields: {', '.join(missing)}.",
            "missing_fields": missing,
            "notes": [],
            "live_check_performed": False,
        }

    normalized = str(definition["provider_type"])
    notes: list[str] = []
    live_check_performed = False
    if normalized == "ollama":
        base_url = str(config.get("base_url") or "http://localhost:11434").rstrip("/")
        probe_ok, probe_note = _probe_url(base_url.replace("/v1", "") + "/api/tags")
        live_check_performed = True
        if probe_ok:
            return {
                "status": "ready",
                "summary": f"Ollama endpoint looks reachable at {base_url}.",
                "missing_fields": [],
                "notes": [probe_note],
                "live_check_performed": True,
            }
        return {
            "status": "attention",
            "summary": f"Ollama config is saved, but {base_url} did not answer.",
            "missing_fields": [],
            "notes": [probe_note],
            "live_check_performed": True,
        }
    if normalized == "google":
        notes.append("Config shape looks valid.")
        notes.append("Gemini uses the OpenAI-compatible Google endpoint in the proxy.")
        return {
            "status": "ready",
            "summary": f"{definition['name']} is configured.",
            "missing_fields": [],
            "notes": notes,
            "live_check_performed": live_check_performed,
        }

    if normalized == "codex-subscription":
        cli_command = str(config.get("cli_command") or "codex").strip() or "codex"
        cli_path = shutil.which(cli_command)
        live_check_performed = True
        if cli_path:
            return {
                "status": "ready",
                "summary": f"Codex CLI found at {cli_path}.",
                "missing_fields": [],
                "notes": [
                    "This checks local CLI availability only.",
                    "A full subscription-backed execution bridge is still a separate runtime path.",
                ],
                "live_check_performed": True,
            }
        return {
            "status": "attention",
            "summary": f"Could not find `{cli_command}` on PATH.",
            "missing_fields": [],
            "notes": [
                "Install/sign in to Codex CLI first.",
                "Lumin still needs a dedicated execution bridge for this provider.",
            ],
            "live_check_performed": True,
        }

    notes.append("Config shape looks valid.")
    notes.append("This preflight does not yet perform a live upstream auth handshake.")
    return {
        "status": "ready",
        "summary": f"{definition['name']} is configured.",
        "missing_fields": [],
        "notes": notes,
        "live_check_performed": live_check_performed,
    }


def check_connector(connector_type: str, config: dict[str, str]) -> dict[str, Any]:
    """Return a lightweight readiness verdict for a connector config."""

    definition = get_connector_definition(connector_type)
    if definition is None:
        raise ValueError(f"Unsupported connector type: {connector_type}")
    missing = _required_missing(list(definition.get("fields") or []), config)
    if missing:
        return {
            "status": "incomplete",
            "summary": f"Missing required fields: {', '.join(missing)}.",
            "missing_fields": missing,
            "notes": [],
            "live_check_performed": False,
        }
    notes = ["Config shape looks valid."]
    notes.append("This preflight verifies saved setup, not a full live connector handshake yet.")
    return {
        "status": "ready",
        "summary": f"{definition['name']} is configured.",
        "missing_fields": [],
        "notes": notes,
        "live_check_performed": False,
    }


def scan_openclaw_source(source_path: str) -> dict[str, Any]:
    """Inspect a local path and report whether it looks like an OpenClaw install."""

    source = Path(source_path).expanduser()
    exists = source.exists()
    result = {
        "source_path": str(source),
        "exists": exists,
        "detected": False,
        "kind": "unknown",
        "signals": [],
        "notes": [],
    }
    if not exists:
        result["notes"].append("Path does not exist.")
        return result
    signals: list[str] = []
    for candidate in (
        "openclaw.json",
        "auth-profiles.json",
        "agents",
        "workspace",
        "channels",
        ".openclaw",
        "control-ui",
    ):
        if (source / candidate).exists():
            signals.append(candidate)
    if (source / "package.json").exists():
        try:
            package_text = (source / "package.json").read_text(encoding="utf-8", errors="ignore")
        except OSError:
            package_text = ""
        if "openclaw" in package_text.lower():
            signals.append("package.json:openclaw")
    result["signals"] = signals
    if signals:
        result["detected"] = True
        result["kind"] = "openclaw"
        result["notes"].append("This path looks like an OpenClaw workspace/state root.")
        if "workspace" not in signals and "agents" not in signals:
            result["notes"].append("Preset import may work, but full workspace/session migration is likely incomplete.")
    else:
        result["notes"].append("No strong OpenClaw markers found.")
    return result


def get_mvp_readiness() -> dict[str, Any]:
    """Summarize the current MVP readiness based on product gaps from research."""

    store = get_state_store()
    settings = get_settings()
    providers = store.list_providers()
    connectors = store.list_connectors()
    agents = store.list_agents()
    presets = get_agent_preset_manager().list_presets()

    configured_provider_types = {
        item["provider_type"] for item in providers if str(item.get("status")) == "configured"
    }
    configured_connector_types = {
        item["connector_type"] for item in connectors if str(item.get("status")) == "configured"
    }
    online_agents = [agent for agent in agents if str(agent.get("status")) == "online"]
    executable_provider_types = configured_provider_types & {"openai", "anthropic", "openrouter", "google", "ollama"}
    mvp_connector_types = configured_connector_types & {"telegram", "slack", "notion"}

    items = [
        {
            "key": "desktop_agent",
            "title": "Desktop agent online",
            "priority": "must",
            "status": "done" if online_agents else "missing",
            "summary": "Dashboard chat depends on an active local NanoClaw desktop agent.",
            "action": "Register and keep one local desktop agent online.",
        },
        {
            "key": "provider_execution",
            "title": "Real provider execution",
            "priority": "must",
            "status": "done" if executable_provider_types else "missing",
            "summary": "At least one executable provider should be configured for real traffic.",
            "action": "Finish and enable OpenRouter or Ollama end-to-end first.",
        },
        {
            "key": "connector_execution",
            "title": "Real connector execution",
            "priority": "must",
            "status": "done" if mvp_connector_types else "missing",
            "summary": "At least one messaging or knowledge connector should be truly usable.",
            "action": "Ship Telegram or Slack plus Notion as first-class end-to-end integrations.",
        },
        {
            "key": "migration_story",
            "title": "OpenClaw migration story",
            "priority": "must",
            "status": "partial" if presets else "missing",
            "summary": "Preset import exists, but full state/workspace/session migration still needs polish.",
            "action": "Add an explicit OpenClaw import flow with scan, mapping, and migration notes.",
        },
        {
            "key": "first_run_setup",
            "title": "First-run setup and doctor",
            "priority": "must",
            "status": "done",
            "summary": "A product-facing readiness report should exist before launch.",
            "action": "Keep this checklist visible and add provider/connector live probes over time.",
        },
        {
            "key": "warm_runtime",
            "title": "Warm NanoClaw runtime",
            "priority": "should",
            "status": "missing",
            "summary": "Cold start overhead is still a major latency source for control chat.",
            "action": "Reuse a warm NanoClaw runtime between requests.",
        },
        {
            "key": "workspace_defaults",
            "title": "Friendly workspace defaults",
            "priority": "should",
            "status": "missing",
            "summary": "Users should not need to understand internal mount paths.",
            "action": "Default repo requests to the host app path users actually mean.",
        },
        {
            "key": "mental_model",
            "title": "Simpler user mental model",
            "priority": "should",
            "status": "missing",
            "summary": "Groups, presets, providers, connectors, and modes still feel like too many concepts for MVP.",
            "action": "Collapse the UX to a smaller set of primary concepts.",
        },
    ]

    must_items = [item for item in items if item["priority"] == "must"]
    done_must = sum(1 for item in must_items if item["status"] == "done")
    score = round((done_must / max(len(must_items), 1)) * 100, 1)
    return {
        "score_pct": score,
        "headline": "Close, but not ready to replace OpenClaw without friction yet."
        if score < 100
        else "Core MVP blockers are covered.",
        "items": items,
        "facts": {
            "configured_provider_types": sorted(configured_provider_types),
            "configured_connector_types": sorted(configured_connector_types),
            "online_agent_count": len(online_agents),
            "preset_count": len(presets),
            "nanoclaw_root_exists": Path(settings.nanoclaw_root).exists(),
        },
    }


def get_doctor_report() -> dict[str, Any]:
    """Return a simple operational doctor report for first-run setup."""

    store = get_state_store()
    settings = get_settings()
    runtime = store.get_runtime_preferences()
    providers = store.list_providers()
    connectors = store.list_connectors()
    checks: list[dict[str, str]] = []

    online_agents = [agent for agent in store.list_agents() if str(agent.get("status")) == "online"]
    checks.append(
        {
            "key": "desktop_agent",
            "status": "ok" if online_agents else "warn",
            "summary": "Desktop agent",
            "detail": "At least one local desktop agent is online."
            if online_agents
            else "No online desktop agent detected.",
        }
    )
    checks.append(
        {
            "key": "nanoclaw_root",
            "status": "ok" if Path(settings.nanoclaw_root).exists() else "fail",
            "summary": "NanoClaw root",
            "detail": settings.nanoclaw_root,
        }
    )
    active_provider = runtime.get("active_provider", "auto")
    provider_ok = False
    if active_provider == "auto":
        provider_ok = bool(
            settings.openai_api_key
            or settings.anthropic_api_key
            or settings.google_api_key
            or store.get_provider_config("openrouter")
            or store.get_provider_config("ollama")
        )
    elif active_provider == "openai":
        provider_ok = bool(settings.openai_api_key)
    elif active_provider == "openrouter":
        provider_ok = bool(store.get_provider_config("openrouter"))
    elif active_provider == "anthropic":
        provider_ok = bool(settings.anthropic_api_key)
    elif active_provider == "google":
        provider_ok = bool(settings.google_api_key or store.get_provider_config("google"))
    elif active_provider == "ollama":
        provider_ok = bool(store.get_provider_config("ollama") or settings.ollama_base_url)
    checks.append(
        {
            "key": "active_provider",
            "status": "ok" if provider_ok else "warn",
            "summary": f"Active provider: {active_provider}",
            "detail": "Configured providers: "
            + (", ".join(item["provider_type"] for item in providers if item.get("status") == "configured") or "none"),
        }
    )
    checks.append(
        {
            "key": "workspace_hint",
            "status": "ok" if runtime.get("workspace_root_hint") else "warn",
            "summary": "Workspace root hint",
            "detail": str(runtime.get("workspace_root_hint") or ""),
        }
    )
    checks.append(
        {
            "key": "connectors",
            "status": "ok" if any(item.get("status") == "configured" for item in connectors) else "warn",
            "summary": "Configured connectors",
            "detail": ", ".join(item["connector_type"] for item in connectors if item.get("status") == "configured") or "none",
        }
    )
    mobile_clients = store.list_mobile_clients()
    checks.append(
        {
            "key": "app_connector",
            "status": "ok" if mobile_clients else "warn",
            "summary": "Lumin app connector",
            "detail": ", ".join(client["device_name"] for client in mobile_clients) or "No paired app devices.",
        }
    )
    overall_status = "ok"
    if any(check["status"] == "fail" for check in checks):
        overall_status = "fail"
    elif any(check["status"] == "warn" for check in checks):
        overall_status = "warn"
    return {"overall_status": overall_status, "checks": checks}
