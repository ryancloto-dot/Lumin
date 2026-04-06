"""Built-in provider catalog and masking helpers."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_PROVIDERS: list[dict[str, Any]] = [
    {
        "provider_type": "openai",
        "name": "OpenAI API",
        "description": "Standard metered OpenAI API access.",
        "category": "API",
        "model_suggestions": [
            {"id": "gpt-5.2", "label": "GPT-5.2"},
            {"id": "gpt-5.2-mini", "label": "GPT-5.2 mini"},
            {"id": "gpt-5.2-codex", "label": "GPT-5.2 Codex"},
            {"id": "gpt-5.1", "label": "GPT-5.1"},
            {"id": "gpt-5.1-codex", "label": "GPT-5.1 Codex"},
            {"id": "gpt-5.1-codex-max", "label": "GPT-5.1 Codex Max"},
            {"id": "gpt-5.1-codex-mini", "label": "GPT-5.1 Codex mini"},
            {"id": "gpt-5", "label": "GPT-5"},
            {"id": "gpt-5-mini", "label": "GPT-5 mini"},
            {"id": "gpt-5-nano", "label": "GPT-5 nano"},
            {"id": "gpt-4.1", "label": "GPT-4.1"},
            {"id": "gpt-4.1-mini", "label": "GPT-4.1 mini"},
            {"id": "gpt-4.1-nano", "label": "GPT-4.1 nano"},
        ],
        "fields": [
            {"key": "api_key", "label": "API key", "kind": "password", "placeholder": "sk-...", "required": True, "secret": True},
            {"key": "base_url", "label": "Base URL", "kind": "text", "placeholder": "https://api.openai.com/v1", "required": False, "secret": False},
            {"key": "default_model", "label": "Default model", "kind": "text", "placeholder": "gpt-5.4-mini", "required": False, "secret": False},
        ],
    },
    {
        "provider_type": "anthropic",
        "name": "Anthropic API",
        "description": "Standard metered Anthropic API access.",
        "category": "API",
        "model_suggestions": [
            {"id": "claude-opus-4-1-20250805", "label": "Claude Opus 4.1"},
            {"id": "claude-opus-4-20250514", "label": "Claude Opus 4"},
            {"id": "claude-sonnet-4-20250514", "label": "Claude Sonnet 4"},
            {"id": "claude-3-7-sonnet-latest", "label": "Claude Sonnet 3.7 latest"},
            {"id": "claude-3-5-haiku-latest", "label": "Claude Haiku 3.5 latest"},
        ],
        "fields": [
            {"key": "api_key", "label": "API key", "kind": "password", "placeholder": "sk-ant-...", "required": True, "secret": True},
            {"key": "base_url", "label": "Base URL", "kind": "text", "placeholder": "https://api.anthropic.com/v1", "required": False, "secret": False},
            {"key": "default_model", "label": "Default model", "kind": "text", "placeholder": "claude-sonnet-4-6", "required": False, "secret": False},
        ],
    },
    {
        "provider_type": "openrouter",
        "name": "OpenRouter",
        "description": "Route OpenAI-compatible requests through OpenRouter.",
        "category": "API",
        "model_suggestions": [
            {"id": "openai/gpt-5.4-mini", "label": "OpenRouter: OpenAI GPT-5.4 mini"},
            {"id": "openai/gpt-5.4", "label": "OpenRouter: OpenAI GPT-5.4"},
            {"id": "anthropic/claude-sonnet-4-6", "label": "OpenRouter: Claude Sonnet 4.6"},
            {"id": "anthropic/claude-opus-4-6", "label": "OpenRouter: Claude Opus 4.6"},
            {"id": "google/gemini-2.5-pro", "label": "OpenRouter: Gemini 2.5 Pro"},
            {"id": "google/gemini-2.5-flash", "label": "OpenRouter: Gemini 2.5 Flash"},
        ],
        "fields": [
            {"key": "api_key", "label": "API key", "kind": "password", "placeholder": "sk-or-...", "required": True, "secret": True},
            {"key": "base_url", "label": "Base URL", "kind": "text", "placeholder": "https://openrouter.ai/api/v1", "required": False, "secret": False},
            {"key": "default_model", "label": "Default model", "kind": "text", "placeholder": "openai/gpt-5.4-mini", "required": False, "secret": False},
        ],
    },
    {
        "provider_type": "google",
        "name": "Google Gemini",
        "description": "Use Gemini models through Google's API or OpenAI-compatible shim.",
        "category": "API",
        "model_suggestions": [
            {"id": "gemini-2.5-pro", "label": "Gemini 2.5 Pro"},
            {"id": "gemini-2.5-flash", "label": "Gemini 2.5 Flash"},
            {"id": "gemini-2.0-flash", "label": "Gemini 2.0 Flash"},
        ],
        "fields": [
            {"key": "api_key", "label": "API key", "kind": "password", "placeholder": "AIza...", "required": True, "secret": True},
            {"key": "base_url", "label": "Base URL", "kind": "text", "placeholder": "https://generativelanguage.googleapis.com/v1beta/openai", "required": False, "secret": False},
            {"key": "default_model", "label": "Default model", "kind": "text", "placeholder": "gemini-2.5-flash", "required": False, "secret": False},
        ],
    },
    {
        "provider_type": "ollama",
        "name": "Ollama",
        "description": "Use local models through Ollama's OpenAI-compatible endpoint.",
        "category": "Local",
        "model_suggestions": [
            {"id": "ollama/llama3.2", "label": "Ollama: Llama 3.2"},
            {"id": "ollama/llama3.1", "label": "Ollama: Llama 3.1"},
            {"id": "ollama/gemma3", "label": "Ollama: Gemma 3"},
            {"id": "ollama/qwen3.5", "label": "Ollama: Qwen 3.5"},
            {"id": "ollama/qwen3-coder", "label": "Ollama: Qwen3 Coder"},
            {"id": "ollama/qwen2.5-coder", "label": "Ollama: Qwen2.5 Coder"},
            {"id": "ollama/gpt-oss:20b", "label": "Ollama: gpt-oss 20b"},
        ],
        "fields": [
            {"key": "base_url", "label": "Base URL", "kind": "text", "placeholder": "http://localhost:11434/v1", "required": True, "secret": False},
            {"key": "default_model", "label": "Default model", "kind": "text", "placeholder": "ollama/llama3.2", "required": False, "secret": False},
            {"key": "api_key", "label": "API key", "kind": "password", "placeholder": "Optional", "required": False, "secret": True},
        ],
    },
]


def list_provider_definitions() -> list[dict[str, Any]]:
    return deepcopy(_PROVIDERS)


def get_provider_definition(provider_type: str) -> dict[str, Any] | None:
    normalized = provider_type.strip().lower()
    for definition in _PROVIDERS:
        if definition["provider_type"] == normalized:
            return deepcopy(definition)
    return None


def mask_provider_config(provider_type: str, config: dict[str, Any]) -> dict[str, str]:
    definition = get_provider_definition(provider_type)
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
