"""Token-efficient output transpilation for Python code blocks."""

from __future__ import annotations

import ast
import io
import keyword
import re
import tokenize
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from models.schemas import ChatMessage

_CODE_BLOCK_RE = re.compile(r"```(?P<lang>[a-zA-Z0-9_-]+)?\s*\n(?P<body>.*?)```", re.DOTALL | re.IGNORECASE)
_LINE_RE = re.compile(r"^@(?P<depth>\d+)\s*(?:(?P<opcode>[A-Z])(?:\s+(?P<body>.*))?|(?P<raw>.*))$")
_ALIAS_HEADER_RE = re.compile(r"^(?P<alias>[§~ƒλΩ∆¤¿¡¶†‡])(?:=(?P<eq_target>.+)|\s+(?P<space_target>.+))$")
_MACRO_HEADER_RE = re.compile(r"^(?P<macro>[!?\^$&+%])\s+(?P<template>.+)$")
_ALIAS_CHARS: tuple[str, ...] = ("§", "~", "ƒ", "λ", "Ω", "∆", "¤", "¿", "¡", "¶", "†", "‡")
_MACRO_CHARS: tuple[str, ...] = ("!", "?", "^", "$", "&", "+", "%")

_BLOCK_OPCODES: dict[str, str] = {
    "D": "def",
    "A": "async def",
    "C": "class",
    "I": "if",
    "L": "elif",
    "O": "else",
    "F": "for",
    "W": "while",
    "U": "with",
    "T": "try",
    "X": "except",
    "N": "finally",
}

_SIMPLE_OPCODES: dict[str, str] = {
    "R": "return",
    "Y": "yield",
    "P": "pass",
    "B": "break",
    "K": "continue",
}


class TranspileDecodeError(ValueError):
    """Raised when `pymin` cannot be decoded safely."""


@dataclass(frozen=True, slots=True)
class PyminBlock:
    """A discovered compressed Python block."""

    raw_fence: str
    fence_lang: str
    compressed_code: str


@dataclass(frozen=True, slots=True)
class StatementTemplate:
    """A repeated one-hole statement template that can be macro-compressed."""

    template: str
    uses: int


@dataclass(frozen=True, slots=True)
class TranspileResult:
    """Outcome of output transpilation handling."""

    enabled: bool
    language: str
    used: bool
    status: str
    content: str
    compressed_tokens: int
    decoded_tokens: int
    savings_tokens: int
    savings_pct: float
    error: str | None = None

    @property
    def savings_cost(self) -> float:
        """Placeholder property for proxy-side cost calculation."""

        return 0.0


def encode_spec_prompt() -> str:
    """Return the Python codec prompt injected into upstream requests."""

    return (
        "If you output Python code, compress only when smaller. Keep prose normal. "
        "Never compress reasoning. If unsure, return normal Python.\n\n"
        "Use ```python fences. Optional headers go first:\n"
        "- alias: `<sym> <original>`\n"
        "- macro: `<sym> <template with one {}>`\n"
        "Body use:\n"
        "- `x = §(item)` after `§ transform_long_name`\n"
        "- `! value` after `! cache['last'] = {}`\n"
        "Keep indentation normal. Do not alias keywords. Usually <=3 aliases, <=2 macros.\n\n"
        "Alias example:\n"
        "```python\n"
        "a = transform_long_name(x)\n"
        "b = transform_long_name(y)\n"
        "```\n"
        "->\n"
        "```python\n"
        "§ transform_long_name\n"
        "a = §(x)\n"
        "b = §(y)\n"
        "```\n\n"
        "Macro example:\n"
        "```python\n"
        "cache['last'] = 'alpha message'\n"
        "cache['last'] = build_message(user_id)\n"
        "```\n"
        "->\n"
        "```python\n"
        "! cache['last'] = {}\n"
        "! 'alpha message'\n"
        "! build_message(user_id)\n"
        "```"
    )


@lru_cache(maxsize=1)
def estimate_spec_prompt_tokens() -> int:
    """Estimate the codec prompt overhead using a local tokenizer."""

    from engine.tokenizer import count_openai_tokens

    return count_openai_tokens(
        "gpt-4o-mini",
        [{"role": "developer", "content": encode_spec_prompt()}],
    )


