#!/usr/bin/env python3
"""Poll Lumin for remote tasks and execute them on the local NanoClaw runtime."""

from __future__ import annotations

import json
import os
import socket
import sys
import time
from pathlib import Path
from urllib import error, request

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import get_settings
from engine.nanoclaw_bridge import CalledProcessError, TimeoutExpired, run_nanoclaw_chat_bridge


def _base_url() -> str:
    configured = os.getenv("LUMIN_BASE_URL", "http://127.0.0.1:8000")
    return configured.rstrip("/")


def _desktop_secret() -> str:
    secret = os.getenv("LUMIN_DESKTOP_SECRET") or get_settings().desktop_secret
    if not secret:
        raise RuntimeError("LUMIN_DESKTOP_SECRET is required for desktop agent registration.")
    return secret


def _request_json(
    method: str,
    path: str,
    body: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> dict[str, object] | None:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = request.Request(
        f"{_base_url()}{path}",
        data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
        method=method,
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
            return json.loads(payload) if payload else None
    except error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed: HTTP {exc.code} {payload}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"{method} {path} failed: {exc.reason}") from exc


def _state_file() -> Path:
    return ROOT_DIR / "data" / "desktop_agent.json"


def _load_cached_registration() -> dict[str, object] | None:
    path = _state_file()
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _save_cached_registration(payload: dict[str, object]) -> None:
    path = _state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _register() -> dict[str, object]:
    cached = _load_cached_registration()
    if cached and cached.get("agent_token"):
        return cached

    payload = _request_json(
        "POST",
        "/api/desktop/register",
        body={
            "name": os.getenv("LUMIN_AGENT_NAME", "Lumin Desktop"),
            "hostname": socket.gethostname(),
            "group_id": os.getenv("LUMIN_AGENT_GROUP", "main"),
            "capabilities": ["nanoclaw", "local-desktop-control"],
        },
        headers={"X-Lumin-Desktop-Key": _desktop_secret()},
        timeout=30.0,
    )
    if payload is None:
        raise RuntimeError("Desktop registration returned no payload.")
    _save_cached_registration(payload)
    return payload


def _heartbeat(agent_token: str) -> dict[str, object]:
    payload = _request_json(
        "POST",
        "/api/desktop/heartbeat",
        body={},
        headers={"X-Lumin-Agent-Token": agent_token},
        timeout=15.0,
    )
    return payload or {}


def _claim(agent_token: str) -> dict[str, object] | None:
    return _request_json(
        "POST",
        "/api/desktop/tasks/claim",
        body={},
        headers={"X-Lumin-Agent-Token": agent_token},
        timeout=60.0,
    )


def _post_result(agent_token: str, task_id: str, *, response_text: str | None, error_text: str | None, latency_ms: int) -> None:
    _request_json(
        "POST",
        f"/api/desktop/tasks/{task_id}/result",
        body={
            "response_text": response_text,
            "error_text": error_text,
            "model_used": "nanoclaw",
            "latency_ms": latency_ms,
        },
        headers={"X-Lumin-Agent-Token": agent_token},
        timeout=30.0,
    )


def _mark_started(agent_token: str, task_id: str) -> None:
    _request_json(
        "POST",
        f"/api/desktop/tasks/{task_id}/started",
        body={"stage": "running"},
        headers={"X-Lumin-Agent-Token": agent_token},
        timeout=15.0,
    )


def main() -> int:
    registration = _register()
    agent_token = str(registration["agent_token"])
    poll_interval = float(registration.get("poll_interval_seconds") or get_settings().desktop_agent_poll_seconds)

    print(f"Desktop agent registered as {registration.get('agent_id')} against {_base_url()}")
    while True:
        try:
            heartbeat = _heartbeat(agent_token)
            poll_interval = float(heartbeat.get("poll_interval_seconds") or poll_interval)
            task = _claim(agent_token)
            if not task:
                time.sleep(poll_interval)
                continue

            task_id = str(task["id"])
            started_at = time.perf_counter()
            try:
                _mark_started(agent_token, task_id)
                result = run_nanoclaw_chat_bridge(
                    message=str(task["message"]),
                    group_id=str(task["group_id"]),
                    timeout_ms=int(get_settings().nanoclaw_cli_timeout_seconds * 1000),
                )
                _post_result(
                    agent_token,
                    task_id,
                    response_text=str(result.get("response", "")),
                    error_text=None,
                    latency_ms=int((time.perf_counter() - started_at) * 1000),
                )
            except (FileNotFoundError, CalledProcessError, TimeoutExpired, json.JSONDecodeError, RuntimeError) as exc:
                _post_result(
                    agent_token,
                    task_id,
                    response_text=None,
                    error_text=str(exc),
                    latency_ms=int((time.perf_counter() - started_at) * 1000),
                )
        except KeyboardInterrupt:
            print("Desktop agent stopped.")
            return 0
        except Exception as exc:
            print(f"Desktop agent loop error: {exc}", file=sys.stderr)
            time.sleep(max(poll_interval, 3.0))


if __name__ == "__main__":
    raise SystemExit(main())
