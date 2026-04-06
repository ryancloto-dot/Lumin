"""OpenAI-compatible proxy endpoint implementation."""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Header, HTTPException, Response
from fastapi.responses import StreamingResponse

from config import RoutingStrategyName, get_model_pricing, get_provider_for_model, get_settings
from engine.cache import (
    get_budget_tracker,
    get_live_event_bus,
    get_request_ledger,
    get_savings_ledger,
    get_semantic_cache,
)
from engine.compressor import compress_messages
from engine.context_compressor import get_nanoclaw_context_compressor
from engine.router import RoutingDecision, decide_route
from engine.state_store import get_state_store
from engine.tokenizer import calculate_cost, count_input_tokens, count_openai_tokens
from engine.toon_converter import ToonConverter
from engine.transpiler import (
    TranspileDecodeError,
    verify_python_block,
    decode_pymin_to_python,
    estimate_spec_prompt_tokens,
    extract_pymin_blocks,
    inject_transpile_prompt,
    replace_with_python_blocks,
)
from engine.workflow_genome import WorkflowGenomeMatch, detect_workflow_genome
from experimental import apply_experiments, resolve_requested_experiments
from models.schemas import ChatCompletionRequest, ChatMessage, LiveEvent, RequestEntry, SavingsSnapshot

logger = logging.getLogger(__name__)
router = APIRouter(tags=["proxy"])
savings_ledger = get_savings_ledger()
semantic_cache = get_semantic_cache()
request_ledger = get_request_ledger()
live_event_bus = get_live_event_bus()
budget_tracker = get_budget_tracker()
nanoclaw_context_compressor = get_nanoclaw_context_compressor()
_PROXY_LOG_SOURCE = "chat_completions"
_INTERNAL_LOG_SOURCE = "internal_control"
_SHARED_HTTP_CLIENT: httpx.AsyncClient | None = None


def _get_shared_http_client() -> httpx.AsyncClient:
    """Return a reusable async client so upstream calls keep warm connections."""

    global _SHARED_HTTP_CLIENT
    settings = get_settings()
    if _SHARED_HTTP_CLIENT is None or _SHARED_HTTP_CLIENT.is_closed:
        client_kwargs = {
            "timeout": settings.request_timeout_seconds,
            "limits": httpx.Limits(
                max_connections=100,
                max_keepalive_connections=20,
                keepalive_expiry=30.0,
            ),
        }
        try:
            _SHARED_HTTP_CLIENT = httpx.AsyncClient(
                **client_kwargs,
                http2=True,
            )
        except ImportError:
            logger.warning(
                "httpx HTTP/2 extras are not installed; falling back to HTTP/1.1 upstream client",
            )
            _SHARED_HTTP_CLIENT = httpx.AsyncClient(**client_kwargs)
    return _SHARED_HTTP_CLIENT


async def close_shared_http_client() -> None:
    """Close the shared upstream HTTP client on app shutdown."""

    global _SHARED_HTTP_CLIENT
    if _SHARED_HTTP_CLIENT is not None and not _SHARED_HTTP_CLIENT.is_closed:
        await _SHARED_HTTP_CLIENT.aclose()
    _SHARED_HTTP_CLIENT = None


def _request_extras(request: ChatCompletionRequest) -> dict[str, Any]:
    """Return preserved request extras."""

    extras = request.model_extra or {}
    return extras if isinstance(extras, dict) else {}


def _extract_usage(payload: dict[str, Any]) -> tuple[int, int]:
    """Extract prompt and completion token counts from a provider response."""

    usage = payload.get("usage") or {}
    prompt_tokens = int(
        usage.get("prompt_tokens")
        or usage.get("input_tokens")
        or 0
    )
    completion_tokens = int(
        usage.get("completion_tokens")
        or usage.get("output_tokens")
        or 0
    )
    return prompt_tokens, completion_tokens


def _messages_to_openai_json(messages: list[Any]) -> list[dict[str, Any]]:
    """Serialize message models to a plain OpenAI-style payload."""

    serialized: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, dict):
            serialized.append({key: value for key, value in message.items() if value is not None})
        else:
            serialized.append(message.model_dump(exclude_none=True))
    return serialized


def _build_openai_request_body(
    request: ChatCompletionRequest,
    messages: list[Any],
    model: str,
) -> dict[str, Any]:
    """Build the outgoing OpenAI payload while preserving extra fields."""

    body = request.model_dump(exclude_none=True)
    body["model"] = model
    body["messages"] = _messages_to_openai_json(messages)
    if model.startswith("gpt-5"):
        if body.get("max_completion_tokens") is None and body.get("max_tokens") is not None:
            body["max_completion_tokens"] = body["max_tokens"]
        body.pop("max_tokens", None)
    for key in list(body):
        if key.startswith("lumin_"):
            body.pop(key, None)
    return body


def _split_anthropic_messages(messages: list[Any]) -> tuple[str | None, list[dict[str, Any]]]:
    """Convert OpenAI-style messages into Anthropic's system and messages format."""

    system_parts: list[str] = []
    converted: list[dict[str, Any]] = []

    for message in messages:
        role = message["role"] if isinstance(message, dict) else message.role
        content: Any = message["content"] if isinstance(message, dict) else message.content

        if role in {"system", "developer"}:
            if isinstance(content, str):
                system_parts.append(content)
            else:
                system_parts.append(json.dumps(content, sort_keys=True))
            continue

        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        converted.append(
            {
                "role": "assistant" if role == "assistant" else "user",
                "content": content,
            }
        )

    return ("\n\n".join(system_parts) if system_parts else None), converted


def _anthropic_to_openai_response(payload: dict[str, Any], model: str) -> dict[str, Any]:
    """Normalize an Anthropic response into OpenAI chat completion format."""

    text_parts = [
        block.get("text", "")
        for block in payload.get("content", [])
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    finish_reason = payload.get("stop_reason") or "stop"
    return {
        "id": payload.get("id", f"chatcmpl-{uuid.uuid4().hex}"),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "".join(text_parts),
                },
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": int(payload.get("usage", {}).get("input_tokens", 0)),
            "completion_tokens": int(payload.get("usage", {}).get("output_tokens", 0)),
            "total_tokens": int(payload.get("usage", {}).get("input_tokens", 0))
            + int(payload.get("usage", {}).get("output_tokens", 0)),
        },
    }


async def _call_openai(body: dict[str, Any]) -> dict[str, Any]:
    """Forward a chat completion request to OpenAI."""

    settings = get_settings()
    if not settings.openai_api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not configured.")

    return await _call_openai_compatible(
        body,
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key,
    )


