"""Tests for connector settings APIs."""

from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

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
class ConnectorsApiTests(unittest.TestCase):
    """Ensure connector settings can be listed, saved, and deleted."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_state_store = state_store_module._STATE_STORE
        state_store_module._STATE_STORE = state_store_module.StateStore(f"{self._tmpdir.name}/state.db")
        self.client = TestClient(main_module.app)

    def tearDown(self) -> None:
        state_store_module._STATE_STORE = self._original_state_store
        self._tmpdir.cleanup()

    def test_list_save_and_delete_connector(self) -> None:
        list_response = self.client.get(
            "/api/settings/connectors",
            headers={"X-Lumin-Key": get_settings().dashboard_key},
        )
        self.assertEqual(list_response.status_code, 200)
        names = {item["connector_type"] for item in list_response.json()}
        self.assertIn("app", names)
        self.assertIn("slack", names)
        self.assertIn("notion", names)
        self.assertIn("telegram", names)
        self.assertIn("whatsapp", names)
        self.assertIn("gmail", names)
        self.assertIn("matrix", names)

        save_response = self.client.post(
            "/api/settings/connectors",
            headers={"X-Lumin-Key": get_settings().dashboard_key},
            json={
                "connector_type": "slack",
                "display_name": "Ops Slack",
                "config": {
                    "workspace_name": "Acme",
                    "bot_token": "xoxb-secret-token",
                    "default_channel": "#ops",
                },
            },
        )
        self.assertEqual(save_response.status_code, 200)
        payload = save_response.json()
        self.assertEqual(payload["status"], "configured")
        self.assertEqual(payload["display_name"], "Ops Slack")
        self.assertEqual(payload["config_values"]["workspace_name"], "Acme")
        self.assertNotEqual(payload["config_values"]["bot_token"], "xoxb-secret-token")

        delete_response = self.client.delete(
            "/api/settings/connectors/slack",
            headers={"X-Lumin-Key": get_settings().dashboard_key},
        )
        self.assertEqual(delete_response.status_code, 200)
        self.assertTrue(delete_response.json()["deleted"])

    def test_telegram_test_and_send_endpoints(self) -> None:
        self.client.post(
            "/api/settings/connectors",
            headers={"X-Lumin-Key": get_settings().dashboard_key},
            json={
                "connector_type": "telegram",
                "display_name": "Telegram Bot",
                "config": {
                    "bot_token": "123:secret",
                    "default_chat_id": "-100123",
                },
            },
        )

        def fake_urlopen(request_obj, timeout=0):  # noqa: ARG001
            class FakeResponse:
                def __init__(self, payload: dict[str, object]) -> None:
                    self._payload = payload

                def read(self) -> bytes:
                    import json

                    return json.dumps(self._payload).encode("utf-8")

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

            url = getattr(request_obj, "full_url", "")
            if url.endswith("/getMe"):
                return FakeResponse({"ok": True, "result": {"username": "lumin_test_bot"}})
            if url.endswith("/sendMessage"):
                return FakeResponse({"ok": True, "result": {"message_id": 42}})
            raise AssertionError(f"Unexpected URL: {url}")

        with patch("engine.connector_runtime.urlopen", side_effect=fake_urlopen):
            test_response = self.client.post(
                "/api/settings/connectors/telegram/test",
                headers={"X-Lumin-Key": get_settings().dashboard_key},
                json={},
            )
            self.assertEqual(test_response.status_code, 200)
            self.assertEqual(test_response.json()["status"], "ready")

            send_response = self.client.post(
                "/api/settings/connectors/telegram/send-test",
                headers={"X-Lumin-Key": get_settings().dashboard_key},
                json={},
            )
            self.assertEqual(send_response.status_code, 200)
            self.assertEqual(send_response.json()["status"], "sent")


if __name__ == "__main__":
    unittest.main()