def build_transpile_instruction_message(template: Any) -> Any:
    """Build a message carrying the codec instructions using the input shape."""

    payload = {
        "role": "developer",
        "name": "lumin_pymin_codec",
        "content": encode_spec_prompt(),
    }
    if isinstance(template, dict):
        return payload
    return ChatMessage(**payload)


def inject_transpile_prompt(messages: list[Any]) -> list[Any]:
    """Inject codec instructions ahead of the first non-system message."""

    if not messages:
        return [build_transpile_instruction_message({})]

    injected = build_transpile_instruction_message(messages[0])
    insert_at = 0
    for index, message in enumerate(messages):
        role = message.get("role") if isinstance(message, dict) else getattr(message, "role", "")
        if role not in {"system", "developer"}:
            insert_at = index
            break
        insert_at = index + 1
    return [*messages[:insert_at], injected, *messages[insert_at:]]


def extract_pymin_blocks(text: str) -> list[PyminBlock]:
    """Find all `pymin` fenced blocks in a response."""

    blocks: list[PyminBlock] = []
    for match in _CODE_BLOCK_RE.finditer(text):
        fence_lang = (match.group("lang") or "").lower()
        if fence_lang not in {"pymin", "python", "py"}:
            continue
        body = match.group("body").strip("\n")
        body_lines = [line for line in body.splitlines() if line.strip()]
        if not body_lines:
            continue
        if not (
            any(_ALIAS_HEADER_RE.match(line.strip()) for line in body_lines[:3])
            or any(_MACRO_HEADER_RE.match(line.strip()) and line.count("{}") == 1 for line in body_lines[:4])
            or _LINE_RE.match(body_lines[0].strip())
        ):
            continue
        blocks.append(
            PyminBlock(
                raw_fence=match.group(0),
                fence_lang=fence_lang,
                compressed_code=body,
            )
        )
    return blocks


def _tokenize_python(text: str) -> list[tokenize.TokenInfo]:
    """Tokenize Python text."""

    return list(tokenize.generate_tokens(io.StringIO(text).readline))


def canonicalize_python(code: str) -> str:
    """Canonicalize Python using the AST when possible.

    This intentionally normalizes formatting and may drop comments. The goal is
    a smaller, semantically equivalent baseline before alias compression.
    """

    try:
        parsed = ast.parse(code)
        canonical = ast.unparse(parsed).strip()
        return canonical or code.strip()
    except (SyntaxError, ValueError):
        return code.strip()


def _wrapped_token_count(model: str, body: str) -> int:
    """Measure a Python block exactly as the model would emit it."""

    from engine.tokenizer import count_input_tokens

    wrapped = f"```python\n{body}\n```"
    return count_input_tokens(model, [{"role": "assistant", "content": wrapped}])


def _parse_compact_headers(text: str) -> tuple[dict[str, str], dict[str, str], str]:
    """Split a compact Python block into alias headers, macro headers, and body."""

    lines = text.splitlines()
    aliases: dict[str, str] = {}
    macros: dict[str, str] = {}
    body_start = 0

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            body_start = index + 1
            continue

        alias_match = _ALIAS_HEADER_RE.match(stripped)
        if alias_match is not None:
            aliases[alias_match.group("alias")] = (
                alias_match.group("eq_target")
                or alias_match.group("space_target")
                or ""
            )
            body_start = index + 1
            continue

        macro_match = _MACRO_HEADER_RE.match(stripped)
        if macro_match is not None:
            template = macro_match.group("template")
            if template.count("{}") == 1:
                macros[macro_match.group("macro")] = template
                body_start = index + 1
                continue

        body_start = index
        break

    body = "\n".join(lines[body_start:]).rstrip()
    return aliases, macros, body


def _render_compact_block(
    aliases: dict[str, str],
    macros: dict[str, str],
    body: str,
) -> str:
    """Serialize alias and macro directives plus body into one block."""

    header_lines = [f"{alias} {target}" for alias, target in aliases.items()]
    header_lines.extend(f"{macro} {template}" for macro, template in macros.items())
    if header_lines and body:
        return "\n".join([*header_lines, body]).rstrip()
    if header_lines:
        return "\n".join(header_lines).rstrip()
    return body.rstrip()


