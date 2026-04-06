"""Tests for the experimental feature framework."""

from __future__ import annotations

import unittest

from experimental.cget import CGETV0Experiment
from experimental.registry import apply_experiments, resolve_requested_experiments
from models.schemas import ChatCompletionRequest


class ExperimentalFrameworkTests(unittest.TestCase):
    """Validate opt-in experiments and safe output trimming."""

    def test_resolve_requested_experiments_from_list(self) -> None:
        request = ChatCompletionRequest.model_validate(
            {
                "model": "gpt-5.4-mini",
                "messages": [{"role": "user", "content": "Review this patch."}],
                "lumin_experiments": ["cget_v0"],
            }
        )
        self.assertIn("cget_v0", resolve_requested_experiments(request))

    def test_cget_trims_preamble(self) -> None:
        request = ChatCompletionRequest.model_validate(
            {
                "model": "gpt-5.4-mini",
                "messages": [{"role": "user", "content": "What port is the FastAPI server using?"}],
                "lumin_experimental_cget": True,
                "lumin_cget_mode": "preamble",
            }
        )
        payload = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": (
                            "Sure! I'd be happy to help with that.\n\n"
                            "The FastAPI server is configured to run on port 8000."
                        ),
                    }
                }
            ]
        }

        outcome = CGETV0Experiment().apply(
            context=type(
                "Ctx",
                (),
                {"request": request, "model": "gpt-5.4-mini", "provider": "openai", "response_payload": payload},
            )()
        )

        self.assertTrue(outcome.applied)
        self.assertEqual(outcome.status, "trimmed")
        self.assertEqual(
            outcome.response_payload["choices"][0]["message"]["content"],
            "The FastAPI server is configured to run on port 8000.",
        )

    def test_cget_skips_conversational_requests(self) -> None:
        request = ChatCompletionRequest.model_validate(
            {
                "model": "gpt-5.4-mini",
                "messages": [{"role": "user", "content": "Tell me a joke about databases."}],
                "lumin_experimental_cget": True,
            }
        )
        payload = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Sure! Here is a joke about databases...",
                    }
                }
            ]
        }
        processed, headers, metadata = apply_experiments(
            request=request,
            model="gpt-5.4-mini",
            provider="openai",
            response_payload=payload,
            enabled_experiments=("cget_v0",),
        )
        self.assertEqual(processed["choices"][0]["message"]["content"], payload["choices"][0]["message"]["content"])
        self.assertEqual(headers["X-Lumin-Experiment-Status"], "none")
        self.assertEqual(metadata["cget_v0"]["status"], "skipped_intent")


if __name__ == "__main__":
    unittest.main()
