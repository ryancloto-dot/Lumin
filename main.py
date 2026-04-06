"""FastAPI entry point for the Lumin MVP and dashboard surfaces."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from subprocess import CalledProcessError, TimeoutExpired, run
import shutil

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from config import get_settings
from engine.cache import get_budget_tracker, get_live_event_bus, get_request_ledger
from engine.agent_presets import get_agent_preset_manager
from engine.connector_runtime import check_connector_runtime, send_test_message
from engine.mvp_readiness import (
    get_doctor_report,
    check_provider,
    get_mvp_readiness,
    scan_openclaw_source,
)
from engine.nanoclaw_bridge import (
    nanoclaw_bridge_available,
    run_nanoclaw_chat_bridge,
)
from engine.state_store import get_state_store
from models.schemas import (
    BudgetStatus,
    ChatCompletionRequest,
    ConnectorRecord,
    ConnectorUpsertRequest,
    DesktopAgentRecord,
    DesktopAgentRegisterRequest,
    DesktopAgentRegisterResponse,
    DesktopHeartbeatResponse,
    DesktopTaskResultRequest,
    DesktopTaskStartedRequest,
    DoctorResponse,
    AgentPresetApplyRequest,
    AgentPresetImportRequest,
    AgentPresetRecord,
    AccountingSettings,
    IntegrationCheckResult,
    IntegrationSendResult,
    MobileClientRecord,
    MobileChatRequest,
    MobileChatResponse,
    MobileChatSavings,
    MvpReadinessResponse,
    OpenClawScanResponse,
    PairingClaimRequest,
    PairingClaimResponse,
    PairingCodeResponse,
    ProviderRecord,
    ProviderUpsertRequest,
    RequestEntry,
    RemoteTaskCreateRequest,
    RemoteTaskRecord,
    RuntimePreferences,
    SettingsResponse,
    StatsResponse,
)
from oracle.predictor import router as predictor_router
from proxy.router import _handle_chat_completion, router as proxy_router
from proxy.router import close_shared_http_client

os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")

if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

for noisy_logger_name, noisy_level in {
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    "sentence_transformers": logging.WARNING,
    "sentence_transformers.SentenceTransformer": logging.WARNING,
    "transformers": logging.WARNING,
    "huggingface_hub": logging.WARNING,
    "urllib3": logging.WARNING,
    "filelock": logging.WARNING,
    "engine.compressor": logging.WARNING,
}.items():
    logging.getLogger(noisy_logger_name).setLevel(noisy_level)

logger = logging.getLogger(__name__)
app = FastAPI(
    title="Lumin",
    description=(
        "AI cost optimization proxy that sits between clients and upstream LLMs "
        "while surfacing silent savings."
    ),
    version="0.1.0",
)
app.include_router(proxy_router)
app.include_router(predictor_router)

_DASHBOARD_PATH = Path(__file__).resolve().parent / "templates" / "dashboard.html"
_SETTINGS_PATH = Path(__file__).resolve().parent / "templates" / "settings.html"
_ASSETS_PATH = Path(__file__).resolve().parent / "assets"
_NANOCLAW_WARMUP_TASK: asyncio.Task[None] | None = None
_NANOCLAW_IMAGE_READY = False

if _ASSETS_PATH.exists():
    app.mount("/assets", StaticFiles(directory=str(_ASSETS_PATH)), name="assets")


def _verify_dashboard_key(x_lumin_key: str | None = Header(default=None)) -> None:
    """Validate the dashboard API key for protected JSON endpoints."""

    if x_lumin_key != get_settings().dashboard_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing or invalid X-Lumin-Key header.",
        )


def _verify_api_access(
    x_lumin_key: str | None = Header(default=None),
    x_lumin_mobile_token: str | None = Header(default=None),
) -> dict[str, str]:
    """Allow either the dashboard admin key or a paired mobile token."""

    settings = get_settings()
    if x_lumin_key == settings.dashboard_key:
        return {"auth_type": "dashboard", "subject_id": "dashboard"}
    if x_lumin_mobile_token:
        client = get_state_store().authenticate_mobile_token(x_lumin_mobile_token)
        if client is not None:
            client_id = str(client["client_id"])
            get_state_store().touch_mobile_client(client_id)
            return {"auth_type": "mobile", "subject_id": client_id}
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Missing or invalid API credentials.",
    )


def _verify_desktop_secret(x_lumin_desktop_key: str | None = Header(default=None)) -> None:
    """Validate the desktop-agent bootstrap secret."""

    if x_lumin_desktop_key != get_settings().desktop_secret:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing or invalid X-Lumin-Desktop-Key header.",
        )


def _authenticate_agent(x_lumin_agent_token: str | None = Header(default=None)) -> dict[str, str]:
    """Resolve a registered desktop agent from its durable token."""

    if not x_lumin_agent_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing X-Lumin-Agent-Token header.",
        )
    agent = get_state_store().authenticate_agent(x_lumin_agent_token)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid X-Lumin-Agent-Token header.",
        )
    return {
        "agent_id": str(agent["agent_id"]),
        "group_id": str(agent["group_id"]),
        "name": str(agent["name"]),
    }


@app.get("/health", tags=["system"])
async def healthcheck() -> dict[str, str]:
    """Return a lightweight healthcheck payload."""

    return {"status": "ok", "service": "lumin"}


@app.get("/dashboard", response_class=HTMLResponse, tags=["dashboard"])
async def dashboard() -> HTMLResponse:
    """Serve the built-in dashboard page."""

    if not _DASHBOARD_PATH.exists():
        raise HTTPException(status_code=500, detail="Dashboard template is missing.")
    return HTMLResponse(_DASHBOARD_PATH.read_text(encoding="utf-8"))


@app.get("/settings", response_class=HTMLResponse, tags=["dashboard"])
async def settings_page() -> HTMLResponse:
    """Serve the lightweight settings page."""

    if not _SETTINGS_PATH.exists():
        raise HTTPException(status_code=500, detail="Settings template is missing.")
    return HTMLResponse(_SETTINGS_PATH.read_text(encoding="utf-8"))


def _prewarm_nanoclaw_runtime() -> None:
    """Pre-build and pre-check the NanoClaw runtime to reduce first-request latency."""

    global _NANOCLAW_IMAGE_READY
    if not nanoclaw_bridge_available():
        return
    try:
        _ensure_nanoclaw_agent_image()
        _NANOCLAW_IMAGE_READY = True
    except Exception as exc:  # pragma: no cover - best-effort warmup only
        logger.info("NanoClaw warmup skipped: %s", exc)


@app.on_event("startup")
async def startup_runtime() -> None:
    """Start best-effort background warmup tasks."""

    global _NANOCLAW_WARMUP_TASK
    _NANOCLAW_WARMUP_TASK = asyncio.create_task(asyncio.to_thread(_prewarm_nanoclaw_runtime))


@app.on_event("shutdown")
async def shutdown_runtime() -> None:
    """Stop best-effort background warmup tasks."""

    global _NANOCLAW_WARMUP_TASK
    if _NANOCLAW_WARMUP_TASK is not None:
        _NANOCLAW_WARMUP_TASK.cancel()
        try:
            await _NANOCLAW_WARMUP_TASK
        except asyncio.CancelledError:
            pass
        _NANOCLAW_WARMUP_TASK = None
    await close_shared_http_client()


@app.get("/api/stats", response_model=StatsResponse, dependencies=[Depends(_verify_api_access)], tags=["dashboard"])
async def api_stats() -> StatsResponse:
    """Return aggregate dashboard metrics."""

    return StatsResponse.model_validate(get_request_ledger().get_stats())


@app.get(
    "/api/requests",
    response_model=list[RequestEntry],
    dependencies=[Depends(_verify_api_access)],
    tags=["dashboard"],
)
async def api_requests(limit: int = Query(default=50, ge=1, le=1000)) -> list[RequestEntry]:
    """Return recent request history for the dashboard."""

    return get_request_ledger().get_recent(limit)


@app.get("/api/budget", response_model=BudgetStatus, dependencies=[Depends(_verify_api_access)], tags=["dashboard"])
async def api_budget() -> BudgetStatus:
    """Return budget and burn-rate information."""

    return BudgetStatus.model_validate(get_budget_tracker().get_status())


@app.get("/api/advanced/settings", response_model=SettingsResponse, dependencies=[Depends(_verify_dashboard_key)], tags=["dashboard"])
@app.get("/api/settings", response_model=SettingsResponse, dependencies=[Depends(_verify_dashboard_key)], tags=["dashboard"])
async def api_settings() -> SettingsResponse:
    """Return protected operational settings and tuning thresholds."""

    settings = get_settings()
    return SettingsResponse(
        experiments_enabled=settings.experiments_enabled,
        allowed_experiments=list(settings.allowed_experiments),
        default_compression_tier=settings.default_compression_tier,
        compression_verify_default=settings.compression_verify_default,
        cache_similarity_threshold=settings.cache_similarity_threshold,
        daily_budget=settings.daily_budget,
        monthly_budget=settings.monthly_budget,
        alert_threshold_pct=settings.alert_threshold_pct,
        context_distill_min_saved_tokens=settings.context_distill_min_saved_tokens,
        context_distill_min_saved_pct=settings.context_distill_min_saved_pct,
        context_distill_max_sessions=settings.context_distill_max_sessions,
        context_distill_max_blocks_per_session=settings.context_distill_max_blocks_per_session,
    )


@app.get("/api/advanced/settings/accounting", response_model=AccountingSettings, dependencies=[Depends(_verify_dashboard_key)], tags=["dashboard"])
@app.get("/api/settings/accounting", response_model=AccountingSettings, dependencies=[Depends(_verify_dashboard_key)], tags=["dashboard"])
async def api_accounting_settings() -> AccountingSettings:
    """Return dashboard accounting mode settings."""

    return AccountingSettings.model_validate(get_state_store().get_accounting_settings())


@app.post("/api/advanced/settings/accounting", response_model=AccountingSettings, dependencies=[Depends(_verify_dashboard_key)], tags=["dashboard"])
@app.post("/api/settings/accounting", response_model=AccountingSettings, dependencies=[Depends(_verify_dashboard_key)], tags=["dashboard"])
async def api_set_accounting_settings(payload: AccountingSettings) -> AccountingSettings:
    """Persist dashboard accounting mode settings."""

    try:
        saved = get_state_store().set_accounting_settings(
            billing_mode=payload.billing_mode,
            subscription_label=payload.subscription_label,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return AccountingSettings.model_validate(saved)


@app.get("/api/advanced/settings/runtime", response_model=RuntimePreferences, dependencies=[Depends(_verify_dashboard_key)], tags=["dashboard"])
@app.get("/api/settings/runtime", response_model=RuntimePreferences, dependencies=[Depends(_verify_dashboard_key)], tags=["dashboard"])
async def api_runtime_preferences() -> RuntimePreferences:
    """Return runtime defaults for provider selection and workspace hints."""

    return RuntimePreferences.model_validate(get_state_store().get_runtime_preferences())


@app.post("/api/advanced/settings/runtime", response_model=RuntimePreferences, dependencies=[Depends(_verify_dashboard_key)], tags=["dashboard"])
@app.post("/api/settings/runtime", response_model=RuntimePreferences, dependencies=[Depends(_verify_dashboard_key)], tags=["dashboard"])
async def api_set_runtime_preferences(payload: RuntimePreferences) -> RuntimePreferences:
    """Persist runtime defaults for provider selection and workspace hints."""

    try:
        saved = get_state_store().set_runtime_preferences(
            active_provider=payload.active_provider,
            active_model=payload.active_model,
            workspace_root_hint=payload.workspace_root_hint,
            main_group=payload.main_group,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RuntimePreferences.model_validate(saved)


@app.get("/api/advanced/settings/agent-presets", response_model=list[AgentPresetRecord], dependencies=[Depends(_verify_dashboard_key)], tags=["dashboard"])
@app.get("/api/settings/agent-presets", response_model=list[AgentPresetRecord], dependencies=[Depends(_verify_dashboard_key)], tags=["dashboard"])
async def api_agent_presets() -> list[AgentPresetRecord]:
    """Return reusable agent presets available for NanoClaw groups."""

    return [
        AgentPresetRecord.model_validate(
            {
                "name": record.name,
                "source_path": record.source_path,
                "imported_at": record.imported_at,
                "description": record.description,
                "files": record.files,
                "applied_groups": record.applied_groups,
                "file_count": record.file_count,
                "skill_count": record.skill_count,
            }
        )
        for record in get_agent_preset_manager().list_presets()
    ]


@app.get("/api/advanced/settings/connectors", response_model=list[ConnectorRecord], dependencies=[Depends(_verify_dashboard_key)], tags=["dashboard"])
@app.get("/api/settings/connectors", response_model=list[ConnectorRecord], dependencies=[Depends(_verify_dashboard_key)], tags=["dashboard"])
async def api_connectors() -> list[ConnectorRecord]:
    """Return built-in connectors with any saved configuration."""

    return [
        ConnectorRecord.model_validate(item)
        for item in get_state_store().list_connectors()
    ]


@app.post("/api/advanced/settings/connectors", response_model=ConnectorRecord, dependencies=[Depends(_verify_dashboard_key)], tags=["dashboard"])
@app.post("/api/settings/connectors", response_model=ConnectorRecord, dependencies=[Depends(_verify_dashboard_key)], tags=["dashboard"])
async def api_upsert_connector(payload: ConnectorUpsertRequest) -> ConnectorRecord:
    """Create or update a connector configuration."""

    try:
        record = get_state_store().upsert_connector(
            connector_type=payload.connector_type,
            display_name=payload.display_name,
            config=payload.config,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ConnectorRecord.model_validate(record)


@app.delete("/api/advanced/settings/connectors/{connector_type}", response_model=dict, dependencies=[Depends(_verify_dashboard_key)], tags=["dashboard"])
@app.delete("/api/settings/connectors/{connector_type}", response_model=dict, dependencies=[Depends(_verify_dashboard_key)], tags=["dashboard"])
async def api_delete_connector(connector_type: str) -> dict[str, bool]:
    """Delete a connector configuration."""

    deleted = get_state_store().delete_connector(connector_type)
    if not deleted:
        raise HTTPException(status_code=404, detail="Connector not found.")
    return {"deleted": True}


@app.get(
    "/api/settings/providers",
    response_model=list[ProviderRecord],
    dependencies=[Depends(_verify_dashboard_key)],
    tags=["dashboard"],
)
async def api_providers() -> list[ProviderRecord]:
    """Return built-in providers with any saved configuration."""

    return [
        ProviderRecord.model_validate(item)
        for item in get_state_store().list_providers()
    ]


@app.post(
    "/api/settings/providers",
    response_model=ProviderRecord,
    dependencies=[Depends(_verify_dashboard_key)],
    tags=["dashboard"],
)
async def api_upsert_provider(payload: ProviderUpsertRequest) -> ProviderRecord:
    """Create or update a provider configuration."""

    try:
        record = get_state_store().upsert_provider(
            provider_type=payload.provider_type,
            display_name=payload.display_name,
            config=payload.config,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ProviderRecord.model_validate(record)


@app.delete(
    "/api/settings/providers/{provider_type}",
    response_model=dict,
    dependencies=[Depends(_verify_dashboard_key)],
    tags=["dashboard"],
)
async def api_delete_provider(provider_type: str) -> dict[str, bool]:
    """Delete a provider configuration."""

    deleted = get_state_store().delete_provider(provider_type)
    if not deleted:
        raise HTTPException(status_code=404, detail="Provider not found.")
    return {"deleted": True}


@app.post("/api/advanced/settings/providers/{provider_type}/test", response_model=IntegrationCheckResult, dependencies=[Depends(_verify_dashboard_key)], tags=["dashboard"])
@app.post("/api/settings/providers/{provider_type}/test", response_model=IntegrationCheckResult, dependencies=[Depends(_verify_dashboard_key)], tags=["dashboard"])
async def api_test_provider(provider_type: str) -> IntegrationCheckResult:
    """Run a lightweight provider preflight using saved config."""

    raw_config = get_state_store().get_provider_config(provider_type)
    if raw_config is None:
        raise HTTPException(status_code=404, detail="Provider not found.")
    try:
        record = check_provider(provider_type, raw_config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return IntegrationCheckResult.model_validate(record)


@app.post("/api/advanced/settings/connectors/{connector_type}/test", response_model=IntegrationCheckResult, dependencies=[Depends(_verify_dashboard_key)], tags=["dashboard"])
@app.post("/api/settings/connectors/{connector_type}/test", response_model=IntegrationCheckResult, dependencies=[Depends(_verify_dashboard_key)], tags=["dashboard"])
async def api_test_connector(connector_type: str) -> IntegrationCheckResult:
    """Run a lightweight connector preflight using saved config."""

    raw_config = get_state_store().get_connector_config(connector_type)
    if raw_config is None and connector_type != "app":
        raise HTTPException(status_code=404, detail="Connector not found.")
    try:
        record = check_connector_runtime(connector_type, raw_config or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return IntegrationCheckResult.model_validate(record)


@app.post("/api/advanced/settings/connectors/{connector_type}/send-test", response_model=IntegrationSendResult, dependencies=[Depends(_verify_dashboard_key)], tags=["dashboard"])
@app.post("/api/settings/connectors/{connector_type}/send-test", response_model=IntegrationSendResult, dependencies=[Depends(_verify_dashboard_key)], tags=["dashboard"])
async def api_send_connector_test(connector_type: str) -> IntegrationSendResult:
    """Send a simple test message through a connector when supported."""

    raw_config = get_state_store().get_connector_config(connector_type)
    if raw_config is None and connector_type != "app":
        raise HTTPException(status_code=404, detail="Connector not found.")
    try:
        result = send_test_message(
            connector_type,
            raw_config or {},
            "Lumin test message: your connector is working.",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return IntegrationSendResult.model_validate(result)


@app.get("/api/advanced/settings/openclaw/scan", response_model=OpenClawScanResponse, dependencies=[Depends(_verify_dashboard_key)], tags=["dashboard"])
@app.get("/api/settings/openclaw/scan", response_model=OpenClawScanResponse, dependencies=[Depends(_verify_dashboard_key)], tags=["dashboard"])
async def api_scan_openclaw_source(source_path: str = Query(..., min_length=1)) -> OpenClawScanResponse:
    """Inspect a local path and report whether it looks like an OpenClaw source."""

    return OpenClawScanResponse.model_validate(scan_openclaw_source(source_path))


@app.get("/api/advanced/mvp/readiness", response_model=MvpReadinessResponse, dependencies=[Depends(_verify_dashboard_key)], tags=["dashboard"])
@app.get("/api/mvp/readiness", response_model=MvpReadinessResponse, dependencies=[Depends(_verify_dashboard_key)], tags=["dashboard"])
async def api_mvp_readiness() -> MvpReadinessResponse:
    """Return a launch-readiness report based on current product gaps."""

    return MvpReadinessResponse.model_validate(get_mvp_readiness())


@app.get("/api/advanced/doctor", response_model=DoctorResponse, dependencies=[Depends(_verify_dashboard_key)], tags=["dashboard"])
@app.get("/api/doctor", response_model=DoctorResponse, dependencies=[Depends(_verify_dashboard_key)], tags=["dashboard"])
async def api_doctor() -> DoctorResponse:
    """Return a simple operator-facing doctor report."""

    return DoctorResponse.model_validate(get_doctor_report())


@app.post("/api/advanced/settings/agent-presets/import", response_model=AgentPresetRecord, dependencies=[Depends(_verify_dashboard_key)], tags=["dashboard"])
@app.post("/api/settings/agent-presets/import", response_model=AgentPresetRecord, dependencies=[Depends(_verify_dashboard_key)], tags=["dashboard"])
async def api_import_agent_preset(payload: AgentPresetImportRequest) -> AgentPresetRecord:
    """Import an OpenClaw/NanoClaw folder into a reusable preset."""

    try:
        record = get_agent_preset_manager().import_from_path(
            preset_name=payload.preset_name,
            source_path=payload.source_path,
        )
        if payload.apply_to_group:
            record = get_agent_preset_manager().apply_preset(
                preset_name=record.name,
                group_id=payload.apply_to_group,
            )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return AgentPresetRecord.model_validate(
        {
            "name": record.name,
            "source_path": record.source_path,
            "imported_at": record.imported_at,
            "description": record.description,
            "files": record.files,
            "applied_groups": record.applied_groups,
            "file_count": record.file_count,
            "skill_count": record.skill_count,
        }
    )


@app.post("/api/advanced/settings/agent-presets/{preset_name}/apply", response_model=AgentPresetRecord, dependencies=[Depends(_verify_dashboard_key)], tags=["dashboard"])
@app.post("/api/settings/agent-presets/{preset_name}/apply", response_model=AgentPresetRecord, dependencies=[Depends(_verify_dashboard_key)], tags=["dashboard"])
async def api_apply_agent_preset(preset_name: str, payload: AgentPresetApplyRequest) -> AgentPresetRecord:
    """Apply a stored preset into a specific NanoClaw group."""

    try:
        record = get_agent_preset_manager().apply_preset(
            preset_name=preset_name,
            group_id=payload.group_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return AgentPresetRecord.model_validate(
        {
            "name": record.name,
            "source_path": record.source_path,
            "imported_at": record.imported_at,
            "description": record.description,
            "files": record.files,
            "applied_groups": record.applied_groups,
            "file_count": record.file_count,
            "skill_count": record.skill_count,
        }
    )


@app.post(
    "/api/pairing/code",
    response_model=PairingCodeResponse,
    dependencies=[Depends(_verify_dashboard_key)],
    tags=["mobile"],
)
async def api_pairing_code() -> PairingCodeResponse:
    """Issue a short-lived code for securely pairing a phone with this computer."""

    issued = get_state_store().create_pairing_code()
    return PairingCodeResponse.model_validate(issued)


@app.post(
    "/api/pairing/claim",
    response_model=PairingClaimResponse,
    tags=["mobile"],
)
async def api_pairing_claim(payload: PairingClaimRequest) -> PairingClaimResponse:
    """Redeem a pairing code for a durable mobile session token."""

    claimed = get_state_store().claim_pairing_code(payload.code, payload.device_name)
    if claimed is None:
        raise HTTPException(status_code=403, detail="Invalid or expired pairing code.")
    return PairingClaimResponse.model_validate(claimed)


async def _wait_for_task_completion(task_id: str, timeout_seconds: float) -> dict[str, object] | None:
    """Poll durable task state until it completes or times out."""

    deadline = time.monotonic() + timeout_seconds
    store = get_state_store()
    while time.monotonic() < deadline:
        task = store.get_task(task_id)
        if task is None:
            return None
        if str(task.get("status")) in {"completed", "failed", "cancelled"}:
            return task
        await asyncio.sleep(0.5)
    return store.get_task(task_id)


def _looks_like_desktop_fallback_error(error_text: str) -> bool:
    """Return whether a desktop runtime error should fall back to direct Lumin chat."""

    lowered = error_text.lower()
    return any(
        marker in lowered
        for marker in (
            "not logged in",
            "please run /login",
            "gateway not reachable",
            "timed out",
            "timeout",
        )
    )


def _normalize_desktop_error(error_text: str) -> str:
    """Return a friendlier task error message for dashboard/mobile users."""

    lowered = error_text.lower()
    if "not logged in" in lowered or "please run /login" in lowered:
        return "NanoClaw could not authenticate with its model provider on this computer."
    if "gateway not reachable" in lowered:
        return "NanoClaw could not reach its local provider gateway."
    if "timed out" in lowered or "timeout" in lowered:
        return "NanoClaw timed out before finishing this task."
    if "no json payload found" in lowered:
        return "NanoClaw returned an invalid response payload."
    return error_text


async def _generate_direct_chat_response(
    *,
    message: str,
    context_id: str | None,
    group_id: str,
) -> tuple[str, MobileChatSavings, str]:
    """Run the safe direct Lumin assistant fallback."""

    settings = get_settings()
    runtime_preferences = get_state_store().get_runtime_preferences()
    preferred_model = str(runtime_preferences.get("active_model") or settings.chat_fallback_model).strip() or settings.chat_fallback_model
    chat_request = ChatCompletionRequest.model_validate(
        {
            "model": preferred_model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are Lumin's mobile companion assistant. Be concise and useful.",
                },
                {"role": "user", "content": message},
            ],
            "lumin_tier": "free",
            "lumin_verify": False,
            "lumin_context_id": str(context_id or group_id or "mobile"),
        }
    )
    response = await _handle_chat_completion(chat_request)
    body = json.loads(response.body)
    headers = response.headers
    content = (
        ((body.get("choices") or [{}])[0].get("message") or {}).get("content")
        if isinstance(body, dict)
        else ""
    )
    stats_header = headers.get("X-Lumin-Stats")
    stats_payload = json.loads(stats_header) if stats_header else {}
    savings = MobileChatSavings(
        tokens_saved=max(
            int(stats_payload.get("original_tokens", 0)) - int(stats_payload.get("sent_tokens", 0)),
            0,
        ),
        dollars_saved=float(stats_payload.get("saved_amount", 0.0)),
        savings_pct=round(
            (
                max(
                    int(stats_payload.get("original_tokens", 0)) - int(stats_payload.get("sent_tokens", 0)),
                    0,
                )
                / int(stats_payload.get("original_tokens", 1))
            )
            * 100,
            4,
        )
        if int(stats_payload.get("original_tokens", 0))
        else 0.0,
        context_compressed=context_id is not None or group_id != "main",
    )
    return str(content or ""), savings, str(body.get("model") or preferred_model)


def _chat_context_id(payload: MobileChatRequest) -> str:
    """Return a stable chat context id for one mobile/dashboard chat request."""

    return str(payload.context_id or payload.group_id or "main")


def _publish_chat_typing(*, context_id: str, agent: str) -> None:
    """Broadcast an immediate typing event for dashboard/mobile listeners."""

    get_live_event_bus().publish(
        LiveEvent(
            type="chat_typing",
            timestamp=datetime.now(timezone.utc).isoformat(),
            context_id=context_id,
            agent=agent,
        )
    )


def _lookup_context_savings(*, context_id: str, started_at: float) -> MobileChatSavings:
    """Return the newest internal-request savings snapshot for one chat context."""

    started_iso = datetime.fromtimestamp(started_at, timezone.utc).isoformat()
    matches = get_request_ledger().find_recent_for_context(
        context_id=context_id,
        since_timestamp=started_iso,
        source="internal_control",
        limit=5,
    )
    if not matches:
        return MobileChatSavings(
            tokens_saved=0,
            dollars_saved=0.0,
            savings_pct=0.0,
            context_compressed=True,
        )
    entry = matches[0]
    return MobileChatSavings(
        tokens_saved=max(int(entry.original_tokens) - int(entry.sent_tokens), 0),
        dollars_saved=max(float(entry.would_have_cost) - float(entry.actual_cost), 0.0),
        savings_pct=float(entry.savings_pct),
        context_compressed=bool(entry.context_id),
    )


async def _desktop_chat_response(
    payload: MobileChatRequest,
    *,
    timeout_seconds: float,
) -> MobileChatResponse | None:
    """Try the desktop NanoClaw worker once, with a hard timeout."""

    state_store = get_state_store()
    preferred_agent = state_store.get_preferred_agent()
    if preferred_agent is None:
        return None

    context_id = _chat_context_id(payload)
    started_epoch = time.time()
    task = state_store.create_task(
        message=payload.message,
        group_id=payload.group_id or preferred_agent["group_id"],
        context_id=context_id,
        origin="mobile_chat",
        metadata={"wait_for_result": True},
    )
    try:
        completed = await asyncio.wait_for(
            _wait_for_task_completion(str(task["id"]), timeout_seconds=timeout_seconds),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        state_store.cancel_task(str(task["id"]))
        return None
    if completed is None:
        return None
    if str(completed.get("status")) != "completed":
        return None
    return MobileChatResponse(
        response=str(completed.get("response_text") or ""),
        savings=_lookup_context_savings(context_id=context_id, started_at=started_epoch),
        model_used=str(completed.get("model_used") or "nanoclaw"),
        latency_ms=int(completed.get("latency_ms") or 0),
        agent="desktop",
    )


async def _nanoclaw_chat_response(
    payload: MobileChatRequest,
    *,
    timeout_seconds: float,
) -> MobileChatResponse | None:
    """Try the local NanoClaw bridge once, with a hard timeout."""

    if not nanoclaw_bridge_available():
        return None

    bridge_started_at = time.time()
    latency_started_at = time.perf_counter()
    timeout_ms = int(timeout_seconds * 1000)

    def _run_bridge_once() -> dict[str, object]:
        _ensure_nanoclaw_agent_image()
        return run_nanoclaw_chat_bridge(
            payload.message,
            payload.group_id or "main",
            timeout_ms,
        )

    try:
        bridged = await asyncio.wait_for(
            asyncio.to_thread(_run_bridge_once),
            timeout=timeout_seconds,
        )
    except (asyncio.TimeoutError, FileNotFoundError, CalledProcessError, TimeoutExpired, RuntimeError, json.JSONDecodeError):
        return None

    return MobileChatResponse(
        response=str(bridged.get("response", "")),
        savings=_lookup_context_savings(
            context_id=_chat_context_id(payload),
            started_at=bridge_started_at,
        ),
        model_used=str(bridged.get("model", "nanoclaw") or "nanoclaw"),
        latency_ms=max(int((time.perf_counter() - latency_started_at) * 1000), 0),
        agent="nanoclaw",
    )


@app.post(
    "/api/desktop/register",
    response_model=DesktopAgentRegisterResponse,
    dependencies=[Depends(_verify_desktop_secret)],
    tags=["desktop"],
)
async def api_desktop_register(payload: DesktopAgentRegisterRequest) -> DesktopAgentRegisterResponse:
    """Register a local computer as the trusted NanoClaw execution host."""

    issued = get_state_store().register_desktop_agent(
        name=payload.name,
        hostname=payload.hostname,
        group_id=payload.group_id,
        metadata={"capabilities": payload.capabilities},
    )
    return DesktopAgentRegisterResponse(
        agent_id=issued["agent_id"],
        agent_token=issued["agent_token"],
        poll_interval_seconds=get_settings().desktop_agent_poll_seconds,
    )


@app.post(
    "/api/desktop/heartbeat",
    response_model=DesktopHeartbeatResponse,
    tags=["desktop"],
)
async def api_desktop_heartbeat(
    agent: dict[str, str] = Depends(_authenticate_agent),
) -> DesktopHeartbeatResponse:
    """Refresh a desktop agent heartbeat."""

    get_state_store().touch_agent(agent["agent_id"], status="online")
    return DesktopHeartbeatResponse(
        agent_id=agent["agent_id"],
        status="online",
        poll_interval_seconds=get_settings().desktop_agent_poll_seconds,
    )


@app.post(
    "/api/desktop/tasks/claim",
    response_model=RemoteTaskRecord | None,
    tags=["desktop"],
)
async def api_desktop_claim_task(
    agent: dict[str, str] = Depends(_authenticate_agent),
) -> RemoteTaskRecord | None:
    """Claim the next queued task for a local desktop agent."""

    get_state_store().touch_agent(agent["agent_id"], status="online")
    task = get_state_store().claim_next_task(agent["agent_id"], agent["group_id"])
    return RemoteTaskRecord.model_validate(task) if task is not None else None


@app.post(
    "/api/desktop/tasks/{task_id}/started",
    response_model=RemoteTaskRecord,
    tags=["desktop"],
)
async def api_desktop_start_task(
    task_id: str,
    payload: DesktopTaskStartedRequest,
    agent: dict[str, str] = Depends(_authenticate_agent),
) -> RemoteTaskRecord:
    """Mark a claimed task as actively running on the desktop runtime."""

    get_state_store().touch_agent(agent["agent_id"], status="online")
    task = get_state_store().start_task(
        task_id=task_id,
        agent_id=agent["agent_id"],
        stage=payload.stage,
    )
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return RemoteTaskRecord.model_validate(task)


@app.post(
    "/api/desktop/tasks/{task_id}/result",
    response_model=RemoteTaskRecord,
    tags=["desktop"],
)
async def api_desktop_complete_task(
    task_id: str,
    payload: DesktopTaskResultRequest,
    agent: dict[str, str] = Depends(_authenticate_agent),
) -> RemoteTaskRecord:
    """Store a completed or failed local NanoClaw task result."""

    get_state_store().touch_agent(agent["agent_id"], status="online")
    existing_task = get_state_store().get_task(task_id)
    if existing_task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    if payload.error_text:
        normalized_error = _normalize_desktop_error(payload.error_text)
        if (
            existing_task.get("origin") in {"mobile", "mobile_chat"}
            and _looks_like_desktop_fallback_error(payload.error_text)
        ):
            logger.warning(
                "Desktop NanoClaw task failed with a recoverable runtime error; falling back: %s",
                payload.error_text,
            )
            try:
                fallback_text, _, fallback_model = await _generate_direct_chat_response(
                    message=str(existing_task.get("message") or ""),
                    context_id=str(existing_task.get("context_id") or "") or None,
                    group_id=str(existing_task.get("group_id") or "main"),
                )
                task = get_state_store().complete_task(
                    task_id=task_id,
                    agent_id=agent["agent_id"],
                    response_text=fallback_text,
                    model_used=fallback_model,
                    latency_ms=payload.latency_ms,
                    metadata_update={
                        "fallback_used": True,
                        "fallback_reason": normalized_error,
                        "final_status": "fallback_used",
                    },
                )
            except Exception as exc:
                logger.warning("Direct fallback failed for desktop task %s: %s", task_id, exc)
                task = get_state_store().fail_task(
                    task_id=task_id,
                    agent_id=agent["agent_id"],
                    error_text=normalized_error,
                    latency_ms=payload.latency_ms,
                )
        else:
            task = get_state_store().fail_task(
                task_id=task_id,
                agent_id=agent["agent_id"],
                error_text=normalized_error,
                latency_ms=payload.latency_ms,
            )
    else:
        task = get_state_store().complete_task(
            task_id=task_id,
            agent_id=agent["agent_id"],
            response_text=payload.response_text or "",
            model_used=payload.model_used,
            latency_ms=payload.latency_ms,
        )
    return RemoteTaskRecord.model_validate(task)


@app.get(
    "/api/desktop/agents",
    response_model=list[DesktopAgentRecord],
    dependencies=[Depends(_verify_dashboard_key)],
    tags=["desktop"],
)
async def api_desktop_agents() -> list[DesktopAgentRecord]:
    """Return known desktop NanoClaw agents for operational visibility."""

    return [
        DesktopAgentRecord.model_validate(agent)
        for agent in get_state_store().list_agents()
    ]


@app.get(
    "/api/mobile/clients",
    response_model=list[MobileClientRecord],
    dependencies=[Depends(_verify_dashboard_key)],
    tags=["mobile"],
)
async def api_mobile_clients() -> list[MobileClientRecord]:
    """Return paired mobile clients for operational visibility."""

    return [
        MobileClientRecord.model_validate(client)
        for client in get_state_store().list_mobile_clients()
    ]


@app.post(
    "/api/tasks",
    response_model=RemoteTaskRecord,
    dependencies=[Depends(_verify_api_access)],
    tags=["desktop"],
)
async def api_create_task(payload: RemoteTaskCreateRequest) -> RemoteTaskRecord:
    """Queue a task for the desktop NanoClaw worker."""

    task = get_state_store().create_task(
        message=payload.message,
        group_id=payload.group_id,
        context_id=payload.context_id,
        origin="mobile",
        metadata={
            "wait_for_result": payload.wait_for_result,
            "client_request_id": payload.client_request_id,
            "requested_model": payload.requested_model,
        },
    )
    return RemoteTaskRecord.model_validate(task)


@app.get(
    "/api/tasks",
    response_model=list[RemoteTaskRecord],
    dependencies=[Depends(_verify_api_access)],
    tags=["desktop"],
)
async def api_list_tasks(limit: int = Query(default=50, ge=1, le=500)) -> list[RemoteTaskRecord]:
    """Return recent remote desktop tasks."""

    return [
        RemoteTaskRecord.model_validate(task)
        for task in get_state_store().list_tasks(limit=limit)
    ]


@app.get(
    "/api/tasks/{task_id}",
    response_model=RemoteTaskRecord,
    dependencies=[Depends(_verify_api_access)],
    tags=["desktop"],
)
async def api_get_task(task_id: str) -> RemoteTaskRecord:
    """Return one remote desktop task."""

    task = get_state_store().get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return RemoteTaskRecord.model_validate(task)


@app.post(
    "/api/tasks/{task_id}/cancel",
    response_model=RemoteTaskRecord,
    dependencies=[Depends(_verify_api_access)],
    tags=["desktop"],
)
async def api_cancel_task(task_id: str) -> RemoteTaskRecord:
    """Cancel a queued or running desktop task."""

    task = get_state_store().cancel_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return RemoteTaskRecord.model_validate(task)

def _ensure_nanoclaw_agent_image() -> None:
    """Build the NanoClaw agent image if it is not already available locally."""

    global _NANOCLAW_IMAGE_READY
    if _NANOCLAW_IMAGE_READY:
        return

    nanoclaw_root = Path(get_settings().nanoclaw_root)
    build_script = nanoclaw_root / "container" / "build.sh"
    docker_path = shutil.which("docker")
    if docker_path is None:
        raise FileNotFoundError("`docker` is not available on PATH.")

    inspect = run(
        [docker_path, "image", "inspect", "nanoclaw-agent:latest"],
        capture_output=True,
        text=True,
        check=False,
    )
    if inspect.returncode == 0:
        _NANOCLAW_IMAGE_READY = True
        return
    if not build_script.exists():
        raise FileNotFoundError(f"NanoClaw build script not found at {build_script}.")

    logger.info("Building missing NanoClaw agent image: nanoclaw-agent:latest")
    run(
        ["bash", str(build_script)],
        cwd=nanoclaw_root,
        check=True,
        timeout=900,
    )
    _NANOCLAW_IMAGE_READY = True


@app.post("/api/chat", response_model=MobileChatResponse, dependencies=[Depends(_verify_api_access)], tags=["chat"])
async def api_chat(payload: MobileChatRequest) -> MobileChatResponse:
    """Send a mobile-originated chat message through NanoClaw or a safe Lumin fallback."""

    started_at = time.perf_counter()
    settings = get_settings()
    context_id = _chat_context_id(payload)
    timeout_seconds = min(8.0, max(1.0, float(settings.desktop_task_wait_seconds)))

    if nanoclaw_bridge_available():
        _publish_chat_typing(context_id=context_id, agent="nanoclaw")
        nanoclaw_response = await _nanoclaw_chat_response(payload, timeout_seconds=timeout_seconds)
        if nanoclaw_response is not None:
            nanoclaw_response.latency_ms = int((time.perf_counter() - started_at) * 1000)
            return nanoclaw_response
        logger.info("NanoClaw path timed out or failed; falling back directly to Lumin chat.")
    elif get_state_store().get_preferred_agent() is not None:
        _publish_chat_typing(context_id=context_id, agent="desktop")
        desktop_response = await _desktop_chat_response(payload, timeout_seconds=timeout_seconds)
        if desktop_response is not None:
            desktop_response.latency_ms = int((time.perf_counter() - started_at) * 1000)
            return desktop_response
        logger.info("Desktop path timed out or failed; falling back directly to Lumin chat.")

    _publish_chat_typing(context_id=context_id, agent="direct")
    content, savings, model_used = await _generate_direct_chat_response(
        message=payload.message,
        context_id=context_id,
        group_id=payload.group_id,
    )
    latency_ms = int((time.perf_counter() - started_at) * 1000)
    return MobileChatResponse(
        response=str(content or ""),
        savings=savings,
        model_used=model_used,
        latency_ms=latency_ms,
        agent="direct",
    )


@app.websocket("/ws/live")
async def live_dashboard_events(websocket: WebSocket, key: str = Query(default="")) -> None:
    """Stream recent and live dashboard/chat events to connected clients."""

    if key != get_settings().dashboard_key:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    queue = get_live_event_bus().subscribe()
    ledger = get_request_ledger()
    try:
        for entry in reversed(ledger.get_recent(10)):
            await websocket.send_json(
                {
                    "type": "request_complete",
                    "timestamp": entry.timestamp,
                    "model": entry.model_requested,
                    "model_routed": entry.model_used,
                    "saved_tokens": max(entry.original_tokens - entry.sent_tokens, 0),
                    "saved_dollars": entry.saved_dollars,
                    "savings_pct": entry.savings_pct,
                    "cache_hit": entry.cache_hit,
                    "compression_tier": entry.compression_tier,
                    "latency_ms": entry.latency_ms,
                }
            )

        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
                await websocket.send_json(event.model_dump())
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
    except WebSocketDisconnect:
        logger.info("Dashboard websocket disconnected.")
    except asyncio.CancelledError:
        logger.info("Dashboard websocket cancelled.")
        raise
    except RuntimeError as exc:
        logger.info("Dashboard websocket closed: %s", exc)
    except Exception as exc:
        logger.warning("Dashboard websocket error: %r", exc)
    finally:
        get_live_event_bus().unsubscribe(queue)
