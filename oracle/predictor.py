"""Cost oracle endpoint and helper utilities."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from engine.cache import get_semantic_cache
from engine.tokenizer import count_and_price, supported_models
from models.schemas import ModelCostBreakdown, PredictRequest, PredictResponse

router = APIRouter(tags=["oracle"])
semantic_cache = get_semantic_cache()


def _resolve_candidate_models(request: PredictRequest) -> list[str]:
    """Resolve candidate models from the request."""

    if request.candidate_models:
        return request.candidate_models
    return supported_models()


def build_prediction(request: PredictRequest) -> PredictResponse:
    """Build a prediction response for the supplied request."""

    projected_output_tokens = (
        request.expected_output_tokens
        or request.max_completion_tokens
        or request.max_tokens
        or 0
    )

    try:
        breakdown = [
            count_and_price(model, request.messages, projected_output_tokens)
            for model in _resolve_candidate_models(request)
        ]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    request_payload = request.model_dump(exclude_none=True)
    serialized_breakdown: list[ModelCostBreakdown] = []
    for item in breakdown:
        cache_estimate = semantic_cache.estimate(
            item.model,
            {**request_payload, "model": item.model},
        )
        serialized_breakdown.append(
            ModelCostBreakdown(
                model=item.model,
                provider=item.provider,
                input_tokens=item.input_tokens,
                projected_output_tokens=item.projected_output_tokens,
                input_cost=round(item.input_cost, 8),
                output_cost=round(item.output_cost, 8),
                total_cost=round(item.total_cost, 8),
                semantic_cache_hit_score=round(cache_estimate["hit_score"], 6),
                cache_adjusted_total_cost=round(
                    max(item.total_cost - cache_estimate["estimated_saved_cost"], 0.0),
                    8,
                ),
            )
        )
    cheapest = min(serialized_breakdown, key=lambda item: item.total_cost)
    cache_adjusted_cheapest = min(
        serialized_breakdown,
        key=lambda item: item.cache_adjusted_total_cost or item.total_cost,
    )
    best_hit_score = max((item.semantic_cache_hit_score or 0.0) for item in serialized_breakdown)

    if request.model and request.model == cheapest.model:
        reason = "Requested model is already the cheapest option for this prompt."
    elif request.model:
        reason = (
            f"{cheapest.model} is cheaper than {request.model} for the current prompt "
            "and projected output size."
        )
    else:
        reason = "Recommended lowest projected cost based on the current prompt."

    return PredictResponse(
        requested_model=request.model,
        cheapest_model=cheapest.model,
        recommended_model_reason=reason,
        breakdown=serialized_breakdown,
        semantic_cache_hit_score=round(best_hit_score, 6),
        semantic_cache_adjusted_cheapest_model=cache_adjusted_cheapest.model,
    )


@router.post("/v1/predict", response_model=PredictResponse)
async def predict(request: PredictRequest) -> PredictResponse:
    """Return preflight token and cost predictions before any model call is made."""

    return build_prediction(request)
