"""In-memory cache, ledgers, budgets, and live dashboard event plumbing."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections import Counter, deque
from datetime import datetime, timezone
from difflib import SequenceMatcher
from threading import Lock
from typing import Any

from config import get_settings
from engine.state_store import get_state_store
from models.schemas import (
    BudgetSnapshot,
    BudgetStatus,
    CacheEntry,
    DashboardCompressionBreakdown,
    DashboardStats,
    LiveEvent,
    RequestEntry,
    RequestLogEntry,
    SavingsSnapshot,
    StatsResponse,
)

_WORD_RE = re.compile(r"[a-z0-9_]+")
_STOPWORDS: frozenset[str] = frozenset(
    {
        "user",
        "assistant",
        "system",
        "developer",
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "for",
        "with",
        "into",
        "from",
        "this",
        "that",
        "these",
        "those",
        "please",
        "just",
        "would",
        "could",
        "should",
        "about",
        "what",
        "are",
        "when",
        "where",
        "which",
        "there",
        "their",
        "have",
        "has",
        "had",
        "will",
        "does",
        "did",
        "your",
        "they",
        "them",
    }
)
_SYNONYMS: dict[str, str] = {
    "summarize": "summary",
    "summarise": "summary",
    "summarized": "summary",
    "summarizing": "summary",
    "main": "key",
    "primary": "key",
    "major": "key",
    "risks": "risk",
    "agreements": "agreement",
    "contract": "agreement",
    "contracts": "agreement",
    "issue": "problem",
    "issues": "problem",
    "bug": "problem",
    "bugs": "problem",
    "error": "problem",
    "errors": "problem",
    "create": "build",
    "creates": "build",
    "creating": "build",
    "generate": "build",
    "generating": "build",
    "generated": "build",
    "write": "build",
    "writes": "build",
    "writing": "build",
    "extract": "parse",
    "extraction": "parse",
    "classify": "label",
    "classification": "label",
}
_ARTIFACT_RE = re.compile(r"(?:`([^`]+)`|\b([A-Za-z0-9_./-]+\.(?:py|ts|tsx|js|jsx|md|json|yaml|yml))\b)")
_FRESHNESS_HINT_RE = re.compile(
    r"\b(new|latest|updated|fresh|current|instead|different|another|changed|switch|swap|recent)\b",
)


def _stable_json(value: Any) -> str:
    """Return a stable JSON representation suitable for hashing."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _hash_payload(value: Any) -> str:
    """Create a deterministic hash for a payload."""

    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _utc_now() -> datetime:
    """Return the current UTC timestamp."""

    return datetime.now(timezone.utc)


def _semantic_text_from_payload(payload: dict[str, Any]) -> str:
    """Extract semantically relevant text from a request payload."""

    messages = payload.get("messages")
    parts: list[str] = []

    if isinstance(messages, list):
        for index, message in enumerate(messages):
            if not isinstance(message, dict):
                continue
            role = str(message.get("role", ""))
            content = message.get("content", "")
            text = content if isinstance(content, str) else _stable_json(content)
            if not text.strip():
                continue
            parts.append(f"{role} {text}")
            if role == "user" and index == len(messages) - 1:
                parts.append(text)

    if not parts:
        return _stable_json(payload)
    return "\n".join(parts)


def _last_user_text_from_payload(payload: dict[str, Any]) -> str:
    """Return the final user-authored text from a request payload."""

    messages = payload.get("messages")
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        if str(message.get("role", "")) != "user":
            continue
        content = message.get("content", "")
        if isinstance(content, str):
            return content.strip()
        return _stable_json(content)
    return ""


def _previous_user_texts_from_payload(payload: dict[str, Any]) -> list[str]:
    """Return prior user-authored texts before the final user message."""

    messages = payload.get("messages")
    if not isinstance(messages, list):
        return []

    user_texts: list[str] = []
    for message in messages:
        if not isinstance(message, dict) or str(message.get("role", "")) != "user":
            continue
        content = message.get("content", "")
        if isinstance(content, str):
            text = content.strip()
        else:
            text = _stable_json(content)
        if text:
            user_texts.append(text)
    return user_texts[:-1]


