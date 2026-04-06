"""Model-aware intelligent chunking for long context reduction."""

from __future__ import annotations

import math
import re
import threading
from dataclasses import dataclass

from config import get_model_pricing, get_settings
from engine.tokenizer import count_input_tokens
from models.schemas import ChatMessage

_CODE_OR_PARAGRAPH_RE = re.compile(r"```[\s\S]*?```|(?:[^\n]+\n?)+?(?=\n\s*\n|$)", re.MULTILINE)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
_STOPWORDS: frozenset[str] = frozenset(
    {
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
        "that",
        "this",
        "these",
        "those",
        "please",
        "about",
        "there",
        "their",
        "have",
        "will",
        "would",
        "could",
        "should",
        "what",
        "when",
        "where",
        "which",
    }
)


class _EmbeddingModel:
    """Thread-safe singleton wrapper around an optional sentence transformer."""

    _instance: object | None = None
    _error: bool = False
    _lock = threading.Lock()

    @classmethod
    def get(cls) -> object | None:
        """Return the cached embedding model or ``None`` if unavailable."""

        if cls._instance is not None:
            return cls._instance
        if cls._error:
            return None
        with cls._lock:
            if cls._instance is not None:
                return cls._instance
            if cls._error:
                return None
            try:
                from sentence_transformers import SentenceTransformer  # type: ignore[import]

                cls._instance = SentenceTransformer("all-MiniLM-L6-v2")
            except Exception:
                cls._error = True
                return None
        return cls._instance


@dataclass(frozen=True, slots=True)
class Chunk:
    """A scored and ranked text chunk."""

    text: str
    token_count: int
    density_score: float
    relevance_score: float
    combined_score: float
    source_index: int


@dataclass(frozen=True, slots=True)
class ChunkSelection:
    """Selected context chunks plus selection metadata."""

    original_tokens: int
    selected_tokens: int
    dropped_tokens: int
    selected_chunks: list[Chunk]
    dropped_chunks: list[Chunk]
    selected_text: str
    target_tokens: int


def _tokenize_terms(text: str) -> list[str]:
    """Return normalized lexical terms for density and similarity scoring."""

    words = [match.group(0).lower() for match in _WORD_RE.finditer(text)]
    return [word for word in words if len(word) >= 3 and word not in _STOPWORDS]


def _density_score(text: str) -> float:
    """Estimate semantic density from instruction/fact heavy language."""

    terms = _tokenize_terms(text)
    if not terms:
        return 0.0
    unique_ratio = len(set(terms)) / len(terms)
    numeral_bonus = 0.12 if re.search(r"\d", text) else 0.0
    instruction_bonus = 0.18 if re.search(r"\b(must|never|always|required|constraint|important|warning)\b", text, re.I) else 0.0
    code_bonus = 0.1 if "```" in text or re.search(r"\b(def|class|import|return|async)\b", text) else 0.0
    sentence_count = len([sentence for sentence in _SENTENCE_RE.split(text.strip()) if sentence.strip()])
    sentence_bonus = min(math.log(sentence_count + 1) * 0.08, 0.18)
    return round(unique_ratio + numeral_bonus + instruction_bonus + code_bonus + sentence_bonus, 6)


def _term_weights(text: str) -> dict[str, float]:
    """Return a sparse lexical signature for similarity scoring."""

    terms = _tokenize_terms(text)
    if not terms:
        return {}
    counts: dict[str, int] = {}
    for term in terms:
        counts[term] = counts.get(term, 0) + 1
    total = sum(counts.values())
    return {term: count / total for term, count in counts.items()}


def _cosine_similarity(left: dict[str, float], right: dict[str, float]) -> float:
    """Compute cosine similarity between sparse lexical signatures."""

    if not left or not right:
        return 0.0
    overlap = set(left) & set(right)
    dot = sum(left[key] * right[key] for key in overlap)
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _embedding_similarity(query: str, chunks: list[str]) -> list[float]:
    """Return embedding-based query/chunk similarity when available."""

    model = _EmbeddingModel.get()
    if model is None or not query.strip() or not chunks:
        return [0.0 for _ in chunks]
    try:
        vectors = model.encode([query, *chunks], normalize_embeddings=True, show_progress_bar=False)
        query_vector = vectors[0]
        return [float(query_vector @ vector) for vector in vectors[1:]]
    except Exception:
        return [0.0 for _ in chunks]


def _target_chunk_tokens(model: str) -> int:
    """Choose chunk size based on the model's input price."""

    pricing = get_model_pricing(model)
    if pricing.input_per_million >= 10:
        return 180
    if pricing.input_per_million >= 3:
        return 240
    if pricing.input_per_million >= 1:
        return 320
    return 420


def _split_segments(text: str) -> list[str]:
    """Split text into semantically meaningful segments before packing."""

    segments = [match.group(0).strip() for match in _CODE_OR_PARAGRAPH_RE.finditer(text) if match.group(0).strip()]
    return segments or [text.strip()]


