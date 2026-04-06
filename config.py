"""Runtime configuration and pricing data for Lumin."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from dotenv import load_dotenv

load_dotenv()

LUMIN_VERSION = "0.1.0"

ProviderName = Literal["openai", "anthropic", "google", "ollama"]
RoutingStrategyName = Literal["economy", "balanced", "performance"]
ExperimentalFeatureName = Literal["cget_v0", "scaffold_fill_v0", "spe_v0"]

FILLER_PHRASES: tuple[str, ...] = (
    "As an AI language model,",
    "Certainly! I'd be happy to",
    "Of course! Let me",
    "Great question!",
    "I hope this helps!",
    "Please let me know if",
    "Feel free to ask",
    "Happy to help!",
    "Absolutely!",
    "Sure thing!",
)


@dataclass(frozen=True, slots=True)
class ModelPricing:
    """Per-million token pricing for a model."""

    provider: ProviderName
    input_per_million: float
    output_per_million: float

    def input_cost(self, tokens: int) -> float:
        """Calculate the input-side cost for a token count."""

        return (tokens / 1_000_000) * self.input_per_million

    def output_cost(self, tokens: int) -> float:
        """Calculate the output-side cost for a token count."""

        return (tokens / 1_000_000) * self.output_per_million


MODEL_PRICING: dict[str, ModelPricing] = {
    "gpt-5.4": ModelPricing("openai", 30.00, 180.00),
    "gpt-5.4-pro": ModelPricing("openai", 30.00, 180.00),
    "gpt-5.4-mini": ModelPricing("openai", 0.75, 4.50),
    "gpt-5.4-nano": ModelPricing("openai", 0.20, 1.25),
    "gpt-4o": ModelPricing("openai", 2.50, 10.00),
    "gpt-4o-mini": ModelPricing("openai", 0.15, 0.60),
    "claude-opus-4-6": ModelPricing("anthropic", 15.00, 75.00),
    "claude-sonnet-4-6": ModelPricing("anthropic", 3.00, 15.00),
    "claude-haiku-4-5": ModelPricing("anthropic", 1.00, 5.00),
    "gemini-2.5-pro": ModelPricing("google", 1.25, 10.00),
    "gemini-2.5-flash": ModelPricing("google", 0.15, 0.60),
    "gemini-2.0-flash": ModelPricing("google", 0.10, 0.40),
}

ROUTING_STRATEGIES: dict[RoutingStrategyName, tuple[str, ...]] = {
    "economy": ("gpt-5.4-nano", "claude-haiku-4-5"),
    "balanced": ("gpt-5.4-mini", "claude-sonnet-4-6"),
    "performance": ("gpt-5.4", "claude-opus-4-6"),
}


def get_model_pricing(model: str) -> ModelPricing:
    """Return pricing for a supported model or raise a clear error."""

    if model.startswith("ollama/"):
        return ModelPricing("ollama", 0.0, 0.0)
    if "/" in model:
        _, _, normalized_model = model.partition("/")
        if normalized_model in MODEL_PRICING:
            return MODEL_PRICING[normalized_model]
    try:
        return MODEL_PRICING[model]
    except KeyError as exc:
        supported = ", ".join(sorted(MODEL_PRICING))
        raise ValueError(f"Unsupported model '{model}'. Supported models: {supported}.") from exc


def get_provider_for_model(model: str) -> ProviderName:
    """Map a model name to the upstream provider."""

    return get_model_pricing(model).provider


def get_models_for_strategy(strategy: RoutingStrategyName) -> tuple[str, ...]:
    """Return the preferred models for a routing strategy."""

    try:
        return ROUTING_STRATEGIES[strategy]
    except KeyError as exc:
        supported = ", ".join(ROUTING_STRATEGIES)
        raise ValueError(
            f"Unsupported routing strategy '{strategy}'. Supported strategies: {supported}."
        ) from exc


@dataclass(frozen=True, slots=True)
class Settings:
    """Resolved environment settings."""

    openai_api_key: str | None
    anthropic_api_key: str | None
    google_api_key: str | None
    openai_base_url: str
    anthropic_base_url: str
    ollama_base_url: str
    anthropic_version: str
    request_timeout_seconds: float
    cache_similarity_threshold: float
    max_cache_entries: int
    default_compression_tier: str
    compression_verify_default: bool
    compression_verifier_model: str
    semantic_similarity_high: float
    semantic_similarity_low: float
    semantic_density_threshold: float
    semantic_summary_trigger_turns: int
    chunk_relevance_threshold: float
    chunk_max_chunks: int
    transpile_min_saved_tokens: int
    transpile_min_saved_pct: float
    daily_budget: float
    monthly_budget: float
    alert_threshold_pct: float
    dashboard_key: str
    desktop_secret: str
    state_db_path: str
    nanoclaw_root: str
    nanoclaw_cli_timeout_seconds: float
    nanoclaw_proxy_url: str
    chat_fallback_model: str
    desktop_task_wait_seconds: float
    desktop_agent_poll_seconds: float
    context_distill_max_sessions: int
    context_distill_max_blocks_per_session: int
    context_distill_min_saved_tokens: int
    context_distill_min_saved_pct: float
    static_context_prefix_chars: int
    static_context_min_tokens: int
    static_context_max_task_chars: int
    experiments_enabled: bool
    allowed_experiments: tuple[str, ...]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and memoize application settings."""

    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
        google_api_key=os.getenv("GOOGLE_API_KEY"),
        openai_base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        anthropic_base_url=os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1"),
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        anthropic_version=os.getenv("ANTHROPIC_VERSION", "2023-06-01"),
        request_timeout_seconds=float(os.getenv("LUMIN_REQUEST_TIMEOUT", "60")),
        cache_similarity_threshold=float(os.getenv("LUMIN_CACHE_SIMILARITY_THRESHOLD", "0.58")),
        max_cache_entries=int(os.getenv("LUMIN_MAX_CACHE_ENTRIES", "512")),
        default_compression_tier=os.getenv("LUMIN_COMPRESSION_TIER", "free"),
        compression_verify_default=os.getenv("LUMIN_VERIFY_COMPRESSED", "true").lower() == "true",
        compression_verifier_model=os.getenv("LUMIN_VERIFIER_MODEL", "gpt-5.4-nano"),
        semantic_similarity_high=float(os.getenv("LUMIN_SEMANTIC_SIMILARITY_HIGH", "0.85")),
        semantic_similarity_low=float(os.getenv("LUMIN_SEMANTIC_SIMILARITY_LOW", "0.30")),
        semantic_density_threshold=float(os.getenv("LUMIN_SEMANTIC_DENSITY_THRESHOLD", "0.32")),
        semantic_summary_trigger_turns=int(os.getenv("LUMIN_SEMANTIC_SUMMARY_TRIGGER_TURNS", "10")),
        chunk_relevance_threshold=float(os.getenv("LUMIN_CHUNK_RELEVANCE_THRESHOLD", "0.18")),
        chunk_max_chunks=int(os.getenv("LUMIN_CHUNK_MAX_CHUNKS", "6")),
        transpile_min_saved_tokens=int(os.getenv("LUMIN_TRANSPILE_MIN_SAVED_TOKENS", "5")),
        transpile_min_saved_pct=float(os.getenv("LUMIN_TRANSPILE_MIN_SAVED_PCT", "8")),
        daily_budget=float(os.getenv("LUMIN_DAILY_BUDGET", "10.00")),
        monthly_budget=float(os.getenv("LUMIN_MONTHLY_BUDGET", "100.00")),
        alert_threshold_pct=float(os.getenv("LUMIN_ALERT_THRESHOLD", "0.80")),
        dashboard_key=os.getenv("LUMIN_DASHBOARD_KEY", "lumin_dev_key_change_me"),
        desktop_secret=os.getenv("LUMIN_DESKTOP_SECRET", "lumin_desktop_secret_change_me"),
        state_db_path=os.getenv("LUMIN_STATE_DB_PATH", "/home/ryan/Lumin/data/lumin_state.db"),
        nanoclaw_root=os.getenv("LUMIN_NANOCLAW_ROOT", "/home/ryan/Lumin/nanoclaw"),
        nanoclaw_cli_timeout_seconds=float(os.getenv("LUMIN_NANOCLAW_CLI_TIMEOUT", "90")),
        nanoclaw_proxy_url=os.getenv("LUMIN_NANOCLAW_PROXY_URL", "http://host.docker.internal:8000"),
        chat_fallback_model=os.getenv("LUMIN_CHAT_FALLBACK_MODEL", "gpt-5.4-mini"),
        desktop_task_wait_seconds=float(os.getenv("LUMIN_DESKTOP_TASK_WAIT_SECONDS", "60")),
        desktop_agent_poll_seconds=float(os.getenv("LUMIN_DESKTOP_AGENT_POLL_SECONDS", "3")),
        context_distill_max_sessions=int(os.getenv("LUMIN_CONTEXT_DISTILL_MAX_SESSIONS", "256")),
        context_distill_max_blocks_per_session=int(
            os.getenv("LUMIN_CONTEXT_DISTILL_MAX_BLOCKS_PER_SESSION", "128")
        ),
        context_distill_min_saved_tokens=int(
            os.getenv("LUMIN_CONTEXT_DISTILL_MIN_SAVED_TOKENS", "8")
        ),
        context_distill_min_saved_pct=float(
            os.getenv("LUMIN_CONTEXT_DISTILL_MIN_SAVED_PCT", "3")
        ),
        static_context_prefix_chars=int(
            os.getenv("LUMIN_STATIC_CONTEXT_PREFIX_CHARS", "1600")
        ),
        static_context_min_tokens=int(
            os.getenv("LUMIN_STATIC_CONTEXT_MIN_TOKENS", "900")
        ),
        static_context_max_task_chars=int(
            os.getenv("LUMIN_STATIC_CONTEXT_MAX_TASK_CHARS", "180")
        ),
        experiments_enabled=os.getenv("LUMIN_ENABLE_EXPERIMENTS", "true").lower() == "true",
        allowed_experiments=tuple(
            item.strip()
            for item in os.getenv(
                "LUMIN_ALLOWED_EXPERIMENTS",
                "cget_v0,scaffold_fill_v0,spe_v0",
            ).split(",")
            if item.strip()
        ),
    )
