"""Pydantic models shared across the application."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatMessage(BaseModel):
    """OpenAI-style chat message."""

    model_config = ConfigDict(extra="allow")

    role: str
    content: Any
    name: str | None = None
    tool_call_id: str | None = None


class ChatCompletionRequest(BaseModel):
    """Subset of the OpenAI chat completion payload with passthrough support."""

    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[ChatMessage]
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    max_completion_tokens: int | None = None
    stream: bool = False


class SavingsSnapshot(BaseModel):
    """Savings metadata surfaced by Lumin headers and logs."""

    original_tokens: int
    sent_tokens: int
    original_cost: float
    actual_cost: float
    saved_amount: float


class ModelCostBreakdown(BaseModel):
    """Per-model prediction data."""

    model: str
    provider: str
    input_tokens: int
    projected_output_tokens: int
    input_cost: float
    output_cost: float
    total_cost: float
    semantic_cache_hit_score: float | None = None
    cache_adjusted_total_cost: float | None = None


class PredictRequest(BaseModel):
    """Prediction request for the cost oracle."""

    model_config = ConfigDict(extra="allow")

    model: str | None = Field(
        default=None,
        description="Optional preferred model to highlight in the response.",
    )
    messages: list[ChatMessage]
    candidate_models: list[str] | None = Field(
        default=None,
        description="Optional subset of models to compare. Defaults to all supported models.",
    )
    expected_output_tokens: int | None = Field(
        default=None,
        description="Optional projected output token count. Falls back to max_tokens if present.",
    )
    max_tokens: int | None = None
    max_completion_tokens: int | None = None


class PredictResponse(BaseModel):
    """Prediction response returned by the cost oracle endpoint."""

    requested_model: str | None
    cheapest_model: str
    recommended_model_reason: str
    breakdown: list[ModelCostBreakdown]
    semantic_cache_hit_score: float | None = None
    semantic_cache_adjusted_cheapest_model: str | None = None


class CacheEntry(BaseModel):
    """A semantic cache entry."""

    key: str
    model: str
    request_fingerprint: str
    semantic_text: str = ""
    semantic_terms: dict[str, float] = Field(default_factory=dict)
    last_user_text: str = ""
    last_user_terms: dict[str, float] = Field(default_factory=dict)
    response: dict[str, Any]
    usage: dict[str, Any] | None = None
    hits: int = 0


class CompressionBreakdownStats(BaseModel):
    """Aggregate savings breakdown used by the dashboard."""

    basic_savings_pct: float
    semantic_savings_pct: float
    cache_hits: int
    transpile_saves: int


class DashboardCompressionBreakdown(BaseModel):
    """Exact compression breakdown shape for `/api/stats`."""

    free_tier_requests: int
    pro_tier_requests: int
    cache_hits: int
    exact_cache_hits: int = 0
    semantic_cache_hits: int = 0
    verification_fallbacks: int = 0
    avg_free_savings_pct: float
    avg_pro_savings_pct: float


class StatsResponse(BaseModel):
    """Exact dashboard stats payload returned by `/api/stats`."""

    total_requests: int
    total_saved_tokens: int
    total_saved_dollars: float
    total_spent_dollars: float
    would_have_spent_dollars: float
    avg_savings_pct: float
    weighted_savings_pct: float = 0.0
    today_weighted_savings_pct: float = 0.0
    cache_hit_rate: float
    requests_today: int
    saved_today: float
    spent_today: float
    would_have_spent_today: float
    top_model_used: str
    compression_breakdown: DashboardCompressionBreakdown


class DashboardStats(BaseModel):
    """Top-level dashboard stats payload."""

    total_requests: int
    total_saved_tokens: int
    total_saved_dollars: float
    total_spent_dollars: float
    would_have_spent_dollars: float
    avg_savings_pct: float
    weighted_savings_pct: float = 0.0
    cache_hit_rate: float
    requests_today: int
    saved_today: float
    top_model_used: str
    compression_breakdown: CompressionBreakdownStats


class RequestLogEntry(BaseModel):
    """A single completed request record for the dashboard."""

    id: str
    timestamp: str
    model_requested: str
    model_used: str
    original_tokens: int
    sent_tokens: int
    savings_pct: float
    saved_dollars: float
    compression_tier: str
    cache_hit: bool
    routing_reason: str
    latency_ms: int
    actual_spent_dollars: float = 0.0
    would_have_spent_dollars: float = 0.0
    transpile_saved_dollars: float = 0.0
    basic_savings_pct: float = 0.0
    semantic_savings_pct: float = 0.0


class RequestEntry(BaseModel):
    """Exact request record shape used by the dashboard APIs."""

    id: str
    timestamp: str
    model_requested: str
    model_used: str
    original_tokens: int
    sent_tokens: int
    savings_pct: float
    saved_dollars: float
    actual_cost: float
    would_have_cost: float
    compression_tier: str
    cache_hit: bool
    cache_type: str = "miss"
    cache_score: float = 0.0
    routing_reason: str
    latency_ms: int
    verification_result: str = "skipped"
    verification_fallback: bool = False
    workflow_genome: str = "unknown"
    workflow_confidence: float = 0.0
    source: str = "proxy"
    context_id: str | None = None


class SettingsResponse(BaseModel):
    """Protected operational settings surfaced for API consumers."""

    experiments_enabled: bool
    allowed_experiments: list[str]
    default_compression_tier: str
    compression_verify_default: bool
    cache_similarity_threshold: float
    daily_budget: float
    monthly_budget: float
    alert_threshold_pct: float
    context_distill_min_saved_tokens: int
    context_distill_min_saved_pct: float
    context_distill_max_sessions: int
    context_distill_max_blocks_per_session: int


class AccountingSettings(BaseModel):
    """How dashboard economics should be interpreted."""

    billing_mode: Literal["metered", "subscription"] = "metered"
    subscription_label: str = ""


class RuntimePreferences(BaseModel):
    """User-facing runtime defaults for the dashboard and control path."""

    active_provider: Literal["auto", "openai", "openrouter", "anthropic", "google", "ollama"] = "auto"
    active_model: str = "gpt-5.4-mini"
    workspace_root_hint: str = "/workspace/hostapp"
    main_group: str = "main"


class AgentPresetRecord(BaseModel):
    """Named reusable agent preset imported from OpenClaw/NanoClaw files."""

    name: str
    source_path: str
    imported_at: str
    description: str = ""
    files: list[str] = Field(default_factory=list)
    applied_groups: list[str] = Field(default_factory=list)
    file_count: int = 0
    skill_count: int = 0


class AgentPresetImportRequest(BaseModel):
    """Import OpenClaw-style files into a named preset."""

    preset_name: str
    source_path: str
    apply_to_group: str | None = None


class AgentPresetApplyRequest(BaseModel):
    """Apply a stored preset into a NanoClaw group."""

    group_id: str = "main"


class ConnectorField(BaseModel):
    """Renderable connector setup field."""

    key: str
    label: str
    kind: str = "text"
    placeholder: str = ""
    required: bool = False
    secret: bool = False


class ConnectorRecord(BaseModel):
    """Configured or available connector in settings."""

    connector_type: str
    name: str
    description: str = ""
    category: str = "General"
    origin: str = "lumin"
    popular: bool = False
    status: str = "not_configured"
    display_name: str = ""
    fields: list[ConnectorField] = Field(default_factory=list)
    config_values: dict[str, str] = Field(default_factory=dict)
    supports_live_test: bool = False
    supports_send_test: bool = False
    created_at: str = ""
    updated_at: str = ""
    configured_at: str | None = None
    last_tested_at: str | None = None


class ConnectorUpsertRequest(BaseModel):
    """Save or update a connector configuration."""

    connector_type: str
    display_name: str | None = None
    config: dict[str, str] = Field(default_factory=dict)


class ProviderField(BaseModel):
    """Renderable provider setup field."""

    key: str
    label: str
    kind: str = "text"
    placeholder: str = ""
    required: bool = False
    secret: bool = False


class ProviderModelOption(BaseModel):
    """Suggested model option for a provider-aware selector."""

    id: str
    label: str


class ProviderRecord(BaseModel):
    """Configured or available provider in settings."""

    provider_type: str
    name: str
    description: str = ""
    category: str = "General"
    status: str = "not_configured"
    display_name: str = ""
    fields: list[ProviderField] = Field(default_factory=list)
    model_suggestions: list[ProviderModelOption] = Field(default_factory=list)
    config_values: dict[str, str] = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    configured_at: str | None = None


class ProviderUpsertRequest(BaseModel):
    """Save or update a provider configuration."""

    provider_type: str
    display_name: str | None = None
    config: dict[str, str] = Field(default_factory=dict)


class IntegrationCheckResult(BaseModel):
    """Result of a lightweight provider or connector preflight."""

    status: str
    summary: str
    missing_fields: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    live_check_performed: bool = False


class IntegrationSendResult(BaseModel):
    """Result of a test send through a connector."""

    status: str
    summary: str
    notes: list[str] = Field(default_factory=list)


class OpenClawScanResponse(BaseModel):
    """Detection result for an OpenClaw source path."""

    source_path: str
    exists: bool
    detected: bool
    kind: str = "unknown"
    signals: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class MvpReadinessItem(BaseModel):
    """One product-readiness checklist item."""

    key: str
    title: str
    priority: str
    status: str
    summary: str
    action: str


class MvpReadinessResponse(BaseModel):
    """High-level MVP readiness report exposed to the dashboard."""

    score_pct: float
    headline: str
    items: list[MvpReadinessItem] = Field(default_factory=list)
    facts: dict[str, Any] = Field(default_factory=dict)


class DoctorCheck(BaseModel):
    """One operator-facing setup check."""

    key: str
    status: str
    summary: str
    detail: str = ""


class DoctorResponse(BaseModel):
    """High-level product doctor output for onboarding and setup."""

    overall_status: str
    checks: list[DoctorCheck] = Field(default_factory=list)


class BudgetSnapshot(BaseModel):
    """Current budget state exposed by the dashboard API."""

    daily_limit: float
    daily_spent: float
    daily_remaining: float
    monthly_limit: float
    monthly_spent: float
    monthly_remaining: float
    burn_rate_per_hour: float
    projected_daily_total: float
    alert_threshold_pct: float


class BudgetStatus(BaseModel):
    """Exact budget payload returned by `/api/budget`."""

    daily_limit: float
    daily_spent: float
    daily_remaining: float
    daily_pct_used: float
    monthly_limit: float
    monthly_spent: float
    monthly_remaining: float
    monthly_pct_used: float
    burn_rate_per_hour: float
    projected_daily_total: float
    alert_triggered: bool


class LiveEvent(BaseModel):
    """Realtime event streamed to dashboard clients."""

    type: str
    timestamp: str
    model: str = ""
    model_routed: str | None = None
    saved_tokens: int = 0
    saved_dollars: float = 0.0
    savings_pct: float = 0.0
    cache_hit: bool = False
    compression_tier: str | None = None
    latency_ms: int | None = None
    context_id: str | None = None
    agent: str | None = None


class MobileChatRequest(BaseModel):
    """Chat payload submitted by the Flutter app."""

    message: str
    group_id: str = "main"
    context_id: str | None = None


class MobileChatSavings(BaseModel):
    """Savings metadata returned by `/api/chat`."""

    tokens_saved: int
    dollars_saved: float
    savings_pct: float
    context_compressed: bool


class MobileChatResponse(BaseModel):
    """Programmatic chat response returned to the mobile companion app."""

    response: str
    savings: MobileChatSavings
    model_used: str
    latency_ms: int
    agent: str = "direct"


class DesktopAgentRegisterRequest(BaseModel):
    """Payload used by a local desktop worker to register with Lumin."""

    name: str
    hostname: str
    group_id: str = "main"
    capabilities: list[str] = Field(default_factory=list)


class DesktopAgentRegisterResponse(BaseModel):
    """Registration response returned to a desktop agent."""

    agent_id: str
    agent_token: str
    poll_interval_seconds: float


class DesktopHeartbeatResponse(BaseModel):
    """Heartbeat acknowledgement for desktop agents."""

    agent_id: str
    status: str
    poll_interval_seconds: float


class PairingCodeResponse(BaseModel):
    """One-time mobile pairing code issued by the local desktop/admin surface."""

    code: str
    expires_at: str


class PairingClaimRequest(BaseModel):
    """Redeem a one-time pairing code for a durable mobile session token."""

    code: str
    device_name: str


class PairingClaimResponse(BaseModel):
    """Durable mobile session credentials returned after successful pairing."""

    client_id: str
    mobile_token: str
    expires_at: str | None = None


class MobileClientRecord(BaseModel):
    """Visible paired mobile client metadata for authenticated admin users."""

    client_id: str
    device_name: str
    status: str
    created_at: str
    updated_at: str
    last_seen_at: str


class RemoteTaskRecord(BaseModel):
    """Persistent remote task metadata for phone-to-desktop control."""

    id: str
    agent_id: str | None = None
    group_id: str
    context_id: str | None = None
    message: str
    origin: str
    status: str
    response_text: str | None = None
    error_text: str | None = None
    model_used: str | None = None
    latency_ms: int = 0
    created_at: str
    updated_at: str
    started_at: str | None = None
    completed_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RemoteTaskCreateRequest(BaseModel):
    """Create a remote task for a desktop NanoClaw worker."""

    message: str
    group_id: str = "main"
    context_id: str | None = None
    wait_for_result: bool = True
    client_request_id: str | None = None
    requested_model: str | None = None


class DesktopTaskResultRequest(BaseModel):
    """Complete or fail a claimed desktop task."""

    response_text: str | None = None
    error_text: str | None = None
    model_used: str = "nanoclaw"
    latency_ms: int = 0


class DesktopTaskStartedRequest(BaseModel):
    """Transition a claimed task into active execution."""

    stage: Literal["running"] = "running"


class DesktopAgentRecord(BaseModel):
    """Visible desktop agent state exposed to authenticated clients."""

    agent_id: str
    name: str
    hostname: str
    group_id: str
    status: str
    created_at: str
    updated_at: str
    last_seen_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)
