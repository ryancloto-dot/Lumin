"""Tests for semantic cache matching and estimates."""

from __future__ import annotations

import unittest

from engine.cache import SemanticCache


class SemanticCacheTests(unittest.TestCase):
    """Validate exact and fuzzy cache behavior."""

    def test_fuzzy_match_handles_simple_paraphrase(self) -> None:
        cache = SemanticCache()
        original_payload = {
            "model": "gpt-5.4-mini",
            "messages": [
                {"role": "user", "content": "Summarize key risks in contract."},
            ],
        }
        cache.put(
            "gpt-5.4-mini",
            original_payload,
            response={"choices": [{"message": {"role": "assistant", "content": "Risk summary"}}]},
            usage={"saved_amount": 0.0125},
        )

        paraphrase_payload = {
            "model": "gpt-5.4-mini",
            "messages": [
                {"role": "user", "content": "What are the main risks in this agreement?"},
            ],
        }
        match = cache.get_with_score("gpt-5.4-mini", paraphrase_payload)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertFalse(match.exact)
        self.assertGreaterEqual(match.score, 0.58)

    def test_estimate_surfaces_hit_probability_and_saved_cost(self) -> None:
        cache = SemanticCache()
        payload = {
            "model": "gpt-5.4-mini",
            "messages": [
                {"role": "user", "content": "Extract customer ids from this CSV."},
            ],
        }
        cache.put(
            "gpt-5.4-mini",
            payload,
            response={"choices": [{"message": {"role": "assistant", "content": "[]"}}]},
            usage={"saved_amount": 0.0042},
        )

        estimate = cache.estimate(
            "gpt-5.4-mini",
            {
                "model": "gpt-5.4-mini",
                "messages": [
                    {"role": "user", "content": "Parse customer IDs out of the CSV file."},
                ],
            },
        )
        self.assertGreater(estimate["hit_score"], 0.0)
        self.assertGreater(estimate["hit_probability"], 0.0)
        self.assertEqual(estimate["estimated_saved_cost"], 0.0042)

    def test_multi_step_coding_requests_do_not_cross_hit_different_artifacts(self) -> None:
        cache = SemanticCache()
        module_payload = {
            "model": "gpt-4o",
            "messages": [
                {"role": "system", "content": "You are a coding agent."},
                {"role": "user", "content": "Write only the contents of `todo_stats.py`."},
            ],
        }
        cache.put(
            "gpt-4o",
            module_payload,
            response={"choices": [{"message": {"role": "assistant", "content": "module code"}}]},
            usage={"saved_amount": 0.001},
        )

        tests_payload = {
            "model": "gpt-4o",
            "messages": [
                {"role": "system", "content": "You are a coding agent."},
                {"role": "assistant", "content": "module code"},
                {"role": "user", "content": "Now write only the contents of `test_todo_stats.py`."},
            ],
        }

        match = cache.get_with_score("gpt-4o", tests_payload)
        self.assertIsNone(match)


if __name__ == "__main__":
    unittest.main()
