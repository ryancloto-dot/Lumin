"""Tests for mobile pairing and token-authenticated control endpoints."""

from __future__ import annotations

import tempfile
import unittest

try:
    from fastapi.testclient import TestClient
    import engine.state_store as state_store_module
    import main as main_module
    from engine.cache import RequestLedger
except ModuleNotFoundError:  # pragma: no cover - depends on local env
    TestClient = None
    state_store_module = None
    main_module = None
    RequestLedger = None

from config import get_settings
from models.schemas import RequestEntry


@unittest.skipIf(TestClient is None or main_module is None, "fastapi test dependencies are unavailable")
class MobilePairingApiTests(unittest.TestCase):
    """Ensure a paired mobile token can access the non-admin control surfaces."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_state_store = state_store_module._STATE_STORE
        self._original_request_ledger = main_module.get_request_ledger()
        state_store_module._STATE_STORE = state_store_module.StateStore(f"{self._tmpdir.name}/state.db")
        main_module.get_request_ledger()._entries.clear()  # type: ignore[attr-defined]
        main_module.get_request_ledger().add(
            RequestEntry(
                id="req_pair_1",
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
                cache_hit=False,
                routing_reason="routing_disabled",
                latency_ms=900,
            )
        )
        self.client = TestClient(main_module.app)

    def tearDown(self) -> None:
        state_store_module._STATE_STORE = self._original_state_store
        self._tmpdir.cleanup()

    def test_pairing_claim_and_mobile_token_auth(self) -> None:
        code_response = self.client.post(
            "/api/pairing/code",
            headers={"X-Lumin-Key": get_settings().dashboard_key},
        )
        self.assertEqual(code_response.status_code, 200)
        code = code_response.json()["code"]

        claim_response = self.client.post(
            "/api/pairing/claim",
            json={"code": code, "device_name": "Ryan iPhone"},
        )
        self.assertEqual(claim_response.status_code, 200)
        mobile_token = claim_response.json()["mobile_token"]

        stats_response = self.client.get(
            "/api/stats",
            headers={"X-Lumin-Mobile-Token": mobile_token},
        )
        self.assertEqual(stats_response.status_code, 200)
        self.assertIn("total_requests", stats_response.json())

        task_response = self.client.post(
            "/api/tasks",
            headers={"X-Lumin-Mobile-Token": mobile_token},
            json={"message": "Check desktop status", "group_id": "main", "wait_for_result": False},
        )
        self.assertEqual(task_response.status_code, 200)
        self.assertEqual(task_response.json()["status"], "queued")

    def test_settings_stays_dashboard_only(self) -> None:
        code = self.client.post(
            "/api/pairing/code",
            headers={"X-Lumin-Key": get_settings().dashboard_key},
        ).json()["code"]
        mobile_token = self.client.post(
            "/api/pairing/claim",
            json={"code": code, "device_name": "Ryan iPhone"},
        ).json()["mobile_token"]

        response = self.client.get(
            "/api/settings",
            headers={"X-Lumin-Mobile-Token": mobile_token},
        )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
