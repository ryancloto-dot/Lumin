"""Confidence-Gated Early Termination experimental feature.

Current implementation is the safe `cget_v0` phase:
- preamble trimming
- optional signoff trimming

No streaming termination is attempted yet.
"""

from __future__ import annotations

import json
import re
from typing import Any

from engine.tokenizer import count_input_tokens
from experimental.base import ExperimentContext, ExperimentOutcome, ExperimentalFeature

_PREAMBLE_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n{2,}")
_PREAMBLE_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"sure!?|"
    r"certainly!?|"
    r"of course!?|"
    r"absolutely!?|"
    r"great question!?|"
    r"happy to help!?|"
    r"i(?:'d| would)?\s+be\s+happy\s+to\b|"
    r"i(?:'ll| will)\b|"
    r"let me\b|"
    r"based on (?:your|the)\s+request\b|"
    r"looking at (?:this|the)\b"
    r")",
    re.IGNORECASE,
)
_SIGNOFF_RE = re.compile(
    r"(?:\n{1,2}|[.!?]\s+)"
    r"(?:(?:let me know if\b|feel free to ask\b|happy to help\b|"
    r"if you'd like,? I can\b|if you want,? I can\b).*)$",
    re.IGNORECASE | re.DOTALL,
)
_SUBSTANCE_CUES = (
    "```",
    "<tool_call",
    "{",
    "[",
    "/",
    ".py",
    ".ts",
    "line ",
    "section ",
    "port ",
    "def ",
    "class ",
    "import ",
    "error",
    "failed",
    "fix",
    "bug",
    "route",
    "model",
    "endpoint",
)
_STRUCTURED_RESPONSE_CUES = (
    "write",
    "build",
    "implement",
    "fix",
    "debug",
    "review",
    "summarize",
    "find",
    "check",
    "read",
    "analyze",
    "what port",
    "what model",
)
_CONVERSATIONAL_CUES = (
    "how are you",
    "what do you think",
    "tell me a story",
    "brainstorm",
    "creative",
    "poem",
    "joke",
)


def _assistant_response_content(payload: dict[str, Any]) -> str | None:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    message = choices[0].get("message", {})
    content = message.get("content")
    return content if isinstance(content, str) else None


def _set_assistant_response_content(payload: dict[str, Any], content: str) -> None:
    payload["choices"][0]["message"]["content"] = content


def _task_intent(request: ExperimentContext) -> str:
    """Classify a request into a conservative intent bucket."""

    joined = "\n".join(
        str(message.content)
        for message in request.request.messages
        if isinstance(message.content, str)
    ).lower()
    if any(cue in joined for cue in _CONVERSATIONAL_CUES):
        return "conversational"
    if any(cue in joined for cue in ("write code", "generate code", "return code", "output code", "fastapi", "python")):
        return "code_gen"
    if any(cue in joined for cue in _STRUCTURED_RESPONSE_CUES):
        return "task"
    return "other"


def _is_substantive(text: str) -> bool:
    lowered = text.lower()
    return any(cue in lowered for cue in _SUBSTANCE_CUES) or len(text.split()) >= 8


def _trim_preamble(text: str) -> tuple[str, int]:
    """Remove obvious leading throat-clearing before the first substantive segment."""

    original = text
    segments = [segment.strip() for segment in _PREAMBLE_SENTENCE_RE.split(text.strip()) if segment.strip()]
    kept: list[str] = []
    dropping = True
    for segment in segments:
        if dropping and _PREAMBLE_PREFIX_RE.match(segment) and not _is_substantive(segment):
            continue
        dropping = False
        kept.append(segment)

    trimmed = "\n\n".join(kept).strip()
    if not trimmed:
        return original, 0
    removed = len(original) - len(trimmed)
    return trimmed if removed > 0 else original, max(removed, 0)


def _trim_signoff(text: str) -> tuple[str, int]:
    """Remove low-signal closing signoffs from the response tail."""

    match = _SIGNOFF_RE.search(text.strip())
    if match is None:
        return text, 0
    trimmed = text[: match.start()].rstrip()
    removed = len(text) - len(trimmed)
    if not trimmed:
        return text, 0
    return trimmed, removed


class CGETV0Experiment(ExperimentalFeature):
    """Safe first phase of output trimming."""

    name = "cget_v0"

    def apply(self, context: ExperimentContext) -> ExperimentOutcome:
        content = _assistant_response_content(context.response_payload)
        if content is None:
            return ExperimentOutcome(
                name=self.name,
                applied=False,
                status="skipped_non_text",
                response_payload=context.response_payload,
                headers={"X-Lumin-CGET": "skipped_non_text"},
            )

        extras = context.request.model_extra or {}
        mode = str(extras.get("lumin_cget_mode") or "preamble").lower()
        intent = _task_intent(context)
        if intent not in {"task", "code_gen"}:
            return ExperimentOutcome(
                name=self.name,
                applied=False,
                status="skipped_intent",
                response_payload=context.response_payload,
                headers={"X-Lumin-CGET": "skipped_intent"},
                metadata={"intent": intent},
            )

        original_text = content
        trimmed_text, preamble_chars = _trim_preamble(content)
        signoff_chars = 0
        if mode in {"signoff", "full"}:
            trimmed_text, signoff_chars = _trim_signoff(trimmed_text)

        if trimmed_text == original_text:
            return ExperimentOutcome(
                name=self.name,
                applied=False,
                status="no_change",
                response_payload=context.response_payload,
                headers={
                    "X-Lumin-CGET": "no_change",
                    "X-Lumin-CGET-Saved-Tokens": "0",
                },
                metadata={"intent": intent, "mode": mode},
            )

        original_tokens = count_input_tokens(context.model, [{"role": "assistant", "content": original_text}])
        trimmed_tokens = count_input_tokens(context.model, [{"role": "assistant", "content": trimmed_text}])
        saved_tokens = max(original_tokens - trimmed_tokens, 0)
        if saved_tokens <= 0:
            return ExperimentOutcome(
                name=self.name,
                applied=False,
                status="below_threshold",
                response_payload=context.response_payload,
                headers={
                    "X-Lumin-CGET": "below_threshold",
                    "X-Lumin-CGET-Saved-Tokens": "0",
                },
                metadata={"intent": intent, "mode": mode},
            )

        updated_payload = json.loads(json.dumps(context.response_payload))
        _set_assistant_response_content(updated_payload, trimmed_text)
        return ExperimentOutcome(
            name=self.name,
            applied=True,
            status="trimmed",
            response_payload=updated_payload,
            headers={
                "X-Lumin-CGET": "trimmed",
                "X-Lumin-CGET-Saved-Tokens": str(saved_tokens),
                "X-Lumin-CGET-Mode": mode,
            },
            metadata={
                "intent": intent,
                "mode": mode,
                "saved_tokens": saved_tokens,
                "preamble_chars_removed": preamble_chars,
                "signoff_chars_removed": signoff_chars,
            },
        )
