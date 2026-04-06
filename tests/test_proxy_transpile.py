"""Tests for transpilation-related proxy helpers."""

from __future__ import annotations

import unittest

from models.schemas import ChatCompletionRequest


class ProxyTranspileTests(unittest.TestCase):
    """Validate transpilation request handling and output decoding."""

    @classmethod
    def setUpClass(cls) -> None:
        try:
            from proxy.router import (
                _process_transpiled_output,
                _resolve_transpile_options,
                _should_attempt_transpile,
            )
        except ModuleNotFoundError as exc:
            raise unittest.SkipTest(f"proxy dependencies unavailable: {exc}") from exc

        cls.process_transpiled_output = staticmethod(_process_transpiled_output)
        cls.resolve_transpile_options = staticmethod(_resolve_transpile_options)
        cls.should_attempt_transpile = staticmethod(_should_attempt_transpile)

    def test_resolve_transpile_options_defaults_off(self) -> None:
        request = ChatCompletionRequest(model="gpt-4o-mini", messages=[])
        self.assertEqual(self.resolve_transpile_options(request), (False, "none"))

    def test_resolve_transpile_options_python_on(self) -> None:
        request = ChatCompletionRequest(
            model="gpt-4o-mini",
            messages=[],
            lumin_transpile=True,
            lumin_transpile_lang="python",
        )
        self.assertEqual(self.resolve_transpile_options(request), (True, "python"))

    def test_process_transpiled_output_decodes_python(self) -> None:
        payload = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "```pymin\n@0 D add(a, b)\n@1 R a + b\n```",
                    }
                }
            ]
        }
        updated, meta = self.process_transpiled_output("gpt-4o-mini", payload)
        self.assertEqual(meta["status"], "pass")
        self.assertTrue(meta["used"])
        self.assertIn("```python", updated["choices"][0]["message"]["content"])
        self.assertGreater(meta["decoded_tokens"], meta["compressed_tokens"])

    def test_process_transpiled_output_no_blocks(self) -> None:
        payload = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "No Python here.",
                    }
                }
            ]
        }
        updated, meta = self.process_transpiled_output("gpt-4o-mini", payload)
        self.assertEqual(updated["choices"][0]["message"]["content"], "No Python here.")
        self.assertEqual(meta["status"], "disabled")

    def test_should_attempt_transpile_skips_explanation_prompt(self) -> None:
        messages = [
            {
                "role": "user",
                "content": "Explain what Python classes are and why inheritance matters.",
            }
        ]
        enabled, status = self.should_attempt_transpile("gpt-4o-mini", messages, "python")
        self.assertFalse(enabled)
        self.assertIn(status, {"not_python_codegen", "estimated_output_too_small", "predicted_unprofitable"})

    def test_should_attempt_transpile_accepts_large_python_codegen(self) -> None:
        messages = [
            {
                "role": "user",
                "content": (
                    "Write Python FastAPI code for a CRUD service with Pydantic models, repository and "
                    "service classes, request/response schemas, validation, and pytest tests."
                ),
            }
        ]
        enabled, status = self.should_attempt_transpile("gpt-5.4", messages, "python")
        self.assertTrue(enabled)
        self.assertEqual(status, "enabled")


if __name__ == "__main__":
    unittest.main()
