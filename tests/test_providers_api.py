"""Tests for provider settings APIs."""

from __future__ import annotations

import tempfile
import unittest

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
class ProvidersApiTests(unittest.TestCase):
    """Ensure provider settings can be listed, saved, and deleted."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_state_store = state_store_module._STATE_STORE
        state_store_module._STATE_STORE = state_store_module.StateStore(f"{self._tmpdir.name}/state.db")
        self.client = TestClient(main_module.app)

    def tearDown(self) -> None:
        state_store_module._STATE_STORE = self._original_state_store
        self._tmpdir.cleanup()

    def test_list_save_and_delete_provider(self) -> None:
        list_response = self.client.get(
            "/api/settings/providers",
            headers={"X-Lumin-Key": get_settings().dashboard_key},
        )
        self.assertEqual(list_response.status_code, 200)
        names = {item["provider_type"] for item in list_response.json()}
        self.assertIn("openai", names)
        self.assertIn("anthropic", names)
        self.assertIn("openrouter", names)
        self.assertIn("google", names)
        self.assertIn("ollama", names)
        self.assertNotIn("codex-subscription", names)
        self.assertNotIn("glm", names)

        save_response = self.client.post(
            "/api/settings/providers",
            headers={"X-Lumin-Key": get_settings().dashboard_key},
            json={
                "provider_type": "openrouter",
                "display_name": "OpenRouter Main",
                "config": {
                    "api_key": "sk-or-secret-token",
                    "default_model": "openai/gpt-5.4-mini",
                },
            },
        )
        self.assertEqual(save_response.status_code, 200)
        payload = save_response.json()
        self.assertEqual(payload["status"], "configured")
        self.assertEqual(payload["display_name"], "OpenRouter Main")
        self.assertNotEqual(payload["config_values"]["api_key"], "sk-or-secret-token")

        delete_response = self.client.delete(
            "/api/settings/providers/openrouter",
            headers={"X-Lumin-Key": get_settings().dashboard_key},
        )
        self.assertEqual(delete_response.status_code, 200)
        self.assertTrue(delete_response.json()["deleted"])


if __name__ == "__main__":
    unittest.main()
