"""TOON conversion helpers for token-efficient structured data prompts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from json import JSONDecodeError, JSONDecoder
from typing import Any

from config import get_settings
from engine.tokenizer import count_input_tokens
from models.schemas import ChatMessage

try:  # pragma: no cover - optional dependency
    import py_toon_format as _TOON_LIB
except Exception:  # pragma: no cover - optional dependency
    _TOON_LIB = None

_TOON_PREFIX = "TOON:\n"
_TOON_HEADER_RE = re.compile(r"^\[(\d+)\]\{(.+)\}:$")
_SAFE_HEADER_STRING_RE = re.compile(r"^[A-Za-z0-9_./-]+$")


@dataclass(frozen=True, slots=True)
class ConversionResult:
    """Prompt conversion outcome for TOON conversion."""

    original_tokens: int
    converted_tokens: int
    savings_tokens: int
    savings_pct: float
    converted_messages: list[Any]
    conversions_made: int


def _message_role(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("role", ""))
    return str(getattr(message, "role", ""))


def _message_content(message: Any) -> Any:
    if isinstance(message, dict):
        return message.get("content")
    return getattr(message, "content", None)


def _replace_text_message(message: Any, text: str) -> Any:
    if isinstance(message, dict):
        updated = dict(message)
        updated["content"] = text
        return updated
    return message.model_copy(update={"content": text})


def _safe_json_dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True)


class ToonConverter:
    """Convert profitable uniform JSON arrays into TOON before upstream send."""

    def __init__(self, model: str):
        self.model = model
        settings = get_settings()
        self.enabled = bool(settings.toon_enabled)
        self.min_savings = int(settings.toon_min_savings)
        self.min_array_size = int(settings.toon_min_array_size)
        self._decoder = JSONDecoder()

    def convert_prompt(self, messages: list[Any]) -> ConversionResult:
        """Scan prompt messages for profitable JSON-array → TOON replacements."""

        original_tokens = count_input_tokens(self.model, messages)
        if not self.enabled:
            return ConversionResult(
                original_tokens=original_tokens,
                converted_tokens=original_tokens,
                savings_tokens=0,
                savings_pct=0.0,
                converted_messages=messages,
                conversions_made=0,
            )

        try:
            converted_messages: list[Any] = []
            conversions_made = 0
            for message in messages:
                content = _message_content(message)
                if not isinstance(content, str) or not content.strip():
                    converted_messages.append(message)
                    continue

                arrays = self._find_json_arrays(content)
                if not arrays:
                    converted_messages.append(message)
                    continue

                updated_text = content
                local_conversions = 0
                for match in reversed(arrays):
                    original_text = str(match["raw"])
                    toon_body = self._convert_array(match["value"])
                    if not toon_body:
                        continue
                    replacement = f"{_TOON_PREFIX}{toon_body}"
                    if int(match["end"]) < len(updated_text):
                        replacement += "\n"
                    if not self._is_profitable(original_text, replacement):
                        continue
                    updated_text = (
                        updated_text[: int(match["start"])]
                        + replacement
                        + updated_text[int(match["end"]):]
                    )
                    local_conversions += 1

                if local_conversions:
                    conversions_made += local_conversions
                    converted_messages.append(_replace_text_message(message, updated_text))
                else:
                    converted_messages.append(message)

            converted_tokens = count_input_tokens(self.model, converted_messages)
            savings_tokens = max(original_tokens - converted_tokens, 0)
            savings_pct = round((savings_tokens / original_tokens) * 100, 4) if original_tokens else 0.0
            return ConversionResult(
                original_tokens=original_tokens,
                converted_tokens=converted_tokens,
                savings_tokens=savings_tokens,
                savings_pct=savings_pct,
                converted_messages=converted_messages,
                conversions_made=conversions_made,
            )
        except Exception:
            return ConversionResult(
                original_tokens=original_tokens,
                converted_tokens=original_tokens,
                savings_tokens=0,
                savings_pct=0.0,
                converted_messages=messages,
                conversions_made=0,
            )

    def convert_response(self, response_text: str) -> str:
        """Convert TOON blocks in model output back to JSON when possible."""

        if not response_text or "[" not in response_text:
            return response_text

        lines = response_text.splitlines()
        rebuilt: list[str] = []
        index = 0
        while index < len(lines):
            start_index = index
            prefixed = False
            if lines[index].strip() in {"TOON:", "TOON format:", "TOON data:"}:
                prefixed = True
                index += 1
                if index >= len(lines):
                    rebuilt.append(lines[start_index])
                    break

            header = lines[index].strip()
            header_match = _TOON_HEADER_RE.match(header)
            if header_match is None:
                rebuilt.append(lines[start_index] if prefixed else lines[index])
                if prefixed:
                    index = start_index + 1
                else:
                    index += 1
                continue

            row_count = int(header_match.group(1))
            row_lines: list[str] = []
            probe = index + 1
            while probe < len(lines) and len(row_lines) < row_count:
                candidate = lines[probe]
                if not candidate.strip():
                    break
                row_lines.append(candidate)
                probe += 1

            if len(row_lines) != row_count:
                rebuilt.append(lines[start_index] if prefixed else lines[index])
                if prefixed:
                    index = start_index + 1
                else:
                    index += 1
                continue

            block = "\n".join([header, *row_lines])
            decoded = self._decode_toon_block(block)
            if decoded is None:
                rebuilt.append(lines[start_index] if prefixed else lines[index])
                if prefixed:
                    index = start_index + 1
                else:
                    index += 1
                continue

            rebuilt.append(_safe_json_dumps(decoded))
            index = probe

        return "\n".join(rebuilt)

    def _find_json_arrays(self, text: str) -> list[dict[str, Any]]:
        """Find uniform arrays of flat objects that are good TOON candidates."""

        matches: list[dict[str, Any]] = []
        index = 0
        while index < len(text):
            start = text.find("[", index)
            if start == -1:
                break
            try:
                value, offset = self._decoder.raw_decode(text[start:])
            except JSONDecodeError:
                index = start + 1
                continue

            if self._is_convertible_array(value):
                matches.append(
                    {
                        "start": start,
                        "end": start + offset,
                        "raw": text[start : start + offset],
                        "value": value,
                    }
                )
                index = start + offset
            else:
                index = start + 1
        return matches

    def _is_profitable(self, original: str, toon: str) -> bool:
        """Return whether the TOON form saves enough tokens to justify rewriting."""

        original_tokens = count_input_tokens(self.model, [ChatMessage(role="user", content=original)])
        toon_tokens = count_input_tokens(self.model, [ChatMessage(role="user", content=toon)])
        return original_tokens - toon_tokens >= self.min_savings

    def _is_convertible_array(self, value: Any) -> bool:
        if not isinstance(value, list) or len(value) < self.min_array_size:
            return False
        if not all(isinstance(item, dict) and item for item in value):
            return False
        first_keys = list(value[0].keys())
        first_key_set = set(first_keys)
        for item in value:
            if set(item.keys()) != first_key_set:
                return False
            if any(isinstance(field_value, (dict, list)) for field_value in item.values()):
                return False
        return True

    def _convert_array(self, json_array: list[dict[str, Any]]) -> str:
        """Convert one uniform JSON object array into TOON text."""

        candidates: list[str] = []
        try:
            external = self._convert_with_library(json_array)
            if external:
                candidates.append(external)
        except Exception:
            pass
        candidates.append(self._convert_array_manually(json_array))
        if len(candidates) == 1:
            return candidates[0]
        return min(
            candidates,
            key=lambda rendered: count_input_tokens(
                self.model,
                [ChatMessage(role="user", content=f"{_TOON_PREFIX}{rendered}")],
            ),
        )

    def _convert_with_library(self, json_array: list[dict[str, Any]]) -> str | None:
        if _TOON_LIB is None:
            return None
        for candidate in ("dumps", "encode"):
            function = getattr(_TOON_LIB, candidate, None)
            if callable(function):
                rendered = function(json_array, delimiter=",")
                if isinstance(rendered, str) and rendered.strip():
                    return rendered.strip()
        return None

    def _convert_array_manually(self, json_array: list[dict[str, Any]]) -> str:
        keys = list(json_array[0].keys())
        constant_fields: dict[str, Any] = {}
        variable_keys: list[str] = []
        for key in keys:
            first_value = json_array[0][key]
            if all(item[key] == first_value for item in json_array) and self._header_constant_supported(first_value):
                constant_fields[key] = first_value
            else:
                variable_keys.append(key)

        header_parts = list(variable_keys)
        for key, value in constant_fields.items():
            header_parts.append(f"{key}={self._encode_header_constant(value)}")

        header = f"[{len(json_array)}]{{{','.join(header_parts)}}}:"
        rows = []
        for item in json_array:
            row_values = [item[key] for key in variable_keys]
            rows.append(_safe_json_dumps(row_values))
        return "\n".join([header, *rows])

    def _header_constant_supported(self, value: Any) -> bool:
        if value is None or isinstance(value, (bool, int, float)):
            return True
        return isinstance(value, str) and bool(_SAFE_HEADER_STRING_RE.fullmatch(value))

    def _encode_header_constant(self, value: Any) -> str:
        if isinstance(value, str) and _SAFE_HEADER_STRING_RE.fullmatch(value):
            return value
        return _safe_json_dumps(value)

    def _decode_toon_block(self, block: str) -> Any | None:
        lines = [line.rstrip() for line in block.strip().splitlines() if line.strip()]
        if not lines:
            return None
        header_match = _TOON_HEADER_RE.match(lines[0].strip())
        if header_match is None:
            return None
        row_count = int(header_match.group(1))
        raw_parts = [part.strip() for part in header_match.group(2).split(",") if part.strip()]
        keys: list[str] = []
        constant_fields: dict[str, Any] = {}
        for part in raw_parts:
            if "=" in part:
                key, raw_value = part.split("=", 1)
                key = key.strip()
                raw_value = raw_value.strip()
                if not key:
                    return None
                try:
                    value: Any = json.loads(raw_value)
                except json.JSONDecodeError:
                    value = raw_value
                constant_fields[key] = value
            else:
                keys.append(part)
        row_lines = lines[1:]
        if len(row_lines) != row_count or (not keys and not constant_fields):
            return None

        decoded_rows: list[dict[str, Any]] = []
        for line in row_lines:
            stripped = line.strip()
            if not (stripped.startswith("[") and stripped.endswith("]")):
                decoded_rows = None
                break
            try:
                values = json.loads(stripped)
            except json.JSONDecodeError:
                decoded_rows = None
                break
            if not isinstance(values, list) or len(values) != len(keys):
                decoded_rows = None
                break
            row = {key: value for key, value in zip(keys, values)}
            row.update(constant_fields)
            decoded_rows.append(row)
        if decoded_rows is not None and len(decoded_rows) == row_count:
            return decoded_rows

        if _TOON_LIB is not None:
            for candidate in ("loads", "decode"):
                function = getattr(_TOON_LIB, candidate, None)
                if callable(function):
                    try:
                        decoded = function(block, strict=False)
                    except TypeError:
                        try:
                            decoded = function(block)
                        except Exception:
                            continue
                    except Exception:
                        continue
                    if decoded not in (None, ""):
                        return decoded
        return None
