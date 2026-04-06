"""Tests for MVP readiness, OpenClaw scan, and integration preflight APIs."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
    import engine.state_store as state_store_module
    import main as main_module
except ModuleNotFoundError:  # pragma: no cover - depends on local env
    TestClient = None
    state_store_module = None
    main_module = None

from config import get_settings


@unittest.skipIf(TestClient is None or main_module is None, "fastapi test dependencies are unavailable")
class MvpReadinessApiTests(unittest.TestCase):
    """Ensure readiness/reporting endpoints expose useful launch signals."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_state_store = state_store_module._STATE_STORE
        state_store_module._STATE_STORE = state_store_module.StateStore(f"{self._tmpdir.name}/state.db")
        self.client = TestClient(main_module.app)

    def tearDown(self) -> None:
        state_store_module._STATE_STORE = self._original_state_store
        self._tmpdir.cleanup()

    def test_scan_openclaw_source_detects_markers(self) -> None:
        source = Path(self._tmpdir.name) / "openclaw-home"
        source.mkdir(parents=True, exist_ok=True)
        (source / "openclaw.json").write_text("{}", encoding="utf-8")
        (source / "workspace").mkdir()
        (source / "auth-profiles.json").write_text("{}", encoding="utf-8")

        response = self.client.get(
            "/api/settings/openclaw/scan",
            headers={"X-Lumin-Key": get_settings().dashboard_key},
            params={"source_path": str(source)},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["detected"])
        self.assertEqual(payload["kind"], "openclaw")
        self.assertIn("openclaw.json", payload["signals"])

    def test_readiness_and_preflight_endpoints(self) -> None:
        save_provider = self.client.post(
            "/api/settings/providers",
            headers={"X-Lumin-Key": get_settings().dashboard_key},
            json={
                "provider_type": "openrouter",
                "display_name": "Main Router",
                "config": {"api_key": "sk-or-secret"},
            },
        )
        self.assertEqual(save_provider.status_code, 200)

        save_connector = self.client.post(
            "/api/settings/connectors",
            headers={"X-Lumin-Key": get_settings().dashboard_key},
            json={
                "connector_type": "telegram",
                "display_name": "Telegram Bot",
                "config": {"bot_token": "123:secret"},
            },
        )
        self.assertEqual(save_connector.status_code, 200)

        issued = state_store_module._STATE_STORE.register_desktop_agent(
            name="My Desktop",
            hostname="test-host",
            group_id="main",
            metadata={"capabilities": ["nanoclaw"]},
        )
        state_store_module._STATE_STORE.touch_agent(issued["agent_id"], status="online")

        provider_check = self.client.post(
            "/api/settings/providers/openrouter/test",
            headers={"X-Lumin-Key": get_settings().dashboard_key},
            json={},
        )
        self.assertEqual(provider_check.status_code, 200)
        self.assertEqual(provider_check.json()["status"], "ready")

        connector_check = self.client.post(
            "/api/settings/connectors/telegram/test",
            headers={"X-Lumin-Key": get_settings().dashboard_key},
            json={},
        )
        self.assertEqual(connector_check.status_code, 200)
        self.assertEqual(connector_check.json()["status"], "ready")

        readiness = self.client.get(
            "/api/mvp/readiness",
            headers={"X-Lumin-Key": get_settings().dashboard_key},
        )
        self.assertEqual(readiness.status_code, 200)
        payload = readiness.json()
        self.assertIn("score_pct", payload)
        self.assertGreaterEqual(payload["score_pct"], 0)
        self.assertIn("desktop_agent", {item["key"] for item in payload["items"]})
        self.assertIn("openrouter", payload["facts"]["configured_provider_types"])
        self.assertIn("telegram", payload["facts"]["configured_connector_types"])
        self.assertEqual(payload["facts"]["online_agent_count"], 1)

    def test_runtime_and_doctor_endpoints(self) -> None:
        runtime_set = self.client.post(
            "/api/settings/runtime",
            headers={"X-Lumin-Key": get_settings().dashboard_key},
            json={
                "active_provider": "openrouter",
                "workspace_root_hint": "/workspace/hostapp",
                "main_group": "main",
            },
        )
        self.assertEqual(runtime_set.status_code, 200)
        self.assertEqual(runtime_set.json()["active_provider"], "openrouter")

        doctor = self.client.get(
            "/api/doctor",
            headers={"X-Lumin-Key": get_settings().dashboard_key},
        )
        self.assertEqual(doctor.status_code, 200)
        payload = doctor.json()
        self.assertIn(payload["overall_status"], {"ok", "warn", "fail"})
        self.assertIn("active_provider", {item["key"] for item in payload["checks"]})


if __name__ == "__main__":
    unittest.main()
