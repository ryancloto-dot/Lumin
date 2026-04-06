"""Tests for agent preset import/apply and desktop fallback behavior."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
    import engine.agent_presets as agent_presets_module
    import engine.state_store as state_store_module
    import main as main_module
except ModuleNotFoundError:  # pragma: no cover - depends on local env
    TestClient = None
    agent_presets_module = None
    state_store_module = None
    main_module = None

from config import get_settings


@unittest.skipIf(TestClient is None or main_module is None, "fastapi test dependencies are unavailable")
class AgentPresetsApiTests(unittest.TestCase):
    """Verify OpenClaw import and desktop fallback APIs."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_state_store = state_store_module._STATE_STORE
        self._original_preset_manager = agent_presets_module._PRESET_MANAGER
        self._original_fallback = main_module._generate_direct_chat_response
        state_store_module._STATE_STORE = state_store_module.StateStore(f"{self._tmpdir.name}/state.db")
        agent_presets_module._PRESET_MANAGER = agent_presets_module.AgentPresetManager(self._tmpdir.name)
        self.client = TestClient(main_module.app)

    def tearDown(self) -> None:
        state_store_module._STATE_STORE = self._original_state_store
        agent_presets_module._PRESET_MANAGER = self._original_preset_manager
        main_module._generate_direct_chat_response = self._original_fallback
        self._tmpdir.cleanup()

    def test_import_preset_and_apply_to_group(self) -> None:
        source_dir = Path(self._tmpdir.name) / "openclaw-src"
        (source_dir / ".claude" / "skills" / "debug-helper").mkdir(parents=True, exist_ok=True)
        (source_dir / "memory.md").write_text("# memory\n- remembers things\n", encoding="utf-8")
        (source_dir / "skills.md").write_text("# skills\n- debug\n", encoding="utf-8")
        (source_dir / ".claude" / "settings.json").write_text('{"sandbox":"workspace-write"}', encoding="utf-8")
        (source_dir / ".claude" / "skills" / "debug-helper" / "SKILL.md").write_text("# Debug Helper\n", encoding="utf-8")

        response = self.client.post(
            "/api/settings/agent-presets/import",
            headers={"X-Lumin-Key": get_settings().dashboard_key},
            json={
                "preset_name": "OpenClaw Main",
                "source_path": str(source_dir),
                "apply_to_group": "main",
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["name"], "openclaw-main")
        self.assertIn("memory.md", payload["files"])
        self.assertIn("main", payload["applied_groups"])
        self.assertEqual(payload["description"], "Imported from an existing OpenClaw or NanoClaw folder.")

        main_group = Path(self._tmpdir.name) / "groups" / "main"
        self.assertTrue((main_group / "memory.md").exists())
        self.assertTrue((main_group / "skills.md").exists())
        self.assertTrue((main_group / ".claude" / "settings.json").exists())
        self.assertTrue((main_group / ".claude" / "skills" / "debug-helper" / "SKILL.md").exists())

    def test_builtin_presets_are_listed(self) -> None:
        response = self.client.get(
            "/api/settings/agent-presets",
            headers={"X-Lumin-Key": get_settings().dashboard_key},
        )
        self.assertEqual(response.status_code, 200)
        names = {item["name"] for item in response.json()}
        self.assertIn("business-partner", names)
        self.assertIn("research-analyst", names)
        self.assertIn("execution-lead", names)
        self.assertIn("autoresearch", names)

    def test_desktop_failure_falls_back_to_direct_lumin_reply(self) -> None:
        async def fake_fallback(*, message: str, context_id: str | None, group_id: str):
            return (f"Fallback answered: {message}", main_module.MobileChatSavings(tokens_saved=0, dollars_saved=0.0, savings_pct=0.0, context_compressed=False), "gpt-5.4-mini")

        main_module._generate_direct_chat_response = fake_fallback

        registration_response = self.client.post(
            "/api/desktop/register",
            headers={"X-Lumin-Desktop-Key": get_settings().desktop_secret},
            json={
                "name": "Desk",
                "hostname": "ryan-pc",
                "group_id": "main",
                "capabilities": ["nanoclaw"],
            },
        )
        self.assertEqual(registration_response.status_code, 200)
        agent_token = registration_response.json()["agent_token"]

        task_response = self.client.post(
            "/api/tasks",
            headers={"X-Lumin-Key": get_settings().dashboard_key},
            json={"message": "Inspect this repo", "group_id": "main", "wait_for_result": False},
        )
        self.assertEqual(task_response.status_code, 200)
        task_id = task_response.json()["id"]

        claim_response = self.client.post(
            "/api/desktop/tasks/claim",
            headers={"X-Lumin-Agent-Token": agent_token},
            json={},
        )
        self.assertEqual(claim_response.status_code, 200)
        self.assertEqual(claim_response.json()["id"], task_id)

        result_response = self.client.post(
            f"/api/desktop/tasks/{task_id}/result",
            headers={"X-Lumin-Agent-Token": agent_token},
            json={
                "error_text": "NanoClaw container is not logged in to its model provider.",
                "model_used": "nanoclaw",
                "latency_ms": 1200,
            },
        )
        self.assertEqual(result_response.status_code, 200)
        payload = result_response.json()
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["model_used"], "gpt-5.4-mini")
        self.assertIn("Fallback answered: Inspect this repo", payload["response_text"])
        self.assertTrue(payload["metadata"]["fallback_used"])
        self.assertEqual(payload["metadata"]["final_status"], "fallback_used")

    def test_claimed_task_can_transition_to_running(self) -> None:
        registration_response = self.client.post(
            "/api/desktop/register",
            headers={"X-Lumin-Desktop-Key": get_settings().desktop_secret},
            json={
                "name": "Desk",
                "hostname": "ryan-pc",
                "group_id": "main",
                "capabilities": ["nanoclaw"],
            },
        )
        agent_token = registration_response.json()["agent_token"]

        task_response = self.client.post(
            "/api/tasks",
            headers={"X-Lumin-Key": get_settings().dashboard_key},
            json={"message": "Inspect this repo", "group_id": "main", "wait_for_result": False},
        )
        task_id = task_response.json()["id"]

        claim_response = self.client.post(
            "/api/desktop/tasks/claim",
            headers={"X-Lumin-Agent-Token": agent_token},
            json={},
        )
        self.assertEqual(claim_response.status_code, 200)
        self.assertEqual(claim_response.json()["status"], "claimed")

        start_response = self.client.post(
            f"/api/desktop/tasks/{task_id}/started",
            headers={"X-Lumin-Agent-Token": agent_token},
            json={"stage": "running"},
        )
        self.assertEqual(start_response.status_code, 200)
        self.assertEqual(start_response.json()["status"], "running")

        cancel_response = self.client.post(
            f"/api/tasks/{task_id}/cancel",
            headers={"X-Lumin-Key": get_settings().dashboard_key},
        )
        self.assertEqual(cancel_response.status_code, 200)
        self.assertEqual(cancel_response.json()["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
