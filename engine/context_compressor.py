"""NanoClaw-aware context compression prior to the main prompt pipeline."""

from __future__ import annotations

import hashlib
import re
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from typing import Any

from config import get_settings
from engine.tokenizer import count_input_tokens

_HEADING_RE = re.compile(r"(?m)^(#{1,3}\s+[^\n]+)\n")
_BULLET_RE = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+")
_EXAMPLE_RE = re.compile(r"(?im)^#{1,3}\s+example\b|^example\b|^sample\b")
_CODE_FENCE_RE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
_MULTISPACE_RE = re.compile(r"[ \t]{2,}")
_MULTIBLANK_RE = re.compile(r"\n{3,}")
_MEMORY_ENTRY_RE = re.compile(r"(?m)^(?:[-*]\s+|\d+[.)]\s+)")
_SIGNATURE_RE = re.compile(r"\b(?:def|class|async def)\b|\([^)]*\)|->|`[^`]+`")

_SUMMARY_PRIORITY_TERMS = (
    "must",
    "never",
    "always",
    "required",
    "constraint",
    "important",
    "use ",
    "avoid",
    "keep",
    "preserve",
    "return",
    "parameter",
    "function",
    "role",
    "goal",
)
_DISTILL_MIN_SECTION_CHARS = 220
_DISTILL_MAX_LINES_FREE = 6
_DISTILL_MAX_LINES_PRO = 4


@dataclass(frozen=True, slots=True)
class ContextCompressionResult:
    """Result for NanoClaw-specific context preprocessing."""

    original_tokens: int
    compressed_tokens: int
    compressed_messages: list[Any]
    savings_tokens: int
    savings_pct: float
    compression_breakdown: dict[str, Any]
    applied: bool


@dataclass(slots=True)
class _SessionBlockState:
    """Cached distilled state for a repeated context block within one session."""

    content_hash: str
    summary: str
    repeat_count: int = 1


def _message_role(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("role", ""))
    return str(getattr(message, "role", ""))


def _message_content(message: Any) -> Any:
    if isinstance(message, dict):
        return message.get("content")
    return getattr(message, "content", None)


def _replace_message_content(message: Any, content: str) -> Any:
    if isinstance(message, dict):
        updated = dict(message)
        updated["content"] = content
        return updated
    return message.model_copy(update={"content": content})


def _normalize_markdown(text: str) -> str:
    text = text.replace("\r\n", "\n")
    text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)
    text = _MULTISPACE_RE.sub(" ", text)
    text = _MULTIBLANK_RE.sub("\n\n", text)
    return text.strip()


def _hash_text(text: str, tier: str, task: str) -> str:
    payload = f"{tier}\0{task}\0{text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _stable_block_id(heading: str, index: int) -> str:
    """Build a stable per-section identifier for session-level tracking."""

    normalized_heading = re.sub(r"\s+", " ", heading.strip().lower())
    if normalized_heading:
        return f"{index}:{normalized_heading}"
    return f"__root__:{index}"


def _split_sections(text: str) -> list[tuple[str, str]]:
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [("", text)]

    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.append((match.group(1).strip(), text[start:end].strip()))
    return sections


