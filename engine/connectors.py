"""Built-in connector catalog and masking helpers."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_CONNECTORS: list[dict[str, Any]] = [
    {
        "connector_type": "app",
        "name": "Lumin App",
        "description": "Paired iPhone/Android companion app for remote control and status.",
        "category": "Companion",
        "origin": "lumin",
        "popular": True,
        "supports_live_test": True,
        "supports_send_test": True,
        "fields": [],
    },
    {
        "connector_type": "slack",
        "name": "Slack",
        "description": "OpenClaw-compatible Slack workspace and channel connector.",
        "category": "Communication",
        "origin": "openclaw_compatible",
        "popular": True,
        "supports_live_test": False,
        "supports_send_test": False,
        "fields": [
            {"key": "workspace_name", "label": "Workspace name", "kind": "text", "placeholder": "Acme", "required": False, "secret": False},
            {"key": "bot_token", "label": "Bot token", "kind": "password", "placeholder": "xoxb-...", "required": True, "secret": True},
            {"key": "app_token", "label": "App token", "kind": "password", "placeholder": "xapp-...", "required": False, "secret": True},
            {"key": "signing_secret", "label": "Signing secret", "kind": "password", "placeholder": "Slack signing secret", "required": False, "secret": True},
            {"key": "default_channel", "label": "Default channel", "kind": "text", "placeholder": "#ops", "required": False, "secret": False},
        ],
    },
    {
        "connector_type": "notion",
        "name": "Notion",
        "description": "OpenClaw-compatible Notion workspace and database connector.",
        "category": "Knowledge",
        "origin": "openclaw_compatible",
        "popular": True,
        "supports_live_test": False,
        "supports_send_test": False,
        "fields": [
            {"key": "workspace_name", "label": "Workspace name", "kind": "text", "placeholder": "Personal HQ", "required": False, "secret": False},
            {"key": "integration_token", "label": "Integration token", "kind": "password", "placeholder": "secret_...", "required": True, "secret": True},
            {"key": "database_id", "label": "Database ID", "kind": "text", "placeholder": "32-char database id", "required": False, "secret": False},
            {"key": "default_parent_page", "label": "Parent page ID", "kind": "text", "placeholder": "Optional page id", "required": False, "secret": False},
        ],
    },
    {
        "connector_type": "discord",
        "name": "Discord",
        "description": "OpenClaw-compatible Discord server and channel connector.",
        "category": "Communication",
        "origin": "openclaw_compatible",
        "popular": True,
        "supports_live_test": False,
        "supports_send_test": False,
        "fields": [
            {"key": "server_name", "label": "Server name", "kind": "text", "placeholder": "Community", "required": False, "secret": False},
            {"key": "bot_token", "label": "Bot token", "kind": "password", "placeholder": "Discord bot token", "required": True, "secret": True},
            {"key": "application_id", "label": "Application ID", "kind": "text", "placeholder": "Discord application id", "required": False, "secret": False},
            {"key": "default_channel_id", "label": "Default channel ID", "kind": "text", "placeholder": "1234567890", "required": False, "secret": False},
        ],
    },
    {
        "connector_type": "telegram",
        "name": "Telegram",
        "description": "OpenClaw-compatible Telegram bot, chat, and channel connector.",
        "category": "Communication",
        "origin": "openclaw_compatible",
        "popular": True,
        "supports_live_test": True,
        "supports_send_test": True,
        "fields": [
            {"key": "bot_token", "label": "Bot token", "kind": "password", "placeholder": "123456:ABCDEF...", "required": True, "secret": True},
            {"key": "default_chat_id", "label": "Default chat ID", "kind": "text", "placeholder": "-1001234567890", "required": False, "secret": False},
            {"key": "bot_username", "label": "Bot username", "kind": "text", "placeholder": "@lumin_bot", "required": False, "secret": False},
        ],
    },
    {
        "connector_type": "whatsapp",
        "name": "WhatsApp",
        "description": "OpenClaw-compatible WhatsApp target for local NanoClaw messaging flows.",
        "category": "Communication",
        "origin": "openclaw_compatible",
        "popular": True,
        "supports_live_test": False,
        "supports_send_test": False,
        "fields": [
            {"key": "session_name", "label": "Session name", "kind": "text", "placeholder": "primary-phone", "required": False, "secret": False},
            {"key": "default_jid", "label": "Default chat JID", "kind": "text", "placeholder": "120363000000000000@g.us", "required": False, "secret": False},
            {"key": "phone_number", "label": "Phone number", "kind": "text", "placeholder": "+14165551234", "required": False, "secret": False},
        ],
    },
    {
        "connector_type": "gmail",
        "name": "Gmail",
        "description": "Save Gmail access details for inbox triage and outbound drafts.",
        "category": "Communication",
        "origin": "lumin",
        "popular": False,
        "supports_live_test": False,
        "supports_send_test": False,
        "fields": [
            {"key": "email_address", "label": "Email address", "kind": "text", "placeholder": "you@gmail.com", "required": False, "secret": False},
            {"key": "client_id", "label": "OAuth client ID", "kind": "password", "placeholder": "Google OAuth client id", "required": False, "secret": True},
            {"key": "client_secret", "label": "OAuth client secret", "kind": "password", "placeholder": "Google OAuth client secret", "required": False, "secret": True},
            {"key": "refresh_token", "label": "Refresh token", "kind": "password", "placeholder": "Google refresh token", "required": False, "secret": True},
        ],
    },
    {
        "connector_type": "matrix",
        "name": "Matrix",
        "description": "OpenClaw-compatible Matrix homeserver and room connector.",
        "category": "Communication",
        "origin": "openclaw_compatible",
        "popular": False,
        "supports_live_test": False,
        "supports_send_test": False,
        "fields": [
            {"key": "homeserver_url", "label": "Homeserver URL", "kind": "text", "placeholder": "https://matrix.org", "required": True, "secret": False},
            {"key": "access_token", "label": "Access token", "kind": "password", "placeholder": "Matrix access token", "required": True, "secret": True},
            {"key": "default_room_id", "label": "Default room ID", "kind": "text", "placeholder": "!roomid:matrix.org", "required": False, "secret": False},
            {"key": "user_id", "label": "User ID", "kind": "text", "placeholder": "@lumin:matrix.org", "required": False, "secret": False},
        ],
    },
    {
        "connector_type": "github",
        "name": "GitHub",
        "description": "Work with repositories, issues, PRs, and actions.",
        "category": "Developer",
        "origin": "lumin",
        "popular": False,
        "supports_live_test": False,
        "supports_send_test": False,
        "fields": [
            {"key": "owner", "label": "Owner or org", "kind": "text", "placeholder": "acme", "required": False, "secret": False},
            {"key": "repository", "label": "Default repository", "kind": "text", "placeholder": "lumin", "required": False, "secret": False},
            {"key": "personal_access_token", "label": "Personal access token", "kind": "password", "placeholder": "ghp_...", "required": True, "secret": True},
        ],
    },
]


def list_connector_definitions() -> list[dict[str, Any]]:
    """Return built-in connector definitions."""

    return deepcopy(_CONNECTORS)


def get_connector_definition(connector_type: str) -> dict[str, Any] | None:
    """Return a single built-in connector definition."""

    normalized = connector_type.strip().lower()
    for definition in _CONNECTORS:
        if definition["connector_type"] == normalized:
            return deepcopy(definition)
    return None


def mask_connector_config(connector_type: str, config: dict[str, Any]) -> dict[str, str]:
    """Return connector config with secrets masked for UI responses."""

    definition = get_connector_definition(connector_type)
    if definition is None:
        return {key: str(value) for key, value in config.items() if value is not None}

    secret_fields = {
        field["key"]
        for field in definition.get("fields", [])
        if field.get("secret")
    }
    masked: dict[str, str] = {}
    for key, value in config.items():
        if value in (None, ""):
            masked[key] = ""
        elif key in secret_fields:
            text = str(value)
            masked[key] = "•" * max(min(len(text), 12), 8)
        else:
            masked[key] = str(value)
    return masked
