"""Registry and resolver for Lumin experimental features."""

from __future__ import annotations

from typing import Any

from config import get_settings
from experimental.base import ExperimentContext, ExperimentOutcome, ExperimentalFeature
from experimental.cget import CGETV0Experiment
from models.schemas import ChatCompletionRequest

_REGISTRY: dict[str, ExperimentalFeature] = {
    "cget_v0": CGETV0Experiment(),
}


def resolve_requested_experiments(request: ChatCompletionRequest) -> tuple[str, ...]:
    """Resolve enabled experiments from request extras and global allowlist."""

    settings = get_settings()
    if not settings.experiments_enabled:
        return ()

    extras = request.model_extra or {}
    names: list[str] = []
    raw = extras.get("lumin_experiments")
    if isinstance(raw, str):
        names.extend(part.strip() for part in raw.split(",") if part.strip())
    elif isinstance(raw, list):
        names.extend(str(part).strip() for part in raw if str(part).strip())

    if _truthy(extras.get("lumin_experimental_cget")):
        names.append("cget_v0")
    if _truthy(extras.get("lumin_experimental_scaffold_fill")):
        names.append("scaffold_fill_v0")
    if _truthy(extras.get("lumin_experimental_spe")):
        names.append("spe_v0")

    allowed = set(settings.allowed_experiments)
    unique_names: list[str] = []
    for name in names:
        if name in allowed and name not in unique_names:
            unique_names.append(name)
    return tuple(unique_names)


def apply_experiments(
    request: ChatCompletionRequest,
    model: str,
    provider: str,
    response_payload: dict[str, Any],
    enabled_experiments: tuple[str, ...],
) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
    """Apply enabled experimental features to a response payload."""

    headers: dict[str, str] = {}
    metadata: dict[str, Any] = {}
    payload = response_payload
    applied_names: list[str] = []

    for name in enabled_experiments:
        feature = _REGISTRY.get(name)
        if feature is None:
            metadata[name] = {"status": "unknown"}
            continue
        outcome = feature.apply(
            ExperimentContext(
                request=request,
                model=model,
                provider=provider,
                response_payload=payload,
            )
        )
        payload = outcome.response_payload
        headers.update(outcome.headers)
        metadata[name] = outcome.metadata | {"status": outcome.status, "applied": outcome.applied}
        if outcome.applied:
            applied_names.append(name)

    headers["X-Lumin-Experiments"] = ",".join(enabled_experiments) if enabled_experiments else "none"
    headers["X-Lumin-Experiment-Status"] = ",".join(applied_names) if applied_names else "none"
    return payload, headers, metadata


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
