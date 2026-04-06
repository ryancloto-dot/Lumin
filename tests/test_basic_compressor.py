"""Regression tests for the free-tier basic compressor."""

from __future__ import annotations

import unittest

from engine.compressor import BasicCompressor
from models.schemas import ChatMessage


class BasicCompressorTests(unittest.TestCase):
    """Exercise conservative free-tier history and chunk compaction."""

    def test_free_compacts_repetitive_assistant_history(self) -> None:
        messages = [
            ChatMessage(
                role="system",
                content="You are a coding agent. Keep changes minimal and preserve user intent.",
            ),
            ChatMessage(role="user", content="Fix the failing invoice export test."),
            ChatMessage(
                role="assistant",
                content=(
                    "Certainly! I will fix the failing invoice export test, keep the patch minimal, "
                    "preserve your intent, and report exactly what changed."
                ),
            ),
            ChatMessage(
                role="assistant",
                content=(
                    "Restating context before acting: keep the patch minimal, preserve the user's "
                    "intent, rerun the focused test, and report exactly what changed."
                ),
            ),
            ChatMessage(
                role="assistant",
                content=(
                    "Before patching, restating the plan: keep the patch minimal, preserve the user's "
                    "intent, rerun the focused test, and report exactly what changed."
                ),
            ),
            ChatMessage(role="user", content="Continue and keep the patch tiny."),
        ]

        result = BasicCompressor("gpt-4o-mini").compress(messages)

        self.assertGreater(result.compression_breakdown["rules"]["history_turns_dropped"], 0)
        self.assertLess(result.compressed_tokens, result.original_tokens)
        self.assertEqual(
            result.compressed_messages[-1].content,
            "Continue and keep the patch tiny.",
        )

    def test_free_compacts_large_older_context(self) -> None:
        large_context = "\n\n".join(
            f"Section {index}: This subsystem handles message routing, retries, file access, "
            f"background workers, and dashboard telemetry for the agent platform."
            for index in range(1, 36)
        )
        messages = [
            ChatMessage(role="system", content="You are an analysis agent."),
            ChatMessage(
                role="assistant",
                content=large_context,
            ),
            ChatMessage(
                role="user",
                content="Focus on dashboard telemetry and summarize the routing implications.",
            ),
        ]

        result = BasicCompressor("gpt-4o-mini").compress(messages)

        self.assertGreater(
            result.compression_breakdown["rules"]["history_turns_summarized"],
            0,
        )
        self.assertLess(result.compressed_tokens, result.original_tokens)

    def test_free_prunes_large_static_system_tail_for_short_task(self) -> None:
        static_prefix = (
            "You are the local OpenClaw agent. Keep the user safe, follow the workspace "
            "instructions, avoid unnecessary changes, and prefer concise direct answers.\n\n"
            "Core rules:\n"
            "- Never fabricate results.\n"
            "- Preserve the user's exact intent.\n"
            "- Keep changes minimal.\n\n"
        )
        large_tail = "\n\n".join(
            f"## Workspace File {index}\n"
            "This file explains long-form workspace policy, tool behavior, style guidance, "
            "routing notes, and repeated operational details for the assistant.\n"
            "It is intentionally verbose so the compressor has a large static tail to trim.\n"
            for index in range(1, 80)
        )
        system_text = static_prefix + large_tail
        messages = [
            ChatMessage(role="system", content=system_text),
            ChatMessage(role="user", content="Reply with exactly: 4"),
        ]

        result = BasicCompressor("gpt-4o-mini").compress(messages)

        self.assertLess(result.compressed_tokens, result.original_tokens)
        self.assertGreater(
            result.compression_breakdown["static_context_pruning"]["messages_pruned"],
            0,
        )
        self.assertTrue(
            result.compressed_messages[0].content.startswith(static_prefix[:120])
        )


if __name__ == "__main__":
    unittest.main()