def _artifact_targets(text: str) -> set[str]:
    """Extract likely artifact/file targets from user text."""

    targets: set[str] = set()
    for match in _ARTIFACT_RE.finditer(text):
        candidate = (match.group(1) or match.group(2) or "").strip().lower()
        if candidate:
            targets.add(candidate)
    return targets


def _normalize_term(term: str) -> str:
    """Normalize a token to a semantic comparison form."""

    lowered = term.lower()
    if len(lowered) > 5 and lowered.endswith("ing"):
        lowered = lowered[:-3]
    elif len(lowered) > 4 and lowered.endswith("ed"):
        lowered = lowered[:-2]
    elif len(lowered) > 4 and lowered.endswith("es"):
        lowered = lowered[:-2]
    elif len(lowered) > 3 and lowered.endswith("s") and not lowered.endswith(("ss", "is")):
        lowered = lowered[:-1]
    return _SYNONYMS.get(lowered, lowered)


def _term_weights(text: str) -> dict[str, float]:
    """Build a lightweight normalized semantic signature for *text*."""

    raw_terms = [_normalize_term(match.group(0)) for match in _WORD_RE.finditer(text.lower())]
    filtered = [term for term in raw_terms if term not in _STOPWORDS and len(term) >= 3]
    if not filtered:
        return {}

    counts = Counter(filtered)
    total = sum(counts.values())
    weights: dict[str, float] = {}
    for term, count in counts.items():
        base_weight = count / total
        if len(term) >= 8:
            base_weight *= 1.2
        weights[term] = round(base_weight, 6)

    bigrams = Counter(
        f"{filtered[index]}_{filtered[index + 1]}"
        for index in range(len(filtered) - 1)
    )
    total_bigrams = sum(bigrams.values()) or 1
    for term, count in bigrams.items():
        weights[term] = round((count / total_bigrams) * 0.7, 6)
    return weights


def _cosine_similarity(left: dict[str, float], right: dict[str, float]) -> float:
    """Compute cosine similarity between sparse semantic signatures."""

    if not left or not right:
        return 0.0

    intersection = set(left) & set(right)
    dot = sum(left[key] * right[key] for key in intersection)
    left_norm = sum(value * value for value in left.values()) ** 0.5
    right_norm = sum(value * value for value in right.values()) ** 0.5
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