def _candidate_replacements(code: str) -> list[str]:
    """Collect repeated expensive Python symbols worth considering for aliasing."""

    tokens = _tokenize_python(code)
    counts: dict[str, int] = {}

    def bump(candidate: str) -> None:
        counts[candidate] = counts.get(candidate, 0) + 1

    for token in tokens:
        if token.type == tokenize.NAME:
            candidate = token.string
            if keyword.iskeyword(candidate) or len(candidate) < 8:
                continue
            bump(candidate)
        elif token.type == tokenize.STRING:
            literal = token.string
            if len(literal) >= 16:
                bump(literal)

    for match in re.finditer(r"\b[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)+\b", code):
        dotted = match.group(0)
        if len(dotted) >= 12:
            bump(dotted)

    for match in re.finditer(r"\b[a-zA-Z_]\w*\([^()\n]{8,}\)", code):
        call_shape = match.group(0)
        if len(call_shape) >= 18:
            bump(call_shape)

    try:
        parsed = ast.parse(code)
        for node in ast.walk(parsed):
            if isinstance(node, ast.Attribute):
                source = ast.get_source_segment(code, node)
                if source and "." in source and len(source) >= 12:
                    bump(source)
            elif isinstance(node, ast.Call):
                func_source = ast.get_source_segment(code, node.func)
                if func_source and len(func_source) >= 8:
                    bump(func_source)
                full_source = ast.get_source_segment(code, node)
                if full_source and len(full_source) >= 24:
                    bump(full_source)
            elif isinstance(node, ast.Subscript):
                source = ast.get_source_segment(code, node)
                if source and len(source) >= 18:
                    bump(source)
    except SyntaxError:
        pass

    return [
        candidate
        for candidate, count in sorted(
            counts.items(),
            key=lambda item: (-(item[1] * len(item[0])), -item[1], -len(item[0]), item[0]),
        )
        if count >= 2
    ]


def _candidate_statement_templates(code: str) -> list[StatementTemplate]:
    """Collect repeated one-hole statement templates worth macro-compressing."""

    try:
        parsed = ast.parse(code)
    except SyntaxError:
        return []

    lines = code.splitlines()
    counts: dict[str, int] = {}

    for node in ast.walk(parsed):
        if not isinstance(node, ast.stmt):
            continue
        if getattr(node, "lineno", None) is None or getattr(node, "end_lineno", None) != node.lineno:
            continue
        if node.lineno < 1 or node.lineno > len(lines):
            continue
        if node.end_col_offset is None or node.col_offset is None:
            continue

        line = lines[node.lineno - 1].rstrip()
        statement = line[node.col_offset:node.end_col_offset].rstrip()
        if not statement or "{}" in statement:
            continue

        for child in ast.walk(node):
            if not isinstance(child, ast.expr):
                continue
            if getattr(child, "lineno", None) != node.lineno or getattr(child, "end_lineno", None) != node.lineno:
                continue
            if child.col_offset is None or child.end_col_offset is None:
                continue

            relative_start = child.col_offset - node.col_offset
            relative_end = child.end_col_offset - node.col_offset
            if relative_start < 0 or relative_end > len(statement) or relative_start >= relative_end:
                continue

            hole = statement[relative_start:relative_end]
            if len(hole.strip()) < 3:
                continue

            template = f"{statement[:relative_start]}{{}}{statement[relative_end:]}".strip()
            if template.count("{}") != 1:
                continue
            if len(template) < 10:
                continue
            if template in {"{}", "return {}", "yield {}", "raise {}"}:
                continue

            counts[template] = counts.get(template, 0) + 1

    return [
        StatementTemplate(template=template, uses=uses)
        for template, uses in sorted(
            counts.items(),
            key=lambda item: (-(item[1] * len(item[0])), -item[1], -len(item[0]), item[0]),
        )
        if uses >= 2
    ]


