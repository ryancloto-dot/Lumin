"""Small baseline-vs-Lumin evaluation runner for quality and savings."""

from __future__ import annotations

import asyncio
import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config import get_settings


def _load_cases(path: str) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Eval case file must contain a top-level JSON array.")
    return [item for item in payload if isinstance(item, dict)]


def _normalize_text(text: str) -> str:
    return " ".join(text.strip().split())


def evaluate_assertion(output_text: str, assertion: dict[str, Any]) -> tuple[bool, str]:
    """Return whether *output_text* satisfies the assertion and why."""

    mode = str(assertion.get("mode") or "nonempty").strip().lower()
    normalized = _normalize_text(output_text)

    if mode == "nonempty":
        return bool(normalized), "nonempty"

    if mode == "exact":
        expected = _normalize_text(str(assertion.get("value") or ""))
        passed = normalized == expected
        return passed, f"exact:{expected}"

    if mode == "contains":
        expected = str(assertion.get("value") or "")
        passed = expected.lower() in output_text.lower()
        return passed, f"contains:{expected}"

    if mode == "all_contains":
        values = [str(item) for item in assertion.get("values") or []]
        missing = [value for value in values if value.lower() not in output_text.lower()]
        return not missing, "all_contains" if not missing else f"missing:{','.join(missing)}"

    raise ValueError(f"Unsupported assertion mode: {mode}")


def _extract_text(response_json: dict[str, Any]) -> str:
    choices = response_json.get("choices") or []
    first_choice = choices[0] if choices else {}
    message = first_choice.get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if content is None:
        return ""
    return json.dumps(content, sort_keys=True)


def _headers_for_endpoint(*, api_key: str | None, dashboard_key: str | None = None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if dashboard_key:
        headers["X-Lumin-Key"] = dashboard_key
    return headers


def _upstream_base_url(provider: str, settings: Any) -> tuple[str, str | None]:
    provider_name = provider.strip().lower()
    if provider_name == "openai":
        return settings.openai_base_url.rstrip("/"), settings.openai_api_key
    if provider_name == "google":
        return "https://generativelanguage.googleapis.com/v1beta/openai", settings.google_api_key
    if provider_name == "ollama":
        return settings.ollama_base_url.rstrip("/") + "/v1", None
    raise ValueError(f"Unsupported baseline provider: {provider}")


@dataclass
class EvalResult:
    case_id: str
    baseline_pass: bool
    lumin_pass: bool
    regression: bool
    baseline_text: str
    lumin_text: str
    saved_amount: float
    savings_pct: float


async def _post_json(
    client: httpx.AsyncClient,
    url: str,
    body: dict[str, Any],
    headers: dict[str, str],
) -> tuple[dict[str, Any], httpx.Headers]:
    response = await client.post(url, json=body, headers=headers)
    response.raise_for_status()
    return response.json(), response.headers


async def run_eval(
    *,
    cases_path: str,
    provider: str,
    lumin_base_url: str,
) -> dict[str, Any]:
    settings = get_settings()
    cases = _load_cases(cases_path)
    upstream_base_url, upstream_api_key = _upstream_base_url(provider, settings)
    lumin_url = lumin_base_url.rstrip("/") + "/v1/chat/completions"
    upstream_url = upstream_base_url.rstrip("/") + "/chat/completions"
    results: list[EvalResult] = []

    async with httpx.AsyncClient(timeout=60.0) as client:
        for case in cases:
            body = {
                "model": str(case.get("model") or "gpt-4o-mini"),
                "messages": case.get("messages") or [],
            }
            assertion = case.get("assertion") or {"mode": "nonempty"}

            baseline_json, _ = await _post_json(
                client,
                upstream_url,
                body,
                _headers_for_endpoint(api_key=upstream_api_key),
            )
            baseline_text = _extract_text(baseline_json)
            baseline_pass, _ = evaluate_assertion(baseline_text, assertion)

            lumin_body = dict(body)
            lumin_body["lumin_disable_cache"] = True
            lumin_body["lumin_context_id"] = f"eval-{case.get('id', 'case')}"
            lumin_json, lumin_headers = await _post_json(
                client,
                lumin_url,
                lumin_body,
                _headers_for_endpoint(api_key=None),
            )
            lumin_text = _extract_text(lumin_json)
            lumin_pass, _ = evaluate_assertion(lumin_text, assertion)
            saved_amount = float(lumin_headers.get("X-Lumin-Saved") or 0.0)
            stats_header = lumin_headers.get("X-Lumin-Stats")
            savings_pct = 0.0
            if stats_header:
                try:
                    stats = json.loads(stats_header)
                    original_tokens = int(stats.get("original_tokens") or 0)
                    sent_tokens = int(stats.get("sent_tokens") or 0)
                    if original_tokens:
                        savings_pct = round(((original_tokens - sent_tokens) / original_tokens) * 100, 4)
                except json.JSONDecodeError:
                    savings_pct = 0.0

            results.append(
                EvalResult(
                    case_id=str(case.get("id") or f"case-{len(results)+1}"),
                    baseline_pass=baseline_pass,
                    lumin_pass=lumin_pass,
                    regression=baseline_pass and not lumin_pass,
                    baseline_text=baseline_text,
                    lumin_text=lumin_text,
                    saved_amount=saved_amount,
                    savings_pct=savings_pct,
                )
            )

    total = len(results)
    baseline_passes = sum(1 for item in results if item.baseline_pass)
    lumin_passes = sum(1 for item in results if item.lumin_pass)
    regressions = sum(1 for item in results if item.regression)
    avg_savings_pct = round(sum(item.savings_pct for item in results) / total, 4) if total else 0.0
    total_saved_amount = round(sum(item.saved_amount for item in results), 8)
    return {
        "cases": total,
        "baseline_pass_rate_pct": round((baseline_passes / total) * 100, 4) if total else 0.0,
        "lumin_pass_rate_pct": round((lumin_passes / total) * 100, 4) if total else 0.0,
        "regressions": regressions,
        "regression_rate_pct": round((regressions / total) * 100, 4) if total else 0.0,
        "avg_savings_pct": avg_savings_pct,
        "total_saved_amount": total_saved_amount,
        "results": [
            {
                "case_id": item.case_id,
                "baseline_pass": item.baseline_pass,
                "lumin_pass": item.lumin_pass,
                "regression": item.regression,
                "saved_amount": item.saved_amount,
                "savings_pct": item.savings_pct,
                "baseline_text": item.baseline_text,
                "lumin_text": item.lumin_text,
            }
            for item in results
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run baseline-vs-Lumin quality evals.")
    parser.add_argument(
        "--cases",
        default="evals/cases/smoke.json",
        help="Path to the eval case JSON file.",
    )
    parser.add_argument(
        "--provider",
        default="openai",
        choices=("openai", "google", "ollama"),
        help="Direct baseline provider to compare against.",
    )
    parser.add_argument(
        "--lumin-base-url",
        default="http://127.0.0.1:8000",
        help="Base URL for the local Lumin instance.",
    )
    args = parser.parse_args()

    result = asyncio.run(
        run_eval(
            cases_path=args.cases,
            provider=args.provider,
            lumin_base_url=args.lumin_base_url,
        )
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
