"""Unit tests for the Python transpilation codec."""

from __future__ import annotations

import unittest

from engine.transpiler import (
    TranspileDecodeError,
    canonicalize_python,
    decode_pymin_to_python,
    encode_python_to_pymin,
    estimate_spec_prompt_tokens,
    extract_pymin_blocks,
    replace_with_python_blocks,
    verify_python_block,
)
from engine.tokenizer import count_input_tokens


class TranspilerTests(unittest.TestCase):
    """Exercise the reversible Python codec."""

    def test_decode_function_block(self) -> None:
        pymin = "@0 D add(a, b)\n@1 R a + b"
        decoded = decode_pymin_to_python(pymin)
        self.assertEqual(decoded, "def add(a, b):\n    return a + b")
        self.assertTrue(verify_python_block(decoded))

    def test_decode_nested_control_flow(self) -> None:
        pymin = "\n".join(
            [
                "@0 I ok",
                "@1 F item in items",
                "@2 print(item)",
                "@1 O",
                "@2 P",
            ]
        )
        decoded = decode_pymin_to_python(pymin)
        expected = "\n".join(
            [
                "if ok:",
                "    for item in items:",
                "        print(item)",
                "    else:",
                "        pass",
            ]
        )
        self.assertEqual(decoded, expected)
        self.assertTrue(verify_python_block(decoded))

    def test_decode_async_try_except_finally(self) -> None:
        pymin = "\n".join(
            [
                "@0 A fetch()",
                "@1 T",
                "@2 R 1",
                "@1 X Exception as exc",
                "@2 R 2",
                "@1 N",
                "@2 P",
            ]
        )
        decoded = decode_pymin_to_python(pymin)
        self.assertIn("async def fetch():", decoded)
        self.assertIn("except Exception as exc:", decoded)
        self.assertIn("finally:", decoded)
        self.assertTrue(verify_python_block(decoded))

    def test_extract_and_replace_blocks(self) -> None:
        text = "Here you go.\n```pymin\n@0 D hi()\n@1 R 'ok'\n```\nDone."
        blocks = extract_pymin_blocks(text)
        self.assertEqual(len(blocks), 1)
        replaced = replace_with_python_blocks(text, ["def hi():\n    return 'ok'"])
        self.assertIn("```python", replaced)
        self.assertNotIn("```pymin", replaced)

    def test_extract_alias_python_block(self) -> None:
        text = "```python\n~ transform_very_long_name\nresult = ~(item)\n```"
        blocks = extract_pymin_blocks(text)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].fence_lang, "python")

    def test_extract_macro_python_block(self) -> None:
        text = "```python\n! cache['last'] = {}\n! value\n```"
        blocks = extract_pymin_blocks(text)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].fence_lang, "python")

    def test_unknown_opcode_fails(self) -> None:
        with self.assertRaises(TranspileDecodeError):
            decode_pymin_to_python("@0 Z nope")

    def test_empty_block_fails(self) -> None:
        with self.assertRaises(TranspileDecodeError):
            decode_pymin_to_python("")

    def test_malformed_indentation_marker_fails(self) -> None:
        with self.assertRaises(TranspileDecodeError):
            decode_pymin_to_python("def add(a, b)")

    def test_canonicalize_python(self) -> None:
        source = "value=(1+2)\n"
        self.assertEqual(canonicalize_python(source), "value = 1 + 2")

    def test_spec_prompt_is_compact(self) -> None:
        self.assertLess(estimate_spec_prompt_tokens(), 260)

    def test_alias_round_trip(self) -> None:
        pymin = "\n".join(
            [
                "§=transform_very_long_name",
                "result = §(item)",
                "other = §(other_item)",
            ]
        )
        decoded = decode_pymin_to_python(pymin)
        self.assertIn("transform_very_long_name", decoded)
        self.assertTrue(verify_python_block(decoded))

    def test_statement_macro_round_trip(self) -> None:
        pymin = "\n".join(
            [
                "! cache['last'] = {}",
                "! value",
                "! build_audit_message(user_id)",
            ]
        )
        decoded = decode_pymin_to_python(pymin)
        self.assertEqual(
            decoded,
            "\n".join(
                [
                    "cache['last'] = value",
                    "cache['last'] = build_audit_message(user_id)",
                ]
            ),
        )
        self.assertTrue(verify_python_block(decoded))

    def test_alias_and_macro_round_trip(self) -> None:
        pymin = "\n".join(
            [
                "§ 'Extremely verbose audit trail message'",
                "! cache['last'] = {}",
                "! §",
                "! build_audit_message(user_id)",
            ]
        )
        decoded = decode_pymin_to_python(pymin)
        self.assertIn("cache['last'] = 'Extremely verbose audit trail message'", decoded)
        self.assertIn("cache['last'] = build_audit_message(user_id)", decoded)
        self.assertTrue(verify_python_block(decoded))

    def test_encode_prefers_profitable_aliasing(self) -> None:
        python_code = "\n".join(
            [
                "result = transform_very_long_name(item)",
                "other = transform_very_long_name(other_item)",
                "third = transform_very_long_name(third_item)",
            ]
        )
        encoded = encode_python_to_pymin(python_code, "gpt-4o-mini")
        self.assertIn("transform_very_long_name", encoded)
        self.assertIn("result = ", encoded)
        self.assertNotEqual(encoded, python_code)

        python_tokens = count_input_tokens(
            "gpt-4o-mini",
            [{"role": "assistant", "content": f"```python\n{python_code}\n```"}],
        )
        pymin_tokens = count_input_tokens(
            "gpt-4o-mini",
            [{"role": "assistant", "content": f"```python\n{encoded}\n```"}],
        )
        self.assertLess(pymin_tokens, python_tokens)

    def test_encode_prefers_profitable_statement_macro(self) -> None:
        python_code = "\n".join(
            [
                "cache['last'] = 'Extremely verbose audit trail message'",
                "cache['last'] = 'Another verbose audit trail message'",
                "cache['last'] = build_fallback_audit_message(user_id)",
            ]
        )
        encoded = encode_python_to_pymin(python_code, "gpt-4o-mini")
        self.assertIn("cache['last'] = {}", encoded)
        self.assertNotEqual(encoded, python_code)

        python_tokens = count_input_tokens(
            "gpt-4o-mini",
            [{"role": "assistant", "content": f"```python\n{python_code}\n```"}],
        )
        pymin_tokens = count_input_tokens(
            "gpt-4o-mini",
            [{"role": "assistant", "content": f"```python\n{encoded}\n```"}],
        )
        self.assertLess(pymin_tokens, python_tokens)

    def test_encode_skips_unprofitable_short_code(self) -> None:
        python_code = "def add(a, b):\n    return a + b"
        encoded = encode_python_to_pymin(python_code, "gpt-4o-mini")
        self.assertEqual(encoded, python_code)


if __name__ == "__main__":
    unittest.main()
