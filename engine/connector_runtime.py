"""Live connector checks and simple execution helpers."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from engine.connectors import get_connector_definition
from engine.state_store import get_state_store


def _required_missing(fields: list[dict[str, Any]], config: dict[str, str]) -> list[str]:
    return [
        str(field.get("key") or "")
        for field in fields
        if field.get("required") and not str(config.get(str(field.get("key") or ""), "")).strip()
    ]


def _telegram_api(token: str, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    try:
        with urlopen(request, timeout=8.0) as response:  # noqa: S310 - fixed Telegram API host
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram API returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Telegram API failed: {exc.reason}") from exc


def check_connector_runtime(connector_type: str, config: dict[str, str]) -> dict[str, Any]:
    """Run a live-ish readiness check for a connector."""

    definition = get_connector_definition(connector_type)
    if definition is None:
        raise ValueError(f"Unsupported connector type: {connector_type}")

    if connector_type == "app":
        clients = get_state_store().list_mobile_clients()
        if clients:
            return {
                "status": "ready",
                "summary": f"{len(clients)} paired app device(s) connected.",
                "missing_fields": [],
                "notes": [", ".join(client["device_name"] for client in clients[:3])],
                "live_check_performed": True,
            }
        return {
            "status": "attention",
            "summary": "No paired mobile app devices found.",
            "missing_fields": [],
            "notes": ["Pair a phone from the Lumin app to make this connector active."],
            "live_check_performed": True,
        }

    missing = _required_missing(list(definition.get("fields") or []), config)
    if missing:
        return {
            "status": "incomplete",
            "summary": f"Missing required fields: {', '.join(missing)}.",
            "missing_fields": missing,
            "notes": [],
            "live_check_performed": False,
        }

    if connector_type == "telegram":
        payload = _telegram_api(str(config.get("bot_token") or "").strip(), "getMe")
        if payload.get("ok"):
            result = payload.get("result") or {}
            username = result.get("username") or result.get("first_name") or "bot"
            notes = [f"Telegram bot reachable as {username}."]
            if str(config.get("default_chat_id") or "").strip():
                notes.append("Default chat ID is configured, so test sends are available.")
            else:
                notes.append("Add a default chat ID to send test messages from the dashboard.")
            return {
                "status": "ready",
                "summary": "Telegram Bot API authentication succeeded.",
                "missing_fields": [],
                "notes": notes,
                "live_check_performed": True,
            }
        return {
            "status": "attention",
            "summary": "Telegram Bot API did not return an OK response.",
            "missing_fields": [],
            "notes": [json.dumps(payload, sort_keys=True)],
            "live_check_performed": True,
        }

    return {
        "status": "ready",
        "summary": f"{definition['name']} is configured.",
        "missing_fields": [],
        "notes": ["Live execution is not implemented for this connector yet."],
        "live_check_performed": False,
    }


def send_test_message(connector_type: str, config: dict[str, str], text: str) -> dict[str, Any]:
    """Send a simple test message through a connector when supported."""

    if connector_type == "telegram":
        token = str(config.get("bot_token") or "").strip()
        chat_id = str(config.get("default_chat_id") or "").strip()
        if not token or not chat_id:
            raise ValueError("Telegram requires bot_token and default_chat_id for test sends.")
        payload = _telegram_api(
            token,
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True,
            },
        )
        if not payload.get("ok"):
            raise RuntimeError("Telegram sendMessage did not return ok=true.")
        result = payload.get("result") or {}
        return {
            "status": "sent",
            "summary": "Telegram test message sent.",
            "notes": [f"message_id={result.get('message_id', 'unknown')}"],
        }

    if connector_type == "app":
        clients = get_state_store().list_mobile_clients()
        if not clients:
            raise ValueError("No paired app devices are available.")
        return {
            "status": "ready",
            "summary": "Paired app devices are available.",
            "notes": ["The companion app currently uses the native Lumin chat/control path instead of a separate push test send."],
        }

    raise ValueError(f"Test sends are not implemented for connector type: {connector_type}")
