"""Tests for NanoClaw-aware context compression and session distillation."""

from __future__ import annotations

import unittest

from engine.context_compressor import NanoClawContextCompressor


class NanoClawContextCompressorTests(unittest.TestCase):
    """Exercise repeated-block distillation for NanoClaw context files."""

    def setUp(self) -> None:
        self.compressor = NanoClawContextCompressor()
        self.base_messages = [
            {
                "role": "system",
                "content": (
                    "# CLAUDE.md\n"
                    "- Always use FastAPI for APIs.\n"
                    "- Always add tests for behavior changes.\n"
                    "- Never break existing endpoints.\n"
                    "- Use typed Python and docstrings.\n"
                    "- Use typed Python and docstrings.\n"
                    "\n"
                    "## Project Context\n"
                    "This project is a cost optimization proxy for LLM APIs. "
                    "It silently reduces costs and reports savings to the user. "
                    "The codebase uses FastAPI, typed Python, dashboards, and a "
                    "mobile companion. Compression must preserve intent, fail safe, "
                    "and never mutate the last user message.\n"
                    "\n"
                    "## Skills\n"
                    "- file_search(path: str) -> str\n"
                    "- run_tests(target: str) -> str\n"
                    "- apply_patch(diff: str) -> None\n"
                    "- explain_changes(summary: str) -> None\n"
                    "\n"
                    "## Memory\n"
                    "- The user cares about cost visibility.\n"
                    "- The user wants mobile support.\n"
                    "- The user wants NanoClaw integrated into Lumin.\n"
                    "- The user prefers simple startup commands.\n"
                ),
            },
            {"role": "user", "content": "Add a health check route and explain the change."},
        ]

    def test_repeated_context_uses_distilled_summary(self) -> None:
        """Second pass for the same session should swap in a smaller summary."""

        first = self.compressor.compress(
            model="gpt-4o-mini",
            messages=self.base_messages,
            tier="aggressive",
            context_id="group-main",
        )
        second = self.compressor.compress(
            model="gpt-4o-mini",
            messages=self.base_messages,
            tier="aggressive",
            context_id="group-main",
        )

        self.assertLess(second.compressed_tokens, first.compressed_tokens)
        self.assertGreaterEqual(second.compression_breakdown["distilled_references_used"], 1)
        system_content = second.compressed_messages[0]["content"]
        self.assertIn("Established context summary:", system_content)
        self.assertIn("Never break existing endpoints", system_content)

    def test_changed_context_uses_updated_summary(self) -> None:
        """A changed block should refresh the cached summary instead of reusing stale text."""

        self.compressor.compress(
            model="gpt-4o-mini",
            messages=self.base_messages,
            tier="aggressive",
            context_id="group-main",
        )
        updated_messages = [
            {
                "role": "system",
                "content": self.base_messages[0]["content"].replace(
                    "Compression must preserve intent, fail safe, and never mutate the last user message.",
                    "Compression must preserve intent, fail safe, never mutate the last user message, and keep backwards compatibility.",
                ),
            },
            self.base_messages[1],
        ]

        updated = self.compressor.compress(
            model="gpt-4o-mini",
            messages=updated_messages,
            tier="aggressive",
            context_id="group-main",
        )

        self.assertGreaterEqual(updated.compression_breakdown["distilled_updates_used"], 1)
        self.assertIn("keep backw", updated.compressed_messages[0]["content"].lower())
        self.assertLess(updated.compressed_tokens, updated.original_tokens)

    def test_without_context_id_only_base_compression_runs(self) -> None:
        """Session distillation should stay off when no conversation context id is provided."""

        result = self.compressor.compress(
            model="gpt-4o-mini",
            messages=self.base_messages,
            tier="free",
            context_id=None,
        )

        self.assertEqual(result.compression_breakdown["distilled_references_used"], 0)
        self.assertEqual(result.compression_breakdown["distilled_updates_used"], 0)
        self.assertLess(result.compressed_tokens, result.original_tokens)

    def test_distillation_is_scoped_per_context_id(self) -> None:
        """Repeated context in one session must not affect a different session."""

        self.compressor.compress(
            model="gpt-4o-mini",
            messages=self.base_messages,
            tier="aggressive",
            context_id="group-main",
        )
        isolated = self.compressor.compress(
            model="gpt-4o-mini",
            messages=self.base_messages,
            tier="aggressive",
            context_id="group-other",
        )

        self.assertEqual(isolated.compression_breakdown["distilled_references_used"], 0)
        self.assertNotIn(
            "Established context summary:",
            isolated.compressed_messages[0]["content"],
        )

    def test_small_section_does_not_trigger_session_distillation(self) -> None:
        """Very small repeated context should stay on the safer base-compression path."""

        small_messages = [
            {"role": "system", "content": "# Memory\n- Use FastAPI.\n- Add tests.\n"},
            {"role": "user", "content": "Add a route."},
        ]

        self.compressor.compress(
            model="gpt-4o-mini",
            messages=small_messages,
            tier="aggressive",
            context_id="tiny",
        )
        repeated = self.compressor.compress(
            model="gpt-4o-mini",
            messages=small_messages,
            tier="aggressive",
            context_id="tiny",
        )

        self.assertEqual(repeated.compression_breakdown["distillation_attempts"], 0)
        self.assertEqual(repeated.compression_breakdown["distilled_references_used"], 0)

    def test_session_state_is_bounded(self) -> None:
        """The per-process session registry should stay bounded as contexts grow."""

        for index in range(300):
            self.compressor.compress(
                model="gpt-4o-mini",
                messages=self.base_messages,
                tier="aggressive",
                context_id=f"group-{index}",
            )

        self.assertLessEqual(len(self.compressor._session_blocks), self.compressor._max_sessions)


if __name__ == "__main__":
    unittest.main()
