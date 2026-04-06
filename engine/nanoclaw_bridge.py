"""Shared helpers for invoking the local NanoClaw bridge."""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from subprocess import CalledProcessError, TimeoutExpired, run
from typing import Any

from config import get_settings


def _looks_like_nanoclaw_auth_failure(result: dict[str, Any]) -> bool:
    """Return whether NanoClaw produced a credential/login failure response."""

    response_text = str(result.get("response") or "").lower()
    return (
        "not logged in" in response_text
        or "please run /login" in response_text
        or "please run /login" in str(result).lower()
    )


def run_nanoclaw_chat_bridge(message: str, group_id: str, timeout_ms: int | None = None) -> dict[str, Any]:
    """Invoke the NanoClaw local bridge CLI and return parsed JSON."""

    settings = get_settings()
    nanoclaw_root = Path(settings.nanoclaw_root)
    script_path = nanoclaw_root / "src" / "lumin-chat.ts"
    if not script_path.exists():
        raise FileNotFoundError(f"NanoClaw bridge script not found at {script_path}.")

    local_tsx = nanoclaw_root / "node_modules" / ".bin" / "tsx"
    npx_path = shutil.which("npx")
    if not local_tsx.exists() and npx_path is None:
        raise FileNotFoundError("Neither local `tsx` nor `npx` is available for NanoClaw bridge execution.")

    env = os.environ.copy()
    env["LUMIN_PROXY_URL"] = settings.nanoclaw_proxy_url
    command = (
        [str(local_tsx), "src/lumin-chat.ts"]
        if local_tsx.exists()
        else [npx_path, "tsx", "src/lumin-chat.ts"]
    )

    completed = run(
        command,
        input=json.dumps(
            {
                "message": message,
                "groupId": group_id,
                "timeoutMs": timeout_ms or int(settings.nanoclaw_cli_timeout_seconds * 1000),
            }
        ),
        text=True,
        capture_output=True,
        cwd=nanoclaw_root,
        env=env,
        check=True,
        timeout=settings.nanoclaw_cli_timeout_seconds + 5,
    )
    stdout = completed.stdout.strip()
    json_line = next(
        (
            line.strip()
            for line in reversed(stdout.splitlines())
            if line.strip().startswith("{") and line.strip().endswith("}")
        ),
        "",
    )
    if not json_line:
        raise json.JSONDecodeError("No JSON payload found in NanoClaw stdout.", stdout, 0)
    result = json.loads(json_line)
    if _looks_like_nanoclaw_auth_failure(result):
        raise RuntimeError("NanoClaw container is not logged in to its model provider.")
    return result


_BRIDGE_AVAILABLE_CACHE: tuple[float, bool] = (0.0, False)
_BRIDGE_AVAILABLE_TTL_SECONDS = 5.0


def nanoclaw_bridge_available() -> bool:
    """Return whether the local NanoClaw bridge script can be executed."""

    global _BRIDGE_AVAILABLE_CACHE
    now = time.monotonic()
    cached_at, cached_value = _BRIDGE_AVAILABLE_CACHE
    if now - cached_at < _BRIDGE_AVAILABLE_TTL_SECONDS:
        return cached_value

    settings = get_settings()
    nanoclaw_root = Path(settings.nanoclaw_root)
    script_path = nanoclaw_root / "src" / "lumin-chat.ts"
    local_tsx = nanoclaw_root / "node_modules" / ".bin" / "tsx"
    available = script_path.exists() and (local_tsx.exists() or shutil.which("npx") is not None)
    _BRIDGE_AVAILABLE_CACHE = (now, available)
    return available


__all__ = [
    "CalledProcessError",
    "TimeoutExpired",
    "nanoclaw_bridge_available",
    "run_nanoclaw_chat_bridge",
]
