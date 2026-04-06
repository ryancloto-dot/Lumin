"""Tests for the lightweight eval runner logic."""

from __future__ import annotations

import unittest

from evals.runner import evaluate_assertion


class EvalRunnerTests(unittest.TestCase):
    """Keep the simple assertion logic stable."""

    def test_exact_assertion(self) -> None:
        passed, reason = evaluate_assertion("4", {"mode": "exact", "value": "4"})
        self.assertTrue(passed)
        self.assertIn("exact:", reason)

    def test_contains_assertion(self) -> None:
        passed, reason = evaluate_assertion(
            "The capital of France is Paris.",
            {"mode": "contains", "value": "Paris"},
        )
        self.assertTrue(passed)
        self.assertEqual(reason, "contains:Paris")

    def test_all_contains_reports_missing_value(self) -> None:
        passed, reason = evaluate_assertion(
            '{"status":"ok"}',
            {"mode": "all_contains", "values": ["status", "result"]},
        )
        self.assertFalse(passed)
        self.assertIn("missing:result", reason)


if __name__ == "__main__":
    unittest.main()
