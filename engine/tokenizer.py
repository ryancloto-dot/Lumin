"""Tokenization and cost accounting utilities."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import tiktoken
from anthropic import Anthropic

from config import MODEL_PRICING, get_model_pricing, get_provider_for_model, get_settings
@dataclass(frozen=True, slots=True)
class TokenCountResult:
    """Token count and cost result for a single model."""

    model: str
    provider: str
    input_tokens: int
    projected_output_tokens: int
    input_cost: float
    output_cost: float

    @property
    def total_cost(self) -> float:
        """Return the full projected cost."""

        return self.input_cost + self.output_cost


def _stringify_content(content: Any) -> str:
    """Convert OpenAI-style message content into a stable text form."""

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        rendered_parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    rendered_parts.append(str(part.get("text", "")))
                else:
                    rendered_parts.append(json.dumps(part, sort_keys=True))
            else:
                rendered_parts.append(str(part))
        return "\n".join(rendered_parts)
    return json.dumps(content, sort_keys=True) if isinstance(content, dict) else str(content)


def _message_value(message: Any, key: str, default: Any = "") -> Any:
    """Read a field from either a dict-style or object-style message."""

    if isinstance(message, dict):
        return message.get(key, default)
    return getattr(message, key, default)


@lru_cache(maxsize=1)
def _anthropic_client() -> Anthropic | None:
    """Create a memoized Anthropic client when credentials are available."""

    settings = get_settings()
    if not settings.anthropic_api_key:
        return None
    return Anthropic(api_key=settings.anthropic_api_key)


def _anthropic_message_payload(messages: list[Any]) -> tuple[str | None, list[dict[str, Any]]]:
    """Convert OpenAI-style messages into Anthropic token-count payloads."""

    system_parts: list[str] = []
    anthropic_messages: list[dict[str, Any]] = []

    for message in messages:
        role = str(_message_value(message, "role", ""))
        content = _message_value(message, "content")

        if role in {"system", "developer"}:
            system_parts.append(_stringify_content(content))
            continue

        if isinstance(content, str):
            anthropic_content: Any = content
        else:
            anthropic_content = content

        anthropic_messages.append(
            {
                "role": "assistant" if role == "assistant" else "user",
                "content": anthropic_content,
            }
        )

    system_prompt = "\n\n".join(part for part in system_parts if part.strip()) or None
    return system_prompt, anthropic_messages


def render_messages_for_token_count(messages: list[Any]) -> str:
    """Render messages into a deterministic string suitable for token counting."""

    parts = [
        f"{_message_value(message, 'role')}:{_message_value(message, 'name') or ''}\n"
        f"{_stringify_content(_message_value(message, 'content'))}"
        for message in messages
    ]
    return "\n\n".join(parts)


def count_openai_tokens(model: str, messages: list[Any]) -> int:
    """Count tokens for an OpenAI model using tiktoken."""

    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        encoding = tiktoken.get_encoding("cl100k_base")
    rendered = render_messages_for_token_count(messages)
    return len(encoding.encode(rendered))


def count_anthropic_tokens(model: str, messages: list[Any]) -> int:
    """Count tokens for a Claude model.

    Prefer Anthropic's token count API when an API key is configured. Fall back
    to a deterministic local approximation if the API is unavailable.
    """

    client = _anthropic_client()
    if client is not None:
        try:
            system_prompt, anthropic_messages = _anthropic_message_payload(messages)
            request_kwargs: dict[str, Any] = {
                "model": model,
                "messages": anthropic_messages,
            }
            if system_prompt:
                request_kwargs["system"] = system_prompt
            response = client.messages.count_tokens(
                **request_kwargs,
            )
            return int(response.input_tokens)
        except Exception:
            pass

    rendered = render_messages_for_token_count(messages)
    encoding = tiktoken.get_encoding("cl100k_base")
    return len(encoding.encode(rendered))


def count_input_tokens(model: str, messages: list[Any]) -> int:
    """Count input tokens for a supported model."""

    provider = get_provider_for_model(model)
    if provider in {"openai", "google", "ollama"}:
        return count_openai_tokens(model, messages)
    return count_anthropic_tokens(model, messages)


def calculate_cost(model: str, input_tokens: int, output_tokens: int = 0) -> TokenCountResult:
    """Calculate projected costs for a model."""

    pricing = get_model_pricing(model)
    return TokenCountResult(
        model=model,
        provider=pricing.provider,
        input_tokens=input_tokens,
        projected_output_tokens=output_tokens,
        input_cost=pricing.input_cost(input_tokens),
        output_cost=pricing.output_cost(output_tokens),
    )


def count_and_price(
    model: str,
    messages: list[Any],
    projected_output_tokens: int = 0,
) -> TokenCountResult:
    """Count tokens and compute projected cost for a model."""

    input_tokens = count_input_tokens(model, messages)
    return calculate_cost(model, input_tokens, projected_output_tokens)


def supported_models() -> list[str]:
    """Return the supported model names."""

    return list(MODEL_PRICING.keys())
