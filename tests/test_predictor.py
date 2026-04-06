"""Tests for cache-aware oracle prediction output."""

from __future__ import annotations

import unittest

from engine.cache import get_semantic_cache
from models.schemas import ChatMessage, PredictRequest


class PredictorTests(unittest.TestCase):
    """Validate cache-aware cost prediction."""

    @classmethod
    def setUpClass(cls) -> None:
        try:
            from oracle.predictor import build_prediction
        except ModuleNotFoundError as exc:
            raise unittest.SkipTest(f"predictor dependencies unavailable: {exc}") from exc
        cls.build_prediction = staticmethod(build_prediction)

    def test_prediction_includes_cache_adjusted_fields(self) -> None:
        cache = get_semantic_cache()
        payload = {
            "model": "gpt-5.4-mini",
            "messages": [
                {"role": "user", "content": "Summarize key risks in contract."},
            ],
        }
        cache.put(
            "gpt-5.4-mini",
            payload,
            response={"choices": [{"message": {"role": "assistant", "content": "risk summary"}}]},
            usage={"saved_amount": 0.01},
        )

        response = self.build_prediction(
            PredictRequest(
                model="gpt-5.4-mini",
                messages=[ChatMessage(role="user", content="What are the main risks in this agreement?")],
                candidate_models=["gpt-5.4-mini"],
                expected_output_tokens=120,
            )
        )
        self.assertGreater(response.semantic_cache_hit_score or 0.0, 0.0)
        self.assertEqual(response.semantic_cache_adjusted_cheapest_model, "gpt-5.4-mini")
        self.assertIsNotNone(response.breakdown[0].cache_adjusted_total_cost)


if __name__ == "__main__":
    unittest.main()
