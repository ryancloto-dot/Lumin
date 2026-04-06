"""Persistent SQLite-backed state for request history and remote control."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
import time
import uuid
from calendar import timegm
from pathlib import Path
from typing import Any

from config import get_settings
from engine.connectors import get_connector_definition, list_connector_definitions, mask_connector_config
from engine.providers import get_provider_definition, list_provider_definitions, mask_provider_config
from models.schemas import RequestEntry


def _utc_timestamp() -> str:
    """Return an ISO-like UTC timestamp without requiring datetime parsing everywhere."""

    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class StateStore:
    """Small SQLite state layer for enterprise-grade durability."""

    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS request_entries (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    model_requested TEXT NOT NULL,
                    model_used TEXT NOT NULL,
                    original_tokens INTEGER NOT NULL,
                    sent_tokens INTEGER NOT NULL,
                    savings_pct REAL NOT NULL,
                    saved_dollars REAL NOT NULL,
                    actual_cost REAL NOT NULL,
                    would_have_cost REAL NOT NULL,
                    compression_tier TEXT NOT NULL,
                    cache_hit INTEGER NOT NULL,
                    cache_type TEXT NOT NULL,
                    cache_score REAL NOT NULL,
                    routing_reason TEXT NOT NULL,
                    latency_ms INTEGER NOT NULL,
                    verification_result TEXT NOT NULL,
                    verification_fallback INTEGER NOT NULL,
                    workflow_genome TEXT NOT NULL,
                    workflow_confidence REAL NOT NULL,
                    source TEXT NOT NULL DEFAULT 'proxy',
                    context_id TEXT,
                    freshness_score REAL NOT NULL DEFAULT 1.0,
                    pivot_detected INTEGER NOT NULL DEFAULT 0,
                    cache_guard_reason TEXT NOT NULL DEFAULT '',
                    toon_conversions INTEGER NOT NULL DEFAULT 0,
                    toon_tokens_saved INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS desktop_agents (
                    agent_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    hostname TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    token_hash TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pairing_codes (
                    code TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    claimed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS mobile_clients (
                    client_id TEXT PRIMARY KEY,
                    device_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    token_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS remote_tasks (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT,
                    group_id TEXT NOT NULL,
                    context_id TEXT,
                    message TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    status TEXT NOT NULL,
                    response_text TEXT,
                    error_text TEXT,
                    model_used TEXT,
                    latency_ms INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    metadata_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS connectors (
                    id TEXT PRIMARY KEY,
                    connector_type TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    configured_at TEXT,
                    last_tested_at TEXT
                );

                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS providers (
                    id TEXT PRIMARY KEY,
                    provider_type TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    configured_at TEXT
                );

                CREATE TABLE IF NOT EXISTS api_projects (
                    project_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    tier TEXT NOT NULL,
                    rpm_limit INTEGER NOT NULL,
                    tpm_limit INTEGER NOT NULL,
                    monthly_spend_limit REAL NOT NULL,
                    allowed_providers_json TEXT NOT NULL,
                    api_key_hash TEXT NOT NULL,
                    api_key_preview TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS api_project_providers (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    provider_type TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    configured_at TEXT,
                    UNIQUE(project_id, provider_type)
                );

                CREATE TABLE IF NOT EXISTS api_rate_windows (
                    project_id TEXT NOT NULL,
                    window_start TEXT NOT NULL,
                    request_count INTEGER NOT NULL,
                    token_count INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (project_id, window_start)
                );
                """
            )
            request_entry_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(request_entries)").fetchall()
            }
            if "source" not in request_entry_columns:
                connection.execute(
                    "ALTER TABLE request_entries ADD COLUMN source TEXT NOT NULL DEFAULT 'proxy'"
                )
            if "context_id" not in request_entry_columns:
                connection.execute(
                    "ALTER TABLE request_entries ADD COLUMN context_id TEXT"
                )
            if "freshness_score" not in request_entry_columns:
                connection.execute(
                    "ALTER TABLE request_entries ADD COLUMN freshness_score REAL NOT NULL DEFAULT 1.0"
                )
            if "pivot_detected" not in request_entry_columns:
                connection.execute(
                    "ALTER TABLE request_entries ADD COLUMN pivot_detected INTEGER NOT NULL DEFAULT 0"
                )
            if "cache_guard_reason" not in request_entry_columns:
                connection.execute(
                    "ALTER TABLE request_entries ADD COLUMN cache_guard_reason TEXT NOT NULL DEFAULT ''"
                )
            if "toon_conversions" not in request_entry_columns:
                connection.execute(
                    "ALTER TABLE request_entries ADD COLUMN toon_conversions INTEGER NOT NULL DEFAULT 0"
                )
            if "toon_tokens_saved" not in request_entry_columns:
                connection.execute(
                    "ALTER TABLE request_entries ADD COLUMN toon_tokens_saved INTEGER NOT NULL DEFAULT 0"
                )

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def add_request_entry(self, entry: RequestEntry) -> None:
        """Persist a request entry."""

        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO request_entries (
                    id, timestamp, model_requested, model_used, original_tokens, sent_tokens,
                    savings_pct, saved_dollars, actual_cost, would_have_cost, compression_tier,
                    cache_hit, cache_type, cache_score, routing_reason, latency_ms,
                    verification_result, verification_fallback, workflow_genome, workflow_confidence,
                    source, context_id, freshness_score, pivot_detected, cache_guard_reason,
                    toon_conversions, toon_tokens_saved
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.id,
                    entry.timestamp,
                    entry.model_requested,
                    entry.model_used,
                    entry.original_tokens,
                    entry.sent_tokens,
                    entry.savings_pct,
                    entry.saved_dollars,
                    entry.actual_cost,
                    entry.would_have_cost,
                    entry.compression_tier,
                    1 if entry.cache_hit else 0,
                    entry.cache_type,
                    entry.cache_score,
                    entry.routing_reason,
                    entry.latency_ms,
                    entry.verification_result,
                    1 if entry.verification_fallback else 0,
                    entry.workflow_genome,
                    entry.workflow_confidence,
                    entry.source,
                    entry.context_id,
                    entry.freshness_score,
                    1 if entry.pivot_detected else 0,
                    entry.cache_guard_reason,
                    entry.toon_conversions,
                    entry.toon_tokens_saved,
                ),
            )

    def list_request_entries(
        self,
        limit: int | None = None,
        *,
        source: str | None = "proxy",
        context_id: str | None = None,
        since_timestamp: str | None = None,
    ) -> list[RequestEntry]:
        """Return recent request entries, newest first."""

        query = "SELECT * FROM request_entries"
        params_list: list[Any] = []
        clauses: list[str] = []
        if source is not None:
            clauses.append("source = ?")
            params_list.append(source)
        if context_id is not None:
            clauses.append("context_id = ?")
            params_list.append(context_id)
        if since_timestamp is not None:
            clauses.append("timestamp >= ?")
            params_list.append(since_timestamp)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY timestamp DESC"
        if limit is not None:
            query += " LIMIT ?"
            params_list.append(limit)
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, tuple(params_list)).fetchall()
        return [
            RequestEntry(
                id=row["id"],
                timestamp=row["timestamp"],
                model_requested=row["model_requested"],
                model_used=row["model_used"],
                original_tokens=int(row["original_tokens"]),
                sent_tokens=int(row["sent_tokens"]),
                savings_pct=float(row["savings_pct"]),
                saved_dollars=float(row["saved_dollars"]),
                actual_cost=float(row["actual_cost"]),
                would_have_cost=float(row["would_have_cost"]),
                compression_tier=row["compression_tier"],
                cache_hit=bool(row["cache_hit"]),
                cache_type=row["cache_type"],
                cache_score=float(row["cache_score"]),
                routing_reason=row["routing_reason"],
                latency_ms=int(row["latency_ms"]),
                verification_result=row["verification_result"],
                verification_fallback=bool(row["verification_fallback"]),
                workflow_genome=row["workflow_genome"],
                workflow_confidence=float(row["workflow_confidence"]),
                source=str(row["source"] or "proxy"),
                context_id=str(row["context_id"]) if row["context_id"] is not None else None,
                freshness_score=float(row["freshness_score"]) if "freshness_score" in row.keys() else 1.0,
                pivot_detected=bool(row["pivot_detected"]) if "pivot_detected" in row.keys() else False,
                cache_guard_reason=str(row["cache_guard_reason"] or "") if "cache_guard_reason" in row.keys() else "",
                toon_conversions=int(row["toon_conversions"]) if "toon_conversions" in row.keys() else 0,
                toon_tokens_saved=int(row["toon_tokens_saved"]) if "toon_tokens_saved" in row.keys() else 0,
            )
            for row in rows
        ]

    def list_connectors(self) -> list[dict[str, Any]]:
        """Return available connectors merged with any saved configuration."""

        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM connectors ORDER BY connector_type ASC"
            ).fetchall()
        saved = {str(row["connector_type"]): row for row in rows}
        connectors: list[dict[str, Any]] = []
        for definition in list_connector_definitions():
            connector_type = str(definition["connector_type"])
            row = saved.get(connector_type)
            config = json.loads(row["config_json"]) if row is not None and row["config_json"] else {}
            status = str(row["status"]) if row is not None else "not_configured"
            if connector_type == "app":
                app_clients = self.list_mobile_clients()
                status = "configured" if app_clients else "not_configured"
                config = {"paired_devices": str(len(app_clients))}
            connectors.append(
                {
                    "connector_type": connector_type,
                    "name": str(definition["name"]),
                    "description": str(definition.get("description") or ""),
                    "category": str(definition.get("category") or "General"),
                    "origin": str(definition.get("origin") or "lumin"),
                    "popular": bool(definition.get("popular")),
                    "status": status,
                    "display_name": str(row["display_name"]) if row is not None else str(definition["name"]),
                    "fields": list(definition.get("fields") or []),
                    "config_values": mask_connector_config(connector_type, config),
                    "supports_live_test": bool(definition.get("supports_live_test")),
                    "supports_send_test": bool(definition.get("supports_send_test")),
                    "created_at": str(row["created_at"]) if row is not None else "",
                    "updated_at": str(row["updated_at"]) if row is not None else "",
                    "configured_at": str(row["configured_at"]) if row is not None and row["configured_at"] else None,
                    "last_tested_at": str(row["last_tested_at"]) if row is not None and row["last_tested_at"] else None,
                }
            )
        return connectors

    def upsert_connector(
        self,
        *,
        connector_type: str,
        display_name: str | None,
        config: dict[str, str],
    ) -> dict[str, Any]:
        """Create or update a connector configuration."""

        definition = get_connector_definition(connector_type)
        if definition is None:
            raise ValueError(f"Unsupported connector type: {connector_type}")

        normalized_type = str(definition["connector_type"])
        cleaned_config = {
            str(key): str(value).strip()
            for key, value in config.items()
            if str(value).strip()
        }
        now = _utc_timestamp()
        connector_id = f"connector_{normalized_type}"
        chosen_name = (display_name or "").strip() or str(definition["name"])

        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM connectors WHERE connector_type = ?",
                (normalized_type,),
            ).fetchone()
            created_at = str(row["created_at"]) if row is not None else now
            connection.execute(
                """
                INSERT OR REPLACE INTO connectors (
                    id, connector_type, display_name, status, config_json,
                    created_at, updated_at, configured_at, last_tested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    connector_id,
                    normalized_type,
                    chosen_name,
                    "configured",
                    json.dumps(cleaned_config, separators=(",", ":"), sort_keys=True),
                    created_at,
                    now,
                    now,
                    None,
                ),
            )
        return next(
            item for item in self.list_connectors() if item["connector_type"] == normalized_type
        )

    def delete_connector(self, connector_type: str) -> bool:
        """Delete a saved connector configuration."""

        definition = get_connector_definition(connector_type)
        normalized_type = connector_type.strip().lower() if definition is None else str(definition["connector_type"])
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM connectors WHERE connector_type = ?",
                (normalized_type,),
            )
            return bool(cursor.rowcount)

    def get_connector_config(self, connector_type: str) -> dict[str, str] | None:
        """Return one connector's raw saved configuration, if present."""

        definition = get_connector_definition(connector_type)
        normalized_type = connector_type.strip().lower() if definition is None else str(definition["connector_type"])
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT config_json FROM connectors WHERE connector_type = ?",
                (normalized_type,),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row["config_json"] or "{}")
        except json.JSONDecodeError:
            return {}
        return {str(key): str(value) for key, value in payload.items() if value is not None}

    def get_accounting_settings(self) -> dict[str, str]:
        """Return dashboard accounting settings."""

        default = {
            "billing_mode": "metered",
            "subscription_label": "",
        }
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT value_json FROM app_settings WHERE key = ?",
                ("accounting",),
            ).fetchone()
        if row is None:
            return default
        try:
            payload = json.loads(row["value_json"])
        except json.JSONDecodeError:
            return default
        billing_mode = str(payload.get("billing_mode") or "metered").strip().lower()
        if billing_mode not in {"metered", "subscription"}:
            billing_mode = "metered"
        return {
            "billing_mode": billing_mode,
            "subscription_label": str(payload.get("subscription_label") or ""),
        }

    def get_runtime_preferences(self) -> dict[str, str]:
        """Return runtime defaults for provider selection and workspace hints."""

        default = {
            "active_provider": "auto",
            "active_model": "gpt-5.4-mini",
            "workspace_root_hint": "/workspace/hostapp",
            "main_group": "main",
        }
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT value_json FROM app_settings WHERE key = ?",
                ("runtime_preferences",),
            ).fetchone()
        if row is None:
            return default
        try:
            payload = json.loads(row["value_json"])
        except json.JSONDecodeError:
            return default
        active_provider = str(payload.get("active_provider") or "auto").strip().lower()
        if active_provider not in {"auto", "openai", "openrouter", "anthropic", "google", "ollama"}:
            active_provider = "auto"
        active_model = str(payload.get("active_model") or "gpt-5.4-mini").strip() or "gpt-5.4-mini"
        workspace_root_hint = str(payload.get("workspace_root_hint") or "/workspace/hostapp").strip() or "/workspace/hostapp"
        main_group = str(payload.get("main_group") or "main").strip() or "main"
        return {
            "active_provider": active_provider,
            "active_model": active_model,
            "workspace_root_hint": workspace_root_hint,
            "main_group": main_group,
        }

    def set_accounting_settings(self, *, billing_mode: str, subscription_label: str) -> dict[str, str]:
        """Persist dashboard accounting settings."""

        normalized_mode = billing_mode.strip().lower()
        if normalized_mode not in {"metered", "subscription"}:
            raise ValueError("Unsupported billing mode.")
        payload = {
            "billing_mode": normalized_mode,
            "subscription_label": subscription_label.strip(),
        }
        now = _utc_timestamp()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO app_settings (key, value_json, updated_at)
                VALUES (?, ?, ?)
                """,
                ("accounting", json.dumps(payload, separators=(",", ":"), sort_keys=True), now),
            )
        return payload

    def set_runtime_preferences(
        self,
        *,
        active_provider: str,
        active_model: str,
        workspace_root_hint: str,
        main_group: str,
    ) -> dict[str, str]:
        """Persist runtime defaults for the control surface."""

        normalized_provider = active_provider.strip().lower()
        if normalized_provider not in {"auto", "openai", "openrouter", "anthropic", "google", "ollama"}:
            raise ValueError("Unsupported active provider.")
        payload = {
            "active_provider": normalized_provider,
            "active_model": active_model.strip() or "gpt-5.4-mini",
            "workspace_root_hint": workspace_root_hint.strip() or "/workspace/hostapp",
            "main_group": main_group.strip() or "main",
        }
        now = _utc_timestamp()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO app_settings (key, value_json, updated_at)
                VALUES (?, ?, ?)
                """,
                ("runtime_preferences", json.dumps(payload, separators=(",", ":"), sort_keys=True), now),
            )
        return payload

    def list_providers(self) -> list[dict[str, Any]]:
        """Return available providers merged with any saved configuration."""

        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM providers ORDER BY provider_type ASC"
            ).fetchall()
        saved = {str(row["provider_type"]): row for row in rows}
        providers: list[dict[str, Any]] = []
        for definition in list_provider_definitions():
            provider_type = str(definition["provider_type"])
            row = saved.get(provider_type)
            config = json.loads(row["config_json"]) if row is not None and row["config_json"] else {}
            providers.append(
                {
                    "provider_type": provider_type,
                    "name": str(definition["name"]),
                    "description": str(definition.get("description") or ""),
                    "category": str(definition.get("category") or "General"),
                    "status": str(row["status"]) if row is not None else "not_configured",
                    "display_name": str(row["display_name"]) if row is not None else str(definition["name"]),
                    "fields": list(definition.get("fields") or []),
                    "model_suggestions": list(definition.get("model_suggestions") or []),
                    "config_values": mask_provider_config(provider_type, config),
                    "created_at": str(row["created_at"]) if row is not None else "",
                    "updated_at": str(row["updated_at"]) if row is not None else "",
                    "configured_at": str(row["configured_at"]) if row is not None and row["configured_at"] else None,
                }
            )
        return providers

    def upsert_provider(
        self,
        *,
        provider_type: str,
        display_name: str | None,
        config: dict[str, str],
    ) -> dict[str, Any]:
        """Create or update provider configuration."""

        definition = get_provider_definition(provider_type)
        if definition is None:
            raise ValueError(f"Unsupported provider type: {provider_type}")
        normalized_type = str(definition["provider_type"])
        cleaned_config = {
            str(key): str(value).strip()
            for key, value in config.items()
            if str(value).strip()
        }
        now = _utc_timestamp()
        provider_id = f"provider_{normalized_type}"
        chosen_name = (display_name or "").strip() or str(definition["name"])
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM providers WHERE provider_type = ?",
                (normalized_type,),
            ).fetchone()
            created_at = str(row["created_at"]) if row is not None else now
            connection.execute(
                """
                INSERT OR REPLACE INTO providers (
                    id, provider_type, display_name, status, config_json,
                    created_at, updated_at, configured_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    provider_id,
                    normalized_type,
                    chosen_name,
                    "configured",
                    json.dumps(cleaned_config, separators=(",", ":"), sort_keys=True),
                    created_at,
                    now,
                    now,
                ),
            )
        return next(item for item in self.list_providers() if item["provider_type"] == normalized_type)

    def delete_provider(self, provider_type: str) -> bool:
        """Delete a saved provider configuration."""

        definition = get_provider_definition(provider_type)
        normalized_type = provider_type.strip().lower() if definition is None else str(definition["provider_type"])
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM providers WHERE provider_type = ?",
                (normalized_type,),
            )
            return bool(cursor.rowcount)

    def get_provider_config(self, provider_type: str) -> dict[str, str] | None:
        """Return one provider's raw saved configuration, if present."""

        definition = get_provider_definition(provider_type)
        normalized_type = provider_type.strip().lower() if definition is None else str(definition["provider_type"])
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT config_json FROM providers WHERE provider_type = ?",
                (normalized_type,),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row["config_json"] or "{}")
        except json.JSONDecodeError:
            return {}
        return {str(key): str(value) for key, value in payload.items() if value is not None}

    def register_desktop_agent(
        self,
        *,
        name: str,
        hostname: str,
        group_id: str,
        metadata: dict[str, Any],
    ) -> dict[str, str]:
        """Create a new desktop agent and return its durable token."""

        agent_id = f"agent_{uuid.uuid4().hex}"
        token = secrets.token_urlsafe(32)
        now = _utc_timestamp()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO desktop_agents (
                    agent_id, name, hostname, group_id, status, token_hash, metadata_json,
                    created_at, updated_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent_id,
                    name,
                    hostname,
                    group_id,
                    "online",
                    self._hash_token(token),
                    json.dumps(metadata, separators=(",", ":"), sort_keys=True),
                    now,
                    now,
                    now,
                ),
            )
        return {"agent_id": agent_id, "agent_token": token}

    def create_pairing_code(self, ttl_seconds: int = 600) -> dict[str, str]:
        """Create a short-lived one-time pairing code for a mobile client."""

        code = f"{secrets.randbelow(1_000_000):06d}"
        now_epoch = time.time()
        expires_epoch = now_epoch + ttl_seconds
        created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_epoch))
        expires_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expires_epoch))
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO pairing_codes (code, created_at, expires_at, claimed_at)
                VALUES (?, ?, ?, ?)
                """,
                (code, created_at, expires_at, None),
            )
        return {"code": code, "expires_at": expires_at}

    def claim_pairing_code(self, code: str, device_name: str) -> dict[str, str] | None:
        """Redeem a valid pairing code and issue a durable mobile token."""

        now_epoch = time.time()
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_epoch))
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM pairing_codes WHERE code = ?",
                (code.strip(),),
            ).fetchone()
            if row is None:
                return None
            if row["claimed_at"]:
                return None
            expires_at = str(row["expires_at"])
            try:
                expires_epoch = timegm(time.strptime(expires_at, "%Y-%m-%dT%H:%M:%SZ"))
            except ValueError:
                return None
            if expires_epoch < now_epoch:
                return None

            client_id = f"mobile_{uuid.uuid4().hex}"
            token = secrets.token_urlsafe(32)
            connection.execute(
                """
                UPDATE pairing_codes
                SET claimed_at = ?
                WHERE code = ?
                """,
                (now, code.strip()),
            )
            connection.execute(
                """
                INSERT INTO mobile_clients (
                    client_id, device_name, status, token_hash,
                    created_at, updated_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    client_id,
                    device_name,
                    "active",
                    self._hash_token(token),
                    now,
                    now,
                    now,
                ),
            )
        return {"client_id": client_id, "mobile_token": token}

    def authenticate_mobile_token(self, token: str) -> dict[str, Any] | None:
        """Return mobile client row for a durable mobile token if valid."""

        token_hash = self._hash_token(token)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM mobile_clients WHERE token_hash = ? AND status = 'active'",
                (token_hash,),
            ).fetchone()
        return dict(row) if row is not None else None

    def touch_mobile_client(self, client_id: str, status: str = "active") -> None:
        """Refresh a paired mobile client's heartbeat metadata."""

        now = _utc_timestamp()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE mobile_clients
                SET status = ?, updated_at = ?, last_seen_at = ?
                WHERE client_id = ?
                """,
                (status, now, now, client_id),
            )

    def list_mobile_clients(self) -> list[dict[str, Any]]:
        """Return paired mobile clients, newest heartbeat first."""

        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM mobile_clients ORDER BY last_seen_at DESC"
            ).fetchall()
        return [
            {
                "client_id": row["client_id"],
                "device_name": row["device_name"],
                "status": row["status"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "last_seen_at": row["last_seen_at"],
            }
            for row in rows
        ]

    def authenticate_agent(self, token: str) -> dict[str, Any] | None:
        """Return agent row for a token if valid."""

        token_hash = self._hash_token(token)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM desktop_agents WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
        return dict(row) if row is not None else None

    def touch_agent(self, agent_id: str, status: str = "online") -> None:
        """Update an agent heartbeat timestamp and status."""

        now = _utc_timestamp()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE desktop_agents
                SET status = ?, updated_at = ?, last_seen_at = ?
                WHERE agent_id = ?
                """,
                (status, now, now, agent_id),
            )

    def list_agents(self) -> list[dict[str, Any]]:
        """Return known desktop agents, newest heartbeat first."""

        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM desktop_agents ORDER BY last_seen_at DESC"
            ).fetchall()
        return [
            {
                "agent_id": row["agent_id"],
                "name": row["name"],
                "hostname": row["hostname"],
                "group_id": row["group_id"],
                "status": row["status"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "last_seen_at": row["last_seen_at"],
                "metadata": json.loads(row["metadata_json"]),
            }
            for row in rows
        ]

    def get_preferred_agent(self, max_idle_seconds: int = 90) -> dict[str, Any] | None:
        """Return the freshest online agent within the allowed idle window."""

        cutoff = time.time() - max_idle_seconds
        candidates = self.list_agents()
        for candidate in candidates:
            try:
                last_seen_epoch = timegm(time.strptime(candidate["last_seen_at"], "%Y-%m-%dT%H:%M:%SZ"))
            except ValueError:
                continue
            if candidate["status"] == "online" and last_seen_epoch >= cutoff:
                return candidate
        return None

    def create_task(
        self,
        *,
        message: str,
        group_id: str,
        context_id: str | None,
        origin: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist a queued remote task."""

        now = _utc_timestamp()
        task_metadata = {
            "attempt_count": 0,
            "max_attempts": 2,
            "fallback_used": False,
            "lifecycle": [{"status": "queued", "timestamp": now}],
            **(metadata or {}),
        }
        with self._lock, self._connect() as connection:
            client_request_id = str(task_metadata.get("client_request_id") or "").strip()
            if client_request_id:
                recent_rows = connection.execute(
                    """
                    SELECT id, metadata_json
                    FROM remote_tasks
                    WHERE origin = ? AND group_id = ?
                    ORDER BY created_at DESC
                    LIMIT 25
                    """,
                    (origin, group_id),
                ).fetchall()
                for row in recent_rows:
                    existing_metadata = json.loads(row["metadata_json"] or "{}")
                    if existing_metadata.get("client_request_id") == client_request_id:
                        existing = self.get_task(str(row["id"]))
                        if existing is not None:
                            return existing

            task_id = f"task_{uuid.uuid4().hex}"
            connection.execute(
                """
                INSERT INTO remote_tasks (
                    id, agent_id, group_id, context_id, message, origin, status,
                    response_text, error_text, model_used, latency_ms,
                    created_at, updated_at, started_at, completed_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    None,
                    group_id,
                    context_id,
                    message,
                    origin,
                    "queued",
                    None,
                    None,
                    None,
                    0,
                    now,
                    now,
                    None,
                    None,
                    json.dumps(task_metadata, separators=(",", ":"), sort_keys=True),
                ),
            )
        return self.get_task(task_id) or {}

    def claim_next_task(self, agent_id: str, group_id: str | None = None) -> dict[str, Any] | None:
        """Atomically claim the oldest queued task for an agent, optionally scoped by group."""

        now = _utc_timestamp()
        with self._lock, self._connect() as connection:
            if group_id:
                row = connection.execute(
                    """
                    SELECT * FROM remote_tasks
                    WHERE status = 'queued' AND group_id = ?
                    ORDER BY created_at ASC
                    LIMIT 1
                    """,
                    (group_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM remote_tasks WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1"
                ).fetchone()
            if row is None:
                return None
            metadata = json.loads(row["metadata_json"] or "{}")
            lifecycle = list(metadata.get("lifecycle") or [])
            lifecycle.append({"status": "claimed", "timestamp": now, "agent_id": agent_id})
            metadata["lifecycle"] = lifecycle
            metadata["attempt_count"] = int(metadata.get("attempt_count") or 0) + 1
            connection.execute(
                """
                UPDATE remote_tasks
                SET status = 'claimed', agent_id = ?, started_at = ?, updated_at = ?, metadata_json = ?
                WHERE id = ?
                """,
                (agent_id, now, now, json.dumps(metadata, separators=(",", ":"), sort_keys=True), row["id"]),
            )
        return self.get_task(str(row["id"]))

    def start_task(
        self,
        *,
        task_id: str,
        agent_id: str,
        stage: str = "running",
    ) -> dict[str, Any] | None:
        """Mark a claimed task as actively executing on the desktop agent."""

        now = _utc_timestamp()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT metadata_json FROM remote_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                return None
            metadata = json.loads(row["metadata_json"] or "{}")
            lifecycle = list(metadata.get("lifecycle") or [])
            lifecycle.append({"status": stage, "timestamp": now, "agent_id": agent_id})
            metadata["lifecycle"] = lifecycle
            connection.execute(
                """
                UPDATE remote_tasks
                SET status = ?, agent_id = ?, updated_at = ?, metadata_json = ?
                WHERE id = ?
                """,
                (
                    stage,
                    agent_id,
                    now,
                    json.dumps(metadata, separators=(",", ":"), sort_keys=True),
                    task_id,
                ),
            )
        return self.get_task(task_id)

    def complete_task(
        self,
        *,
        task_id: str,
        agent_id: str,
        response_text: str,
        model_used: str,
        latency_ms: int,
        metadata_update: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Mark a task as completed."""

        now = _utc_timestamp()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT metadata_json FROM remote_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                return None
            metadata = json.loads(row["metadata_json"] or "{}")
            lifecycle = list(metadata.get("lifecycle") or [])
            lifecycle.append({"status": "completed", "timestamp": now, "agent_id": agent_id})
            metadata["lifecycle"] = lifecycle
            if metadata_update:
                metadata.update(metadata_update)
            connection.execute(
                """
                UPDATE remote_tasks
                SET status = 'completed', agent_id = ?, response_text = ?, model_used = ?,
                    latency_ms = ?, completed_at = ?, updated_at = ?, metadata_json = ?
                WHERE id = ?
                """,
                (
                    agent_id,
                    response_text,
                    model_used,
                    latency_ms,
                    now,
                    now,
                    json.dumps(metadata, separators=(",", ":"), sort_keys=True),
                    task_id,
                ),
            )
        return self.get_task(task_id)

    def fail_task(
        self,
        *,
        task_id: str,
        agent_id: str,
        error_text: str,
        latency_ms: int,
        metadata_update: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Mark a task as failed."""

        now = _utc_timestamp()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT metadata_json FROM remote_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                return None
            metadata = json.loads(row["metadata_json"] or "{}")
            lifecycle = list(metadata.get("lifecycle") or [])
            lifecycle.append({"status": "failed", "timestamp": now, "agent_id": agent_id})
            metadata["lifecycle"] = lifecycle
            metadata["last_error"] = error_text
            if metadata_update:
                metadata.update(metadata_update)
            connection.execute(
                """
                UPDATE remote_tasks
                SET status = 'failed', agent_id = ?, error_text = ?, latency_ms = ?,
                    completed_at = ?, updated_at = ?, metadata_json = ?
                WHERE id = ?
                """,
                (
                    agent_id,
                    error_text,
                    latency_ms,
                    now,
                    now,
                    json.dumps(metadata, separators=(",", ":"), sort_keys=True),
                    task_id,
                ),
            )
        return self.get_task(task_id)

    def cancel_task(self, task_id: str) -> dict[str, Any] | None:
        """Cancel a queued or running task."""

        now = _utc_timestamp()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT metadata_json FROM remote_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                return None
            metadata = json.loads(row["metadata_json"] or "{}")
            lifecycle = list(metadata.get("lifecycle") or [])
            lifecycle.append({"status": "cancelled", "timestamp": now})
            metadata["lifecycle"] = lifecycle
            connection.execute(
                """
                UPDATE remote_tasks
                SET status = 'cancelled', updated_at = ?, completed_at = ?, metadata_json = ?
                WHERE id = ? AND status IN ('queued', 'claimed', 'running')
                """,
                (now, now, json.dumps(metadata, separators=(",", ":"), sort_keys=True), task_id),
            )
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        """Return one task by id."""

        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM remote_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "agent_id": row["agent_id"],
            "group_id": row["group_id"],
            "context_id": row["context_id"],
            "message": row["message"],
            "origin": row["origin"],
            "status": row["status"],
            "response_text": row["response_text"],
            "error_text": row["error_text"],
            "model_used": row["model_used"],
            "latency_ms": int(row["latency_ms"] or 0),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
        }

    def list_tasks(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return recent tasks, newest first."""

        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM remote_tasks ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self.get_task(str(row["id"])) for row in rows if row is not None]

    def count_tasks_today(self) -> int:
        """Return how many remote tasks were created today in UTC."""

        today = time.strftime("%Y-%m-%d", time.gmtime())
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM remote_tasks
                WHERE substr(created_at, 1, 10) = ?
                """,
                (today,),
            ).fetchone()
        return int(row["count"] or 0) if row is not None else 0


_STATE_STORE: StateStore | None = None
_STATE_STORE_LOCK = threading.Lock()


def get_state_store() -> StateStore:
    """Return the shared persistent state store."""

    global _STATE_STORE
    if _STATE_STORE is None:
        with _STATE_STORE_LOCK:
            if _STATE_STORE is None:
                settings = get_settings()
                path = getattr(settings, "state_db_path", None) or "/tmp/lumin_state.db"
                _STATE_STORE = StateStore(path)
    return _STATE_STORE
