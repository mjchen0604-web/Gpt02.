from __future__ import annotations

import fnmatch
import json
import os
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from flask import Response, current_app, g, make_response, request

from .http import build_cors_headers
from .utils import get_max_retry_interval_seconds


_DEFAULT_CHANNELS_ENV = "CHATGPT_LOCAL_CHANNELS_PATH"
_SUPPORTED_GATEWAY_TRANSPORTS = {"chatgpt-backend", "codex-app-server"}


def _default_route_families(transport: str) -> list[str]:
    normalized = str(transport or "").strip().lower()
    if normalized in ("chatgpt-backend", "codex-app-server"):
        return ["openai", "anthropic", "ollama"]
    if normalized == "anthropic":
        return ["anthropic"]
    if normalized == "ollama":
        return ["ollama"]
    return ["openai"]


def _string_list(value: Any, default: list[str]) -> list[str]:
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
        return items or list(default)
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",") if part.strip()]
        return parts or list(default)
    return list(default)


def _bool_value(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _int_value(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _normalize_group(value: Any) -> str:
    raw = str(value or "").strip()
    return raw or "default"


def _normalize_pattern(value: str) -> str:
    return value.strip().lower()


def _matches_any(candidate: str, patterns: Iterable[str]) -> bool:
    candidate_norm = _normalize_pattern(candidate or "")
    for pattern in patterns:
        normalized = _normalize_pattern(pattern)
        if not normalized or normalized == "*":
            return True
        if fnmatch.fnmatchcase(candidate_norm, normalized):
            return True
    return False


def _request_token() -> str:
    auth_header = str(request.headers.get("Authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    for header_name in ("x-api-key", "api-key"):
        value = str(request.headers.get(header_name) or "").strip()
        if value:
            return value
    return ""


def _allow_anonymous_gateway_access() -> bool:
    raw = str(os.getenv("CHATMOCK_GATEWAY_ALLOW_ANONYMOUS") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return False


@dataclass(slots=True)
class GatewayAccess:
    key_id: str
    groups: list[str]
    models: list[str]
    anonymous: bool = False

    def allows_group(self, group: str) -> bool:
        return _matches_any(group, self.groups)

    def allows_model(self, model_name: str) -> bool:
        return _matches_any(model_name, self.models)


@dataclass(slots=True)
class GatewayChannel:
    id: str
    transport: str
    enabled: bool = True
    priority: int = 0
    weight: int = 1
    groups: list[str] = field(default_factory=lambda: ["default"])
    models: list[str] = field(default_factory=lambda: ["*"])
    route_families: list[str] = field(default_factory=lambda: ["openai"])
    auth_label: str | None = None
    url: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    api_key_env: str | None = None
    organization: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    timeout_seconds: int = 600

    @classmethod
    def from_dict(cls, payload: dict[str, Any], index: int) -> "GatewayChannel":
        transport = str(payload.get("transport") or payload.get("type") or "").strip().lower()
        route_families = _string_list(
            payload.get("route_families") or payload.get("formats"),
            _default_route_families(transport),
        )
        headers = payload.get("headers") if isinstance(payload.get("headers"), dict) else {}
        return cls(
            id=str(payload.get("id") or f"channel-{index}").strip() or f"channel-{index}",
            transport=transport,
            enabled=_bool_value(payload.get("enabled"), default=True),
            priority=_int_value(payload.get("priority"), 0),
            weight=max(1, _int_value(payload.get("weight"), 1)),
            groups=_string_list(payload.get("groups"), ["default"]),
            models=_string_list(payload.get("models"), ["*"]),
            route_families=[item.strip().lower() for item in route_families if item.strip()],
            auth_label=str(payload.get("auth_label") or payload.get("label") or "").strip() or None,
            url=str(payload.get("url") or "").strip() or None,
            base_url=str(payload.get("base_url") or "").strip() or None,
            api_key=str(payload.get("api_key") or "").strip() or None,
            api_key_env=str(payload.get("api_key_env") or "").strip() or None,
            organization=str(payload.get("organization") or "").strip() or None,
            headers={str(k): str(v) for k, v in headers.items()},
            timeout_seconds=max(10, _int_value(payload.get("timeout_seconds"), 600)),
        )

    def supports_route_family(self, route_family: str) -> bool:
        families = self.route_families or ["openai"]
        return _matches_any(route_family, families)

    def supports_group(self, group: str) -> bool:
        return _matches_any(group, self.groups)

    def supports_model(self, model_name: str) -> bool:
        return _matches_any(model_name, self.models)

    def effective_openai_api_key(self) -> str | None:
        if isinstance(self.api_key, str) and self.api_key.strip():
            return self.api_key.strip()
        if isinstance(self.api_key_env, str) and self.api_key_env.strip():
            value = os.getenv(self.api_key_env.strip()) or ""
            value = value.strip()
            return value or None
        return None

    def public_models(self) -> list[str]:
        out: list[str] = []
        for item in self.models:
            candidate = str(item).strip()
            if not candidate or "*" in candidate or "?" in candidate:
                continue
            if candidate not in out:
                out.append(candidate)
        return out


class ManagedGatewayUpstream:
    def __init__(self, upstream: Any, manager: "GatewayManager", channel_id: str) -> None:
        self._upstream = upstream
        self._manager = manager
        self._channel_id = channel_id
        self._marked = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._upstream, name)

    @property
    def channel_id(self) -> str:
        return self._channel_id

    def close(self) -> None:
        return self._upstream.close()

    def mark_success(self) -> None:
        self._mark(True)

    def mark_failure(self, error_message: str | None = None) -> None:
        self._mark(False, error_message=error_message)

    def _mark(self, success: bool, *, error_message: str | None = None, status_code: int | None = None) -> None:
        if self._marked:
            return
        self._marked = True
        self._manager.mark_channel_result(
            self._channel_id,
            success=success,
            status_code=status_code,
            error_message=error_message,
        )

    def iter_lines(self, decode_unicode: bool = False):
        saw_completed = False
        error_message: str | None = None
        try:
            for raw in self._upstream.iter_lines(decode_unicode=decode_unicode):
                line = raw
                if isinstance(raw, (bytes, bytearray)):
                    line = raw.decode("utf-8", errors="ignore")
                if isinstance(line, str) and line.startswith("data: "):
                    data = line[len("data: "):].strip()
                    if data and data != "[DONE]":
                        try:
                            event = json.loads(data)
                        except Exception:
                            event = None
                        if isinstance(event, dict):
                            kind = event.get("type")
                            if kind == "response.failed":
                                error_message = (
                                    ((event.get("response") or {}).get("error") or {}).get("message")
                                    or "response.failed"
                                )
                                self._mark(False, error_message=error_message)
                            elif kind == "response.completed":
                                saw_completed = True
                yield raw
            if not self._marked:
                if saw_completed:
                    self._mark(True)
                else:
                    self._mark(False, error_message=error_message or "stream ended before response.completed")
        except Exception as exc:
            self._mark(False, error_message=str(exc))
            raise


class GatewayManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._config_path: str | None = None
        self._config_mtime: float | None = None
        self._api_keys: list[dict[str, Any]] = []
        self._channels: list[GatewayChannel] = []
        self._state: dict[str, dict[str, Any]] = {}
        self._rr_index = 0

    def is_enabled(self) -> bool:
        mode = str(current_app.config.get("UPSTREAM_MODE") or "").strip().lower()
        return mode == "gateway"

    def _config_path_value(self) -> str:
        return str(os.getenv(_DEFAULT_CHANNELS_ENV) or "").strip()

    def _load_if_needed(self) -> None:
        path = self._config_path_value()
        if not path:
            with self._lock:
                self._config_path = None
                self._config_mtime = None
                self._api_keys = []
                self._channels = []
            return

        file_path = Path(path).expanduser()
        try:
            stat = file_path.stat()
            mtime = float(stat.st_mtime)
        except OSError:
            mtime = None

        with self._lock:
            if path == self._config_path and mtime == self._config_mtime:
                return

        data: dict[str, Any] = {}
        if mtime is not None:
            try:
                with open(file_path, "r", encoding="utf-8") as fp:
                    parsed = json.load(fp)
                if isinstance(parsed, dict):
                    data = parsed
            except Exception:
                data = {}

        api_keys_raw = data.get("api_keys") if isinstance(data.get("api_keys"), list) else []
        channels_raw = data.get("channels") if isinstance(data.get("channels"), list) else []

        api_keys: list[dict[str, Any]] = []
        for index, item in enumerate(api_keys_raw, start=1):
            if isinstance(item, str):
                api_keys.append(
                    {
                        "name": f"key-{index}",
                        "key": item,
                        "groups": ["default"],
                        "models": ["*"],
                        "enabled": True,
                    }
                )
                continue
            if not isinstance(item, dict):
                continue
            key_value = str(item.get("key") or "").strip()
            if not key_value:
                continue
            api_keys.append(
                {
                    "name": str(item.get("name") or f"key-{index}").strip() or f"key-{index}",
                    "key": key_value,
                    "groups": _string_list(item.get("groups"), ["default"]),
                    "models": _string_list(item.get("models"), ["*"]),
                    "enabled": _bool_value(item.get("enabled"), default=True),
                }
            )

        channels: list[GatewayChannel] = []
        for index, item in enumerate(channels_raw, start=1):
            if not isinstance(item, dict):
                continue
            channel = GatewayChannel.from_dict(item, index)
            if not channel.transport:
                continue
            channels.append(channel)

        try:
            control_plane = current_app.config.get("CONTROL_PLANE_MANAGER")
        except Exception:
            control_plane = None
        if control_plane is not None and hasattr(control_plane, "export_gateway_api_keys"):
            exported = list(control_plane.export_gateway_api_keys() or [])
            seen_keys = {str(item.get("key") or "").strip() for item in api_keys}
            for item in exported:
                key_value = str(item.get("key") or "").strip()
                if not key_value or key_value in seen_keys:
                    continue
                seen_keys.add(key_value)
                api_keys.append(
                    {
                        "name": str(item.get("name") or f"db-key-{len(api_keys) + 1}").strip() or f"db-key-{len(api_keys) + 1}",
                        "key": key_value,
                        "groups": _string_list(item.get("groups"), ["default"]),
                        "models": _string_list(item.get("models"), ["*"]),
                        "enabled": _bool_value(item.get("enabled"), default=True),
                    }
                )

        with self._lock:
            self._config_path = path
            self._config_mtime = mtime
            self._api_keys = api_keys
            self._channels = channels

    def config_snapshot(self) -> dict[str, Any]:
        self._load_if_needed()
        with self._lock:
            return {
                "path": self._config_path or "",
                "channels": [channel.id for channel in self._channels],
                "api_keys": [str(item.get("name") or "") for item in self._api_keys],
            }

    def channel_status_snapshot(self) -> list[dict[str, Any]]:
        self._load_if_needed()
        now = time.time()
        with self._lock:
            channels = list(self._channels)
            state_map = dict(self._state)
        snapshots: list[dict[str, Any]] = []
        for channel in channels:
            state = dict(state_map.get(channel.id) or {})
            cooldown_until = float(state.get("cooldown_until") or 0.0)
            snapshots.append(
                {
                    "id": channel.id,
                    "transport": channel.transport,
                    "enabled": bool(channel.enabled),
                    "priority": int(channel.priority or 0),
                    "weight": int(channel.weight or 1),
                    "groups": list(channel.groups or []),
                    "models": list(channel.models or []),
                    "routeFamilies": list(channel.route_families or []),
                    "authLabel": channel.auth_label or "",
                    "url": channel.url or "",
                    "baseUrl": channel.base_url or "",
                    "timeoutSeconds": int(channel.timeout_seconds or 600),
                    "healthy": cooldown_until <= now,
                    "failures": int(state.get("failures") or 0),
                    "cooldownUntil": cooldown_until,
                    "cooldownRemaining": max(0, int(cooldown_until - now)),
                    "lastStatus": state.get("last_status"),
                    "lastError": str(state.get("last_error") or ""),
                    "updatedAt": state.get("updated_at"),
                }
            )
        return snapshots

    def validate_raw_config(self, payload: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if not isinstance(payload, dict):
            return ["gateway config must be a JSON object"]

        api_keys_raw = payload.get("api_keys")
        channels_raw = payload.get("channels")
        if api_keys_raw is not None and not isinstance(api_keys_raw, list):
            errors.append("api_keys must be an array when provided")
        if channels_raw is not None and not isinstance(channels_raw, list):
            errors.append("channels must be an array when provided")
        if errors:
            return errors

        seen_channel_ids: set[str] = set()
        for index, item in enumerate(channels_raw or [], start=1):
            if not isinstance(item, dict):
                errors.append(f"channels[{index}] must be an object")
                continue
            channel_id = str(item.get("id") or f"channel-{index}").strip() or f"channel-{index}"
            if channel_id in seen_channel_ids:
                errors.append(f"channels[{index}] duplicate id '{channel_id}'")
            seen_channel_ids.add(channel_id)
            transport = str(item.get("transport") or item.get("type") or "").strip().lower()
            if not transport:
                errors.append(f"channels[{index}] missing transport")
            elif transport not in _SUPPORTED_GATEWAY_TRANSPORTS:
                supported = ", ".join(sorted(_SUPPORTED_GATEWAY_TRANSPORTS))
                errors.append(
                    f"channels[{index}] transport '{transport}' is not supported; use one of: {supported}"
                )
            models = item.get("models")
            if models is not None and not isinstance(models, (list, str)):
                errors.append(f"channels[{index}] models must be a string or array")
            groups = item.get("groups")
            if groups is not None and not isinstance(groups, (list, str)):
                errors.append(f"channels[{index}] groups must be a string or array")
            if transport == "codex-app-server":
                url = str(item.get("url") or "").strip()
                if not url:
                    errors.append(f"channels[{index}] codex-app-server transport requires url")
        return errors

    def current_config_path(self) -> str:
        return self._config_path_value()

    def load_raw_config(self) -> dict[str, Any]:
        path = self._config_path_value()
        if not path:
            return {"api_keys": [], "channels": []}
        file_path = Path(path).expanduser()
        try:
            with open(file_path, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {"api_keys": [], "channels": []}

    def save_raw_config(self, payload: dict[str, Any], *, path: str | None = None) -> str:
        target = str(path or self._config_path_value() or "").strip()
        if not target:
            target = str((Path.cwd() / "gateway.channels.json").resolve())
        file_path = Path(target).expanduser()
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, indent=2)
        os.environ[_DEFAULT_CHANNELS_ENV] = str(file_path)
        with self._lock:
            self._config_path = None
            self._config_mtime = None
        self._load_if_needed()
        return str(file_path)

    def invalidate(self) -> None:
        with self._lock:
            self._config_path = None
            self._config_mtime = None

    def authorize_request(self) -> Response | None:
        self._load_if_needed()
        with self._lock:
            api_keys = [dict(item) for item in self._api_keys if _bool_value(item.get("enabled"), default=True)]

        if not api_keys:
            if _allow_anonymous_gateway_access():
                g.chatmock_gateway_access = GatewayAccess(
                    key_id="anonymous",
                    groups=["*"],
                    models=["*"],
                    anonymous=True,
                )
                return None
            return self._error_response("Missing gateway API key", 401)

        token = _request_token()
        if not token:
            return self._error_response("Missing gateway API key", 401)

        for item in api_keys:
            if token != str(item.get("key") or "").strip():
                continue
            g.chatmock_gateway_access = GatewayAccess(
                key_id=str(item.get("name") or "gateway-key"),
                groups=_string_list(item.get("groups"), ["default"]),
                models=_string_list(item.get("models"), ["*"]),
                anonymous=False,
            )
            return None

        return self._error_response("Invalid gateway API key", 401)

    def current_access(self) -> GatewayAccess:
        access = getattr(g, "chatmock_gateway_access", None)
        if isinstance(access, GatewayAccess):
            return access
        return GatewayAccess(key_id="anonymous", groups=["*"], models=["*"], anonymous=True)

    def requested_group(self, payload: dict[str, Any] | None = None) -> str:
        for header_name in ("X-ChatMock-Group", "X-ChatGPT-Local-Group", "X-Route-Group"):
            value = request.headers.get(header_name)
            if isinstance(value, str) and value.strip():
                return _normalize_group(value)
        query_value = request.args.get("group")
        if isinstance(query_value, str) and query_value.strip():
            return _normalize_group(query_value)
        if isinstance(payload, dict):
            value = payload.get("group")
            if isinstance(value, str) and value.strip():
                return _normalize_group(value)
        return "default"

    def ordered_channels(self, route_family: str, model_name: str, group: str) -> list[GatewayChannel]:
        self._load_if_needed()
        access = self.current_access()
        if not access.allows_group(group):
            return []
        if model_name and model_name != "*" and not access.allows_model(model_name):
            return []

        with self._lock:
            channels = [channel for channel in self._channels if channel.enabled]

        matching = [
            channel
            for channel in channels
            if channel.supports_route_family(route_family)
            and channel.supports_group(group)
            and (
                not model_name
                or model_name == "*"
                or channel.supports_model(model_name)
            )
        ]
        if not matching:
            return []

        now = time.time()
        available = [channel for channel in matching if self._cooldown_until(channel.id) <= now]
        candidate_pool = available or matching

        priorities = sorted({channel.priority for channel in candidate_pool}, reverse=True)
        ordered: list[GatewayChannel] = []
        for priority in priorities:
            same_priority = [channel for channel in candidate_pool if channel.priority == priority]
            ordered.extend(self._order_group(same_priority))
        return ordered

    def list_public_models(self, route_family: str, group: str) -> list[str]:
        access = self.current_access()
        out: list[str] = []
        for channel in self.ordered_channels(route_family, "*", group):
            for model_name in channel.public_models():
                if not access.allows_model(model_name):
                    continue
                if model_name not in out:
                    out.append(model_name)
        return out

    def wrap_upstream(self, channel_id: str, upstream: Any) -> ManagedGatewayUpstream:
        return ManagedGatewayUpstream(upstream, self, channel_id)

    def mark_channel_result(
        self,
        channel_id: str,
        *,
        success: bool,
        status_code: int | None = None,
        error_message: str | None = None,
    ) -> None:
        now = time.time()
        max_retry_interval = max(5, get_max_retry_interval_seconds())
        with self._lock:
            state = dict(self._state.get(channel_id) or {})
            if success:
                state["failures"] = 0
                state["cooldown_until"] = 0.0
                state["last_status"] = status_code if isinstance(status_code, int) else 200
                state["last_error"] = ""
                state["updated_at"] = now
                self._state[channel_id] = state
                return

            failures = int(state.get("failures") or 0) + 1
            if isinstance(status_code, int) and status_code in (401, 403):
                base = 10
            elif isinstance(status_code, int) and status_code == 429:
                base = 4
            else:
                base = 2
            cooldown = min(max_retry_interval, base * (2 ** max(0, failures - 1)))
            state["failures"] = failures
            state["cooldown_until"] = now + float(cooldown)
            state["last_status"] = status_code
            state["last_error"] = error_message or ""
            state["updated_at"] = now
            self._state[channel_id] = state

    def _cooldown_until(self, channel_id: str) -> float:
        with self._lock:
            state = self._state.get(channel_id) or {}
        try:
            return float(state.get("cooldown_until") or 0.0)
        except Exception:
            return 0.0

    def _order_group(self, channels: list[GatewayChannel]) -> list[GatewayChannel]:
        strategy = str(os.getenv("CHATGPT_LOCAL_ROUTING_STRATEGY") or "round-robin").strip().lower()
        if len(channels) <= 1:
            return list(channels)

        if strategy == "first":
            return list(channels)

        if strategy == "random":
            pool: list[GatewayChannel] = []
            for channel in channels:
                pool.extend([channel] * max(1, int(channel.weight or 1)))
            random.shuffle(pool)
            out: list[GatewayChannel] = []
            seen: set[str] = set()
            for channel in pool:
                if channel.id in seen:
                    continue
                seen.add(channel.id)
                out.append(channel)
            return out

        with self._lock:
            start = self._rr_index % len(channels)
            self._rr_index += 1
        return channels[start:] + channels[:start]

    def _error_response(self, message: str, status: int) -> Response:
        resp = make_response({"error": {"message": message}}, status)
        for key, value in build_cors_headers().items():
            resp.headers.setdefault(key, value)
        return resp


def get_gateway_manager() -> GatewayManager | None:
    manager = current_app.config.get("GATEWAY_MANAGER")
    return manager if isinstance(manager, GatewayManager) else None
