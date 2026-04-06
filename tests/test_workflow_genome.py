"""Tests for workflow genome mapping heuristics."""

from __future__ import annotations

import unittest

from engine.workflow_genome import detect_workflow_genome


class WorkflowGenomeTests(unittest.TestCase):
    """Validate conservative workflow shape detection."""

    def test_detects_debug_test_failure(self) -> None:
        messages = [
            {"role": "user", "content": "Fix the failing pytest case and keep the patch small."},
            {
                "role": "assistant",
                "content": (
                    "Tool result: pytest tests/test_export.py -q\n"
                    "FAILED test_invoice_export_handles_empty_tax_id\n"
                    "AssertionError: expected empty string, got None"
                ),
            },
            {
                "role": "assistant",
                "content": "We should patch billing/exporter.py and rerun the focused test.",
            },
        ]
        match = detect_workflow_genome(messages)
        self.assertEqual(match.genome, "debug_test_failure")
        self.assertTrue(match.agentic)

    def test_detects_explore_codebase(self) -> None:
        messages = [
            {"role": "user", "content": "Understand this codebase and tell me the next files to inspect."},
            {"role": "assistant", "content": "Tool result: list_directory src/ -> config.ts, router.ts, index.ts"},
            {"role": "assistant", "content": "Next I should read config.ts and router.ts."},
        ]
        match = detect_workflow_genome(messages)
        self.assertEqual(match.genome, "explore_codebase")
        self.assertTrue(match.agentic)

    def test_unknown_for_plain_chat(self) -> None:
        messages = [
            {"role": "user", "content": "Tell me a joke about databases."},
        ]
        match = detect_workflow_genome(messages)
        self.assertEqual(match.genome, "unknown")


if __name__ == "__main__":
    unittest.main()