def _split_oversized_segment(model: str, segment: str, target_tokens: int) -> list[tuple[str, int]]:
    """Split a single oversized segment by sentences when needed."""

    segment = segment.strip()
    token_count = count_input_tokens(model, [ChatMessage(role="user", content=segment)])
    if token_count <= target_tokens:
        return [(segment, token_count)]

    sentences = [sentence.strip() for sentence in _SENTENCE_RE.split(segment) if sentence.strip()]
    if len(sentences) < 2:
        return [(segment, token_count)]

    chunks: list[tuple[str, int]] = []
    current_parts: list[str] = []
    for sentence in sentences:
        candidate = " ".join([*current_parts, sentence]).strip()
        candidate_tokens = count_input_tokens(model, [ChatMessage(role="user", content=candidate)])
        if current_parts and candidate_tokens > target_tokens:
            chunk_text = " ".join(current_parts).strip()
            chunks.append(
                (
                    chunk_text,
                    count_input_tokens(model, [ChatMessage(role="user", content=chunk_text)]),
                )
            )
            current_parts = [sentence]
            continue
        current_parts.append(sentence)

    if current_parts:
        chunk_text = " ".join(current_parts).strip()
        chunks.append(
            (
                chunk_text,
                count_input_tokens(model, [ChatMessage(role="user", content=chunk_text)]),
            )
        )
    return chunks


def _pack_chunks(model: str, text: str, target_tokens: int) -> list[tuple[str, int]]:
    """Pack segments into chunks near the desired token size."""

    settings = get_settings()
    segments = _split_segments(text)
    if len(segments) <= settings.chunk_max_chunks:
        unpacked: list[tuple[str, int]] = []
        for segment in segments:
            unpacked.extend(_split_oversized_segment(model, segment, target_tokens))
        return unpacked

    chunks: list[tuple[str, int]] = []
    current_parts: list[str] = []

    for segment in segments:
        candidate = "\n\n".join([*current_parts, segment]).strip()
        candidate_tokens = count_input_tokens(model, [ChatMessage(role="user", content=candidate)])
        if current_parts and candidate_tokens > target_tokens:
            chunk_text = "\n\n".join(current_parts).strip()
            chunks.append(
                (
                    chunk_text,
                    count_input_tokens(model, [ChatMessage(role="user", content=chunk_text)]),
                )
            )
            current_parts = [segment]
            continue
        current_parts.append(segment)

    if current_parts:
        chunk_text = "\n\n".join(current_parts).strip()
        chunks.append(
            (
                chunk_text,
                count_input_tokens(model, [ChatMessage(role="user", content=chunk_text)]),
            )
        )

    return chunks


def rank_chunks(
    model: str,
    text: str,
    task: str | None = None,
    target_tokens: int | None = None,
) -> list[Chunk]:
    """Split, score, and rank chunks by density and task relevance."""

    target = target_tokens or _target_chunk_tokens(model)
    packed = _pack_chunks(model, text, target)
    task_weights = _term_weights(task or "")
    embedding_scores = _embedding_similarity(task or "", [chunk_text for chunk_text, _ in packed])

    ranked: list[Chunk] = []
    for index, ((chunk_text, token_count), embedding_score) in enumerate(zip(packed, embedding_scores, strict=True)):
        density = _density_score(chunk_text)
        lexical_relevance = _cosine_similarity(task_weights, _term_weights(chunk_text)) if task else density
        relevance = lexical_relevance
        if task:
            relevance = round((0.6 * lexical_relevance) + (0.4 * max(embedding_score, 0.0)), 6)
        combined = round((0.55 * density) + (0.45 * relevance), 6)
        ranked.append(
            Chunk(
                text=chunk_text,
                token_count=token_count,
                density_score=density,
                relevance_score=relevance,
                combined_score=combined,
                source_index=index,
            )
        )
    return sorted(ranked, key=lambda chunk: (chunk.combined_score, chunk.density_score), reverse=True)


def select_relevant_chunks(
    model: str,
    text: str,
    task: str,
    max_context_tokens: int | None = None,
    target_tokens: int | None = None,
) -> ChunkSelection:
    """Select the highest-value chunks under a token budget."""

    settings = get_settings()
    ranked = rank_chunks(model=model, text=text, task=task, target_tokens=target_tokens)
    original_tokens = count_input_tokens(model, [ChatMessage(role="user", content=text)])
    budget = max_context_tokens or min(
        max(_target_chunk_tokens(model) * settings.chunk_max_chunks, _target_chunk_tokens(model)),
        original_tokens,
    )

    selected: list[Chunk] = []
    dropped: list[Chunk] = []
    selected_tokens = 0
    selected_indices: set[int] = set()

    for chunk in ranked:
        if task and chunk.relevance_score < settings.chunk_relevance_threshold:
            dropped.append(chunk)
            continue
        if not task and chunk.combined_score < settings.chunk_relevance_threshold and selected:
            dropped.append(chunk)
            continue
        if len(selected) >= settings.chunk_max_chunks:
            dropped.append(chunk)
            continue
        if selected_tokens + chunk.token_count > budget and selected:
            dropped.append(chunk)
            continue
        selected.append(chunk)
        selected_indices.add(chunk.source_index)
        selected_tokens += chunk.token_count

    if not selected and ranked:
        best = ranked[0]
        selected = [best]
        selected_indices.add(best.source_index)
        selected_tokens = best.token_count

    for chunk in ranked:
        if chunk.source_index not in selected_indices:
            dropped.append(chunk)

    selected_in_original_order = sorted(selected, key=lambda chunk: chunk.source_index)
    selected_text = "\n\n".join(chunk.text for chunk in selected_in_original_order).strip()
    return ChunkSelection(
        original_tokens=original_tokens,
        selected_tokens=selected_tokens,
        dropped_tokens=max(original_tokens - selected_tokens, 0),
        selected_chunks=selected_in_original_order,
        dropped_chunks=sorted(dropped, key=lambda chunk: chunk.source_index),
        selected_text=selected_text or text.strip(),
        target_tokens=budget,
    )


def chunk_text(model: str, text: str, target_tokens: int = 400) -> list[Chunk]:
    """Backward-compatible helper returning density-ranked chunks."""

    return rank_chunks(model=model, text=text, task=None, target_tokens=target_tokens)