def _apply_aliases(code: str, aliases: dict[str, str]) -> str:
    """Apply aliases to Python code or quoted literals."""

    result = code
    for alias, target in sorted(aliases.items(), key=lambda item: -len(item[1])):
        if target.startswith(("'", '"')):
            result = result.replace(target, alias)
            continue
        result = re.sub(rf"\b{re.escape(target)}\b", alias, result)
    return result


def _apply_statement_macro(body: str, macro_char: str, template: str) -> tuple[str, int]:
    """Replace repeated one-hole lines with a compact statement macro."""

    prefix, suffix = template.split("{}", 1)
    updated_lines: list[str] = []
    replacements = 0

    for line in body.splitlines():
        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]

        if stripped.startswith(prefix) and stripped.endswith(suffix):
            if suffix:
                argument = stripped[len(prefix):-len(suffix)]
            else:
                argument = stripped[len(prefix):]
            if argument.strip():
                updated_lines.append(f"{indent}{macro_char} {argument.strip()}")
                replacements += 1
                continue

        updated_lines.append(line)

    return "\n".join(updated_lines).rstrip(), replacements


def encode_python_to_pymin(code: str, model: str = "gpt-4o-mini") -> str:
    """Encode Python using tokenizer-aware alias and macro compression when profitable."""

    original_body = code.strip()
    canonical_body = canonicalize_python(code)
    original_tokens = _wrapped_token_count(model, original_body)
    canonical_tokens = _wrapped_token_count(model, canonical_body)

    if canonical_tokens < original_tokens:
        base_code = canonical_body
    else:
        base_code = original_body
    best_plain_tokens = _wrapped_token_count(model, base_code)

    aliases: dict[str, str] = {}
    macros: dict[str, str] = {}
    candidates = _candidate_replacements(base_code)
    remaining_aliases = list(_ALIAS_CHARS)
    chosen = True
    best_body = base_code
    best_tokens = best_plain_tokens

    while candidates and remaining_aliases and chosen:
        chosen = False
        current_best_tokens = best_tokens
        current_best_body = best_body
        current_best_aliases = aliases
        current_best_candidate: str | None = None
        current_best_alias_char: str | None = None

        for candidate in candidates:
            for alias_char in remaining_aliases:
                proposed_aliases = {**aliases, alias_char: candidate}
                aliased_body = _apply_aliases(base_code, proposed_aliases)
                candidate_body = _render_compact_block(proposed_aliases, {}, aliased_body)
                candidate_tokens = _wrapped_token_count(model, candidate_body)
                if candidate_tokens < current_best_tokens:
                    current_best_tokens = candidate_tokens
                    current_best_body = candidate_body
                    current_best_aliases = proposed_aliases
                    current_best_candidate = candidate
                    current_best_alias_char = alias_char
                    chosen = True

        if chosen and current_best_candidate is not None and current_best_alias_char is not None:
            aliases = current_best_aliases
            best_body = current_best_body
            best_tokens = current_best_tokens
            candidates = [candidate for candidate in candidates if candidate != current_best_candidate]
            remaining_aliases.remove(current_best_alias_char)

    _, _, aliased_code_body = _parse_compact_headers(best_body)
    statement_templates = _candidate_statement_templates(base_code)
    remaining_macros = list(_MACRO_CHARS)
    chosen = True

    while statement_templates and remaining_macros and chosen:
        chosen = False
        current_best_tokens = best_tokens
        current_best_body = best_body
        current_best_macros = macros
        current_best_template: str | None = None
        current_best_macro_char: str | None = None

        for template in statement_templates:
            aliased_template = _apply_aliases(template.template, aliases)
            for macro_char in remaining_macros:
                transformed_body, replacements = _apply_statement_macro(
                    aliased_code_body,
                    macro_char,
                    aliased_template,
                )
                if replacements < 2 or transformed_body == aliased_code_body:
                    continue
                proposed_macros = {**macros, macro_char: aliased_template}
                candidate_body = _render_compact_block(aliases, proposed_macros, transformed_body)
                candidate_tokens = _wrapped_token_count(model, candidate_body)
                if candidate_tokens < current_best_tokens:
                    current_best_tokens = candidate_tokens
                    current_best_body = candidate_body
                    current_best_macros = proposed_macros
                    current_best_template = template.template
                    current_best_macro_char = macro_char
                    chosen = True

        if chosen and current_best_template is not None and current_best_macro_char is not None:
            macros = current_best_macros
            best_body = current_best_body
            best_tokens = current_best_tokens
            statement_templates = [
                template
                for template in statement_templates
                if template.template != current_best_template
            ]
            remaining_macros.remove(current_best_macro_char)
            _, _, aliased_code_body = _parse_compact_headers(best_body)

    if best_tokens < best_plain_tokens:
        return best_body
    return base_code