class SemanticCache:
    """In-memory cache with exact match and meaning-aware fuzzy retrieval."""

    def __init__(self) -> None:
        settings = get_settings()
        self._entries: dict[str, CacheEntry] = {}
        self._lock = Lock()
        self._threshold = settings.cache_similarity_threshold
        self._max_entries = settings.max_cache_entries
        self._lookups = 0
        self._hits = 0
        self._exact_hits = 0
        self._semantic_hits = 0

    def _prune_if_needed(self) -> None:
        """Trim the cache to the configured size."""

        if len(self._entries) <= self._max_entries:
            return
        oldest_key = min(
            self._entries,
            key=lambda key: (
                self._entries[key].hits,
                float((self._entries[key].usage or {}).get("created_at", 0.0)),
            ),
        )
        self._entries.pop(oldest_key, None)

    def _freshness_profile(
        self,
        request_payload: dict[str, Any],
        *,
        last_user_text: str,
        last_user_terms: dict[str, float],
        message_count: int,
    ) -> tuple[float, bool, str]:
        """Estimate whether the current conversation appears to have pivoted."""

        if message_count < 3 or not last_user_text.strip():
            return 1.0, False, ""

        previous_user_texts = _previous_user_texts_from_payload(request_payload)
        if not previous_user_texts:
            return 1.0, False, ""

        previous_targets = set().union(*(_artifact_targets(text) for text in previous_user_texts))
        requested_targets = _artifact_targets(last_user_text)
        if requested_targets and previous_targets and requested_targets.isdisjoint(previous_targets):
            return 0.15, True, "artifact_pivot"

        best_previous_score = 0.0
        for previous_text in previous_user_texts[-3:]:
            previous_terms = _term_weights(previous_text)
            lexical = _cosine_similarity(last_user_terms, previous_terms)
            stringish = SequenceMatcher(None, last_user_text, previous_text).ratio()
            score = round((0.7 * lexical) + (0.3 * stringish), 6)
            if score > best_previous_score:
                best_previous_score = score

        freshness_hint = bool(_FRESHNESS_HINT_RE.search(last_user_text.lower()))
        if freshness_hint and best_previous_score < 0.78:
            return round(best_previous_score, 6), True, "freshness_hint"
        if best_previous_score < 0.34:
            return round(best_previous_score, 6), True, "conversation_pivot"
        return 1.0, False, ""

    def _score_entry(
        self,
        semantic_text: str,
        semantic_terms: dict[str, float],
        last_user_text: str,
        last_user_terms: dict[str, float],
        message_count: int,
        freshness_score: float,
        pivot_detected: bool,
        entry: CacheEntry,
    ) -> float:
        """Return a blended semantic similarity score for a cached entry."""

        lexical = _cosine_similarity(semantic_terms, entry.semantic_terms)
        stringish = SequenceMatcher(None, semantic_text, entry.semantic_text).ratio()
        overlap = 0.0
        if semantic_terms and entry.semantic_terms:
            overlap = len(set(semantic_terms) & set(entry.semantic_terms)) / max(
                len(set(semantic_terms) | set(entry.semantic_terms)),
                1,
            )
        last_user_lexical = _cosine_similarity(last_user_terms, entry.last_user_terms)
        last_user_stringish = SequenceMatcher(None, last_user_text, entry.last_user_text).ratio()
        last_user_score = round((0.8 * last_user_lexical) + (0.2 * last_user_stringish), 6)

        requested_targets = _artifact_targets(last_user_text)
        entry_targets = _artifact_targets(entry.last_user_text)
        if requested_targets and entry_targets and requested_targets.isdisjoint(entry_targets):
            return 0.0

        if message_count >= 3 and last_user_score < 0.45:
            return 0.0
        if pivot_detected:
            same_artifact = bool(requested_targets and entry_targets and not requested_targets.isdisjoint(entry_targets))
            if not same_artifact and last_user_score < 0.82:
                return 0.0

        score = round((0.55 * lexical) + (0.15 * overlap) + (0.1 * stringish) + (0.2 * last_user_score), 6)
        if pivot_detected:
            score = round(score * max(freshness_score, 0.35), 6)
        if message_count <= 1:
            if last_user_score >= 0.7 and lexical >= 0.7:
                return max(score, 0.93)
            if last_user_score >= 0.55 and lexical >= 0.55:
                return max(score, 0.92)
            if last_user_score >= 0.45 and lexical >= 0.18:
                return max(score, 0.86)
        if lexical >= 0.82 and overlap >= 0.5:
            return max(score, 0.93 if last_user_score >= 0.75 else score)
        if lexical >= 0.68 and overlap >= 0.35 and stringish >= 0.55:
            return max(score, 0.92 if last_user_score >= 0.7 else score)
        if lexical >= 0.72 and overlap >= 0.4:
            return max(score, 0.86 if last_user_score >= 0.65 else score)
        return score

    def _inspect(
        self,
        model: str,
        request_payload: dict[str, Any],
        increment_hits: bool,
    ) -> CacheDecision:
        """Return the best cache decision for *request_payload*."""

        fingerprint = _hash_payload({"model": model, "payload": request_payload})
        semantic_text = _semantic_text_from_payload(request_payload)
        semantic_terms = _term_weights(semantic_text)
        last_user_text = _last_user_text_from_payload(request_payload)
        last_user_terms = _term_weights(last_user_text)
        message_count = len(request_payload.get("messages", [])) if isinstance(request_payload.get("messages"), list) else 0
        freshness_score, pivot_detected, guard_reason = self._freshness_profile(
            request_payload,
            last_user_text=last_user_text,
            last_user_terms=last_user_terms,
            message_count=message_count,
        )

        with self._lock:
            self._lookups += 1
            exact = self._entries.get(fingerprint)
            if exact is not None:
                self._hits += 1
                self._exact_hits += 1
                if increment_hits:
                    exact.hits += 1
                return CacheDecision(
                    match=CacheLookupResult(
                        entry=exact,
                        score=1.0,
                        exact=True,
                        freshness_score=1.0,
                        pivot_detected=False,
                        guard_reason="",
                    ),
                    freshness_score=1.0,
                    pivot_detected=False,
                    guard_reason="",
                )

            best_entry: CacheEntry | None = None
            best_score = 0.0
            for entry in self._entries.values():
                if entry.model != model:
                    continue
                score = self._score_entry(
                    semantic_text,
                    semantic_terms,
                    last_user_text,
                    last_user_terms,
                    message_count,
                    freshness_score,
                    pivot_detected,
                    entry,
                )
                if score > best_score:
                    best_score = score
                    best_entry = entry

            if best_entry is None or best_score < self._threshold:
                return CacheDecision(
                    match=None,
                    freshness_score=round(freshness_score, 6),
                    pivot_detected=pivot_detected,
                    guard_reason=guard_reason,
                    best_score=round(best_score, 6),
                )
            self._hits += 1
            self._semantic_hits += 1
            if increment_hits:
                best_entry.hits += 1
            return CacheDecision(
                match=CacheLookupResult(
                    entry=best_entry,
                    score=best_score,
                    exact=False,
                    freshness_score=round(freshness_score, 6),
                    pivot_detected=pivot_detected,
                    guard_reason=guard_reason,
                ),
                freshness_score=round(freshness_score, 6),
                pivot_detected=pivot_detected,
                guard_reason=guard_reason,
                best_score=round(best_score, 6),
            )

    def get(self, model: str, request_payload: dict[str, Any]) -> CacheEntry | None:
        """Return an exact or semantic cache match."""

        decision = self._inspect(model, request_payload, increment_hits=True)
        return decision.match.entry if decision.match is not None else None

    def get_with_score(self, model: str, request_payload: dict[str, Any]) -> CacheLookupResult | None:
        """Return a cache hit along with its similarity score."""

        return self.inspect(model, request_payload, increment_hits=True).match

    def inspect(
        self,
        model: str,
        request_payload: dict[str, Any],
        *,
        increment_hits: bool = True,
    ) -> CacheDecision:
        """Return detailed cache decision metadata, even on misses."""

        return self._inspect(model, request_payload, increment_hits=increment_hits)

    def estimate(self, model: str, request_payload: dict[str, Any]) -> dict[str, Any]:
        """Estimate the likelihood and value of a semantic cache hit."""

        decision = self._inspect(model, request_payload, increment_hits=False)
        if decision.match is None:
            return {
                "hit_score": 0.0,
                "hit_probability": 0.0,
                "estimated_saved_cost": 0.0,
                "matched_key": None,
            }

        usage = decision.match.entry.usage or {}
        estimated_saved_cost = float(usage.get("saved_amount") or 0.0)
        return {
            "hit_score": decision.match.score,
            "hit_probability": round(min(max(decision.match.score, 0.0), 1.0), 6),
            "estimated_saved_cost": round(estimated_saved_cost, 8),
            "matched_key": decision.match.entry.key,
        }

    def put(
        self,
        model: str,
        request_payload: dict[str, Any],
        response: dict[str, Any],
        usage: dict[str, Any] | None = None,
    ) -> None:
        """Store a cache entry."""

        fingerprint = _hash_payload({"model": model, "payload": request_payload})
        semantic_text = _semantic_text_from_payload(request_payload)
        semantic_terms = _term_weights(semantic_text)
        last_user_text = _last_user_text_from_payload(request_payload)
        last_user_terms = _term_weights(last_user_text)
        entry_usage = dict(usage or {})
        entry_usage.setdefault("created_at", time.time())

        with self._lock:
            self._entries[fingerprint] = CacheEntry(
                key=fingerprint,
                model=model,
                request_fingerprint=_stable_json(request_payload),
                semantic_text=semantic_text,
                semantic_terms=semantic_terms,
                last_user_text=last_user_text,
                last_user_terms=last_user_terms,
                response=response,
                usage=entry_usage,
            )
            self._prune_if_needed()

    def stats(self) -> dict[str, float | int]:
        """Return aggregate cache stats for the dashboard."""

        with self._lock:
            hit_rate = (self._hits / self._lookups) if self._lookups else 0.0
            return {
                "entries": len(self._entries),
                "lookups": self._lookups,
                "hits": self._hits,
                "exact_hits": self._exact_hits,
                "semantic_hits": self._semantic_hits,
                "hit_rate": round(hit_rate, 6),
            }


