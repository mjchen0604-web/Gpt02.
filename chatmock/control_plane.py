from __future__ import annotations

import datetime
import json
import os
import secrets
import sqlite3
import threading
from pathlib import Path
from typing import Any

from flask import Response, current_app, g, jsonify, make_response

from .http import build_cors_headers


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _utc_iso() -> str:
    return _utc_now().isoformat()


def _month_bounds() -> tuple[str, str]:
    now = _utc_now()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start.isoformat(), end.isoformat()


def _clean_string(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _clean_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _clean_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _json_list(value: Any, default: list[str]) -> list[str]:
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
        return items or list(default)
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",") if part.strip()]
        return parts or list(default)
    return list(default)


def _mask_token(token: str) -> str:
    if not isinstance(token, str) or len(token) < 10:
        return token or ""
    return f"{token[:6]}...{token[-4:]}"


class ControlPlaneManager:
    def __init__(self, db_path: str) -> None:
        self.db_path = str(Path(db_path).expanduser())
        self._lock = threading.RLock()
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    monthly_quota_tokens INTEGER NOT NULL DEFAULT 0,
                    prompt_price_per_million REAL NOT NULL DEFAULT 0,
                    completion_price_per_million REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS api_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    token TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL DEFAULT 'active',
                    groups_json TEXT NOT NULL DEFAULT '["default"]',
                    models_json TEXT NOT NULL DEFAULT '["*"]',
                    expires_at TEXT DEFAULT NULL,
                    last_used_at TEXT DEFAULT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS usage_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    api_key_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    endpoint TEXT NOT NULL,
                    model TEXT NOT NULL,
                    prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    estimated_cost REAL NOT NULL DEFAULT 0,
                    status_code INTEGER NOT NULL DEFAULT 200,
                    request_id TEXT NOT NULL DEFAULT '',
                    channel_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (api_key_id) REFERENCES api_keys(id) ON DELETE CASCADE,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                """
            )

    def has_managed_keys(self) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM api_keys").fetchone()
        return bool((row["count"] if row else 0) > 0)

    def export_gateway_api_keys(self) -> list[dict[str, Any]]:
        now_iso = _utc_iso()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT api_keys.name, api_keys.token, api_keys.groups_json, api_keys.models_json
                FROM api_keys
                JOIN users ON users.id = api_keys.user_id
                WHERE api_keys.status = 'active'
                  AND users.status = 'active'
                  AND (api_keys.expires_at IS NULL OR api_keys.expires_at = '' OR api_keys.expires_at > ?)
                ORDER BY api_keys.id ASC
                """,
                (now_iso,),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "name": str(row["name"] or "").strip(),
                    "key": str(row["token"] or "").strip(),
                    "groups": self._decode_list(row["groups_json"], ["default"]),
                    "models": self._decode_list(row["models_json"], ["*"]),
                    "enabled": True,
                }
            )
        return out

    def authorize_gateway_token(self, token: str) -> Response | None:
        token_value = _clean_string(token)
        if not token_value or not self.has_managed_keys():
            return None

        now_iso = _utc_iso()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    api_keys.id AS api_key_id,
                    api_keys.name AS api_key_name,
                    api_keys.token AS token,
                    api_keys.status AS api_key_status,
                    api_keys.groups_json AS groups_json,
                    api_keys.models_json AS models_json,
                    api_keys.expires_at AS expires_at,
                    users.id AS user_id,
                    users.name AS user_name,
                    users.email AS user_email,
                    users.status AS user_status,
                    users.monthly_quota_tokens AS monthly_quota_tokens,
                    users.prompt_price_per_million AS prompt_price_per_million,
                    users.completion_price_per_million AS completion_price_per_million
                FROM api_keys
                JOIN users ON users.id = api_keys.user_id
                WHERE api_keys.token = ?
                LIMIT 1
                """,
                (token_value,),
            ).fetchone()

            if row is None:
                return None
            if str(row["api_key_status"] or "").lower() != "active":
                return self._error_response("Managed API key is disabled", 403)
            if str(row["user_status"] or "").lower() != "active":
                return self._error_response("User is disabled", 403)
            expires_at = str(row["expires_at"] or "").strip()
            if expires_at and expires_at <= now_iso:
                return self._error_response("Managed API key has expired", 403)

            quota_tokens = max(0, _clean_int(row["monthly_quota_tokens"], 0))
            month_start, month_end = _month_bounds()
            usage_row = conn.execute(
                """
                SELECT COALESCE(SUM(total_tokens), 0) AS total_tokens
                FROM usage_events
                WHERE user_id = ? AND created_at >= ? AND created_at < ?
                """,
                (int(row["user_id"]), month_start, month_end),
            ).fetchone()
            used_tokens = int((usage_row["total_tokens"] if usage_row else 0) or 0)

            if quota_tokens > 0 and used_tokens >= quota_tokens:
                return self._error_response("Monthly token quota exceeded", 429)

            conn.execute(
                "UPDATE api_keys SET last_used_at = ?, updated_at = ? WHERE id = ?",
                (now_iso, now_iso, int(row["api_key_id"])),
            )
            conn.commit()

        g.chatmock_control_access = {
            "api_key_id": int(row["api_key_id"]),
            "api_key_name": str(row["api_key_name"] or ""),
            "user_id": int(row["user_id"]),
            "user_name": str(row["user_name"] or ""),
            "user_email": str(row["user_email"] or ""),
            "groups": self._decode_list(row["groups_json"], ["default"]),
            "models": self._decode_list(row["models_json"], ["*"]),
            "monthly_quota_tokens": quota_tokens,
            "used_tokens": used_tokens,
            "remaining_tokens": (quota_tokens - used_tokens) if quota_tokens > 0 else None,
            "prompt_price_per_million": _clean_float(row["prompt_price_per_million"], 0.0),
            "completion_price_per_million": _clean_float(row["completion_price_per_million"], 0.0),
        }
        return None

    def record_usage(
        self,
        *,
        endpoint: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        status_code: int,
        request_id: str = "",
        channel_id: str = "",
    ) -> None:
        access = getattr(g, "chatmock_control_access", None)
        if not isinstance(access, dict):
            return
        prompt_tokens = max(0, _clean_int(prompt_tokens, 0))
        completion_tokens = max(0, _clean_int(completion_tokens, 0))
        total_tokens = max(0, _clean_int(total_tokens, prompt_tokens + completion_tokens))
        estimated_cost = (
            (prompt_tokens * _clean_float(access.get("prompt_price_per_million"), 0.0)) / 1_000_000.0
            + (completion_tokens * _clean_float(access.get("completion_price_per_million"), 0.0)) / 1_000_000.0
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO usage_events (
                    api_key_id, user_id, endpoint, model, prompt_tokens, completion_tokens,
                    total_tokens, estimated_cost, status_code, request_id, channel_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(access["api_key_id"]),
                    int(access["user_id"]),
                    _clean_string(endpoint),
                    _clean_string(model),
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    estimated_cost,
                    _clean_int(status_code, 200),
                    _clean_string(request_id),
                    _clean_string(channel_id),
                    _utc_iso(),
                ),
            )
            conn.commit()

    def list_users(self) -> list[dict[str, Any]]:
        month_start, month_end = _month_bounds()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    users.*,
                    COALESCE(keys.key_count, 0) AS key_count,
                    COALESCE(usage.total_tokens, 0) AS used_tokens_month,
                    COALESCE(usage.estimated_cost, 0) AS estimated_cost_month
                FROM users
                LEFT JOIN (
                    SELECT user_id, COUNT(*) AS key_count
                    FROM api_keys
                    GROUP BY user_id
                ) AS keys ON keys.user_id = users.id
                LEFT JOIN (
                    SELECT user_id, SUM(total_tokens) AS total_tokens, SUM(estimated_cost) AS estimated_cost
                    FROM usage_events
                    WHERE created_at >= ? AND created_at < ?
                    GROUP BY user_id
                ) AS usage ON usage.user_id = users.id
                ORDER BY users.id ASC
                """,
                (month_start, month_end),
            ).fetchall()
        return [self._row_to_user(row) for row in rows]

    def save_user(self, payload: dict[str, Any]) -> dict[str, Any]:
        user_id = _clean_int(payload.get("id"), 0)
        name = _clean_string(payload.get("name"))
        if not name:
            raise ValueError("name is required")
        now_iso = _utc_iso()
        status = _clean_string(payload.get("status"), "active").lower() or "active"
        if status not in ("active", "disabled"):
            status = "active"
        email = _clean_string(payload.get("email"))
        monthly_quota_tokens = max(0, _clean_int(payload.get("monthlyQuotaTokens"), 0))
        prompt_price_per_million = max(0.0, _clean_float(payload.get("promptPricePerMillion"), 0.0))
        completion_price_per_million = max(0.0, _clean_float(payload.get("completionPricePerMillion"), 0.0))

        with self._connect() as conn:
            if user_id > 0:
                conn.execute(
                    """
                    UPDATE users
                    SET name = ?, email = ?, status = ?, monthly_quota_tokens = ?,
                        prompt_price_per_million = ?, completion_price_per_million = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        name,
                        email,
                        status,
                        monthly_quota_tokens,
                        prompt_price_per_million,
                        completion_price_per_million,
                        now_iso,
                        user_id,
                    ),
                )
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO users (
                        name, email, status, monthly_quota_tokens, prompt_price_per_million,
                        completion_price_per_million, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        name,
                        email,
                        status,
                        monthly_quota_tokens,
                        prompt_price_per_million,
                        completion_price_per_million,
                        now_iso,
                        now_iso,
                    ),
                )
                user_id = int(cursor.lastrowid)
            conn.commit()

        return self.get_user(user_id)

    def get_user(self, user_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
        if row is None:
            raise ValueError("user not found")
        return self._row_to_user(row)

    def delete_user(self, user_id: int) -> None:
        with self._connect() as conn:
            row = conn.execute("SELECT id FROM users WHERE id = ?", (int(user_id),)).fetchone()
            if row is None:
                raise ValueError("user not found")
            conn.execute("DELETE FROM users WHERE id = ?", (int(user_id),))
            conn.commit()

    def list_api_keys(self) -> list[dict[str, Any]]:
        month_start, month_end = _month_bounds()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    api_keys.*,
                    users.name AS user_name,
                    users.email AS user_email,
                    users.status AS user_status,
                    COALESCE(usage.total_tokens, 0) AS used_tokens_month,
                    COALESCE(usage.estimated_cost, 0) AS estimated_cost_month
                FROM api_keys
                JOIN users ON users.id = api_keys.user_id
                LEFT JOIN (
                    SELECT api_key_id, SUM(total_tokens) AS total_tokens, SUM(estimated_cost) AS estimated_cost
                    FROM usage_events
                    WHERE created_at >= ? AND created_at < ?
                    GROUP BY api_key_id
                ) AS usage ON usage.api_key_id = api_keys.id
                ORDER BY api_keys.id ASC
                """,
                (month_start, month_end),
            ).fetchall()
        return [self._row_to_api_key(row) for row in rows]

    def create_api_key(self, payload: dict[str, Any]) -> dict[str, Any]:
        user_id = _clean_int(payload.get("userId"), 0)
        if user_id <= 0:
            raise ValueError("userId is required")
        name = _clean_string(payload.get("name"), "default")
        token = _clean_string(payload.get("token"))
        if not token:
            token = f"sk-chatmock-{secrets.token_urlsafe(24).rstrip('=')}"
        groups_json = json.dumps(_json_list(payload.get("groups"), ["default"]), ensure_ascii=False)
        models_json = json.dumps(_json_list(payload.get("models"), ["*"]), ensure_ascii=False)
        status = _clean_string(payload.get("status"), "active").lower() or "active"
        if status not in ("active", "disabled"):
            status = "active"
        expires_at = _clean_string(payload.get("expiresAt")) or None
        now_iso = _utc_iso()

        with self._connect() as conn:
            row = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
            if row is None:
                raise ValueError("user not found")
            cursor = conn.execute(
                """
                INSERT INTO api_keys (
                    user_id, name, token, status, groups_json, models_json, expires_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    name,
                    token,
                    status,
                    groups_json,
                    models_json,
                    expires_at,
                    now_iso,
                    now_iso,
                ),
            )
            conn.commit()
            key_id = int(cursor.lastrowid)
        item = self.get_api_key(key_id)
        item["token"] = token
        item["maskedToken"] = _mask_token(token)
        return item

    def update_api_key(self, key_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        key_id = int(key_id)
        if key_id <= 0:
            raise ValueError("invalid key id")
        name = _clean_string(payload.get("name"))
        status = _clean_string(payload.get("status")).lower()
        expires_at = _clean_string(payload.get("expiresAt"))
        groups = payload.get("groups")
        models = payload.get("models")
        now_iso = _utc_iso()

        with self._connect() as conn:
            row = conn.execute("SELECT * FROM api_keys WHERE id = ?", (key_id,)).fetchone()
            if row is None:
                raise ValueError("api key not found")
            next_name = name or str(row["name"] or "")
            next_status = status if status in ("active", "disabled") else str(row["status"] or "active")
            next_groups_json = (
                json.dumps(_json_list(groups, ["default"]), ensure_ascii=False)
                if groups is not None
                else str(row["groups_json"] or '["default"]')
            )
            next_models_json = (
                json.dumps(_json_list(models, ["*"]), ensure_ascii=False)
                if models is not None
                else str(row["models_json"] or '["*"]')
            )
            next_expires_at = expires_at if "expiresAt" in payload else row["expires_at"]
            conn.execute(
                """
                UPDATE api_keys
                SET name = ?, status = ?, groups_json = ?, models_json = ?, expires_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    next_name,
                    next_status,
                    next_groups_json,
                    next_models_json,
                    (next_expires_at or None),
                    now_iso,
                    key_id,
                ),
            )
            conn.commit()
        return self.get_api_key(key_id)

    def get_api_key(self, key_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT api_keys.*, users.name AS user_name, users.email AS user_email, users.status AS user_status
                FROM api_keys
                JOIN users ON users.id = api_keys.user_id
                WHERE api_keys.id = ?
                """,
                (int(key_id),),
            ).fetchone()
        if row is None:
            raise ValueError("api key not found")
        return self._row_to_api_key(row)

    def delete_api_key(self, key_id: int) -> None:
        with self._connect() as conn:
            row = conn.execute("SELECT id FROM api_keys WHERE id = ?", (int(key_id),)).fetchone()
            if row is None:
                raise ValueError("api key not found")
            conn.execute("DELETE FROM api_keys WHERE id = ?", (int(key_id),))
            conn.commit()

    def usage_summary(self, *, limit: int = 200) -> dict[str, Any]:
        month_start, month_end = _month_bounds()
        limit = max(20, min(500, _clean_int(limit, 200)))
        with self._connect() as conn:
            totals = conn.execute(
                """
                SELECT
                    COALESCE(SUM(total_tokens), 0) AS total_tokens,
                    COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                    COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                    COALESCE(SUM(estimated_cost), 0) AS estimated_cost
                FROM usage_events
                WHERE created_at >= ? AND created_at < ?
                """,
                (month_start, month_end),
            ).fetchone()
            by_user = conn.execute(
                """
                SELECT
                    users.id AS user_id,
                    users.name AS user_name,
                    COALESCE(SUM(usage_events.total_tokens), 0) AS total_tokens,
                    COALESCE(SUM(usage_events.estimated_cost), 0) AS estimated_cost
                FROM users
                LEFT JOIN usage_events
                  ON usage_events.user_id = users.id
                 AND usage_events.created_at >= ?
                 AND usage_events.created_at < ?
                GROUP BY users.id, users.name
                ORDER BY total_tokens DESC, users.id ASC
                """,
                (month_start, month_end),
            ).fetchall()
            by_key = conn.execute(
                """
                SELECT
                    api_keys.id AS api_key_id,
                    api_keys.name AS api_key_name,
                    users.name AS user_name,
                    COALESCE(SUM(usage_events.total_tokens), 0) AS total_tokens,
                    COALESCE(SUM(usage_events.estimated_cost), 0) AS estimated_cost
                FROM api_keys
                JOIN users ON users.id = api_keys.user_id
                LEFT JOIN usage_events
                  ON usage_events.api_key_id = api_keys.id
                 AND usage_events.created_at >= ?
                 AND usage_events.created_at < ?
                GROUP BY api_keys.id, api_keys.name, users.name
                ORDER BY total_tokens DESC, api_keys.id ASC
                """,
                (month_start, month_end),
            ).fetchall()
            events = conn.execute(
                """
                SELECT
                    usage_events.*,
                    users.name AS user_name,
                    api_keys.name AS api_key_name
                FROM usage_events
                JOIN users ON users.id = usage_events.user_id
                JOIN api_keys ON api_keys.id = usage_events.api_key_id
                ORDER BY usage_events.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return {
            "monthStart": month_start,
            "monthEnd": month_end,
            "totals": {
                "promptTokens": int((totals["prompt_tokens"] if totals else 0) or 0),
                "completionTokens": int((totals["completion_tokens"] if totals else 0) or 0),
                "totalTokens": int((totals["total_tokens"] if totals else 0) or 0),
                "estimatedCost": float((totals["estimated_cost"] if totals else 0.0) or 0.0),
            },
            "byUser": [
                {
                    "userId": int(row["user_id"]),
                    "userName": str(row["user_name"] or ""),
                    "totalTokens": int(row["total_tokens"] or 0),
                    "estimatedCost": float(row["estimated_cost"] or 0.0),
                }
                for row in by_user
            ],
            "byKey": [
                {
                    "apiKeyId": int(row["api_key_id"]),
                    "apiKeyName": str(row["api_key_name"] or ""),
                    "userName": str(row["user_name"] or ""),
                    "totalTokens": int(row["total_tokens"] or 0),
                    "estimatedCost": float(row["estimated_cost"] or 0.0),
                }
                for row in by_key
            ],
            "events": [
                {
                    "id": int(row["id"]),
                    "createdAt": str(row["created_at"] or ""),
                    "userName": str(row["user_name"] or ""),
                    "apiKeyName": str(row["api_key_name"] or ""),
                    "endpoint": str(row["endpoint"] or ""),
                    "model": str(row["model"] or ""),
                    "promptTokens": int(row["prompt_tokens"] or 0),
                    "completionTokens": int(row["completion_tokens"] or 0),
                    "totalTokens": int(row["total_tokens"] or 0),
                    "estimatedCost": float(row["estimated_cost"] or 0.0),
                    "statusCode": int(row["status_code"] or 0),
                    "requestId": str(row["request_id"] or ""),
                    "channelId": str(row["channel_id"] or ""),
                }
                for row in events
            ],
        }

    def _decode_list(self, raw: Any, default: list[str]) -> list[str]:
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = None
            if isinstance(parsed, list):
                return _json_list(parsed, default)
            return _json_list(raw, default)
        return _json_list(raw, default)

    def _row_to_user(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "name": str(row["name"] or ""),
            "email": str(row["email"] or ""),
            "status": str(row["status"] or "active"),
            "monthlyQuotaTokens": int(row["monthly_quota_tokens"] or 0),
            "promptPricePerMillion": float(row["prompt_price_per_million"] or 0.0),
            "completionPricePerMillion": float(row["completion_price_per_million"] or 0.0),
            "keyCount": int(row["key_count"] or 0) if "key_count" in row.keys() else 0,
            "usedTokensMonth": int(row["used_tokens_month"] or 0) if "used_tokens_month" in row.keys() else 0,
            "estimatedCostMonth": float(row["estimated_cost_month"] or 0.0) if "estimated_cost_month" in row.keys() else 0.0,
            "createdAt": str(row["created_at"] or ""),
            "updatedAt": str(row["updated_at"] or ""),
        }

    def _row_to_api_key(self, row: sqlite3.Row) -> dict[str, Any]:
        token = str(row["token"] or "")
        return {
            "id": int(row["id"]),
            "userId": int(row["user_id"]),
            "userName": str(row["user_name"] or ""),
            "userEmail": str(row["user_email"] or ""),
            "userStatus": str(row["user_status"] or "active"),
            "name": str(row["name"] or ""),
            "maskedToken": _mask_token(token),
            "status": str(row["status"] or "active"),
            "groups": self._decode_list(row["groups_json"], ["default"]),
            "models": self._decode_list(row["models_json"], ["*"]),
            "expiresAt": str(row["expires_at"] or ""),
            "lastUsedAt": str(row["last_used_at"] or ""),
            "usedTokensMonth": int(row["used_tokens_month"] or 0) if "used_tokens_month" in row.keys() else 0,
            "estimatedCostMonth": float(row["estimated_cost_month"] or 0.0) if "estimated_cost_month" in row.keys() else 0.0,
            "createdAt": str(row["created_at"] or ""),
            "updatedAt": str(row["updated_at"] or ""),
        }

    def _error_response(self, message: str, status: int) -> Response:
        resp = make_response(jsonify({"error": {"message": message}}), status)
        for key, value in build_cors_headers().items():
            resp.headers.setdefault(key, value)
        return resp


def default_control_db_path() -> str:
    explicit = _clean_string(os.getenv("CHATMOCK_CONTROL_DB_PATH"))
    if explicit:
        return str(Path(explicit).expanduser())
    data_dir = _clean_string(os.getenv("CHATMOCK_DATA_DIR"))
    if data_dir:
        return str((Path(data_dir).expanduser() / "chatmock-control.db").resolve())
    return str((Path.cwd() / "chatmock-control.db").resolve())


def get_control_plane_manager() -> ControlPlaneManager | None:
    manager = current_app.config.get("CONTROL_PLANE_MANAGER")
    return manager if isinstance(manager, ControlPlaneManager) else None