def _decode_line(line: str) -> str:
    """Decode one `pymin` line into standard Python."""

    match = _LINE_RE.match(line.rstrip())
    if match is None:
        raise TranspileDecodeError(f"Malformed pymin line: {line!r}")

    depth = int(match.group("depth"))
    opcode = match.group("opcode")
    body = (match.group("body") or "").rstrip()
    raw = match.group("raw")
    indent = "    " * depth

    if opcode is None:
        return indent + (raw or "").rstrip()

    if opcode in _BLOCK_OPCODES:
        keyword = _BLOCK_OPCODES[opcode]
        if opcode in {"O", "T", "N"} and body:
            raise TranspileDecodeError(f"Opcode {opcode} cannot have trailing content.")
        if opcode not in {"O", "T", "N"} and not body:
            raise TranspileDecodeError(f"Opcode {opcode} requires trailing content.")
        suffix = f" {body}" if body else ""
        return f"{indent}{keyword}{suffix}:"

    if opcode in _SIMPLE_OPCODES:
        keyword = _SIMPLE_OPCODES[opcode]
        if opcode in {"P", "B", "K"} and body:
            raise TranspileDecodeError(f"Opcode {opcode} cannot have trailing content.")
        suffix = f" {body}" if body else ""
        return f"{indent}{keyword}{suffix}".rstrip()

    raise TranspileDecodeError(f"Unknown opcode: {opcode}")


def _decode_alias_pymin(text: str) -> str:
    """Decode alias- and macro-based `pymin` into normal Python."""

    aliases, macros, body = _parse_compact_headers(text)
    if not aliases and not macros:
        raise TranspileDecodeError("No compact headers found.")
    if not body:
        raise TranspileDecodeError("Alias pymin block has no body.")

    expanded_lines: list[str] = []
    for line in body.splitlines():
        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]
        if stripped:
            macro_char, separator, argument = stripped.partition(" ")
            template = macros.get(macro_char)
            if separator and template is not None:
                expanded_lines.append(f"{indent}{template.replace('{}', argument, 1)}")
                continue
        expanded_lines.append(line)

    decoded = "\n".join(expanded_lines).rstrip()
    for alias, target in aliases.items():
        decoded = decoded.replace(alias, target)
    return decoded


def decode_pymin_to_python(text: str) -> str:
    """Decode a single `pymin` code payload into standard Python."""

    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise TranspileDecodeError("Empty pymin block.")
    if any(_ALIAS_HEADER_RE.match(line.strip()) for line in lines) or any(
        _MACRO_HEADER_RE.match(line.strip()) and line.count("{}") == 1 for line in lines
    ):
        return _decode_alias_pymin(text)
    return "\n".join(_decode_line(line) for line in lines)


def verify_python_block(decoded_code: str) -> bool:
    """Verify that decoded Python is syntactically valid and compilable."""

    try:
        parsed = ast.parse(decoded_code)
        compile(parsed, "<lumin-pymin>", "exec")
    except SyntaxError:
        return False
    return True


def replace_with_python_blocks(text: str, decoded_blocks: list[str]) -> str:
    """Replace each `pymin` fence with a standard Python code block."""

    result = text
    blocks = extract_pymin_blocks(text)
    if len(blocks) != len(decoded_blocks):
        raise TranspileDecodeError("Decoded block count does not match source block count.")

    for block, decoded in zip(blocks, decoded_blocks, strict=True):
        replacement = f"```python\n{decoded}\n```"
        result = result.replace(block.raw_fence, replacement, 1)
    return result