class SavingsLedger:
    """Track recent request savings in memory."""

    def __init__(self, max_entries: int = 1000) -> None:
        self._events: deque[dict[str, Any]] = deque(maxlen=max_entries)
        self._lock = Lock()

    def record(self, model: str, snapshot: SavingsSnapshot) -> None:
        """Append a savings event."""

        event = {"model": model, "timestamp": time.time(), **snapshot.model_dump()}
        with self._lock:
            self._events.append(event)

    def recent(self, limit: int = 25) -> list[dict[str, Any]]:
        """Return the most recent savings events."""

        with self._lock:
            return list(self._events)[-limit:]


class RequestLedger:
    """Store recent completed request metadata for the dashboard."""

    def __init__(self, max_entries: int = 1000) -> None:
        self._entries: deque[RequestEntry] = deque(maxlen=max_entries)
        self._lock = Lock()

    def add(self, entry: RequestEntry) -> None:
        """Append a completed request record."""

        with self._lock:
            self._entries.append(entry)
        get_state_store().add_request_entry(entry)

    def record(self, entry: RequestEntry) -> None:
        """Backward-compatible alias for `add`."""

        self.add(entry)

    def get_recent(self, limit: int = 50) -> list[RequestEntry]:
        """Return the most recent request records."""

        persisted = get_state_store().list_request_entries(limit=max(limit, 1), source="proxy")
        if persisted:
            return persisted
        with self._lock:
            items = [entry for entry in self._entries if entry.source == "proxy"]
        return items[-max(limit, 1):][::-1]

    def recent(self, limit: int = 50) -> list[RequestEntry]:
        """Backward-compatible alias for `get_recent`."""

        return self.get_recent(limit)

    def all(self) -> list[RequestEntry]:
        """Return all in-memory request records."""

        persisted = get_state_store().list_request_entries(limit=None, source="proxy")
        if persisted:
            return list(reversed(persisted))
        with self._lock:
            return [entry for entry in self._entries if entry.source == "proxy"]

    def find_recent_for_context(
        self,
        *,
        context_id: str,
        since_timestamp: str,
        source: str | None = None,
        limit: int = 10,
    ) -> list[RequestEntry]:
        """Return recent request records for one context, newest first."""

        persisted = get_state_store().list_request_entries(
            limit=max(limit, 1),
            source=source,
            context_id=context_id,
            since_timestamp=since_timestamp,
        )
        if persisted:
            return persisted
        with self._lock:
            matches = [
                entry
                for entry in self._entries
                if entry.context_id == context_id
                and entry.timestamp >= since_timestamp
                and (source is None or entry.source == source)
            ]
        return list(reversed(matches[-max(limit, 1):]))

    def get_today_stats(self) -> dict[str, float | int]:
        """Return today's request aggregates."""

        entries = self.all()
        today = _utc_now().date()
        today_entries = [
            entry
            for entry in entries
            if datetime.fromisoformat(entry.timestamp).astimezone(timezone.utc).date() == today
        ]
        return {
            "requests_today": len(today_entries),
            "saved_today": round(
                sum(max(float(entry.would_have_cost) - float(entry.actual_cost), 0.0) for entry in today_entries),
                8,
            ),
            "spent_today": round(sum(entry.actual_cost for entry in today_entries), 8),
            "would_have_spent_today": round(sum(entry.would_have_cost for entry in today_entries), 8),
        }

    def get_stats(self) -> dict[str, Any]:
        """Aggregate exact dashboard stats over recorded requests."""

        entries = self.all()
        total_requests = len(entries)
        total_saved_tokens = sum(max(entry.original_tokens - entry.sent_tokens, 0) for entry in entries)
        total_saved_dollars = round(
            sum(max(float(entry.would_have_cost) - float(entry.actual_cost), 0.0) for entry in entries),
            8,
        )
        total_spent = round(sum(entry.actual_cost for entry in entries), 8)
        would_have_spent = round(sum(entry.would_have_cost for entry in entries), 8)
        avg_savings = round(
            (sum(entry.savings_pct for entry in entries) / total_requests) if total_requests else 0.0,
            4,
        )
        weighted_savings = round(
            ((total_saved_dollars / would_have_spent) * 100) if would_have_spent > 0 else 0.0,
            4,
        )
        today_stats = self.get_today_stats()
        task_count_today = get_state_store().count_tasks_today()
        today_weighted_savings = round(
            ((float(today_stats["saved_today"]) / float(today_stats["would_have_spent_today"])) * 100)
            if float(today_stats["would_have_spent_today"]) > 0
            else 0.0,
            4,
        )
        model_counts = Counter(entry.model_used for entry in entries)
        top_model = model_counts.most_common(1)[0][0] if model_counts else "none"
        cache_hits = sum(1 for entry in entries if entry.cache_hit)
        exact_cache_hits = sum(1 for entry in entries if entry.cache_type == "exact")
        semantic_cache_hits = sum(1 for entry in entries if entry.cache_type == "semantic")
        verification_fallbacks = sum(1 for entry in entries if entry.verification_fallback)
        toon_conversions = sum(int(entry.toon_conversions) for entry in entries)
        toon_tokens_saved = sum(int(entry.toon_tokens_saved) for entry in entries)
        pivoted_requests = sum(1 for entry in entries if entry.pivot_detected)
        guarded_requests = sum(1 for entry in entries if entry.cache_guard_reason)
        stale_cache_blocks = sum(
            1
            for entry in entries
            if entry.cache_guard_reason and not entry.cache_hit
        )
        avg_freshness_score = round(
            (sum(float(entry.freshness_score) for entry in entries) / total_requests)
            if total_requests
            else 1.0,
            4,
        )
        optimized_entries = [entry for entry in entries if entry.compression_tier == "free"]
        legacy_nonfree_entries = [entry for entry in entries if entry.compression_tier != "free"]

        cache_hit_rate = round((cache_hits / total_requests), 6) if total_requests else 0.0
        return {
            "total_requests": total_requests,
            "total_saved_tokens": total_saved_tokens,
            "total_saved_dollars": total_saved_dollars,
            "total_spent_dollars": total_spent,
            "would_have_spent_dollars": would_have_spent,
            "avg_savings_pct": avg_savings,
            "weighted_savings_pct": weighted_savings,
            "today_weighted_savings_pct": today_weighted_savings,
            "cache_hit_rate": float(cache_hit_rate),
            "requests_today": int(today_stats["requests_today"]) + int(task_count_today),
            "saved_today": float(today_stats["saved_today"]),
            "spent_today": float(today_stats["spent_today"]),
            "would_have_spent_today": float(today_stats["would_have_spent_today"]),
            "top_model_used": top_model,
            "quality_metrics": {
                "avg_freshness_score": avg_freshness_score,
                "pivoted_requests": pivoted_requests,
                "pivot_rate_pct": round((pivoted_requests / total_requests) * 100, 4) if total_requests else 0.0,
                "stale_cache_blocks": stale_cache_blocks,
                "quality_guard_rate_pct": round((guarded_requests / total_requests) * 100, 4) if total_requests else 0.0,
            },
            "compression_breakdown": {
                "free_tier_requests": len(optimized_entries),
                "pro_tier_requests": len(legacy_nonfree_entries),
                "cache_hits": cache_hits,
                "exact_cache_hits": exact_cache_hits,
                "semantic_cache_hits": semantic_cache_hits,
                "verification_fallbacks": verification_fallbacks,
                "toon_conversions": toon_conversions,
                "toon_tokens_saved": toon_tokens_saved,
                "avg_free_savings_pct": round(
                    (sum(entry.savings_pct for entry in optimized_entries) / len(optimized_entries))
                    if optimized_entries
                    else 0.0,
                    4,
                ),
                "avg_pro_savings_pct": round(
                    (sum(entry.savings_pct for entry in legacy_nonfree_entries) / len(legacy_nonfree_entries))
                    if legacy_nonfree_entries
                    else 0.0,
                    4,
                ),
            },
        }

    def stats(self) -> StatsResponse:
        """Backward-compatible typed stats response."""

        return StatsResponse.model_validate(self.get_stats())


