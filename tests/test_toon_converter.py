"""Tests for JSON -> TOON prompt conversion."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from engine.toon_converter import ToonConverter


class ToonConverterTests(unittest.TestCase):
    """Validate safe TOON conversion behavior."""

    def setUp(self) -> None:
        self.converter = ToonConverter("gpt-4o-mini")

    def test_uniform_array_converts_and_saves_tokens(self) -> None:
        messages = [
            {
                "role": "user",
                "content": (
                    "Here is my data:\n"
                    '[{"id": 1, "name": "Alice", "role": "admin"},\n'
                    ' {"id": 2, "name": "Bob", "role": "user"},\n'
                    ' {"id": 3, "name": "Carol", "role": "editor"}]\n'
                    "What patterns do you see?"
                ),
            }
        ]

        result = self.converter.convert_prompt(messages)

        self.assertGreater(result.conversions_made, 0)
        self.assertGreater(result.savings_tokens, 0)
        self.assertIn("TOON:", result.converted_messages[0]["content"])

    def test_non_uniform_array_skips(self) -> None:
        messages = [
            {
                "role": "user",
                "content": '[{"id":1,"name":"Alice"},{"id":2,"role":"user"},{"id":3,"name":"Carol"}]',
            }
        ]
        result = self.converter.convert_prompt(messages)
        self.assertEqual(result.conversions_made, 0)
        self.assertEqual(result.converted_messages, messages)

    def test_small_array_skips(self) -> None:
        messages = [
            {
                "role": "user",
                "content": '[{"id":1,"name":"Alice"},{"id":2,"name":"Bob"}]',
            }
        ]
        result = self.converter.convert_prompt(messages)
        self.assertEqual(result.conversions_made, 0)

    def test_unprofitable_conversion_skips(self) -> None:
        messages = [
            {
                "role": "user",
                "content": (
                    "Data:\n"
                    '[{"identifier":1,"display_name":"Alice","access_role":"admin"},'
                    '{"identifier":2,"display_name":"Bob","access_role":"user"},'
                    '{"identifier":3,"display_name":"Carol","access_role":"editor"}]'
                ),
            }
        ]
        with patch.object(ToonConverter, "_is_profitable", return_value=False):
            result = self.converter.convert_prompt(messages)
        self.assertEqual(result.conversions_made, 0)
        self.assertEqual(result.converted_messages, messages)

    def test_no_json_returns_unchanged(self) -> None:
        messages = [{"role": "user", "content": "Nothing structured here."}]
        result = self.converter.convert_prompt(messages)
        self.assertEqual(result.conversions_made, 0)
        self.assertEqual(result.converted_messages, messages)

    def test_mixed_content_converts_only_json_part(self) -> None:
        original = (
            "Intro text.\n"
            '[{"id": 1, "name": "Alice", "role": "admin"},\n'
            ' {"id": 2, "name": "Bob", "role": "user"},\n'
            ' {"id": 3, "name": "Carol", "role": "editor"}]\n'
            "Outro text."
        )
        result = self.converter.convert_prompt([{"role": "user", "content": original}])
        converted = result.converted_messages[0]["content"]
        self.assertIn("Intro text.", converted)
        self.assertIn("Outro text.", converted)
        self.assertIn("TOON:", converted)

    def test_nested_objects_skip(self) -> None:
        messages = [
            {
                "role": "user",
                "content": (
                    '[{"id":1,"meta":{"name":"Alice"}},{"id":2,"meta":{"name":"Bob"}},{"id":3,"meta":{"name":"Carol"}}]'
                ),
            }
        ]
        result = self.converter.convert_prompt(messages)
        self.assertEqual(result.conversions_made, 0)

    def test_response_with_toon_converts_back_to_json(self) -> None:
        response = (
            "TOON:\n"
            "[3]{id,name,role}:\n"
            '  [1,"Alice","admin"]\n'
            '  [2,"Bob","user"]\n'
            '  [3,"Carol","editor"]'
        )
        converted = self.converter.convert_response(response)
        self.assertIn('"Alice"', converted)
        self.assertIn('"role":"admin"', converted)
        self.assertNotIn("TOON:", converted)

    def test_manual_fallback_works_when_library_unavailable(self) -> None:
        messages = [
            {
                "role": "user",
                "content": (
                    "Structured export:\n"
                    '[{"id": 1, "name": "Alice", "role": "admin"},\n'
                    ' {"id": 2, "name": "Bob", "role": "user"},\n'
                    ' {"id": 3, "name": "Carol", "role": "editor"}]'
                ),
            }
        ]
        with patch("engine.toon_converter._TOON_LIB", None):
            result = ToonConverter("gpt-4o-mini").convert_prompt(messages)
        self.assertGreaterEqual(result.conversions_made, 1)
        self.assertIn("[3]{id,name,role}:", result.converted_messages[0]["content"])

    def test_any_exception_returns_original_unchanged(self) -> None:
        messages = [{"role": "user", "content": '[{"id":1},{"id":2},{"id":3}]'}]
        with patch.object(ToonConverter, "_find_json_arrays", side_effect=RuntimeError("boom")):
            result = self.converter.convert_prompt(messages)
        self.assertEqual(result.conversions_made, 0)
        self.assertEqual(result.converted_messages, messages)


if __name__ == "__main__":
    unittest.main()
