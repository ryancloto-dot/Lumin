"""Tests for protected enterprise diagnostics surfaces."""

from __future__ import annotations

import tempfile
import unittest

from config import get_settings
from engine.cache import RequestLedger
import engine.state_store as state_store_module
from models.schemas import RequestEntry

try:
    from fastapi.testclient import TestClient
    from main import app
except ModuleNotFoundError:  # pragma: no cover - depends on local env
    TestClient = None
    app = None


class EnterpriseDiagnosticsTests(unittest.TestCase):
    """Ensure API-only diagnostics remain available and structured."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_state_store = state_store_module._STATE_STORE
        state_store_module._STATE_STORE = state_store_module.StateStore(f"{self._tmpdir.name}/state.db")
        with state_store_module.get_state_store()._connect() as connection:
            connection.execute("DELETE FROM request_entries")

    def tearDown(self) -> None:
        state_store_module._STATE_STORE = self._original_state_store
        self._tmpdir.cleanup()

    def test_request_ledger_stats_include_enterprise_breakdown(self) -> None:
        ledger = RequestLedger()
        ledger.add(
            RequestEntry(
                id="req_1",
                timestamp="2026-04-04T12:00:00+00:00",
                model_requested="gpt-4o",
                model_used="gpt-4o",
                original_tokens=100,
                sent_tokens=60,
                savings_pct=40.0,
                saved_dollars=0.01,
                actual_cost=0.02,
                would_have_cost=0.03,
                compression_tier="free",
                cache_hit=True,
                cache_type="semantic",
                cache_score=0.93,
                routing_reason="routing_disabled",
                latency_ms=1000,
                verification_result="fail",
                verification_fallback=True,
                workflow_genome="refactor",
                workflow_confidence=0.83,
            )
        )

        stats = ledger.get_stats()
        breakdown = stats["compression_breakdown"]

        self.assertIn("exact_cache_hits", breakdown)
        self.assertIn("semantic_cache_hits", breakdown)
        self.assertIn("verification_fallbacks", breakdown)
        self.assertEqual(stats["weighted_savings_pct"], 33.3333)
        self.assertEqual(breakdown["semantic_cache_hits"], 1)
        self.assertEqual(breakdown["verification_fallbacks"], 1)

    def test_api_settings_requires_key_and_returns_payload(self) -> None:
        if TestClient is None or app is None:
            self.skipTest("fastapi test dependencies are unavailable in this interpreter")
        client = TestClient(app)
        response = client.get(
            "/api/settings",
            headers={"X-Lumin-Key": get_settings().dashboard_key},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("cache_similarity_threshold", body)
        self.assertIn("allowed_experiments", body)
        self.assertIn("context_distill_max_blocks_per_session", body)


if __name__ == "__main__":
    unittest.main()
