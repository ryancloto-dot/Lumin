"""Deterministic prompt routing for toggleable cost optimization."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from config import RoutingStrategyName, get_settings
from engine.state_store import get_state_store
from engine.tokenizer import count_input_tokens

ComplexityBand = Literal["easy", "medium", "hard"]

_REASONING_RE = re.compile(
    r"\b(compare|analy[sz]e|debug|design|tradeoff|why|architecture|reason|investigate)\b",
    flags=re.IGNORECASE,
)
_RISK_RE = re.compile(
    r"\b(medical|legal|financial|security|compliance|contract|production|high[- ]stakes?)\b",
    flags=re.IGNORECASE,
)
_EASY_RE = re.compile(
    r"\b(extract|classify|summari[sz]e|rewrite|reformat|convert|json|label|tag)\b",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """A resolved routing outcome."""

    enabled: bool
    strategy: RoutingStrategyName
    requested_model: str
    routed_model: str
    complexity_band: ComplexityBand
    complexity_score: int
    reason: str


def _stringify_content(content: Any) -> str:
    """Convert message content into plain text."""

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text", "")))
            else:
                parts.append(str(part))
        return "\n".join(parts)
    return str(content)


def _message_text(messages: list[Any]) -> str:
    """Flatten messages into a single analyzable string."""

    parts: list[str] = []
    for message in messages:
        if isinstance(message, dict):
            role = str(message.get("role", ""))
            content = message.get("content")
        else:
            role = str(getattr(message, "role", ""))
            content = getattr(message, "content", "")
        parts.append(f"{role}: {_stringify_content(content)}")
    return "\n".join(parts)


def _complexity_score(messages: list[Any], requested_model: str) -> tuple[int, bool, str]:
    """Score a prompt for routing complexity and risk."""

    score = 0
    text = _message_text(messages)
    input_tokens = count_input_tokens(requested_model, messages)

    if input_tokens > 250:
        score += 1
    if input_tokens > 800:
        score += 2
    if len(messages) > 4:
        score += 1
    if len(messages) > 10:
        score += 2
    if "```" in text:
        score += 2
    if re.search(r"traceback|exception|stack trace|error:", text, re.IGNORECASE):
        score += 2
    if _REASONING_RE.search(text):
        score += 2
    if _EASY_RE.search(text):
        score -= 1

    high_risk = bool(_RISK_RE.search(text))
    if high_risk:
        score += 4

    score = max(score, 0)
    reason = f"tokens={input_tokens}, messages={len(messages)}, high_risk={'yes' if high_risk else 'no'}"
    return score, high_risk, reason


def _complexity_band(score: int, high_risk: bool) -> ComplexityBand:
    """Map complexity score to a routing band."""

    if high_risk or score >= 6:
        return "hard"
    if score >= 3:
        return "medium"
    return "easy"


def _provider_available(model: str) -> bool:
    """Check whether credentials exist for the model's provider."""

    settings = get_settings()
    store = get_state_store()
    if model.startswith("claude-"):
        anthropic_config = store.get_provider_config("anthropic")
        return bool(settings.anthropic_api_key or (anthropic_config and str(anthropic_config.get("api_key") or "").strip()))
    if model.startswith("gemini-"):
        google_config = store.get_provider_config("google")
        return bool(settings.google_api_key or (google_config and str(google_config.get("api_key") or "").strip()))
    if model.startswith("ollama/"):
        ollama_config = store.get_provider_config("ollama")
        return bool((ollama_config and str(ollama_config.get("base_url") or "").strip()) or settings.ollama_base_url)
    openai_config = store.get_provider_config("openai")
    if settings.openai_api_key or (openai_config and str(openai_config.get("api_key") or "").strip()):
        return True
    openrouter_config = store.get_provider_config("openrouter")
    return bool(openrouter_config and str(openrouter_config.get("api_key") or "").strip())


def _fallback_available_model(models: list[str], requested_model: str) -> str:
    """Choose the first credential-available model, else keep the request."""

    for model in models:
        if _provider_available(model):
            return model
    return requested_model


def _select_model(
    strategy: RoutingStrategyName,
    band: ComplexityBand,
    requested_model: str,
) -> str:
    """Select the routed model from the strategy ladder."""

    ladders: dict[RoutingStrategyName, dict[ComplexityBand, list[str]]] = {
        "economy": {
            "easy": ["gpt-5.4-nano", "claude-haiku-4-5"],
            "medium": ["claude-haiku-4-5", "gpt-5.4-mini"],
            "hard": ["gpt-5.4-mini", "claude-sonnet-4-6"],
        },
        "balanced": {
            "easy": ["gpt-5.4-mini", "claude-haiku-4-5"],
            "medium": ["claude-sonnet-4-6", "gpt-5.4-mini"],
            "hard": ["gpt-5.4", "claude-sonnet-4-6"],
        },
        "performance": {
            "easy": ["gpt-5.4-mini", "gpt-5.4"],
            "medium": ["gpt-5.4", "claude-sonnet-4-6"],
            "hard": ["claude-opus-4-6", "gpt-5.4"],
        },
    }
    return _fallback_available_model(ladders[strategy][band], requested_model)


def decide_route(
    requested_model: str,
    messages: list[Any],
    enabled: bool,
    strategy: RoutingStrategyName = "balanced",
) -> RoutingDecision:
    """Choose an effective model when routing is enabled."""

    if not enabled:
        return RoutingDecision(
            enabled=False,
            strategy=strategy,
            requested_model=requested_model,
            routed_model=requested_model,
            complexity_band="medium",
            complexity_score=0,
            reason="routing_disabled",
        )

    score, high_risk, detail = _complexity_score(messages, requested_model)
    band = _complexity_band(score, high_risk)
    routed_model = _select_model(strategy, band, requested_model)
    if routed_model == requested_model:
        reason = f"routing_enabled_kept_requested_model; band={band}; {detail}"
    else:
        reason = f"routing_enabled_selected_{routed_model}; band={band}; {detail}"
    return RoutingDecision(
        enabled=True,
        strategy=strategy,
        requested_model=requested_model,
        routed_model=routed_model,
        complexity_band=band,
        complexity_score=score,
        reason=reason,
    )
