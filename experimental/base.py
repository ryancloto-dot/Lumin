"""Shared types for Lumin experimental features."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from models.schemas import ChatCompletionRequest


@dataclass(frozen=True, slots=True)
class ExperimentContext:
    """Context passed to experimental features."""

    request: ChatCompletionRequest
    model: str
    provider: str
    response_payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ExperimentOutcome:
    """Result returned by one experimental feature."""

    name: str
    applied: bool
    status: str
    response_payload: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class ExperimentalFeature:
    """Interface for one opt-in experimental feature."""

    name: str

    def apply(self, context: ExperimentContext) -> ExperimentOutcome:
        raise NotImplementedError
