"""Tests for intelligent chunk ranking and selection."""

from __future__ import annotations

import unittest

from engine.chunker import rank_chunks, select_relevant_chunks


class ChunkerTests(unittest.TestCase):
    """Validate semantic density and relevance-driven chunking."""

    def test_select_relevant_chunks_prefers_task_related_sections(self) -> None:
        text = """
Intro paragraph about the company picnic, lunch menu, and office parking updates.

The agreement has three key risks. First, the indemnity clause is uncapped and exposes the buyer to unlimited liability.
Second, termination rights are one-sided and allow the seller to exit without cure.
Third, confidentiality survives forever and blocks operational disclosures.

Another paragraph about office snacks, coffee machine repairs, and team schedules.
""".strip()
        selection = select_relevant_chunks(
            model="gpt-5.4-mini",
            text=text,
            task="Summarize the main contract risks in this agreement.",
            max_context_tokens=120,
        )
        self.assertIn("key risks", selection.selected_text.lower())
        self.assertNotIn("coffee machine", selection.selected_text.lower())
        self.assertLess(selection.selected_tokens, selection.original_tokens)

    def test_rank_chunks_orders_high_signal_chunks_first(self) -> None:
        text = """
Filler about team lunch and hallway paint colors.

Important constraints: Never expose API keys. Always validate input schemas. Required retention period is 30 days.

More filler about office chairs and parking passes.
""".strip()
        ranked = rank_chunks(
            model="gpt-5.4-mini",
            text=text,
            task="List the security constraints and required retention period.",
            target_tokens=80,
        )
        self.assertTrue(ranked)
        self.assertIn("never expose api keys", ranked[0].text.lower())


if __name__ == "__main__":
    unittest.main()
