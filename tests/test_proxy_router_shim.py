"""Targeted regression tests for the Anthropic shim and NanoClaw bridge helpers."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from fastapi import Response
    from fastapi.testclient import TestClient
    import engine.state_store as state_store_module
    import main as main_module
    import proxy.router as router_module
except ModuleNotFoundError:  # pragma: no cover - depends on local env
    Response = None
    TestClient = None
    state_store_module = None
    main_module = None
    router_module = None

from models.schemas import ChatCompletionRequest, ChatMessage
from engine.nanoclaw_bridge import run_nanoclaw_chat_bridge


@unittest.skipIf(router_module is None, "fastapi router dependencies are unavailable")
class ProxyRouterShimUnitTests(unittest.TestCase):
    """Verify request-shim details that broke the real NanoClaw runtime."""

    def test_gpt5_requests_use_max_completion_tokens(self) -> None:
        request = ChatCompletionRequest(
            model="gpt-5.4-mini",
            messages=[ChatMessage(role="user", content="Hello")],
            max_tokens=321,
            lumin_tier="free",
        )

        body = router_module._build_openai_request_body(request, request.messages, "gpt-5.4-mini")

        self.assertNotIn("max_tokens", body)
        self.assertEqual(body["max_completion_tokens"], 321)

    def test_openrouter_provider_override_uses_saved_default_model(self) -> None:
        request = ChatCompletionRequest(
            model="gpt-5.4-mini",
            messages=[ChatMessage(role="user", content="Hello")],
            lumin_provider="openrouter",
        )

        fake_store = type(
            "FakeStore",
            (),
            {
                "get_runtime_preferences": staticmethod(lambda: {"active_provider": "auto"}),
                "get_provider_config": staticmethod(
                    lambda provider_type: {
                        "api_key": "sk-or-secret",
                        "base_url": "https://openrouter.ai/api/v1",
                        "default_model": "openai/gpt-5.4-mini",
                    }
                    if provider_type == "openrouter"
                    else None
                )
            },
        )()

        with patch.object(router_module, "get_state_store", return_value=fake_store):
            provider, upstream_model, config = router_module._resolve_upstream_provider(
                request,
                "gpt-5.4-mini",
            )

        self.assertEqual(provider, "openrouter")
        self.assertEqual(upstream_model, "openai/gpt-5.4-mini")
        self.assertEqual(config["api_key"], "sk-or-secret")

    def test_runtime_active_provider_uses_openrouter_without_request_override(self) -> None:
        request = ChatCompletionRequest(
            model="gpt-5.4-mini",
            messages=[ChatMessage(role="user", content="Hello")],
        )

        fake_store = type(
            "FakeStore",
            (),
            {
                "get_runtime_preferences": staticmethod(lambda: {"active_provider": "openrouter"}),
                "get_provider_config": staticmethod(
                    lambda provider_type: {
                        "api_key": "sk-or-secret",
                        "base_url": "https://openrouter.ai/api/v1",
                        "default_model": "openai/gpt-5.4-mini",
                    }
                    if provider_type == "openrouter"
                    else None
                ),
            },
        )()

        with patch.object(router_module, "get_state_store", return_value=fake_store), patch.object(
            router_module,
            "get_settings",
            return_value=type("Settings", (), {"openai_api_key": None})(),
        ):
            provider, upstream_model, _ = router_module._resolve_upstream_provider(
                request,
                "gpt-5.4-mini",
            )

        self.assertEqual(provider, "openrouter")
        self.assertEqual(upstream_model, "openai/gpt-5.4-mini")

    def test_openrouter_can_backfill_openai_availability_when_no_openai_key(self) -> None:
        fake_store = type(
            "FakeStore",
            (),
            {
                "get_provider_config": staticmethod(
                    lambda provider_type: {"api_key": "sk-or-secret"} if provider_type == "openrouter" else None
                )
            },
        )()
        fake_settings = type("Settings", (), {"openai_api_key": None, "anthropic_api_key": None})()

        with patch("engine.router.get_state_store", return_value=fake_store), patch(
            "engine.router.get_settings",
            return_value=fake_settings,
        ):
            from engine.router import _provider_available

            self.assertTrue(_provider_available("gpt-5.4-mini"))

    def test_send_upstream_openrouter_calls_openai_compatible_endpoint(self) -> None:
        request = ChatCompletionRequest(
            model="gpt-5.4-mini",
            messages=[ChatMessage(role="user", content="Hello")],
        )

        async def fake_call_openai_compatible(body, *, base_url, api_key, extra_headers=None):
            self.assertEqual(body["model"], "openai/gpt-5.4-mini")
            self.assertEqual(base_url, "https://openrouter.ai/api/v1")
            self.assertEqual(api_key, "sk-or-secret")
            self.assertEqual(extra_headers["X-Title"], "Lumin")
            return {
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 123,
                "model": "openai/gpt-5.4-mini",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            }

        with patch.object(router_module, "_call_openai_compatible", side_effect=fake_call_openai_compatible):
            payload = asyncio.run(
                router_module._send_upstream(
                    "openrouter",
                    "openai/gpt-5.4-mini",
                    request,
                    request.messages,
                    {
                        "api_key": "sk-or-secret",
                        "base_url": "https://openrouter.ai/api/v1",
                    },
                )
            )

        self.assertEqual(payload["choices"][0]["message"]["content"], "OK")

    def test_runtime_active_provider_uses_google_without_request_override(self) -> None:
        request = ChatCompletionRequest(
            model="gemini-2.5-flash",
            messages=[ChatMessage(role="user", content="Hello")],
        )

        fake_store = type(
            "FakeStore",
            (),
            {
                "get_runtime_preferences": staticmethod(lambda: {"active_provider": "google"}),
                "get_provider_config": staticmethod(
                    lambda provider_type: {
                        "api_key": "google-secret",
                        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                        "default_model": "gemini-2.5-flash",
                    }
                    if provider_type == "google"
                    else None
                ),
            },
        )()

        with patch.object(router_module, "get_state_store", return_value=fake_store):
            provider, upstream_model, config = router_module._resolve_upstream_provider(
                request,
                "gemini-2.5-flash",
            )

        self.assertEqual(provider, "google")
        self.assertEqual(upstream_model, "gemini-2.5-flash")
        self.assertEqual(config["api_key"], "google-secret")

    def test_send_upstream_google_calls_openai_compatible_endpoint(self) -> None:
        request = ChatCompletionRequest(
            model="gemini-2.5-flash",
            messages=[ChatMessage(role="user", content="Hello")],
        )

        async def fake_call_openai_compatible(body, *, base_url, api_key, extra_headers=None):
            del extra_headers
            self.assertEqual(body["model"], "gemini-2.5-flash")
            self.assertEqual(base_url, "https://generativelanguage.googleapis.com/v1beta/openai")
            self.assertEqual(api_key, "google-secret")
            return {
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 123,
                "model": "gemini-2.5-flash",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "Gemini OK"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            }

        with patch.object(router_module, "_call_openai_compatible", side_effect=fake_call_openai_compatible):
            payload = asyncio.run(
                router_module._send_upstream(
                    "google",
                    "gemini-2.5-flash",
                    request,
                    request.messages,
                    {
                        "api_key": "google-secret",
                        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                    },
                )
            )

        self.assertEqual(payload["choices"][0]["message"]["content"], "Gemini OK")

    def test_runtime_active_provider_uses_ollama_without_request_override(self) -> None:
        request = ChatCompletionRequest(
            model="ollama/llama3.2",
            messages=[ChatMessage(role="user", content="Hello")],
        )

        fake_store = type(
            "FakeStore",
            (),
            {
                "get_runtime_preferences": staticmethod(lambda: {"active_provider": "ollama"}),
                "get_provider_config": staticmethod(
                    lambda provider_type: {
                        "base_url": "http://localhost:11434/v1",
                        "default_model": "ollama/llama3.2",
                    }
                    if provider_type == "ollama"
                    else None
                ),
            },
        )()

        with patch.object(router_module, "get_state_store", return_value=fake_store):
            provider, upstream_model, config = router_module._resolve_upstream_provider(
                request,
                "ollama/llama3.2",
            )

        self.assertEqual(provider, "ollama")
        self.assertEqual(upstream_model, "ollama/llama3.2")
        self.assertEqual(config["base_url"], "http://localhost:11434/v1")

    def test_send_upstream_ollama_calls_openai_compatible_endpoint(self) -> None:
        request = ChatCompletionRequest(
            model="ollama/llama3.2",
            messages=[ChatMessage(role="user", content="Hello")],
        )

        async def fake_call_openai_compatible(body, *, base_url, api_key, extra_headers=None):
            del extra_headers
            self.assertEqual(body["model"], "llama3.2")
            self.assertEqual(base_url, "http://localhost:11434/v1")
            self.assertIsNone(api_key)
            return {
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 123,
                "model": "llama3.2",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "Ollama OK"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            }

        with patch.object(router_module, "_call_openai_compatible", side_effect=fake_call_openai_compatible):
            payload = asyncio.run(
                router_module._send_upstream(
                    "ollama",
                    "ollama/llama3.2",
                    request,
                    request.messages,
                    {
                        "base_url": "http://localhost:11434/v1",
                    },
                )
            )

        self.assertEqual(payload["choices"][0]["message"]["content"], "Ollama OK")


@unittest.skipIf(TestClient is None or main_module is None or router_module is None, "fastapi test dependencies are unavailable")
class ProxyRouterShimApiTests(unittest.TestCase):
    """Verify Anthropic-compatible runtime paths stay reachable."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_state_store = state_store_module._STATE_STORE
        state_store_module._STATE_STORE = state_store_module.StateStore(f"{self._tmpdir.name}/state.db")
        self.client = TestClient(main_module.app)

    def tearDown(self) -> None:
        state_store_module._STATE_STORE = self._original_state_store
        self._tmpdir.cleanup()

    def test_anthropic_probe_accepts_head_and_get(self) -> None:
        head_response = self.client.head("/anthropic/main")
        get_response = self.client.get("/anthropic/main")

        self.assertEqual(head_response.status_code, 200)
        self.assertEqual(get_response.status_code, 200)

    def test_anthropic_v1_messages_alias_works(self) -> None:
        async def fake_handle_chat_completion(request: ChatCompletionRequest):
            del request
            return Response(
                content=json.dumps(
                    {
                        "id": "chatcmpl-test",
                        "object": "chat.completion",
                        "created": 123,
                        "model": "gpt-5.4-mini",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": "Bridge OK"},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
                    }
                ),
                media_type="application/json",
                headers={"X-Lumin-Request-Id": "req_test"},
            )

        with patch.object(router_module, "_handle_chat_completion", side_effect=fake_handle_chat_completion):
            response = self.client.post(
                "/anthropic/main/v1/messages",
                json={
                    "model": "sonnet",
                    "messages": [{"role": "user", "content": [{"type": "text", "text": "Hi"}]}],
                    "max_tokens": 128,
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["content"][0]["text"], "Bridge OK")
        self.assertEqual(response.headers["X-Lumin-Request-Id"], "req_test")


class NanoClawBridgeParsingTests(unittest.TestCase):
    """Verify the bridge can parse final JSON after log noise."""

    def test_bridge_parses_last_json_line_after_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            src = root / "src"
            src.mkdir(parents=True, exist_ok=True)
            (src / "lumin-chat.ts").write_text("// test bridge stub\n", encoding="utf-8")

            settings_overrides = {
                "nanoclaw_root": str(root),
                "nanoclaw_cli_timeout_seconds": 5,
                "nanoclaw_proxy_url": "http://127.0.0.1:8000",
            }

            completed = type(
                "Completed",
                (),
                {
                    "stdout": '\n'.join(
                        [
                            "[14:00:00.000] INFO (123): Booting",
                            '{"response":"Bridge reply","groupId":"main","modelUsed":"nanoclaw"}',
                        ]
                    )
                },
            )()

            with patch("engine.nanoclaw_bridge.get_settings") as mock_settings, patch(
                "engine.nanoclaw_bridge.shutil.which",
                return_value="/usr/bin/npx",
            ), patch("engine.nanoclaw_bridge.run", return_value=completed):
                mock_settings.return_value = type("Settings", (), settings_overrides)()
                result = run_nanoclaw_chat_bridge("hi", "main", 1000)

        self.assertEqual(result["response"], "Bridge reply")