class BudgetTracker:
    """Track daily/monthly spend and projections from recorded requests."""

    def add_spend(self, amount: float) -> None:
        """Budget tracking is derived from the request ledger, so this is a no-op hook."""

        del amount

    def get_status(self) -> dict[str, Any]:
        """Return exact budget status payload."""

        settings = get_settings()
        now = _utc_now()
        today = now.date()
        month_key = (now.year, now.month)
        entries = get_request_ledger().all()

        daily_entries = [
            entry
            for entry in entries
            if datetime.fromisoformat(entry.timestamp).astimezone(timezone.utc).date() == today
        ]
        monthly_entries = [
            entry
            for entry in entries
            if (
                datetime.fromisoformat(entry.timestamp).astimezone(timezone.utc).year,
                datetime.fromisoformat(entry.timestamp).astimezone(timezone.utc).month,
            )
            == month_key
        ]

        daily_spent = round(sum(entry.actual_cost for entry in daily_entries), 8)
        monthly_spent = round(sum(entry.actual_cost for entry in monthly_entries), 8)
        daily_remaining = round(max(settings.daily_budget - daily_spent, 0.0), 8)
        monthly_remaining = round(max(settings.monthly_budget - monthly_spent, 0.0), 8)

        if daily_entries:
            first = min(
                datetime.fromisoformat(entry.timestamp).astimezone(timezone.utc)
                for entry in daily_entries
            )
            elapsed_hours = max((now - first).total_seconds() / 3600.0, 1 / 60)
            burn_rate = daily_spent / elapsed_hours
            projected_daily_total = burn_rate * 24.0
        else:
            burn_rate = 0.0
            projected_daily_total = 0.0

        daily_pct_used = round((daily_spent / settings.daily_budget) if settings.daily_budget else 0.0, 6)
        monthly_pct_used = round((monthly_spent / settings.monthly_budget) if settings.monthly_budget else 0.0, 6)
        alert_triggered = (
            daily_pct_used >= settings.alert_threshold_pct
            or monthly_pct_used >= settings.alert_threshold_pct
        )

        return {
            "daily_limit": settings.daily_budget,
            "daily_spent": daily_spent,
            "daily_remaining": daily_remaining,
            "daily_pct_used": daily_pct_used,
            "monthly_limit": settings.monthly_budget,
            "monthly_spent": monthly_spent,
            "monthly_remaining": monthly_remaining,
            "monthly_pct_used": monthly_pct_used,
            "burn_rate_per_hour": round(burn_rate, 8),
            "projected_daily_total": round(projected_daily_total, 8),
            "alert_triggered": alert_triggered,
        }

    def is_alert_triggered(self) -> bool:
        """Return whether the configured budget alert threshold has been crossed."""

        return bool(self.get_status()["alert_triggered"])

    def snapshot(self) -> BudgetStatus:
        """Backward-compatible typed budget status."""

        return BudgetStatus.model_validate(self.get_status())