def _dedupe_lines(lines: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for line in lines:
        key = line.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(line.strip())
    return deduped


def _lexical_overlap_score(text: str, task: str) -> float:
    text_terms = set(re.findall(r"\b[a-z0-9_]{4,}\b", text.lower()))
    task_terms = set(re.findall(r"\b[a-z0-9_]{4,}\b", task.lower()))
    if not text_terms or not task_terms:
        return 0.0
    return len(text_terms & task_terms) / max(len(task_terms), 1)


def _compress_general_section(body: str, aggressive: bool) -> tuple[str, dict[str, int]]:
    code_blocks = _CODE_FENCE_RE.findall(body)
    body_wo_code = _CODE_FENCE_RE.sub("", body)
    lines = [line.strip() for line in body_wo_code.splitlines() if line.strip()]
    original_line_count = len(lines)
    lines = _dedupe_lines(lines)

    examples_seen = 0
    trimmed_lines: list[str] = []
    bullet_groups: list[str] = []
    for line in lines:
        if _EXAMPLE_RE.match(line):
            examples_seen += 1
            if examples_seen > (2 if aggressive else 3):
                continue
        if _BULLET_RE.match(line):
            bullet_groups.append(_BULLET_RE.sub("", line))
            continue
        trimmed_lines.append(line)

    if bullet_groups:
        keep = bullet_groups[: (4 if aggressive else 6)]
        trimmed_lines.append("Key points: " + "; ".join(keep) + ".")

    compressed = "\n".join(trimmed_lines).strip()
    if code_blocks:
        compressed = "\n\n".join(part for part in [compressed, *code_blocks] if part).strip()
    return _normalize_markdown(compressed), {
        "lines_removed": max(original_line_count - len(trimmed_lines), 0),
        "examples_trimmed": max(examples_seen - (2 if aggressive else 3), 0),
        "bullet_groups_collapsed": 1 if bullet_groups else 0,
    }


def _compress_skill_section(body: str, aggressive: bool) -> tuple[str, dict[str, int]]:
    lines = [line.rstrip() for line in body.splitlines() if line.strip()]
    kept: list[str] = []
    examples = 0
    paragraphs_kept = 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            kept.append(stripped)
            continue
        if _BULLET_RE.match(stripped):
            kept.append(stripped)
            continue
        lowered = stripped.lower()
        if any(token in lowered for token in ("must", "never", "always", "constraint", "parameter", "return", "usage")):
            kept.append(stripped)
            continue
        if _EXAMPLE_RE.match(stripped):
            examples += 1
            if examples <= 2:
                kept.append(stripped)
            continue
        if paragraphs_kept < (1 if aggressive else 2):
            kept.append(stripped)
            paragraphs_kept += 1

    compressed = "\n".join(_dedupe_lines(kept))
    return _normalize_markdown(compressed), {
        "skills_lines_kept": len(kept),
        "skills_examples_trimmed": max(examples - 2, 0),
    }


def _split_memory_entries(body: str) -> list[str]:
    if _MEMORY_ENTRY_RE.search(body):
        return [chunk.strip() for chunk in _MEMORY_ENTRY_RE.split(body) if chunk.strip()]
    return [chunk.strip() for chunk in re.split(r"\n{2,}", body) if chunk.strip()]


def _prioritize_summary_lines(lines: list[str], *, aggressive: bool) -> list[str]:
    """Keep the highest-signal lines for a repeated-block distilled summary."""

    if not lines:
        return []

    max_lines = _DISTILL_MAX_LINES_PRO if aggressive else _DISTILL_MAX_LINES_FREE
    must_keep: list[str] = []
    useful: list[str] = []
    filler: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        if any(term in lowered for term in _SUMMARY_PRIORITY_TERMS) or _SIGNATURE_RE.search(stripped):
            must_keep.append(stripped)
        elif _BULLET_RE.match(stripped) or stripped.startswith(("Key points:", "Relevant memory:")):
            useful.append(stripped)
        else:
            filler.append(stripped)

    ordered = _dedupe_lines([*must_keep, *useful, *filler])
    return ordered[:max_lines]


def _shorten_summary_line(line: str, *, aggressive: bool) -> str:
    """Trim verbose prose into a smaller, still-meaningful reminder."""

    stripped = line.strip()
    if stripped.startswith("Key points:"):
        items = [item.strip(" .") for item in stripped[len("Key points:"):].split(";") if item.strip()]
        keep = items[: (3 if aggressive else 4)]
        return "Key points: " + "; ".join(keep) + "."

    max_chars = 96 if aggressive else 140
    if len(stripped) <= max_chars:
        return stripped

    sentence_candidates = [part.strip(" .") for part in re.split(r"[.;]\s+", stripped) if part.strip()]
    priority_parts = [
        part for part in sentence_candidates
        if any(term in part.lower() for term in _SUMMARY_PRIORITY_TERMS)
    ]
    ordered_parts = _dedupe_lines([*(priority_parts or sentence_candidates[:1]), *sentence_candidates[:2]])
    shortened = "; ".join(ordered_parts)
    if len(shortened) > max_chars:
        shortened = shortened[: max_chars - 1].rstrip(" ,;:") + "…"
    return shortened


def _build_distilled_summary(heading: str, compressed: str, *, aggressive: bool) -> str:
    """Create a compact deterministic replacement for repeated context blocks."""

    text = compressed.strip()
    if not text:
        return ""

    body = text
    if heading and text.startswith(heading):
        body = text[len(heading):].strip()

    body = _CODE_FENCE_RE.sub("", body)
    body_lines = [line.strip() for line in body.splitlines() if line.strip()]
    summary_lines = _prioritize_summary_lines(body_lines, aggressive=aggressive)
    summary_lines = [_shorten_summary_line(line, aggressive=aggressive) for line in summary_lines]

    if not summary_lines:
        fallback = _normalize_markdown(body)[:240 if aggressive else 320].strip()
        summary_lines = [fallback] if fallback else []

    prefix = "Established context summary:"
    summary_body = "\n".join(f"- {line}" if not line.startswith("- ") else line for line in summary_lines)
    if heading:
        return _normalize_markdown(f"{heading}\n{prefix}\n{summary_body}")
    return _normalize_markdown(f"{prefix}\n{summary_body}")


def _compress_memory_section(body: str, task: str, aggressive: bool) -> tuple[str, dict[str, int]]:
    entries = _split_memory_entries(body)
    if not entries:
        return "", {"memory_entries_kept": 0, "memory_entries_dropped": 0}

    recent = entries[-3:]
    scored = [
        (entry, _lexical_overlap_score(entry, task))
        for entry in entries[:-3]
    ]
    scored.sort(key=lambda item: item[1], reverse=True)
    top_k = 5 if aggressive else 7
    relevant = [entry for entry, score in scored[:top_k] if score > 0.0]
    ordered: list[str] = []
    seen: set[str] = set()
    for entry in [*relevant, *recent]:
        if entry in seen:
            continue
        seen.add(entry)
        ordered.append(entry)
    prefix = "Relevant memory:\n" if ordered else ""
    compressed = prefix + "\n".join(f"- {entry}" for entry in ordered)
    return _normalize_markdown(compressed), {
        "memory_entries_kept": len(ordered),
        "memory_entries_dropped": max(len(entries) - len(ordered), 0),
    }


class NanoClawContextCompressor:
    """Compress NanoClaw-injected context files inside system prompts."""

    def __init__(self) -> None:
        settings = get_settings()
        self._cache: dict[str, str] = {}
        self._summary_cache: dict[str, str] = {}
        self._session_blocks: OrderedDict[str, OrderedDict[str, _SessionBlockState]] = OrderedDict()
        self._max_sessions = settings.context_distill_max_sessions
        self._max_blocks_per_session = settings.context_distill_max_blocks_per_session
        self._min_saved_tokens = settings.context_distill_min_saved_tokens
        self._min_saved_pct = settings.context_distill_min_saved_pct
        self._lock = Lock()

    @staticmethod
    def _base_breakdown() -> dict[str, int]:
        """Return a fully-populated baseline breakdown dict."""

        return {
            "context_messages_compressed": 0,
            "distilled_references_used": 0,
            "distilled_updates_used": 0,
            "session_cache_hits": 0,
            "distillation_attempts": 0,
            "distillation_skips": 0,
            "distillation_safe_fallbacks": 0,
        }

    def _estimate_text_tokens(self, model: str, text: str) -> int:
        """Estimate tokens for a single text block using the request tokenizer."""

        return count_input_tokens(model, [{"role": "system", "content": text}])

    def _touch_session(self, context_id: str) -> OrderedDict[str, _SessionBlockState]:
        """Return the session block map while keeping total session state bounded."""

        session_blocks = self._session_blocks.get(context_id)
        if session_blocks is None:
            session_blocks = OrderedDict()
            self._session_blocks[context_id] = session_blocks
        else:
            self._session_blocks.move_to_end(context_id)

        while len(self._session_blocks) > self._max_sessions:
            self._session_blocks.popitem(last=False)
        return session_blocks

    def _store_session_block(
        self,
        context_id: str,
        block_id: str,
        state: _SessionBlockState,
    ) -> None:
        """Insert/update a session block while keeping per-session state bounded."""

        session_blocks = self._touch_session(context_id)
        session_blocks[block_id] = state
        session_blocks.move_to_end(block_id)
        while len(session_blocks) > self._max_blocks_per_session:
            session_blocks.popitem(last=False)

    def _distillation_is_worth_it(self, model: str, original: str, distilled: str) -> bool:
        """Return True only when the distilled replacement clears the savings floor."""

        if not distilled or distilled == original:
            return False
        original_tokens = self._estimate_text_tokens(model, original)
        distilled_tokens = self._estimate_text_tokens(model, distilled)
        saved_tokens = original_tokens - distilled_tokens
        if saved_tokens < self._min_saved_tokens:
            return False
        saved_pct = (saved_tokens / original_tokens) * 100 if original_tokens else 0.0
        return saved_pct >= self._min_saved_pct

    def _compress_section(self, heading: str, body: str, task: str, tier: str) -> tuple[str, dict[str, int]]:
        heading_lower = heading.lower()
        aggressive = str(tier).strip().lower() != "free"
        if "skill" in heading_lower:
            compressed, breakdown = _compress_skill_section(body, aggressive=aggressive)
        elif "memory" in heading_lower:
            compressed, breakdown = _compress_memory_section(body, task=task, aggressive=aggressive)
        else:
            compressed, breakdown = _compress_general_section(body, aggressive=aggressive)

        if heading:
            compressed = f"{heading}\n{compressed}".strip()
        return compressed, breakdown

    def _compress_text(self, text: str, task: str, tier: str) -> tuple[str, dict[str, int]]:
        normalized = _normalize_markdown(text)
        cache_key = _hash_text(normalized, tier, task)
        with self._lock:
            cached = self._cache.get(cache_key)
        if cached is not None:
            return cached, {"cache_hit": 1}

        sections = _split_sections(normalized)
        compressed_sections: list[str] = []
        breakdown: dict[str, int] = {"cache_hit": 0}
        for heading, body in sections:
            compressed, section_breakdown = self._compress_section(heading, body, task, tier)
            if compressed:
                compressed_sections.append(compressed)
            for key, value in section_breakdown.items():
                breakdown[key] = breakdown.get(key, 0) + value

        compressed_text = _normalize_markdown("\n\n".join(compressed_sections))
        with self._lock:
            self._cache[cache_key] = compressed_text
        return compressed_text, breakdown

    def _summarize_text(self, text: str, task: str, tier: str) -> str:
        """Build or fetch a compact deterministic summary for repeated context."""

        normalized = _normalize_markdown(text)
        cache_key = _hash_text(normalized, tier, task)
        with self._lock:
            cached = self._summary_cache.get(cache_key)
        if cached is not None:
            return cached

        sections = _split_sections(normalized)
        aggressive = str(tier).strip().lower() != "free"
        summaries: list[str] = []
        for heading, body in sections:
            compressed, _ = self._compress_section(heading, body, task, tier)
            summary = _build_distilled_summary(heading, compressed, aggressive=aggressive)
            if summary:
                summaries.append(summary)

        summary_text = _normalize_markdown("\n\n".join(summaries))
        with self._lock:
            self._summary_cache[cache_key] = summary_text
        return summary_text

    def compress(
        self,
        *,
        model: str,
        messages: list[Any],
        tier: str,
        context_id: str | None,
    ) -> ContextCompressionResult:
        """Compress NanoClaw context-bearing system messages before main compression."""
        original_tokens = count_input_tokens(model, messages)
        if not messages:
            return ContextCompressionResult(
                original_tokens=0,
                compressed_tokens=0,
                compressed_messages=messages,
                savings_tokens=0,
                savings_pct=0.0,
                compression_breakdown=self._base_breakdown(),
                applied=False,
            )

        last_user_text = ""
        for message in reversed(messages):
            if _message_role(message) == "user" and isinstance(_message_content(message), str):
                last_user_text = str(_message_content(message))
                break

        transformed = list(messages)
        compressed_count = 0
        breakdown: dict[str, int] = self._base_breakdown()
        for index, message in enumerate(messages):
            if _message_role(message) not in {"system", "developer"}:
                continue
            content = _message_content(message)
            if not isinstance(content, str):
                continue
            lowered = content.lower()
            if not any(marker in lowered for marker in ("claude.md", "skill", "memory", "# claude", "# skills", "# memory")):
                continue
            compressed_text, section_breakdown = self._compress_text(content, last_user_text, tier)
            candidate_text = compressed_text
            if context_id:
                sections = _split_sections(compressed_text)
                distilled_sections: list[str] = []
                distillation_changed = False
                for section_index, (heading, body) in enumerate(sections):
                    section_text = f"{heading}\n{body}".strip() if heading else body.strip()
                    if not section_text:
                        continue
                    block_id = _stable_block_id(heading, section_index)
                    content_hash = _hash_text(section_text, tier, last_user_text)
                    distilled_section = section_text

                    if len(section_text) >= _DISTILL_MIN_SECTION_CHARS:
                        breakdown["distillation_attempts"] = breakdown.get("distillation_attempts", 0) + 1
                        summary = self._summarize_text(section_text, last_user_text, tier)
                        with self._lock:
                            session_blocks = self._touch_session(context_id)
                            previous = session_blocks.get(block_id)
                            if previous is None:
                                self._store_session_block(
                                    context_id,
                                    block_id,
                                    _SessionBlockState(content_hash=content_hash, summary=summary),
                                )
                            else:
                                previous.repeat_count += 1
                                session_blocks.move_to_end(block_id)
                                breakdown["session_cache_hits"] = breakdown.get("session_cache_hits", 0) + 1
                                if previous.content_hash == content_hash:
                                    if self._distillation_is_worth_it(model, section_text, previous.summary):
                                        distilled_section = previous.summary
                                        breakdown["distilled_references_used"] = (
                                            breakdown.get("distilled_references_used", 0) + 1
                                        )
                                        distillation_changed = True
                                    else:
                                        breakdown["distillation_skips"] = breakdown.get("distillation_skips", 0) + 1
                                else:
                                    updated_state = _SessionBlockState(
                                        content_hash=content_hash,
                                        summary=summary,
                                        repeat_count=previous.repeat_count,
                                    )
                                    self._store_session_block(context_id, block_id, updated_state)
                                    if self._distillation_is_worth_it(model, section_text, summary):
                                        distilled_section = summary
                                        breakdown["distilled_updates_used"] = (
                                            breakdown.get("distilled_updates_used", 0) + 1
                                        )
                                        distillation_changed = True
                                    else:
                                        breakdown["distillation_skips"] = breakdown.get("distillation_skips", 0) + 1

                    distilled_sections.append(distilled_section)

                if distillation_changed and distilled_sections:
                    distilled_candidate = _normalize_markdown("\n\n".join(distilled_sections))
                    if distilled_candidate and len(distilled_candidate) < len(candidate_text):
                        candidate_text = distilled_candidate
                    else:
                        breakdown["distillation_safe_fallbacks"] = (
                            breakdown.get("distillation_safe_fallbacks", 0) + 1
                        )

            if candidate_text and len(candidate_text) < len(content):
                transformed[index] = _replace_message_content(message, candidate_text)
                compressed_count += 1
                for key, value in section_breakdown.items():
                    breakdown[key] = breakdown.get(key, 0) + value

        compressed_tokens = count_input_tokens(model, transformed)
        if compressed_tokens >= original_tokens:
            return ContextCompressionResult(
                original_tokens=original_tokens,
                compressed_tokens=original_tokens,
                compressed_messages=messages,
                savings_tokens=0,
                savings_pct=0.0,
                compression_breakdown={**self._base_breakdown(), "fallback_original": 1},
                applied=False,
            )

        savings_tokens = original_tokens - compressed_tokens
        breakdown["context_messages_compressed"] = compressed_count
        return ContextCompressionResult(
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            compressed_messages=transformed,
            savings_tokens=savings_tokens,
            savings_pct=round((savings_tokens / original_tokens) * 100, 4) if original_tokens else 0.0,
            compression_breakdown=breakdown,
            applied=compressed_count > 0,
        )


_context_compressor = NanoClawContextCompressor()


def get_nanoclaw_context_compressor() -> NanoClawContextCompressor:
    """Return the shared NanoClaw context compressor singleton."""

    return _context_compressor
