"""Prompt compression with optional semantic verification."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from config import FILLER_PHRASES, get_settings
from engine.chunker import select_relevant_chunks
from engine.format_normalizer import FormatNormalizer
from engine.toon_converter import ToonConverter
from engine.tokenizer import calculate_cost, count_input_tokens
from models.schemas import ChatMessage

logger = logging.getLogger(__name__)

_MULTISPACE_RE = re.compile(r"[ \t]{2,}")
_MULTIBLANK_RE = re.compile(r"\n{3,}")
_TRAILING_WHITESPACE_RE = re.compile(r"[ \t]+$", flags=re.MULTILINE)
_ALWAYS_RE = re.compile(r"You should always\s+([^.!?]+)[.!?]", flags=re.IGNORECASE)
_EXAMPLE_RE = re.compile(r"^(?:example|sample)\b", flags=re.IGNORECASE)
_BULLET_RE = re.compile(r"^(\s*(?:[-*]|\d+[.)]))\s+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_YOU_ARE_REVIEWING_RE = re.compile(r"^you are reviewing\b", flags=re.IGNORECASE)
_YOU_ARE_PREFIX_RE = re.compile(r"^you are (?:an?|the)\s+", flags=re.IGNORECASE)
_SYSTEM_PHRASE_REWRITES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"^(?:(?:you are )?reviewing|review) code for bugs and regressions\.?$",
            flags=re.IGNORECASE,
        ),
        "Review code issues.",
    ),
)
_USER_PHRASE_REWRITES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\breview this small diff for issues:\s*", flags=re.IGNORECASE), "Review diff: "),
    (re.compile(r"\breview this diff for issues:\s*", flags=re.IGNORECASE), "Review diff: "),
    (re.compile(r"\breview diff:\s*", flags=re.IGNORECASE), "Diff: "),
)
_ASSISTANT_META_PREFIX_RE = re.compile(
    r"^(?:"
    r"certainly!\s*|"
    r"great question!\s*|"
    r"i will\s+|"
    r"restating context before acting:\s*|"
    r"before patching, restating the plan:\s*|"
    r"restating the exact patch intent one more time:\s*|"
    r"final pre-patch summary:\s*|"
    r"the fix appears straightforward:\s*|"
    r"tool result:\s*"
    r")",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class CompressResult:
    """Compression outcome returned to the proxy layer."""

    original_tokens: int
    compressed_tokens: int
    savings_tokens: int
    savings_pct: float
    savings_cost: float
    compressed_messages: list[Any]
    verification_passed: bool
    toon_conversions: int = 0
    toon_tokens_saved: int = 0
    compression_breakdown: dict[str, Any] = field(default_factory=dict)


def _stringify_content(content: Any) -> str:
    """Convert arbitrary message content into stable text."""

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text", "")))
            else:
                parts.append(json.dumps(part, sort_keys=True))
        return "\n".join(parts)
    if isinstance(content, dict):
        return json.dumps(content, sort_keys=True)
    return str(content)


def _normalize_for_compare(text: str) -> str:
    """Normalize text for conservative equivalence checks."""

    return " ".join(re.findall(r"[A-Za-z0-9_:/.-]+", text.lower()))


def _message_role(message: Any) -> str:
    """Return a message role from dict-style or object-style payloads."""

    if isinstance(message, dict):
        return str(message.get("role", ""))
    return str(getattr(message, "role", ""))


def _message_content(message: Any) -> Any:
    """Return message content from dict-style or object-style payloads."""

    if isinstance(message, dict):
        return message.get("content")
    return getattr(message, "content", None)


def _extract_text_message(message: Any) -> str | None:
    """Return text content for a message if available."""

    content = _message_content(message)
    if isinstance(content, str):
        return content
    return None


def _replace_text_message(message: Any, text: str) -> Any:
    """Return a cloned message with updated text content."""

    if isinstance(message, dict):
        updated = dict(message)
        updated["content"] = text
        return updated
    return message.model_copy(update={"content": text})


def _build_message_like(template: Any, role: str, content: str, name: str | None = None) -> Any:
    """Create a message using the same general shape as the template."""

    payload: dict[str, Any] = {"role": role, "content": content}
    if name is not None:
        payload["name"] = name
    if isinstance(template, dict):
        return payload
    return ChatMessage(**payload)


def _find_last_user_index(messages: list[Any]) -> int | None:
    """Return the index of the last user-authored message."""

    for index in range(len(messages) - 1, -1, -1):
        if _message_role(messages[index]) == "user":
            return index
    return None


def _normalize_whitespace(text: str) -> tuple[str, dict[str, int]]:
    """Apply deterministic whitespace cleanup."""

    text = text.replace("\r\n", "\n")
    trailing_matches = len(_TRAILING_WHITESPACE_RE.findall(text))
    text = _TRAILING_WHITESPACE_RE.sub("", text)
    before_blank_runs = len(_MULTIBLANK_RE.findall(text))
    text = _MULTIBLANK_RE.sub("\n\n", text)

    lines = text.split("\n")
    in_code_block = False
    normalized_lines: list[str] = []
    removed_code_empty_lines = 0
    for line in lines:
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            normalized_lines.append(line.rstrip())
            continue

        collapsed = _MULTISPACE_RE.sub(" ", line) if not in_code_block else line.rstrip()
        if in_code_block and not collapsed.strip():
            removed_code_empty_lines += 1
            continue
        normalized_lines.append(collapsed)

    return "\n".join(normalized_lines).strip(), {
        "trailing_whitespace_removed": trailing_matches,
        "blank_runs_collapsed": before_blank_runs,
        "empty_code_lines_removed": removed_code_empty_lines,
    }


def _remove_filler_phrases(text: str) -> tuple[str, int]:
    """Strip configurable filler phrases from a message."""

    updated = text
    removed = 0
    for phrase in FILLER_PHRASES:
        escaped = re.escape(phrase)
        updated, count = re.subn(
            rf"(^|[\s]){escaped}[^.!?\n]*[.!?]?\s*",
            " ",
            updated,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        removed += count
    updated = re.sub(r"\s{2,}", " ", updated)
    return updated.strip(), removed


def _strip_assistant_meta_prefixes(text: str) -> tuple[str, int]:
    """Strip low-signal assistant prefixes while preserving the substantive clause."""

    updated = text.strip()
    removed = 0
    while True:
        match = _ASSISTANT_META_PREFIX_RE.match(updated)
        if match is None:
            break
        updated = updated[match.end():].lstrip()
        removed += 1
    updated = re.sub(r"^[,;:\-\s]+", "", updated).strip()
    return updated or text.strip(), removed


def _compress_system_text(text: str) -> tuple[str, dict[str, int]]:
    """Compress system prompts without changing their intent."""

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    deduped_lines = list(dict.fromkeys(lines))
    duplicate_lines_removed = max(len(lines) - len(deduped_lines), 0)

    example_lines = [line for line in deduped_lines if _EXAMPLE_RE.match(line)]
    examples_trimmed = max(len(example_lines) - 3, 0)
    if examples_trimmed:
        kept = 0
        trimmed_lines: list[str] = []
        for line in deduped_lines:
            if _EXAMPLE_RE.match(line):
                kept += 1
                if kept > 3:
                    continue
            trimmed_lines.append(line)
        deduped_lines = trimmed_lines

    system_text = "\n".join(deduped_lines)

    repeated_sentences_removed = 0
    sentence_counts: dict[str, int] = {}
    ordered_sentences: list[str] = []
    for sentence in _SENTENCE_SPLIT_RE.split(system_text):
        cleaned = sentence.strip()
        if not cleaned:
            continue
        count = sentence_counts.get(cleaned, 0)
        if count == 0:
            ordered_sentences.append(cleaned)
        else:
            repeated_sentences_removed += 1
        sentence_counts[cleaned] = count + 1
    if repeated_sentences_removed:
        system_text = " ".join(ordered_sentences).strip()

    shortened_role_prefix = 0
    if system_text:
        compact = _YOU_ARE_REVIEWING_RE.sub("Review", system_text, count=1).strip()
        if compact == system_text:
            compact = _YOU_ARE_PREFIX_RE.sub("", system_text, count=1).strip()
        if compact and compact != system_text:
            if compact and compact[0].isalpha():
                compact = compact[0].upper() + compact[1:]
            original_tokens = count_input_tokens("gpt-4o-mini", [ChatMessage(role="system", content=system_text)])
            compact_tokens = count_input_tokens("gpt-4o-mini", [ChatMessage(role="system", content=compact)])
            if compact_tokens < original_tokens or len(compact) < len(system_text):
                system_text = compact
                shortened_role_prefix = 1

    always_statements = _ALWAYS_RE.findall(system_text)
    always_collapsed = 0
    if len(always_statements) > 1:
        system_text = _ALWAYS_RE.sub("", system_text).strip()
        compressed_always = "Always: " + ", ".join(
            statement.strip().rstrip(".") for statement in always_statements
        ) + "."
        system_text = f"{compressed_always}\n{system_text}".strip()
        always_collapsed = len(always_statements) - 1

    lines = [line for line in system_text.splitlines() if line.strip()]
    bullet_lines = [line for line in lines if _BULLET_RE.match(line)]
    bullet_compressed = 0
    if len(bullet_lines) >= 3:
        verbs: list[str] = []
        remainders: list[str] = []
        for line in bullet_lines:
            stripped = _BULLET_RE.sub("", line)
            parts = stripped.split(maxsplit=1)
            if len(parts) < 2:
                verbs = []
                break
            verbs.append(parts[0].lower())
            remainders.append(parts[1].strip())
        if verbs and len(set(verbs)) == 1:
            first_verb = verbs[0].capitalize()
            non_bullets = [line for line in lines if not _BULLET_RE.match(line)]
            non_bullets.append(f"{first_verb}: " + "; ".join(remainders) + ".")
            lines = non_bullets
            bullet_compressed = len(bullet_lines) - 1

    phrase_rewrites = 0
    compact_text = "\n".join(lines).strip()
    for pattern, replacement in _SYSTEM_PHRASE_REWRITES:
        candidate, count = pattern.subn(replacement, compact_text)
        if count <= 0:
            continue
        if count_input_tokens("gpt-4o-mini", [ChatMessage(role="system", content=candidate)]) < count_input_tokens(
            "gpt-4o-mini",
            [ChatMessage(role="system", content=compact_text)],
        ):
            compact_text = candidate
            phrase_rewrites += count

    return compact_text, {
        "duplicate_system_lines_removed": duplicate_lines_removed,
        "repeated_system_sentences_removed": repeated_sentences_removed,
        "shortened_role_prefixes": shortened_role_prefix,
        "examples_trimmed": examples_trimmed,
        "always_statements_collapsed": always_collapsed,
        "bullet_groups_compressed": bullet_compressed,
        "system_phrase_rewrites": phrase_rewrites,
    }


def _compress_user_text(model: str, text: str) -> tuple[str, int]:
    """Apply a tiny set of token-aware user prompt rewrites."""

    updated = text
    rewrites = 0
    for pattern, replacement in _USER_PHRASE_REWRITES:
        candidate, count = pattern.subn(replacement, updated)
        if count <= 0:
            continue
        if count_input_tokens(model, [ChatMessage(role="user", content=candidate)]) < count_input_tokens(
            model,
            [ChatMessage(role="user", content=updated)],
        ):
            updated = candidate
            rewrites += count
    return updated, rewrites


def _summarize_history(messages: list[Any]) -> tuple[list[Any], dict[str, int]]:
    """Conservatively compact older assistant history without adding new text.

    Free tier stays strictly subtractive: it never inserts synthetic summary
    markers or new messages. It only shortens obviously repetitive older
    assistant turns or drops near-duplicate meta restatements.
    """

    last_user_index = _find_last_user_index(messages)
    if last_user_index is None:
        return messages, {"history_turns_summarized": 0, "history_turns_dropped": 0}

    transformed = list(messages)
    prior_assistant_signatures: list[set[str]] = []
    summarized = 0
    dropped = 0

    for index, message in enumerate(messages):
        if index >= last_user_index:
            break
        if _message_role(message) != "assistant":
            continue

        text = _extract_text_message(message)
        if text is None:
            continue

        compacted_text, did_summarize, did_drop = _compact_assistant_history_turn(
            text=text,
            prior_signatures=prior_assistant_signatures,
        )
        if did_drop:
            transformed[index] = _replace_text_message(message, "")
            dropped += 1
            continue

        if did_summarize:
            transformed[index] = _replace_text_message(message, compacted_text)
            summarized += 1

        signature = _message_signature(compacted_text if did_summarize else text)
        if signature:
            prior_assistant_signatures.append(signature)

    return {
        "messages": transformed,
        "breakdown": {
            "history_turns_summarized": summarized,
            "history_turns_dropped": dropped,
        },
    }["messages"], {
        "history_turns_summarized": summarized,
        "history_turns_dropped": dropped,
    }


def _message_signature(text: str) -> set[str]:
    """Return a coarse lexical signature for duplicate-history detection."""

    return {
        token
        for token in re.findall(r"[A-Za-z0-9_]+", text.lower())
        if len(token) >= 4 and token not in {"that", "with", "this", "from", "have"}
    }


def _signature_overlap(left: set[str], right: set[str]) -> float:
    """Return overlap ratio for two message signatures."""

    if not left or not right:
        return 0.0
    return len(left & right) / max(min(len(left), len(right)), 1)


def _compact_assistant_history_turn(
    text: str,
    prior_signatures: list[set[str]],
) -> tuple[str, bool, bool]:
    """Shorten or drop obviously repetitive older assistant turns."""

    stripped = text.strip()
    if not stripped or "```" in stripped:
        return stripped, False, False

    signature = _message_signature(stripped)
    is_meta = bool(
        _ASSISTANT_META_PREFIX_RE.match(stripped)
        or re.search(
            r"\b(restating|restate|summary|summarize|before patching|pre-patch|plan|intent)\b",
            stripped,
            flags=re.IGNORECASE,
        )
    )

    if is_meta and signature and any(
        _signature_overlap(signature, previous) >= 0.7 for previous in prior_signatures
    ):
        return "", False, True

    sentences = [sentence.strip() for sentence in _SENTENCE_SPLIT_RE.split(stripped) if sentence.strip()]
    if len(sentences) < 2:
        return stripped, False, False

    scored_sentences: list[tuple[float, int, str, set[str]]] = []
    for position, sentence in enumerate(sentences):
        sentence_signature = _message_signature(sentence)
        overlap = max(
            (_signature_overlap(sentence_signature, previous) for previous in prior_signatures),
            default=0.0,
        )
        score = _sentence_density(sentence) - (overlap * 0.55)
        if re.search(r"\b(please let me know|i hope this helps|happy to help)\b", sentence, re.I):
            score -= 0.4
        scored_sentences.append((score, position, sentence, sentence_signature))

    kept: list[tuple[int, str]] = []
    for score, position, sentence, sentence_signature in sorted(
        scored_sentences,
        key=lambda item: item[0],
        reverse=True,
    ):
        overlap = max(
            (_signature_overlap(sentence_signature, previous) for previous in prior_signatures),
            default=0.0,
        )
        if overlap >= 0.78:
            continue
        if score <= 0.22 and not kept:
            continue
        kept.append((position, sentence))
        if len(kept) >= 2:
            break

    if not kept:
        return stripped, False, False

    candidate = " ".join(sentence for _, sentence in sorted(kept, key=lambda item: item[0])).strip()
    if not candidate or len(candidate) >= len(stripped):
        return stripped, False, False

    return candidate, True, False


def _sentence_density(sentence: str) -> float:
    """Estimate information density for a sentence."""

    words = re.findall(r"\w+", sentence.lower())
    if not words:
        return 0.0
    unique_ratio = len(set(words)) / len(words)
    numeral_bonus = 0.15 if re.search(r"\d", sentence) else 0.0
    keyword_bonus = 0.15 if re.search(r"\b(must|never|always|required|constraint|example)\b", sentence, re.I) else 0.0
    return unique_ratio + numeral_bonus + keyword_bonus


def _apply_intelligent_chunking(
    model: str,
    messages: list[Any],
    last_user_index: int | None,
) -> tuple[list[Any], dict[str, Any]]:
    """Select only the most relevant chunks from older long-context messages."""

    if last_user_index is None:
        return messages, {"messages_chunked": 0, "tokens_dropped": 0}

    task_text = _extract_text_message(messages[last_user_index]) or ""
    if len(task_text.strip()) < 24:
        return messages, {"messages_chunked": 0, "tokens_dropped": 0, "skipped": "short_task_query"}

    transformed = list(messages)
    messages_chunked = 0
    tokens_dropped = 0
    chunks_selected = 0

    for index, message in enumerate(messages):
        if index == last_user_index:
            continue
        if _message_role(message) in {"system", "developer"}:
            continue

        text = _extract_text_message(message)
        if text is None or len(text.strip()) < 200:
            continue

        original_tokens = count_input_tokens(model, [_replace_text_message(message, text)])
        if original_tokens < 240:
            continue

        selection = select_relevant_chunks(model=model, text=text, task=task_text)
        if (
            not selection.selected_text
            or selection.selected_tokens >= original_tokens
            or selection.dropped_tokens < 24
        ):
            continue

        transformed[index] = _replace_text_message(message, selection.selected_text)
        messages_chunked += 1
        chunks_selected += len(selection.selected_chunks)
        tokens_dropped += selection.dropped_tokens

    return transformed, {
        "messages_chunked": messages_chunked,
        "chunks_selected": chunks_selected,
        "tokens_dropped": tokens_dropped,
    }


def _apply_static_context_pruning(
    model: str,
    messages: list[Any],
    last_user_index: int | None,
    settings: Any,
) -> tuple[list[Any], dict[str, Any]]:
    """Trim the tail of very large system/developer prompts for short tasks.

    This keeps the leading instruction prefix intact and only prunes the later
    appended context blocks when there is clear token upside. The goal is to
    reduce giant generated bootstraps without destabilizing normal prompts.
    """

    if last_user_index is None:
        return messages, {"messages_pruned": 0, "tokens_dropped": 0}

    task_text = _extract_text_message(messages[last_user_index]) or ""
    if not task_text.strip():
        return messages, {"messages_pruned": 0, "tokens_dropped": 0, "skipped": "empty_task"}
    if len(task_text.strip()) > settings.static_context_max_task_chars:
        return messages, {
            "messages_pruned": 0,
            "tokens_dropped": 0,
            "skipped": "task_too_long",
        }

    transformed = list(messages)
    messages_pruned = 0
    tokens_dropped = 0

    for index, message in enumerate(messages):
        if index == last_user_index or _message_role(message) not in {"system", "developer"}:
            continue

        text = _extract_text_message(message)
        if text is None or len(text) <= settings.static_context_prefix_chars:
            continue

        original_tokens = count_input_tokens(model, [_replace_text_message(message, text)])
        if original_tokens < settings.static_context_min_tokens:
            continue

        prefix = text[: settings.static_context_prefix_chars]
        remainder = text[settings.static_context_prefix_chars :]
        if len(remainder.strip()) < 400:
            continue

        selection = select_relevant_chunks(model=model, text=remainder, task=task_text)
        if not selection.selected_text:
            continue

        candidate_text = (prefix.rstrip() + "\n\n" + selection.selected_text.lstrip()).strip()
        candidate_tokens = count_input_tokens(
            model,
            [_replace_text_message(message, candidate_text)],
        )
        dropped = original_tokens - candidate_tokens
        if dropped < 128 or candidate_tokens >= original_tokens:
            continue

        transformed[index] = _replace_text_message(message, candidate_text)
        messages_pruned += 1
        tokens_dropped += dropped

    return transformed, {
        "messages_pruned": messages_pruned,
        "tokens_dropped": tokens_dropped,
    }


class BaseCompressor:
    """Shared compressor interface."""

    def __init__(self, model: str) -> None:
        self.model = model
        self.settings = get_settings()

    def compress(
        self,
        messages: list[Any],
        tier: str = "optimized",
        verify: bool = True,
        context_id: str | None = None,
    ) -> CompressResult:
        """Compress messages synchronously without external verification."""

        del tier, verify, context_id
        original_tokens = count_input_tokens(self.model, messages)
        return CompressResult(
            original_tokens=original_tokens,
            compressed_tokens=original_tokens,
            savings_tokens=0,
            savings_pct=0.0,
            savings_cost=0.0,
            compressed_messages=messages,
            verification_passed=False,
            compression_breakdown={"fallback_reason": "base_compressor_noop"},
        )

    async def acompress(
        self,
        messages: list[ChatMessage],
        tier: str = "optimized",
        verify: bool = True,
        context_id: str | None = None,
    ) -> CompressResult:
        """Compress messages and optionally verify meaning preservation."""

        result = self.compress(messages=messages, tier=tier, verify=verify, context_id=context_id)
        result = self._apply_toon_conversion(result)
        if result.savings_tokens <= 0:
            return result

        if not verify:
            breakdown = dict(result.compression_breakdown)
            breakdown["verification_result"] = "skipped"
            return CompressResult(
                original_tokens=result.original_tokens,
                compressed_tokens=result.compressed_tokens,
                savings_tokens=result.savings_tokens,
                savings_pct=result.savings_pct,
                savings_cost=result.savings_cost,
                compressed_messages=result.compressed_messages,
                verification_passed=False,
                toon_conversions=result.toon_conversions,
                toon_tokens_saved=result.toon_tokens_saved,
                compression_breakdown=breakdown,
            )

        verified, reason = await self._verify(messages, result.compressed_messages)
        if verified is None:
            breakdown = dict(result.compression_breakdown)
            breakdown["verification_result"] = "skipped"
            if reason:
                breakdown["verification_reason"] = reason
            return CompressResult(
                original_tokens=result.original_tokens,
                compressed_tokens=result.compressed_tokens,
                savings_tokens=result.savings_tokens,
                savings_pct=result.savings_pct,
                savings_cost=result.savings_cost,
                compressed_messages=result.compressed_messages,
                verification_passed=False,
                toon_conversions=result.toon_conversions,
                toon_tokens_saved=result.toon_tokens_saved,
                compression_breakdown=breakdown,
            )
        if verified:
            breakdown = dict(result.compression_breakdown)
            breakdown["verification_result"] = "pass"
            if reason:
                breakdown["verification_reason"] = reason
            return CompressResult(
                original_tokens=result.original_tokens,
                compressed_tokens=result.compressed_tokens,
                savings_tokens=result.savings_tokens,
                savings_pct=result.savings_pct,
                savings_cost=result.savings_cost,
                compressed_messages=result.compressed_messages,
                verification_passed=True,
                toon_conversions=result.toon_conversions,
                toon_tokens_saved=result.toon_tokens_saved,
                compression_breakdown=breakdown,
            )

        breakdown = dict(result.compression_breakdown)
        breakdown["verification_result"] = "fail"
        if reason:
            breakdown["verification_reason"] = reason
        breakdown["fallback_reason"] = "verification_failed"
        log_method = logger.info if reason == "verification_failed" else logger.warning
        log_method("Compression verification failed; falling back to original prompt: %s", reason)
        return CompressResult(
            original_tokens=result.original_tokens,
            compressed_tokens=result.original_tokens,
            savings_tokens=0,
            savings_pct=0.0,
            savings_cost=0.0,
            compressed_messages=messages,
            verification_passed=False,
            toon_conversions=result.toon_conversions,
            toon_tokens_saved=result.toon_tokens_saved,
            compression_breakdown=breakdown,
        )

    def _apply_toon_conversion(self, result: CompressResult) -> CompressResult:
        """Apply optional TOON conversion after text compression and before send."""

        result = self._apply_format_normalization(result)
        converter = ToonConverter(self.model)
        conversion = converter.convert_prompt(result.compressed_messages)
        breakdown = dict(result.compression_breakdown)
        breakdown["toon"] = {
            "enabled": converter.enabled,
            "conversions_made": conversion.conversions_made,
            "tokens_saved": conversion.savings_tokens,
        }
        if conversion.conversions_made <= 0 or conversion.savings_tokens <= 0:
            return CompressResult(
                original_tokens=result.original_tokens,
                compressed_tokens=result.compressed_tokens,
                savings_tokens=result.savings_tokens,
                savings_pct=result.savings_pct,
                savings_cost=result.savings_cost,
                compressed_messages=result.compressed_messages,
                verification_passed=result.verification_passed,
                toon_conversions=0,
                toon_tokens_saved=0,
                compression_breakdown=breakdown,
            )

        compressed_tokens = conversion.converted_tokens
        savings_tokens = max(result.original_tokens - compressed_tokens, 0)
        savings_pct = round((savings_tokens / result.original_tokens) * 100, 4) if result.original_tokens else 0.0
        savings_cost = round(
            max(
                calculate_cost(self.model, result.original_tokens).total_cost
                - calculate_cost(self.model, compressed_tokens).total_cost,
                0.0,
            ),
            8,
        )
        return CompressResult(
            original_tokens=result.original_tokens,
            compressed_tokens=compressed_tokens,
            savings_tokens=savings_tokens,
            savings_pct=savings_pct,
            savings_cost=savings_cost,
            compressed_messages=conversion.converted_messages,
            verification_passed=result.verification_passed,
            toon_conversions=conversion.conversions_made,
            toon_tokens_saved=conversion.savings_tokens,
            compression_breakdown=breakdown,
        )

    def _apply_format_normalization(self, result: CompressResult) -> CompressResult:
        """Apply safe structured-format normalization before TOON conversion."""

        if not self.settings.format_normalize_enabled:
            breakdown = dict(result.compression_breakdown)
            breakdown["format_normalization"] = {
                "enabled": False,
                "normalizations_made": 0,
                "tokens_saved": 0,
            }
            return CompressResult(
                original_tokens=result.original_tokens,
                compressed_tokens=result.compressed_tokens,
                savings_tokens=result.savings_tokens,
                savings_pct=result.savings_pct,
                savings_cost=result.savings_cost,
                compressed_messages=result.compressed_messages,
                verification_passed=result.verification_passed,
                toon_conversions=result.toon_conversions,
                toon_tokens_saved=result.toon_tokens_saved,
                compression_breakdown=breakdown,
            )

        normalizer = FormatNormalizer(
            self.model,
            min_savings_tokens=self.settings.format_normalize_min_savings,
        )
        normalized = normalizer.normalize_prompt(result.compressed_messages)
        breakdown = dict(result.compression_breakdown)
        breakdown["format_normalization"] = {
            "enabled": True,
            "normalizations_made": normalized.normalizations_made,
            "tokens_saved": normalized.savings_tokens,
        }
        if normalized.normalizations_made <= 0 or normalized.savings_tokens <= 0:
            return CompressResult(
                original_tokens=result.original_tokens,
                compressed_tokens=result.compressed_tokens,
                savings_tokens=result.savings_tokens,
                savings_pct=result.savings_pct,
                savings_cost=result.savings_cost,
                compressed_messages=result.compressed_messages,
                verification_passed=result.verification_passed,
                toon_conversions=result.toon_conversions,
                toon_tokens_saved=result.toon_tokens_saved,
                compression_breakdown=breakdown,
            )

        compressed_tokens = normalized.normalized_tokens
        savings_tokens = max(result.original_tokens - compressed_tokens, 0)
        savings_pct = round((savings_tokens / result.original_tokens) * 100, 4) if result.original_tokens else 0.0
        savings_cost = round(
            max(
                calculate_cost(self.model, result.original_tokens).total_cost
                - calculate_cost(self.model, compressed_tokens).total_cost,
                0.0,
            ),
            8,
        )
        return CompressResult(
            original_tokens=result.original_tokens,
            compressed_tokens=compressed_tokens,
            savings_tokens=savings_tokens,
            savings_pct=savings_pct,
            savings_cost=savings_cost,
            compressed_messages=normalized.normalized_messages,
            verification_passed=result.verification_passed,
            toon_conversions=result.toon_conversions,
            toon_tokens_saved=result.toon_tokens_saved,
            compression_breakdown=breakdown,
        )

    async def _verify(
        self,
        original_messages: list[ChatMessage],
        compressed_messages: list[ChatMessage],
    ) -> tuple[bool | None, str]:
        """Verify that compression preserves meaning using a cheap model."""

        if not self.settings.openai_api_key:
            return None, "verification_unavailable_missing_openai_api_key"

        original_text = "\n\n".join(
            f"{_message_role(message)}: {_stringify_content(_message_content(message))}"
            for message in original_messages
        )
        compressed_text = "\n\n".join(
            f"{_message_role(message)}: {_stringify_content(_message_content(message))}"
            for message in compressed_messages
        )
        body = {
            "model": self.settings.compression_verifier_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You verify compressed prompts. Reply PASS if the compressed version "
                        "preserves all key instructions, facts, constraints, and examples. "
                        "Reply FAIL: <reason> if anything material is missing or changed."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Original:\n"
                        f"{original_text}\n\n"
                        "Compressed:\n"
                        f"{compressed_text}"
                    ),
                },
            ],
            "temperature": 0,
            "max_completion_tokens": 80,
        }
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
                response = await client.post(
                    f"{self.settings.openai_base_url}/chat/completions",
                    headers=headers,
                    json=body,
                )
            response.raise_for_status()
            payload = response.json()
            content = (
                payload.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )
            if content.upper().startswith("PASS"):
                return True, content
            return False, content or "verification_failed"
        except Exception as exc:
            logger.info("Compression verification failed: %s", exc)
            return None, f"verification_error:{type(exc).__name__}"


class BasicCompressor(BaseCompressor):
    """Fast deterministic rule-based compressor."""

    def compress(
        self,
        messages: list[ChatMessage],
        tier: str = "optimized",
        verify: bool = True,
        context_id: str | None = None,
    ) -> CompressResult:
        """Compress using deterministic cleanup and conservative summarization."""

        del tier, verify
        original_messages = messages
        original_tokens = count_input_tokens(self.model, original_messages)
        last_user_index = _find_last_user_index(original_messages)
        protected_last_user = (
            original_messages[last_user_index] if last_user_index is not None else None
        )
        breakdown: dict[str, Any] = {
            "mode": "optimized",
            "rules": {},
            "context_id": context_id,
        }

        try:
            candidate_messages, history_breakdown = _summarize_history(original_messages)
            breakdown["rules"].update(history_breakdown)

            transformed_messages: list[Any] = []
            filler_removed_total = 0
            assistant_meta_removed_total = 0
            user_rewrites_total = 0
            normalized_stats = {
                "trailing_whitespace_removed": 0,
                "blank_runs_collapsed": 0,
                "empty_code_lines_removed": 0,
            }
            system_stats = {
                "duplicate_system_lines_removed": 0,
                "repeated_system_sentences_removed": 0,
                "shortened_role_prefixes": 0,
                "examples_trimmed": 0,
                "always_statements_collapsed": 0,
                "bullet_groups_compressed": 0,
                "system_phrase_rewrites": 0,
            }

            for message in candidate_messages:
                text = _extract_text_message(message)
                if text is None:
                    transformed_messages.append(message)
                    continue

                is_protected_last_user = protected_last_user is not None and message is protected_last_user
                normalized_text, whitespace_stats = _normalize_whitespace(text)

                candidate_text = normalized_text
                allow_user_rewrite = False
                if is_protected_last_user and _message_role(message) == "user":
                    candidate_text, rewrites = _compress_user_text(self.model, candidate_text)
                    user_rewrites_total += rewrites
                    if rewrites > 0:
                        transformed_messages.append(_replace_text_message(message, candidate_text))
                    else:
                        transformed_messages.append(message)
                    continue
                if is_protected_last_user:
                    transformed_messages.append(message)
                    continue

                for key, value in whitespace_stats.items():
                    normalized_stats[key] += value

                if _message_role(message) in {"system", "developer", "assistant"}:
                    candidate_text, removed = _remove_filler_phrases(candidate_text)
                    filler_removed_total += removed

                if _message_role(message) == "assistant":
                    candidate_text, removed = _strip_assistant_meta_prefixes(candidate_text)
                    assistant_meta_removed_total += removed

                if _message_role(message) in {"system", "developer"}:
                    candidate_text, message_system_stats = _compress_system_text(candidate_text)
                    for key, value in message_system_stats.items():
                        system_stats[key] += value
                elif _message_role(message) == "user":
                    candidate_text, rewrites = _compress_user_text(self.model, candidate_text)
                    user_rewrites_total += rewrites
                    allow_user_rewrite = rewrites > 0

                if (
                    _message_role(message) not in {"system", "developer"}
                    and _message_role(message) != "assistant"
                    and not allow_user_rewrite
                    and _normalize_for_compare(text) != _normalize_for_compare(candidate_text)
                ):
                    transformed_messages.append(message)
                    continue

                transformed_messages.append(_replace_text_message(message, candidate_text))

            breakdown["rules"].update(normalized_stats)
            breakdown["rules"]["filler_phrases_removed"] = filler_removed_total
            breakdown["rules"]["assistant_meta_prefixes_removed"] = assistant_meta_removed_total
            breakdown["rules"]["user_prompt_rewrites"] = user_rewrites_total
            breakdown["rules"].update(system_stats)

            pruned_messages, static_context_stats = _apply_static_context_pruning(
                model=self.model,
                messages=transformed_messages,
                last_user_index=_find_last_user_index(transformed_messages),
                settings=self.settings,
            )
            if count_input_tokens(self.model, pruned_messages) < count_input_tokens(
                self.model,
                transformed_messages,
            ):
                transformed_messages = pruned_messages
            else:
                static_context_stats["fallback_reason"] = "static_pruning_no_extra_savings"
            breakdown["static_context_pruning"] = static_context_stats

            chunked_messages, chunking_stats = _apply_intelligent_chunking(
                model=self.model,
                messages=transformed_messages,
                last_user_index=_find_last_user_index(transformed_messages),
            )
            chunked_tokens = count_input_tokens(self.model, chunked_messages)
            if chunked_tokens < count_input_tokens(self.model, transformed_messages):
                final_messages = chunked_messages
                compressed_tokens = chunked_tokens
            else:
                final_messages = transformed_messages
                compressed_tokens = count_input_tokens(self.model, transformed_messages)
                chunking_stats["fallback_reason"] = "chunking_no_extra_savings"

            breakdown["intelligent_chunking"] = chunking_stats
            if compressed_tokens >= original_tokens:
                return CompressResult(
                    original_tokens=original_tokens,
                    compressed_tokens=original_tokens,
                    savings_tokens=0,
                    savings_pct=0.0,
                    savings_cost=0.0,
                    compressed_messages=original_messages,
                    verification_passed=False,
                    compression_breakdown={**breakdown, "fallback_reason": "no_token_savings"},
                )

            savings_tokens = original_tokens - compressed_tokens
            savings_pct = round((savings_tokens / original_tokens) * 100, 4) if original_tokens else 0.0
            savings_cost = round(
                max(
                    calculate_cost(self.model, original_tokens).total_cost
                    - calculate_cost(self.model, compressed_tokens).total_cost,
                    0.0,
                ),
                8,
            )
            return CompressResult(
                original_tokens=original_tokens,
                compressed_tokens=compressed_tokens,
                savings_tokens=savings_tokens,
                savings_pct=savings_pct,
                savings_cost=savings_cost,
                compressed_messages=final_messages,
                verification_passed=False,
                compression_breakdown=breakdown,
            )
        except Exception as exc:
            logger.exception("Basic compression failed: %s", exc)
            return CompressResult(
                original_tokens=original_tokens,
                compressed_tokens=original_tokens,
                savings_tokens=0,
                savings_pct=0.0,
                savings_cost=0.0,
                compressed_messages=original_messages,
                verification_passed=False,
                compression_breakdown={**breakdown, "fallback_reason": f"error:{type(exc).__name__}"},
            )


async def compress_messages(
    model: str,
    messages: list[ChatMessage],
    tier: str = "optimized",
    verify: bool = True,
    context_id: str | None = None,
) -> CompressResult:
    """Compress messages using the best local optimization pipeline with fail-safe fallback."""

    compressor: BaseCompressor = BasicCompressor(model)
    effective_tier = "optimized"

    try:
        result = await compressor.acompress(
            messages=messages,
            tier=effective_tier,
            verify=verify,
            context_id=context_id,
        )
        logger.info(
            "compression model=%s tier=%s original_tokens=%s compressed_tokens=%s verification=%s breakdown=%s",
            model,
            effective_tier,
            result.original_tokens,
            result.compressed_tokens,
            result.verification_passed,
            result.compression_breakdown,
        )
        return result
    except Exception as exc:
        logger.exception("Compression pipeline failed: %s", exc)
        original_tokens = count_input_tokens(model, messages)
        return CompressResult(
            original_tokens=original_tokens,
            compressed_tokens=original_tokens,
            savings_tokens=0,
            savings_pct=0.0,
            savings_cost=0.0,
            compressed_messages=messages,
            verification_passed=False,
            compression_breakdown={"fallback_reason": f"pipeline_error:{type(exc).__name__}", "tier": effective_tier},
        )