class LiveEventBus:
    """Simple asyncio fan-out bus for dashboard websocket clients."""

    def __init__(self) -> None:
        self._queues: set[asyncio.Queue[LiveEvent]] = set()
        self._lock = Lock()

    def subscribe(self) -> asyncio.Queue[LiveEvent]:
        """Register a subscriber queue."""

        queue: asyncio.Queue[LiveEvent] = asyncio.Queue(maxsize=100)
        with self._lock:
            self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[LiveEvent]) -> None:
        """Remove a subscriber queue."""

        with self._lock:
            self._queues.discard(queue)

    def publish(self, event: LiveEvent) -> None:
        """Broadcast an event to all subscribers without blocking request flow."""

        with self._lock:
            queues = list(self._queues)
        for queue in queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    continue


class CacheLookupResult:
    """A cache match and its score metadata."""

    def __init__(
        self,
        entry: CacheEntry,
        score: float,
        exact: bool,
        freshness_score: float = 1.0,
        pivot_detected: bool = False,
        guard_reason: str = "",
    ) -> None:
        self.entry = entry
        self.score = score
        self.exact = exact
        self.freshness_score = freshness_score
        self.pivot_detected = pivot_detected
        self.guard_reason = guard_reason


class CacheDecision:
    """Detailed outcome of one semantic cache lookup."""

    def __init__(
        self,
        *,
        match: CacheLookupResult | None,
        freshness_score: float,
        pivot_detected: bool,
        guard_reason: str,
        best_score: float = 0.0,
    ) -> None:
        self.match = match
        self.freshness_score = freshness_score
        self.pivot_detected = pivot_detected
        self.guard_reason = guard_reason
        self.best_score = best_score


