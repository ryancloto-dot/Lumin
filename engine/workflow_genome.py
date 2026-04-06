"""Workflow genome mapping for agentic session shape detection.

This is a conservative v0 implementation:
- heuristic only
- no learned clustering yet
- zero hard behavior changes by default
- used for headers, logging, benchmark labeling, and future optimization hooks
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class WorkflowGenomeMatch:
    """A lightweight structural workflow classification."""

    genome: str
    confidence: float
    agentic: bool
    expected_steps: tuple[int, int]
    loop_threshold: int
    notes: str


_TEST_FAILURE_RE = re.compile(
    r"\b(pytest|unittest|test_[a-z0-9_]+|assertionerror|failed\b|traceback)\b",
    re.IGNORECASE,
)
_READ_OP_RE = re.compile(
    r"\b(read_file|open file|inspect|look at|main\.py|config\.ts|\.py\b|\.ts\b)\b",
    re.IGNORECASE,
)
_LIST_OP_RE = re.compile(
    r"\b(list_directory|ls |tree |files?:|directory|workspace|codebase)\b",
    re.IGNORECASE,
)
_FEATURE_RE = re.compile(
    r"\b(add|build|create|implement|generate|feature|endpoint|crud|route|schema|model)\b",
    re.IGNORECASE,
)
_REFACTOR_RE = re.compile(r"\b(refactor|cleanup|reorganize|rename|extract)\b", re.IGNORECASE)
_ERROR_RE = re.compile(r"\b(error|exception|traceback|stack trace|failed)\b", re.IGNORECASE)
_RESEARCH_RE = re.compile(
    r"\b(contract|agreement|risk|summarize|research|analysis|vendor|evidence)\b",
    re.IGNORECASE,
)
_NANOCLAW_RE = re.compile(
    r"\b(nanoclaw|openclaw|claude\.md|skill\.md|memory\.md|group context|mobile companion)\b",
    re.IGNORECASE,
)
_PATCH_RE = re.compile(r"\b(edit_file|apply_patch|patch|fix|change)\b", re.IGNORECASE)


def _stringify_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_stringify_content(item) for item in content)
    if isinstance(content, dict):
        if content.get("type") == "text":
            return str(content.get("text", ""))
        return " ".join(f"{key}:{value}" for key, value in content.items())
    return str(content)


def _flatten_messages(messages: list[Any]) -> str:
    parts: list[str] = []
    for message in messages:
        if isinstance(message, dict):
            role = str(message.get("role", ""))
            content = message.get("content", "")
        else:
            role = str(getattr(message, "role", ""))
            content = getattr(message, "content", "")
        parts.append(f"{role}: {_stringify_content(content)}")
    return "\n".join(parts)


def detect_workflow_genome(messages: list[Any]) -> WorkflowGenomeMatch:
    """Classify a request/session into a known workflow genome shape."""

    text = _flatten_messages(messages)
    lowered = text.lower()
    read_hits = len(_READ_OP_RE.findall(text))
    list_hits = len(_LIST_OP_RE.findall(text))
    feature_hits = len(_FEATURE_RE.findall(text))
    refactor_hits = len(_REFACTOR_RE.findall(text))
    error_hits = len(_ERROR_RE.findall(text))
    test_hits = len(_TEST_FAILURE_RE.findall(text))
    patch_hits = len(_PATCH_RE.findall(text))
    research_hits = len(_RESEARCH_RE.findall(text))
    nanoclaw_hits = len(_NANOCLAW_RE.findall(text))

    if test_hits >= 2 and patch_hits >= 1:
        return WorkflowGenomeMatch(
            genome="debug_test_failure",
            confidence=0.91,
            agentic=True,
            expected_steps=(8, 12),
            loop_threshold=14,
            notes="test failure + patch cycle detected",
        )
    if research_hits >= 3 and error_hits == 0:
        return WorkflowGenomeMatch(
            genome="research_analysis",
            confidence=0.86,
            agentic=True,
            expected_steps=(6, 12),
            loop_threshold=16,
            notes="evidence-backed analysis workflow detected",
        )
    if refactor_hits >= 1 and read_hits >= 2:
        return WorkflowGenomeMatch(
            genome="refactor",
            confidence=0.83,
            agentic=True,
            expected_steps=(10, 18),
            loop_threshold=24,
            notes="multi-step refactor workflow detected",
        )
    if feature_hits >= 3 and patch_hits + read_hits >= 2:
        return WorkflowGenomeMatch(
            genome="add_feature",
            confidence=0.84,
            agentic=True,
            expected_steps=(8, 15),
            loop_threshold=18,
            notes="feature build/codegen workflow detected",
        )
    if error_hits >= 2 and patch_hits >= 1:
        return WorkflowGenomeMatch(
            genome="fix_from_error_log",
            confidence=0.8,
            agentic=True,
            expected_steps=(6, 11),
            loop_threshold=14,
            notes="error-log-driven repair workflow detected",
        )
    if list_hits >= 1 and read_hits >= 2 and feature_hits == 0:
        return WorkflowGenomeMatch(
            genome="explore_codebase",
            confidence=0.87,
            agentic=True,
            expected_steps=(10, 20),
            loop_threshold=24,
            notes="exploratory read-heavy codebase workflow detected",
        )
    if nanoclaw_hits >= 3:
        return WorkflowGenomeMatch(
            genome="nanoclaw_control",
            confidence=0.82,
            agentic=True,
            expected_steps=(6, 14),
            loop_threshold=18,
            notes="NanoClaw/OpenClaw control workflow detected",
        )

    return WorkflowGenomeMatch(
        genome="unknown",
        confidence=0.0,
        agentic=("tool result" in lowered) or (read_hits + list_hits + patch_hits >= 3),
        expected_steps=(0, 0),
        loop_threshold=0,
        notes="no strong genome match",
    )
