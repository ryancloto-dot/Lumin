"""Tests for the persistent SQLite-backed state store."""

from __future__ import annotations

import tempfile
import unittest

from models.schemas import RequestEntry
from engine.state_store import StateStore


class StateStoreTests(unittest.TestCase):
    """Exercise durable request history, pairing, and remote task flows."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.store = StateStore(f"{self._tmpdir.name}/state.db")

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_request_entries_are_persisted(self) -> None:
        entry = RequestEntry(
            id="req_state_1",
            timestamp="2026-04-04T12:00:00+00:00",
            model_requested="gpt-4o",
            model_used="gpt-4o",
            original_tokens=100,
            sent_tokens=80,
            savings_pct=20.0,
            saved_dollars=0.01,
            actual_cost=0.02,
            would_have_cost=0.03,
            compression_tier="free",
            cache_hit=False,
            source="internal_control",
            context_id="ctx_1",
            routing_reason="routing_disabled",
            latency_ms=1200,
        )

        self.store.add_request_entry(entry)
        entries = self.store.list_request_entries(limit=10, source="internal_control", context_id="ctx_1")

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].id, "req_state_1")
        self.assertEqual(entries[0].actual_cost, 0.02)
        self.assertEqual(entries[0].source, "internal_control")
        self.assertEqual(entries[0].context_id, "ctx_1")

    def test_pairing_code_can_be_claimed_once(self) -> None:
        issued = self.store.create_pairing_code(ttl_seconds=60)
        claimed = self.store.claim_pairing_code(issued["code"], "Ryan iPhone")

        self.assertIsNotNone(claimed)
        assert claimed is not None
        client = self.store.authenticate_mobile_token(claimed["mobile_token"])
        self.assertIsNotNone(client)
        self.assertEqual(client["device_name"], "Ryan iPhone")

        second_claim = self.store.claim_pairing_code(issued["code"], "Another Device")
        self.assertIsNone(second_claim)

    def test_agent_registration_and_task_lifecycle(self) -> None:
        registration = self.store.register_desktop_agent(
            name="Desk",
            hostname="ryan-pc",
            group_id="main",
            metadata={"capabilities": ["nanoclaw"]},
        )
        agent = self.store.authenticate_agent(registration["agent_token"])
        self.assertIsNotNone(agent)
        assert agent is not None

        task = self.store.create_task(
            message="Inspect the repo",
            group_id="main",
            context_id="ctx_1",
            origin="mobile_chat",
            metadata={"wait_for_result": True},
        )
        claimed = self.store.claim_next_task(str(agent["agent_id"]))
        self.assertIsNotNone(claimed)
        assert claimed is not None
        self.assertEqual(claimed["status"], "claimed")

        running = self.store.start_task(
            task_id=claimed["id"],
            agent_id=str(agent["agent_id"]),
        )
        self.assertIsNotNone(running)
        assert running is not None
        self.assertEqual(running["status"], "running")

        completed = self.store.complete_task(
            task_id=running["id"],
            agent_id=str(agent["agent_id"]),
            response_text="Done",
            model_used="nanoclaw",
            latency_ms=1500,
        )
        self.assertIsNotNone(completed)
        assert completed is not None
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["response_text"], "Done")
        self.assertEqual(completed["model_used"], "nanoclaw")
        self.assertEqual(task["id"], completed["id"])

    def test_task_claim_respects_group_id(self) -> None:
        registration = self.store.register_desktop_agent(
            name="Desk",
            hostname="ryan-pc",
            group_id="main",
            metadata={},
        )
        agent = self.store.authenticate_agent(registration["agent_token"])
        assert agent is not None

        self.store.create_task(
            message="wrong-group",
            group_id="other",
            context_id=None,
            origin="mobile_chat",
        )
        self.store.create_task(
            message="right-group",
            group_id="main",
            context_id=None,
            origin="mobile_chat",
        )

        claimed = self.store.claim_next_task(str(agent["agent_id"]), "main")
        self.assertIsNotNone(claimed)
        assert claimed is not None
        self.assertEqual(claimed["message"], "right-group")

    def test_create_task_is_idempotent_for_same_client_request_id(self) -> None:
        first = self.store.create_task(
            message="List the repo",
            group_id="main",
            context_id=None,
            origin="mobile",
            metadata={
                "wait_for_result": False,
                "client_request_id": "dash-req-123",
            },
        )
        second = self.store.create_task(
            message="List the repo",
            group_id="main",
            context_id=None,
            origin="mobile",
            metadata={
                "wait_for_result": False,
                "client_request_id": "dash-req-123",
            },
        )

        self.assertEqual(first["id"], second["id"])
        tasks = self.store.list_tasks(limit=10)
        self.assertEqual(len(tasks), 1)

    def test_runtime_preferences_round_trip(self) -> None:
        saved = self.store.set_runtime_preferences(
            active_provider="openrouter",
            active_model="openai/gpt-5.2",
            workspace_root_hint="/workspace/hostapp",
            main_group="ops",
        )

        self.assertEqual(saved["active_provider"], "openrouter")
        self.assertEqual(saved["active_model"], "openai/gpt-5.2")
        loaded = self.store.get_runtime_preferences()
        self.assertEqual(loaded["active_provider"], "openrouter")
        self.assertEqual(loaded["active_model"], "openai/gpt-5.2")
        self.assertEqual(loaded["workspace_root_hint"], "/workspace/hostapp")
        self.assertEqual(loaded["main_group"], "ops")

        saved_google = self.store.set_runtime_preferences(
            active_provider="google",
            active_model="gemini-2.5-flash",
            workspace_root_hint="/workspace/hostapp",
            main_group="main",
        )
        self.assertEqual(saved_google["active_provider"], "google")

        saved_ollama = self.store.set_runtime_preferences(
            active_provider="ollama",
            active_model="ollama/llama3.2",
            workspace_root_hint="/workspace/hostapp",
            main_group="main",
        )
        self.assertEqual(saved_ollama["active_provider"], "ollama")

if __name__ == "__main__":
    unittest.main()