async def _call_openai_compatible(
    body: dict[str, Any],
    *,
    base_url: str,
    api_key: str | None,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Forward a chat completion request to an OpenAI-compatible endpoint."""

    normalized_base_url = base_url.rstrip("/")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if extra_headers:
        headers.update(extra_headers)
    client = _get_shared_http_client()
    response = await client.post(
        f"{normalized_base_url}/chat/completions",
        headers=headers,
        json=body,
    )
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    return response.json()


async def _call_anthropic(
    model: str,
    request: ChatCompletionRequest,
    messages: list[Any],
    provider_config: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Forward a chat request to Anthropic and normalize the response."""

    settings = get_settings()
    config = provider_config or {}
    api_key = str(config.get("api_key") or settings.anthropic_api_key).strip()
    base_url = str(config.get("base_url") or settings.anthropic_base_url).strip()
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY is not configured.")

    system_prompt, anthropic_messages = _split_anthropic_messages(messages)
    body: dict[str, Any] = {
        "model": model,
        "messages": anthropic_messages,
        "max_tokens": request.max_completion_tokens or request.max_tokens or 1024,
    }
    if system_prompt:
        body["system"] = system_prompt
    if request.temperature is not None:
        body["temperature"] = request.temperature
    if request.top_p is not None:
        body["top_p"] = request.top_p

    headers = {
        "x-api-key": api_key,
        "anthropic-version": settings.anthropic_version,
        "content-type": "application/json",
    }
    client = _get_shared_http_client()
    response = await client.post(
        f"{base_url}/messages",
        headers=headers,
        json=body,
    )
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    return _anthropic_to_openai_response(response.json(), model)


def _build_savings_snapshot(
    pricing_model: str,
    original_prompt_tokens: int,
    sent_prompt_tokens: int,
    completion_tokens: int,
) -> SavingsSnapshot:
    """Compute savings metadata for the response."""

    original_cost_result = calculate_cost(pricing_model, original_prompt_tokens, completion_tokens)
    actual_cost_result = calculate_cost(pricing_model, sent_prompt_tokens, completion_tokens)
    saved_amount = max(original_cost_result.total_cost - actual_cost_result.total_cost, 0.0)
    return SavingsSnapshot(
        original_tokens=original_prompt_tokens,
        sent_tokens=sent_prompt_tokens,
        original_cost=round(original_cost_result.total_cost, 8),
        actual_cost=round(actual_cost_result.total_cost, 8),
        saved_amount=round(saved_amount, 8),
    )


def _convert_toon_response_payload(model: str, payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Convert any TOON content in assistant text back into JSON."""

    converter = ToonConverter(model)
    converted_blocks = 0
    choices = payload.get("choices")
    if not isinstance(choices, list):
        return payload, converted_blocks

    updated_payload = json.loads(json.dumps(payload))
    updated_choices = updated_payload.get("choices") or []
    for choice in updated_choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, str) or "[" not in content:
            continue
        converted = converter.convert_response(content)
        if converted != content:
            message["content"] = converted
            converted_blocks += 1
    return updated_payload, converted_blocks


def _anthropic_blocks_to_text(content: Any) -> str:
    """Collapse Anthropic block content into plain text."""

    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return json.dumps(content, sort_keys=True)

    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        elif isinstance(block, dict):
            parts.append(json.dumps(block, sort_keys=True))
        else:
            parts.append(str(block))
    return "\n".join(part for part in parts if part).strip()


def _resolve_anthropic_shim_model(requested_model: str) -> str:
    """Choose the actual model used behind the Anthropic compatibility shim."""

    settings = get_settings()
    model = requested_model.strip()
    if not model:
        return settings.chat_fallback_model

    try:
        provider = get_provider_for_model(model)
    except ValueError:
        return settings.chat_fallback_model

    if provider == "anthropic" and not settings.anthropic_api_key:
        return settings.chat_fallback_model
    if provider == "openai" and not settings.openai_api_key:
        return settings.chat_fallback_model
    return model


def _anthropic_messages_to_openai_messages(
    system_prompt: str | list[dict[str, Any]] | None,
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert Anthropic-style messages into OpenAI-style messages."""

    converted: list[dict[str, Any]] = []
    system_text = _anthropic_blocks_to_text(system_prompt) if system_prompt is not None else ""
    if system_text.strip():
        converted.append({"role": "system", "content": system_text})

    for message in messages:
        role = str(message.get("role", "user"))
        content = _anthropic_blocks_to_text(message.get("content", ""))
        converted.append(
            {
                "role": "assistant" if role == "assistant" else "user",
                "content": content,
            }
        )
    return converted


def _openai_to_anthropic_response(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert an OpenAI-style chat completion payload back into Anthropic shape."""

    choices = payload.get("choices") or []
    first_choice = choices[0] if choices else {}
    message = first_choice.get("message") or {}
    content = message.get("content") or ""
    usage = payload.get("usage") or {}
    finish_reason = first_choice.get("finish_reason") or "end_turn"
    return {
        "id": payload.get("id", f"msg_{uuid.uuid4().hex}"),
        "type": "message",
        "role": "assistant",
        "model": payload.get("model"),
        "content": [{"type": "text", "text": content}],
        "stop_reason": finish_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens", 0)),
            "output_tokens": int(usage.get("completion_tokens", 0)),
        },
    }


def _compression_stage_savings_pct(tier: str, compression: Any) -> tuple[float, float]:
    """Return coarse stage contribution percentages for dashboards."""

    return round(float(compression.savings_pct), 4), 0.0


def _record_request_event(
    *,
    source: str,
    request_id: str,
    model_used: str,
    snapshot: SavingsSnapshot,
    compression_tier: str,
    cache_hit: bool,
    cache_type: str,
    cache_score: float,
    routing_reason: str,
    latency_ms: int,
    transpile_saved_dollars: float,
    compression: Any,
    requested_model: str,
    workflow_genome: str,
    workflow_confidence: float,
    context_id: str | None = None,
    freshness_score: float = 1.0,
    pivot_detected: bool = False,
    cache_guard_reason: str = "",
    toon_conversions: int = 0,
    toon_tokens_saved: int = 0,
) -> None:
    """Persist request history and push a live dashboard event."""

    del transpile_saved_dollars
    would_have_spent = round(snapshot.original_cost, 8)
    actual_spent = 0.0 if cache_hit else round(snapshot.actual_cost, 8)
    total_saved = round(max(would_have_spent - actual_spent, 0.0), 8)
    entry = RequestEntry(
        id=request_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        model_requested=requested_model,
        model_used=model_used,
        original_tokens=snapshot.original_tokens,
        sent_tokens=snapshot.sent_tokens,
        savings_pct=round(
            ((snapshot.original_tokens - snapshot.sent_tokens) / snapshot.original_tokens) * 100,
            4,
        )
        if snapshot.original_tokens
        else 0.0,
        saved_dollars=total_saved,
        compression_tier=compression_tier,
        cache_hit=cache_hit,
        cache_type=cache_type,
        cache_score=round(cache_score, 6),
        routing_reason=routing_reason,
        latency_ms=latency_ms,
        actual_cost=actual_spent,
        would_have_cost=would_have_spent,
        verification_result=str(compression.compression_breakdown.get("verification_result", "skipped")),
        verification_fallback=str(compression.compression_breakdown.get("fallback_reason", "")) == "verification_failed",
        workflow_genome=workflow_genome,
        workflow_confidence=round(workflow_confidence, 4),
        source=source,
        context_id=context_id,
        freshness_score=round(freshness_score, 6),
        pivot_detected=pivot_detected,
        cache_guard_reason=cache_guard_reason,
        toon_conversions=int(toon_conversions),
        toon_tokens_saved=int(toon_tokens_saved),
    )
    request_ledger.add(entry)
    if source != _PROXY_LOG_SOURCE:
        return
    budget_tracker.add_spend(actual_spent)
    live_event_bus.publish(
        LiveEvent(
            type="request_complete",
            timestamp=entry.timestamp,
            model=requested_model,
            model_routed=model_used,
            saved_tokens=max(snapshot.original_tokens - snapshot.sent_tokens, 0),
            saved_dollars=total_saved,
            savings_pct=entry.savings_pct,
            cache_hit=cache_hit,
            compression_tier=compression_tier,
            latency_ms=latency_ms,
        )
    )


def _cache_payload(
    request: ChatCompletionRequest,
    messages: list[Any],
    model: str,
    enabled_experiments: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Create the cache key payload for a request."""

    body = request.model_dump(exclude_none=True)
    body["model"] = model
    body["messages"] = _messages_to_openai_json(messages)
    body.pop("stream", None)
    for key in list(body):
        if key.startswith("lumin_"):
            body.pop(key, None)
    if enabled_experiments:
        body["_lumin_experiments"] = list(enabled_experiments)
    return body


async def _resolve_compression_options(request: ChatCompletionRequest) -> tuple[str, bool, str | None]:
    """Resolve compression settings from request extras or environment defaults."""

    extras = _request_extras(request)
    tier = "free"
    verify = False
    context_id = extras.get("lumin_context_id")
    if context_id is not None:
        context_id = str(context_id)
    return tier, verify, context_id


def _resolve_internal_controls(request: ChatCompletionRequest) -> tuple[bool, bool]:
    """Return whether this request should skip semantic cache and dashboard logging."""

    extras = _request_extras(request)
    return bool(extras.get("lumin_disable_cache")), bool(extras.get("lumin_internal_request"))


def _request_context_id(request: ChatCompletionRequest) -> str | None:
    """Return the stable request context id when available."""

    extras = _request_extras(request)
    value = extras.get("lumin_context_id")
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _resolve_upstream_provider(
    request: ChatCompletionRequest,
    effective_model: str,
) -> tuple[str, str, dict[str, str]]:
    """Resolve the actual upstream provider and target model for this request."""

    extras = _request_extras(request)
    store = get_state_store()
    runtime = store.get_runtime_preferences()
    requested_provider = str(extras.get("lumin_provider") or runtime.get("active_provider") or "").strip().lower()
    openrouter_config = store.get_provider_config("openrouter") or {}
    openai_config = store.get_provider_config("openai") or {}
    anthropic_config = store.get_provider_config("anthropic") or {}
    google_config = store.get_provider_config("google") or {}
    ollama_config = store.get_provider_config("ollama") or {}
    if not requested_provider:
        default_provider = get_provider_for_model(effective_model)
        settings = get_settings()
        if (
            default_provider == "openai"
            and not str(openai_config.get("api_key") or settings.openai_api_key or "").strip()
            and str(openrouter_config.get("api_key") or "").strip()
        ):
            upstream_model = str(openrouter_config.get("default_model") or "").strip() or effective_model
            return "openrouter", upstream_model, openrouter_config
        if default_provider == "openai" and str(openai_config.get("api_key") or settings.openai_api_key or "").strip():
            upstream_model = str(openai_config.get("default_model") or "").strip() or effective_model
            return "openai", upstream_model, openai_config
        if default_provider == "anthropic" and str(anthropic_config.get("api_key") or settings.anthropic_api_key or "").strip():
            upstream_model = str(anthropic_config.get("default_model") or "").strip() or effective_model
            return "anthropic", upstream_model, anthropic_config
        if default_provider == "google":
            if str(google_config.get("api_key") or settings.google_api_key or "").strip():
                upstream_model = str(google_config.get("default_model") or "").strip() or effective_model
                return "google", upstream_model, google_config
        if default_provider == "ollama":
            if str(ollama_config.get("base_url") or settings.ollama_base_url or "").strip():
                upstream_model = str(ollama_config.get("default_model") or "").strip() or effective_model
                return "ollama", upstream_model, ollama_config
        return default_provider, effective_model, {}

    if requested_provider in {"", "auto"}:
        return get_provider_for_model(effective_model), effective_model, {}

    if requested_provider == "openai":
        upstream_model = str(openai_config.get("default_model") or "").strip() or effective_model
        return "openai", upstream_model, openai_config

    if requested_provider == "anthropic":
        upstream_model = str(anthropic_config.get("default_model") or "").strip() or effective_model
        return "anthropic", upstream_model, anthropic_config

    if requested_provider == "google":
        upstream_model = str(google_config.get("default_model") or "").strip() or effective_model
        return "google", upstream_model, google_config

    if requested_provider == "ollama":
        upstream_model = str(ollama_config.get("default_model") or "").strip() or effective_model
        return "ollama", upstream_model, ollama_config

    if requested_provider != "openrouter":
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported lumin_provider override: {requested_provider}",
        )

    if not openrouter_config:
        raise HTTPException(
            status_code=400,
            detail="OpenRouter is not configured in Lumin settings.",
        )
    upstream_model = str(openrouter_config.get("default_model") or "").strip() or effective_model
    return "openrouter", upstream_model, openrouter_config


def _maybe_compress_nanoclaw_context(
    model: str,
    messages: list[Any],
    tier: str,
    context_id: str | None,
) -> tuple[list[Any], dict[str, Any]]:
    """Compress NanoClaw-specific context files ahead of the normal pipeline."""

    if context_id is None:
        return messages, {
            "applied": False,
            "original_tokens": count_input_tokens(model, messages),
            "compressed_tokens": count_input_tokens(model, messages),
            "compression_breakdown": {"context_messages_compressed": 0},
        }

    has_nanoclaw_markers = False
    for message in messages:
        role = message["role"] if isinstance(message, dict) else getattr(message, "role", "")
        if role not in {"system", "developer"}:
            continue
        content = message["content"] if isinstance(message, dict) else getattr(message, "content", None)
        if not isinstance(content, str):
            continue
        lowered = content.lower()
        if any(marker in lowered for marker in ("claude.md", "skill.md", "memory.md", "# skills", "# memory")):
            has_nanoclaw_markers = True
            break

    original_tokens = count_input_tokens(model, messages)
    if not has_nanoclaw_markers:
        return messages, {
            "applied": False,
            "original_tokens": original_tokens,
            "compressed_tokens": original_tokens,
            "compression_breakdown": {"context_messages_compressed": 0},
        }

    result = nanoclaw_context_compressor.compress(
        model=model,
        messages=messages,
        tier=tier,
        context_id=context_id,
    )
    return result.compressed_messages, {
        "applied": result.applied,
        "original_tokens": result.original_tokens,
        "compressed_tokens": result.compressed_tokens,
        "compression_breakdown": result.compression_breakdown,
    }


def _resolve_routing_options(request: ChatCompletionRequest) -> tuple[bool, RoutingStrategyName]:
    """Resolve routing settings from request extras."""

    extras = request.model_extra or {}
    route_raw = extras.get("lumin_route")
    if route_raw is None:
        route = False
    elif isinstance(route_raw, bool):
        route = route_raw
    else:
        route = str(route_raw).strip().lower() in {"1", "true", "yes", "on"}

    strategy_raw = str(extras.get("lumin_strategy") or "balanced").lower()
    strategy: RoutingStrategyName = "balanced"
    if strategy_raw in {"economy", "balanced", "performance"}:
        strategy = strategy_raw  # type: ignore[assignment]
    return route, strategy


def _verification_header_value(verification_enabled: bool, verification_passed: bool) -> str:
    """Serialize verification state for response headers."""

    if not verification_enabled:
        return "skipped"
    return "pass" if verification_passed else "fail"


def _resolve_transpile_options(request: ChatCompletionRequest) -> tuple[bool, str]:
    """Resolve output transpilation settings from request extras."""

    extras = request.model_extra or {}
    enabled_raw = extras.get("lumin_transpile")
    if enabled_raw is None:
        enabled = False
    elif isinstance(enabled_raw, bool):
        enabled = enabled_raw
    else:
        enabled = str(enabled_raw).strip().lower() in {"1", "true", "yes", "on"}

    language = str(extras.get("lumin_transpile_lang") or "python").lower()
    if request.stream:
        return False, "none"
    if not enabled or language != "python":
        return False, "none"
    return True, "python"


def _message_text(message: Any) -> str:
    """Render message content into a stable text string for heuristics."""

    content = message["content"] if isinstance(message, dict) else message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            else:
                parts.append(json.dumps(item, sort_keys=True))
        return "\n".join(parts)
    if isinstance(content, dict):
        return json.dumps(content, sort_keys=True)
    return str(content)


def _should_attempt_transpile(model: str, messages: list[Any], language: str) -> tuple[bool, str]:
    """Estimate whether Python transpilation is likely to be net-positive."""

    if language != "python" or not messages:
        return False, "disabled"

    joined = "\n".join(_message_text(message) for message in messages if _message_text(message).strip())
    lowered = joined.lower()
    prompt_tokens = count_openai_tokens("gpt-4o-mini", messages)

    python_cues = (
        "python",
        "fastapi",
        "pydantic",
        "pytest",
        "unittest",
        "class ",
        "def ",
        "async def",
        "import ",
        "router",
    )
    generation_cues = (
        "write",
        "build",
        "implement",
        "create",
        "generate",
        "refactor",
        "add",
        "make",
        "return code",
        "output code",
    )
    repetitive_cues = (
        "boilerplate",
        "template",
        "crud",
        "schema",
        "model",
        "tests",
        "logging",
        "audit",
        "service",
        "repository",
        "transform",
        "serializer",
    )
    non_codegen_cues = (
        "explain",
        "what is",
        "why does",
        "summarize",
        "describe",
    )

    python_hits = sum(cue in lowered for cue in python_cues)
    generation_hits = sum(cue in lowered for cue in generation_cues)
    repetitive_hits = sum(cue in lowered for cue in repetitive_cues)
    non_codegen_hits = sum(cue in lowered for cue in non_codegen_cues)
    code_fence_hits = joined.count("```")

    if python_hits == 0 and generation_hits == 0 and code_fence_hits == 0:
        return False, "not_python_codegen"

    estimated_output_tokens = max(
        60,
        int(prompt_tokens * 0.8)
        + (110 * generation_hits)
        + (60 * python_hits)
        + (70 * repetitive_hits)
        + (25 * code_fence_hits)
        - (70 * non_codegen_hits),
    )
    expected_savings_rate = min(0.32, 0.04 + (0.03 * python_hits) + (0.05 * repetitive_hits))
    expected_saved_output_tokens = estimated_output_tokens * expected_savings_rate

    pricing = get_model_pricing(model)
    spec_cost = pricing.input_cost(estimate_spec_prompt_tokens())
    expected_saved_cost = pricing.output_cost(int(expected_saved_output_tokens))

    if estimated_output_tokens < 140:
        return False, "estimated_output_too_small"
    if expected_saved_cost <= spec_cost * 1.2:
        return False, "predicted_unprofitable"
    return True, "enabled"


def _transpile_headers(enabled: bool, language: str, status: str, saved_amount: float) -> dict[str, str]:
    """Return response headers describing transpilation behavior."""

    return {
        "X-Lumin-Transpile": "on" if enabled else "off",
        "X-Lumin-Transpile-Lang": language if enabled else "none",
        "X-Lumin-Transpile-Status": status,
        "X-Lumin-Transpile-Saved": f"{saved_amount:.8f}",
    }


def _transpile_meets_threshold(meta: dict[str, Any]) -> bool:
    """Return whether transpilation savings clear the configured minimum."""

    settings = get_settings()
    return (
        int(meta.get("saved_tokens", 0)) >= settings.transpile_min_saved_tokens
        and float(meta.get("saved_pct", 0.0)) >= settings.transpile_min_saved_pct
    )


def _routing_headers(decision: RoutingDecision) -> dict[str, str]:
    """Return response headers describing routing behavior."""

    return {
        "X-Lumin-Routing": "on" if decision.enabled else "off",
        "X-Lumin-Requested-Model": decision.requested_model,
        "X-Lumin-Routed-Model": decision.routed_model,
        "X-Lumin-Routing-Reason": decision.reason,
        "X-Lumin-Complexity-Band": decision.complexity_band,
        "X-Lumin-Complexity-Score": str(decision.complexity_score),
    }


def _workflow_genome_headers(match: WorkflowGenomeMatch) -> dict[str, str]:
    """Return response headers describing the detected workflow genome."""

    return {
        "X-Lumin-Workflow-Genome": match.genome,
        "X-Lumin-Genome-Confidence": f"{match.confidence:.2f}",
        "X-Lumin-Agentic-Workflow": "true" if match.agentic else "false",
    }


async def _send_upstream(
    provider: str,
    model: str,
    request: ChatCompletionRequest,
    messages: list[Any],
    provider_config: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Dispatch a request to the selected upstream provider."""

    if provider == "openai":
        config = provider_config or {}
        api_key = str(config.get("api_key") or "").strip()
        if api_key:
            return await _call_openai_compatible(
                _build_openai_request_body(request, messages, model),
                base_url=str(config.get("base_url") or get_settings().openai_base_url).strip(),
                api_key=api_key,
            )
        return await _call_openai(_build_openai_request_body(request, messages, model))
    if provider == "openrouter":
        config = provider_config or {}
        api_key = str(config.get("api_key") or "").strip()
        if not api_key:
            raise HTTPException(status_code=500, detail="OpenRouter API key is not configured.")
        base_url = str(config.get("base_url") or "https://openrouter.ai/api/v1").strip()
        return await _call_openai_compatible(
            _build_openai_request_body(request, messages, model),
            base_url=base_url,
            api_key=api_key,
            extra_headers={
                "HTTP-Referer": "https://lumin.local",
                "X-Title": "Lumin",
            },
        )
    if provider == "google":
        config = provider_config or {}
        api_key = str(config.get("api_key") or get_settings().google_api_key or "").strip()
        if not api_key:
            raise HTTPException(status_code=500, detail="GOOGLE_API_KEY is not configured.")
        base_url = str(config.get("base_url") or "https://generativelanguage.googleapis.com/v1beta/openai").strip()
        return await _call_openai_compatible(
            _build_openai_request_body(request, messages, model),
            base_url=base_url,
            api_key=api_key,
        )
    if provider == "ollama":
        config = provider_config or {}
        base_url = str(config.get("base_url") or get_settings().ollama_base_url).strip()
        api_key = str(config.get("api_key") or "").strip() or None
        outgoing_model = model.removeprefix("ollama/")
        return await _call_openai_compatible(
            _build_openai_request_body(request, messages, outgoing_model),
            base_url=base_url if base_url.endswith("/v1") else f"{base_url.rstrip('/')}/v1",
            api_key=api_key,
        )
    return await _call_anthropic(model, request, messages, provider_config)


async def _stream_openai_compatible(
    body: dict[str, Any],
    *,
    base_url: str,
    api_key: str | None,
    extra_headers: dict[str, str] | None = None,
) -> tuple[AsyncIterator[bytes], dict[str, Any]]:
    """Open an OpenAI-compatible SSE stream and expose raw upstream chunks."""

    normalized_base_url = base_url.rstrip("/")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if extra_headers:
        headers.update(extra_headers)
    client = _get_shared_http_client()
    request = client.build_request(
        "POST",
        f"{normalized_base_url}/chat/completions",
        headers=headers,
        json=body,
    )
    response = await client.send(request, stream=True)
    if response.status_code >= 400:
        detail = await response.aread()
        await response.aclose()
        raise HTTPException(status_code=response.status_code, detail=detail.decode("utf-8", errors="replace"))

    async def iter_bytes() -> AsyncIterator[bytes]:
        try:
            async for chunk in response.aiter_bytes():
                if chunk:
                    yield chunk
        finally:
            await response.aclose()

    return iter_bytes(), dict(response.headers)


async def _send_upstream_stream(
    provider: str,
    model: str,
    request: ChatCompletionRequest,
    messages: list[Any],
    provider_config: dict[str, str] | None = None,
) -> tuple[AsyncIterator[bytes], dict[str, Any]]:
    """Dispatch a streaming request to the selected upstream provider."""

    body = _build_openai_request_body(request, messages, model)
    body["stream"] = True
    body.setdefault("stream_options", {"include_usage": True})

    if provider == "openai":
        config = provider_config or {}
        api_key = str(config.get("api_key") or "").strip()
        if api_key:
            return await _stream_openai_compatible(
                body,
                base_url=str(config.get("base_url") or get_settings().openai_base_url).strip(),
                api_key=api_key,
            )
        settings = get_settings()
        if not settings.openai_api_key:
            raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not configured.")
        return await _stream_openai_compatible(
            body,
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
        )
    if provider == "openrouter":
        config = provider_config or {}
        api_key = str(config.get("api_key") or "").strip()
        if not api_key:
            raise HTTPException(status_code=500, detail="OpenRouter API key is not configured.")
        base_url = str(config.get("base_url") or "https://openrouter.ai/api/v1").strip()
        return await _stream_openai_compatible(
            body,
            base_url=base_url,
            api_key=api_key,
            extra_headers={
                "HTTP-Referer": "https://lumin.local",
                "X-Title": "Lumin",
            },
        )
    if provider == "google":
        config = provider_config or {}
        api_key = str(config.get("api_key") or get_settings().google_api_key or "").strip()
        if not api_key:
            raise HTTPException(status_code=500, detail="GOOGLE_API_KEY is not configured.")
        base_url = str(config.get("base_url") or "https://generativelanguage.googleapis.com/v1beta/openai").strip()
        return await _stream_openai_compatible(body, base_url=base_url, api_key=api_key)
    if provider == "ollama":
        config = provider_config or {}
        base_url = str(config.get("base_url") or get_settings().ollama_base_url).strip()
        api_key = str(config.get("api_key") or "").strip() or None
        body["model"] = model.removeprefix("ollama/")
        return await _stream_openai_compatible(
            body,
            base_url=base_url if base_url.endswith("/v1") else f"{base_url.rstrip('/')}/v1",
            api_key=api_key,
        )
    raise HTTPException(
        status_code=501,
        detail="Streaming is currently supported only for OpenAI-compatible upstream providers.",
    )
def _assistant_response_content(payload: dict[str, Any]) -> str | None:
    """Extract assistant content from an OpenAI-style response payload."""

    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    message = choices[0].get("message", {})
    content = message.get("content")
    return content if isinstance(content, str) else None


def _set_assistant_response_content(payload: dict[str, Any], content: str) -> None:
    """Replace assistant content inside an OpenAI-style response payload."""

    payload["choices"][0]["message"]["content"] = content


def _estimated_output_tokens(model: str, content: str) -> int:
    """Estimate output token count using the chat tokenizer on assistant content."""

    return count_input_tokens(model, [{"role": "assistant", "content": content}])


def _process_transpiled_output(model: str, payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Decode and verify `pymin` blocks in an OpenAI-style response."""

    content = _assistant_response_content(payload)
    if content is None:
        return payload, {
            "status": "disabled",
            "used": False,
            "saved_tokens": 0,
            "saved_pct": 0.0,
            "saved_amount": 0.0,
            "compressed_tokens": 0,
            "decoded_tokens": 0,
            "error": None,
        }

    blocks = extract_pymin_blocks(content)
    if not blocks:
        return payload, {
            "status": "disabled",
            "used": False,
            "saved_tokens": 0,
            "saved_pct": 0.0,
            "saved_amount": 0.0,
            "compressed_tokens": 0,
            "decoded_tokens": 0,
            "error": None,
        }

    decoded_blocks: list[str] = []
    for block in blocks:
        decoded = decode_pymin_to_python(block.compressed_code)
        if not verify_python_block(decoded):
            raise TranspileDecodeError("Decoded Python failed AST verification.")
        decoded_blocks.append(decoded)

    updated_payload = json.loads(json.dumps(payload))
    decoded_content = replace_with_python_blocks(content, decoded_blocks)
    _set_assistant_response_content(updated_payload, decoded_content)

    compressed_tokens = _estimated_output_tokens(model, content)
    decoded_tokens = _estimated_output_tokens(model, decoded_content)
    saved_tokens = max(decoded_tokens - compressed_tokens, 0)
    saved_pct = round((saved_tokens / decoded_tokens) * 100, 4) if decoded_tokens else 0.0
    saved_amount = round(
        max(
            calculate_cost(model, 0, decoded_tokens).total_cost
            - calculate_cost(model, 0, compressed_tokens).total_cost,
            0.0,
        ),
        8,
    )
    return updated_payload, {
        "status": "pass",
        "used": True,
        "saved_tokens": saved_tokens,
        "saved_pct": saved_pct,
        "saved_amount": saved_amount,
        "compressed_tokens": compressed_tokens,
        "decoded_tokens": decoded_tokens,
        "error": None,
    }


async def _handle_chat_completion(
    request: ChatCompletionRequest,
) -> Response:
    """Handle the shared Lumin chat completion flow."""

    started_at = time.perf_counter()
    request_id = f"req_{uuid.uuid4().hex}"

    try:
        pricing = get_model_pricing(request.model)
        del pricing
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    transpile_enabled, transpile_language = _resolve_transpile_options(request)
    enabled_experiments = resolve_requested_experiments(request)
    workflow_genome = detect_workflow_genome(request.messages)
    route_enabled, routing_strategy = _resolve_routing_options(request)
    routing_decision = decide_route(
        requested_model=request.model,
        messages=request.messages,
        enabled=route_enabled,
        strategy=routing_strategy,
    )
    effective_model = routing_decision.routed_model

    try:
        pricing = get_model_pricing(effective_model)
        del pricing
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    tier, verify, context_id = await _resolve_compression_options(request)
    disable_cache, internal_request = _resolve_internal_controls(request)
    log_source = _INTERNAL_LOG_SOURCE if internal_request else _PROXY_LOG_SOURCE
    context_messages, context_meta = _maybe_compress_nanoclaw_context(
        effective_model,
        request.messages,
        tier,
        context_id,
    )
    compression = await compress_messages(
        effective_model,
        context_messages,
        tier=tier,
        verify=verify,
        context_id=context_id,
    )
    overall_original_tokens = int(context_meta["original_tokens"])
    transpile_attempt, transpile_status = (
        _should_attempt_transpile(effective_model, compression.compressed_messages, transpile_language)
        if transpile_enabled
        else (False, "disabled")
    )
    upstream_messages = (
        inject_transpile_prompt(compression.compressed_messages)
        if transpile_attempt and transpile_language == "python"
        else compression.compressed_messages
    )
    provider, upstream_model, provider_config = _resolve_upstream_provider(
        request,
        effective_model,
    )
    outgoing_payload = _cache_payload(request, upstream_messages, effective_model, enabled_experiments)
    default_provider = get_provider_for_model(effective_model)
    cache_model_key = (
        effective_model
        if provider == default_provider
        else f"{provider}:{effective_model}"
    )

    if request.stream:
        stream_started_at = datetime.now(timezone.utc).isoformat()
        upstream_stream, upstream_headers = await _send_upstream_stream(
            provider,
            upstream_model,
            request,
            upstream_messages,
            provider_config,
        )
        transpile_status = "todo_streaming_disabled"
        usage_prompt_tokens = compression.compressed_tokens
        usage_completion_tokens = 0
        accumulated_content_parts: list[str] = []

        async def stream_generator() -> AsyncIterator[bytes]:
            nonlocal usage_prompt_tokens, usage_completion_tokens
            buffer = b""
            try:
                async for chunk in upstream_stream:
                    buffer += chunk
                    while b"\n" in buffer:
                        line_bytes, buffer = buffer.split(b"\n", 1)
                        line = line_bytes.decode("utf-8", errors="replace")
                        stripped = line.strip()
                        if stripped.startswith("data: "):
                            payload_text = stripped[6:].strip()
                            if payload_text == "[DONE]":
                                yield b"data: [DONE]\n\n"
                                continue
                            try:
                                payload = json.loads(payload_text)
                            except json.JSONDecodeError:
                                yield line_bytes + b"\n"
                                continue
                            choice = ((payload.get("choices") or [{}])[0]) if isinstance(payload, dict) else {}
                            delta = choice.get("delta") or {}
                            if isinstance(delta, dict):
                                content_delta = delta.get("content")
                                if isinstance(content_delta, str) and content_delta:
                                    accumulated_content_parts.append(content_delta)
                            usage = payload.get("usage") if isinstance(payload, dict) else None
                            if isinstance(usage, dict):
                                usage_prompt_tokens = int(
                                    usage.get("prompt_tokens")
                                    or usage.get("input_tokens")
                                    or usage_prompt_tokens
                                )
                                usage_completion_tokens = int(
                                    usage.get("completion_tokens")
                                    or usage.get("output_tokens")
                                    or usage_completion_tokens
                                )
                            yield line_bytes + b"\n"
                        else:
                            yield line_bytes + b"\n"
                    if buffer and buffer.endswith(b"\n\n"):
                        yield b"\n"
                        buffer = b""
            finally:
                if usage_completion_tokens == 0 and accumulated_content_parts:
                    usage_completion_tokens = count_openai_tokens(
                        "gpt-4o-mini",
                        [{"role": "assistant", "content": "".join(accumulated_content_parts)}],
                    )
                snapshot = _build_savings_snapshot(
                    effective_model,
                    overall_original_tokens,
                    usage_prompt_tokens,
                    usage_completion_tokens,
                )
                savings_ledger.record(effective_model, snapshot)
                latency_ms = int((time.perf_counter() - started_at) * 1000)
                _record_request_event(
                    source=log_source,
                    request_id=request_id,
                    model_used=effective_model,
                    snapshot=snapshot,
                    compression_tier=tier,
                    cache_hit=False,
                    cache_type="miss",
                    cache_score=0.0,
                    routing_reason=routing_decision.reason,
                    latency_ms=latency_ms,
                    transpile_saved_dollars=0.0,
                    compression=compression,
                    requested_model=request.model,
                    workflow_genome=workflow_genome.genome,
                    workflow_confidence=workflow_genome.confidence,
                    context_id=context_id,
                    toon_conversions=int(getattr(compression, "toon_conversions", 0)),
                    toon_tokens_saved=int(getattr(compression, "toon_tokens_saved", 0)),
                )

        return StreamingResponse(
            stream_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Lumin-Request-Id": request_id,
                "X-Lumin-Cache": "miss",
                "X-Lumin-Cache-Type": "miss",
                "X-Lumin-Cache-Score": "0.000000",
                "X-Lumin-Compression-Tier": tier,
                "X-Lumin-Verification": _verification_header_value(verify, compression.verification_passed),
                "X-Lumin-Verification-Result": str(compression.compression_breakdown.get("verification_result", "skipped")),
                "X-Lumin-Verification-Fallback": "true" if str(compression.compression_breakdown.get("fallback_reason", "")) == "verification_failed" else "false",
                **_transpile_headers(False, "none", transpile_status, 0.0),
                **_workflow_genome_headers(workflow_genome),
                **_routing_headers(routing_decision),
                "X-Lumin-Stream-Started-At": stream_started_at,
                "X-Lumin-Upstream-Content-Type": str(upstream_headers.get("content-type") or ""),
            },
        )

    cache_decision = None if disable_cache else semantic_cache.inspect(
        cache_model_key,
        outgoing_payload,
        increment_hits=True,
    )
    cache_match = cache_decision.match if cache_decision is not None else None
    cached = cache_match.entry if cache_match is not None else None

    if cached is not None:
        cached_transpile_status = str((cached.usage or {}).get("transpile_status", transpile_status))
        cached_transpile_saved = float((cached.usage or {}).get("transpile_saved_amount", 0.0))
        snapshot = _build_savings_snapshot(
            effective_model,
            overall_original_tokens,
            compression.compressed_tokens,
            int((cached.usage or {}).get("completion_tokens", 0)),
        )
        savings_ledger.record(effective_model, snapshot)
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        _record_request_event(
            source=log_source,
            request_id=request_id,
            model_used=effective_model,
            snapshot=snapshot,
            compression_tier=tier,
            cache_hit=True,
            cache_type="exact" if (cache_match.exact if cache_match else True) else "semantic",
            cache_score=(cache_match.score if cache_match else 1.0),
            routing_reason=routing_decision.reason,
            latency_ms=latency_ms,
            transpile_saved_dollars=cached_transpile_saved,
            compression=compression,
            requested_model=request.model,
            workflow_genome=workflow_genome.genome,
            workflow_confidence=workflow_genome.confidence,
            context_id=context_id,
            freshness_score=cache_match.freshness_score,
            pivot_detected=cache_match.pivot_detected,
            cache_guard_reason=cache_match.guard_reason,
            toon_conversions=int(getattr(compression, "toon_conversions", 0)),
            toon_tokens_saved=int(getattr(compression, "toon_tokens_saved", 0)),
        )
        return Response(
            content=json.dumps(cached.response),
            media_type="application/json",
            headers={
                "X-Lumin-Request-Id": request_id,
                "X-Lumin-Saved": f"{snapshot.saved_amount:.8f}",
                "X-Lumin-Stats": json.dumps(snapshot.model_dump(), separators=(",", ":")),
                "X-Lumin-Cache": "hit",
                "X-Lumin-Cache-Type": "exact" if (cache_match.exact if cache_match else True) else "semantic",
                "X-Lumin-Cache-Score": f"{(cache_match.score if cache_match else 1.0):.6f}",
                "X-Lumin-Compression-Tier": tier,
                "X-Lumin-TOON-Conversions": str(int(getattr(compression, "toon_conversions", 0))),
                "X-Lumin-TOON-Tokens-Saved": str(int(getattr(compression, "toon_tokens_saved", 0))),
                "X-Lumin-Verification": _verification_header_value(verify, compression.verification_passed),
                "X-Lumin-Verification-Result": str(compression.compression_breakdown.get("verification_result", "skipped")),
                "X-Lumin-Verification-Fallback": "true" if str(compression.compression_breakdown.get("fallback_reason", "")) == "verification_failed" else "false",
                "X-Lumin-Experiments": ",".join(enabled_experiments) if enabled_experiments else "none",
                "X-Lumin-Experiment-Status": "cached" if enabled_experiments else "none",
                **_transpile_headers(
                    transpile_enabled,
                    transpile_language,
                    cached_transpile_status,
                    cached_transpile_saved,
                ),
                **_routing_headers(routing_decision),
            },
        )

    upstream_response = await _send_upstream(
        provider,
        upstream_model,
        request,
        upstream_messages,
        provider_config,
    )
    upstream_response, response_toon_conversions = _convert_toon_response_payload(effective_model, upstream_response)
    transpile_saved_amount = 0.0
    transpile_usage_penalty_prompt = 0
    transpile_usage_penalty_completion = 0

    if transpile_attempt and transpile_language == "python":
        try:
            upstream_response, transpile_meta = _process_transpiled_output(effective_model, upstream_response)
            if transpile_meta["used"] and not _transpile_meets_threshold(transpile_meta):
                transpile_status = "below_threshold"
                transpile_saved_amount = 0.0
            else:
                transpile_status = str(transpile_meta["status"])
                transpile_saved_amount = float(transpile_meta["saved_amount"])
        except TranspileDecodeError as exc:
            first_prompt_tokens, first_completion_tokens = _extract_usage(upstream_response)
            transpile_usage_penalty_prompt += first_prompt_tokens
            transpile_usage_penalty_completion += first_completion_tokens
            logger.warning("Transpile decode failed, retrying without transpilation: %s", exc)
            transpile_status = "decode_fail"

            retry_response = await _send_upstream(
                provider,
                upstream_model,
                request,
                compression.compressed_messages,
                provider_config,
            )
            retry_content = _assistant_response_content(retry_response)
            if retry_content is not None and extract_pymin_blocks(retry_content):
                raise HTTPException(
                    status_code=502,
                    detail="Retry response still contained undecoded pymin blocks.",
                ) from exc
            upstream_response = retry_response
            transpile_status = "retry_fallback"
            transpile_saved_amount = 0.0

    experiment_headers: dict[str, str] = {}
    experiment_meta: dict[str, Any] = {}
    if enabled_experiments:
        upstream_response, experiment_headers, experiment_meta = apply_experiments(
            request=request,
            model=effective_model,
            provider=provider,
            response_payload=upstream_response,
            enabled_experiments=enabled_experiments,
        )

    prompt_tokens, completion_tokens = _extract_usage(upstream_response)
    prompt_tokens += transpile_usage_penalty_prompt
    completion_tokens += transpile_usage_penalty_completion
    if prompt_tokens == 0:
        prompt_tokens = compression.compressed_tokens
        upstream_response.setdefault("usage", {})
        upstream_response["usage"]["prompt_tokens"] = prompt_tokens
        upstream_response["usage"]["completion_tokens"] = completion_tokens
        upstream_response["usage"]["total_tokens"] = prompt_tokens + completion_tokens

    snapshot = _build_savings_snapshot(
        effective_model,
        overall_original_tokens,
        prompt_tokens,
        completion_tokens,
    )
    savings_ledger.record(effective_model, snapshot)
    if not disable_cache:
        semantic_cache.put(
            cache_model_key,
            outgoing_payload,
            upstream_response,
            usage={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "saved_amount": snapshot.saved_amount,
                "transpile_status": transpile_status,
                "transpile_saved_amount": transpile_saved_amount,
                "experiments": list(enabled_experiments),
                "toon_response_conversions": response_toon_conversions,
            },
        )
    latency_ms = int((time.perf_counter() - started_at) * 1000)
    _record_request_event(
        source=log_source,
        request_id=request_id,
        model_used=effective_model,
        snapshot=snapshot,
        compression_tier=tier,
        cache_hit=False,
        cache_type="miss",
        cache_score=0.0,
        routing_reason=routing_decision.reason,
        latency_ms=latency_ms,
        transpile_saved_dollars=transpile_saved_amount,
        compression=compression,
        requested_model=request.model,
        workflow_genome=workflow_genome.genome,
        workflow_confidence=workflow_genome.confidence,
        context_id=context_id,
        freshness_score=(cache_decision.freshness_score if cache_decision is not None else 1.0),
        pivot_detected=(cache_decision.pivot_detected if cache_decision is not None else False),
        cache_guard_reason=(cache_decision.guard_reason if cache_decision is not None else ""),
        toon_conversions=int(getattr(compression, "toon_conversions", 0)),
        toon_tokens_saved=int(getattr(compression, "toon_tokens_saved", 0)),
    )

    logger.info(
        "requested_model=%s routed_model=%s provider=%s original_tokens=%s sent_tokens=%s saved_amount=%.8f",
        request.model,
        effective_model,
        provider,
        overall_original_tokens,
        prompt_tokens,
        snapshot.saved_amount,
    )
    logger.info(
        "routing enabled=%s strategy=%s band=%s score=%s reason=%s",
        routing_decision.enabled,
        routing_decision.strategy,
        routing_decision.complexity_band,
        routing_decision.complexity_score,
        routing_decision.reason,
    )
    logger.info(
        "workflow_genome=%s confidence=%.2f agentic=%s notes=%s",
        workflow_genome.genome,
        workflow_genome.confidence,
        workflow_genome.agentic,
        workflow_genome.notes,
    )
    logger.info(
        "compression tier=%s verification=%s context_breakdown=%s breakdown=%s",
        tier,
        compression.verification_passed,
        context_meta["compression_breakdown"],
        compression.compression_breakdown,
    )
    logger.info(
        "transpile enabled=%s lang=%s status=%s saved_amount=%.8f",
        transpile_enabled,
        transpile_language,
        transpile_status,
        transpile_saved_amount,
    )
    if enabled_experiments:
        logger.info("experiments enabled=%s meta=%s", enabled_experiments, experiment_meta)

    return Response(
        content=json.dumps(upstream_response),
        media_type="application/json",
        headers={
            "X-Lumin-Request-Id": request_id,
            "X-Lumin-Saved": f"{snapshot.saved_amount:.8f}",
            "X-Lumin-Stats": json.dumps(snapshot.model_dump(), separators=(",", ":")),
            "X-Lumin-Cache": "miss",
            "X-Lumin-Cache-Type": "miss",
            "X-Lumin-Cache-Score": "0.000000",
            "X-Lumin-Compression-Tier": tier,
            "X-Lumin-TOON-Conversions": str(int(getattr(compression, "toon_conversions", 0))),
            "X-Lumin-TOON-Tokens-Saved": str(int(getattr(compression, "toon_tokens_saved", 0))),
            "X-Lumin-Verification": _verification_header_value(verify, compression.verification_passed),
            "X-Lumin-Verification-Result": str(compression.compression_breakdown.get("verification_result", "skipped")),
            "X-Lumin-Verification-Fallback": "true" if str(compression.compression_breakdown.get("fallback_reason", "")) == "verification_failed" else "false",
            **experiment_headers,
            **_transpile_headers(
                transpile_enabled,
                transpile_language,
                transpile_status,
                transpile_saved_amount,
            ),
            **_workflow_genome_headers(workflow_genome),
            **_routing_headers(routing_decision),
        },
    )


@router.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest) -> Response:
    """Handle OpenAI-compatible chat completion requests."""

    return await _handle_chat_completion(request)


@router.post("/anthropic/{context_id}/messages")
@router.post("/anthropic/{context_id}/v1/messages")
async def anthropic_messages_proxy(context_id: str, payload: dict[str, Any]) -> Response:
    """Anthropic-compatible shim for NanoClaw requests routed through Lumin."""

    requested_model = str(payload.get("model") or "")
    if not requested_model:
        raise HTTPException(status_code=400, detail="Anthropic payload missing model.")
    resolved_model = _resolve_anthropic_shim_model(requested_model)

    openai_request = ChatCompletionRequest.model_validate(
        {
            "model": resolved_model,
            "messages": _anthropic_messages_to_openai_messages(
                payload.get("system"),
                payload.get("messages") if isinstance(payload.get("messages"), list) else [],
            ),
            "max_tokens": payload.get("max_tokens"),
            "temperature": payload.get("temperature"),
            "top_p": payload.get("top_p"),
            "lumin_tier": "free",
            "lumin_verify": False,
            "lumin_context_id": context_id,
            "lumin_disable_cache": True,
            "lumin_internal_request": True,
        }
    )
    response = await _handle_chat_completion(openai_request)
    openai_payload = json.loads(response.body)
    openai_payload["model"] = requested_model
    anthropic_payload = _openai_to_anthropic_response(openai_payload)
    passthrough_headers = {
        key: value
        for key, value in dict(response.headers).items()
        if key.lower().startswith("x-lumin-")
    }
    return Response(
        content=json.dumps(anthropic_payload),
        media_type="application/json",
        status_code=response.status_code,
        headers=passthrough_headers,
    )


@router.head("/anthropic/{context_id}")
@router.get("/anthropic/{context_id}")
async def anthropic_messages_probe(context_id: str) -> Response:
    """Lightweight probe endpoint for Claude SDK base URL checks."""

    del context_id
    return Response(status_code=200)