_SEMANTIC_CACHE: SemanticCache | None = None
_SAVINGS_LEDGER: SavingsLedger | None = None
_REQUEST_LEDGER: RequestLedger | None = None
_BUDGET_TRACKER: BudgetTracker | None = None
_LIVE_EVENT_BUS: LiveEventBus | None = None
_SINGLETON_LOCK = Lock()


def get_semantic_cache() -> SemanticCache:
    """Return the shared semantic cache instance."""

    global _SEMANTIC_CACHE
    if _SEMANTIC_CACHE is None:
        with _SINGLETON_LOCK:
            if _SEMANTIC_CACHE is None:
                _SEMANTIC_CACHE = SemanticCache()
    return _SEMANTIC_CACHE


def get_savings_ledger() -> SavingsLedger:
    """Return the shared in-memory savings ledger."""

    global _SAVINGS_LEDGER
    if _SAVINGS_LEDGER is None:
        with _SINGLETON_LOCK:
            if _SAVINGS_LEDGER is None:
                _SAVINGS_LEDGER = SavingsLedger()
    return _SAVINGS_LEDGER


def get_request_ledger() -> RequestLedger:
    """Return the shared request ledger."""

    global _REQUEST_LEDGER
    if _REQUEST_LEDGER is None:
        with _SINGLETON_LOCK:
            if _REQUEST_LEDGER is None:
                _REQUEST_LEDGER = RequestLedger()
    return _REQUEST_LEDGER


def get_budget_tracker() -> BudgetTracker:
    """Return the shared budget tracker."""

    global _BUDGET_TRACKER
    if _BUDGET_TRACKER is None:
        with _SINGLETON_LOCK:
            if _BUDGET_TRACKER is None:
                _BUDGET_TRACKER = BudgetTracker()
    return _BUDGET_TRACKER


def get_live_event_bus() -> LiveEventBus:
    """Return the shared live event bus."""

    global _LIVE_EVENT_BUS
    if _LIVE_EVENT_BUS is None:
        with _SINGLETON_LOCK:
            if _LIVE_EVENT_BUS is None:
                _LIVE_EVENT_BUS = LiveEventBus()
    return _LIVE_EVENT_BUS
