from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import secrets
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Blueprint, current_app, jsonify, make_response, request, send_from_directory

from .control_plane import default_control_db_path, get_control_plane_manager
from .gateway import get_gateway_manager
from .utils import (
    get_chatgpt_auth_records,
    get_max_retry_interval_seconds,
    get_request_retry_limit,
    write_auth_file,
)


dashboard_bp = Blueprint("dashboard", __name__)

_DASHBOARD_DIR = Path(__file__).resolve().parent / "dashboard"
_VALID_ROUTING_STRATEGIES = {"round-robin", "random", "first"}
_VALID_REASONING_EFFORT = {"minimal", "low", "medium", "high", "xhigh"}
_VALID_REASONING_SUMMARY = {"auto", "concise", "detailed", "none"}
_VALID_REASONING_COMPAT = {"legacy", "o3", "think-tags", "current"}


def _dashboard_admin_token() -> str:
    return _clean_string(os.getenv("CHATMOCK_DASHBOARD_ADMIN_TOKEN"))


def _dashboard_request_token() -> str:
    auth_header = _clean_string(request.headers.get("Authorization"))
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    header_token = _clean_string(
        request.headers.get("X-Dashboard-Token") or request.headers.get("X-Admin-Token")
    )
    if header_token:
        return header_token
    return _clean_string(request.args.get("dashboard_token"))


@dashboard_bp.before_request
def dashboard_require_admin_token():
    expected = _dashboard_admin_token()
    if not expected:
        return None
    if request.method == "OPTIONS":
        return None
    if request.path in ("/dashboard", "/dashboard/", "/dashboard/app.js", "/dashboard/styles.css", "/api/dashboard/auth-status"):
        return None
    provided = _dashboard_request_token()
    if secrets.compare_digest(provided, expected):
        return None
    return make_response(jsonify({"ok": False, "error": "dashboard admin token required"}), 401)


def _model_ids(expose_variants: bool) -> List[str]:
    model_groups = [
        ("gpt-5", ["high", "medium", "low", "minimal"]),
        ("gpt-5.1", ["high", "medium", "low"]),
        ("gpt-5.2", ["xhigh", "high", "medium", "low"]),
        ("gpt-5.4", ["xhigh", "high", "medium", "low"]),
        ("gpt-5.4-fast", ["xhigh", "high", "medium", "low"]),
        ("gpt-5.3-codex", ["xhigh", "high", "medium", "low"]),
        ("gpt-5-codex", ["high", "medium", "low"]),
        ("gpt-5.2-codex", ["xhigh", "high", "medium", "low"]),
        ("gpt-5.1-codex", ["high", "medium", "low"]),
        ("gpt-5.1-codex-max", ["xhigh", "high", "medium", "low"]),
        ("gpt-5.1-codex-mini", []),
        ("codex-mini", []),
    ]
    out: List[str] = []
    for base, efforts in model_groups:
        out.append(base)
        if expose_variants:
            out.extend([f"{base}-{effort}" for effort in efforts])
    return out


def _default_log_path() -> str:
    explicit = (os.getenv("CHATMOCK_DASHBOARD_LOG_PATH") or "").strip()
    if explicit:
        return explicit
    env_log = (os.getenv("CHATGPT_LOCAL_LOG_PATH") or "").strip()
    if env_log:
        return env_log
    return str(Path.cwd() / "chatmock.log")


def _read_log_tail(path: str, lines: int) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            data = f.readlines()
        return "".join(data[-lines:])
    except Exception as exc:
        return f"failed to read log: {exc}"


def _bool_env(name: str, default: bool = True) -> bool:
    raw = str(os.getenv(name, "1" if default else "0")).strip().lower()
    return raw not in ("0", "false", "no", "off")


def _bool_value(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    raw = str(value).strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _clean_string(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _clean_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(minimum, min(maximum, parsed))


def _clean_choice(value: Any, allowed: set[str], default: str) -> str:
    candidate = _clean_string(value, default=default).lower()
    if candidate not in allowed:
        return default
    return candidate


def _dedupe_paths(paths: List[str]) -> List[str]:
    out: List[str] = []
    for item in paths:
        path = _clean_string(item)
        if path and path not in out:
            out.append(path)
    return out


def _current_auth_files() -> List[str]:
    raw = (os.getenv("CHATGPT_LOCAL_AUTH_FILES") or "").strip()
    if not raw:
        return []
    return _dedupe_paths(raw.split(","))


def _parse_auth_files_payload(value: Any, fallback: List[str]) -> List[str]:
    if isinstance(value, list):
        return _dedupe_paths([str(v) for v in value])
    if isinstance(value, str):
        return _dedupe_paths(value.split(","))
    return list(fallback)


def _auth_storage_root() -> Path:
    explicit = (os.getenv("CHATMOCK_DASHBOARD_AUTH_DIR") or "").strip()
    if explicit:
        root = Path(explicit)
        root.mkdir(parents=True, exist_ok=True)
        return root

    data_dir = (os.getenv("CHATMOCK_DATA_DIR") or "").strip()
    if data_dir:
        root = Path(data_dir).expanduser() / "accounts"
        root.mkdir(parents=True, exist_ok=True)
        return root

    existing = _current_auth_files()
    if existing:
        first = Path(existing[0]).expanduser()
        if first.name == "auth.json":
            root = first.parent.parent
            root.mkdir(parents=True, exist_ok=True)
            return root

    root = Path("/tmp/chatmock-accounts")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _discover_auth_files(root: Path | None = None) -> List[str]:
    base = root
    if base is None:
        explicit = (os.getenv("CHATMOCK_DASHBOARD_AUTH_DIR") or "").strip()
        if explicit:
            base = Path(explicit).expanduser()
        else:
            data_dir = (os.getenv("CHATMOCK_DATA_DIR") or "").strip()
            if data_dir:
                base = Path(data_dir).expanduser() / "accounts"
            else:
                current = _current_auth_files()
                if current:
                    first = Path(current[0]).expanduser()
                    if first.name == "auth.json":
                        base = first.parent.parent
                if base is None:
                    fallback = Path("/tmp/chatmock-accounts")
                    if fallback.exists():
                        base = fallback
    if base is None or not base.exists():
        return []
    files = [str(path) for path in sorted(base.glob("acc*/auth.json")) if path.is_file()]
    return _dedupe_paths(files)


def _runtime_codex_manager():
    return current_app.config.get("CODEX_APP_SERVER_MANAGER")


def _runtime_gateway_manager():
    return get_gateway_manager()


def _runtime_control_plane_manager():
    return get_control_plane_manager()


def _invalidate_gateway_manager() -> None:
    manager = _runtime_gateway_manager()
    if manager is not None and hasattr(manager, "invalidate"):
        try:
            manager.invalidate()
        except Exception:
            pass


def _settings_path() -> Path:
    explicit = (os.getenv("CHATMOCK_DASHBOARD_SETTINGS_PATH") or "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    return _auth_storage_root() / "_dashboard_settings.json"


def _gateway_channels_default_path() -> Path:
    explicit = (os.getenv("CHATGPT_LOCAL_CHANNELS_PATH") or "").strip()
    if explicit:
        return Path(explicit).expanduser()

    data_dir = (os.getenv("CHATMOCK_DATA_DIR") or "").strip()
    if data_dir:
        return Path(data_dir).expanduser() / "gateway.channels.json"

    auth_root = _auth_storage_root()
    if auth_root.name == "accounts":
        return auth_root.parent / "gateway.channels.json"
    return auth_root / "gateway.channels.json"


def _read_settings_file() -> Dict[str, Any]:
    path = _settings_path()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        if isinstance(data, dict):
            return data
    except Exception:
        return {}
    return {}


def _write_settings_file(data: Dict[str, Any]) -> None:
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fp:
        if hasattr(os, "fchmod"):
            os.fchmod(fp.fileno(), 0o600)
        json.dump(data, fp, ensure_ascii=False, indent=2)


def _get_runtime_app():
    try:
        return current_app._get_current_object()
    except RuntimeError:
        return None


def _current_settings_snapshot(app=None) -> Dict[str, Any]:
    runtime_app = app or _get_runtime_app()
    stored = _read_settings_file()
    auth_files = _merge_auth_files(_current_auth_files(), _discover_auth_files(_auth_storage_root()), replace=False)
    channels_path = _clean_string(
        os.getenv("CHATGPT_LOCAL_CHANNELS_PATH") or stored.get("channelsPath"),
        default=str(_gateway_channels_default_path()),
    )

    if runtime_app is not None:
        reasoning_effort = str(runtime_app.config.get("REASONING_EFFORT", "medium"))
        reasoning_summary = str(runtime_app.config.get("REASONING_SUMMARY", "auto"))
        reasoning_compat = str(runtime_app.config.get("REASONING_COMPAT", "think-tags"))
        expose_reasoning_models = bool(runtime_app.config.get("EXPOSE_REASONING_MODELS"))
        enable_web_search = bool(runtime_app.config.get("DEFAULT_WEB_SEARCH"))
        verbose = bool(runtime_app.config.get("VERBOSE"))
        verbose_obfuscation = bool(runtime_app.config.get("VERBOSE_OBFUSCATION"))
    else:
        reasoning_effort = os.getenv("CHATGPT_LOCAL_REASONING_EFFORT", "medium")
        reasoning_summary = os.getenv("CHATGPT_LOCAL_REASONING_SUMMARY", "auto")
        reasoning_compat = os.getenv("CHATGPT_LOCAL_REASONING_COMPAT", "think-tags")
        expose_reasoning_models = _bool_env("CHATGPT_LOCAL_EXPOSE_REASONING_MODELS", default=False)
        enable_web_search = _bool_env("CHATGPT_LOCAL_ENABLE_WEB_SEARCH", default=False)
        verbose = _bool_env("CHATGPT_LOCAL_VERBOSE", default=False)
        verbose_obfuscation = _bool_env("CHATGPT_LOCAL_VERBOSE_OBFUSCATION", default=False)

    return {
        "routingStrategy": _clean_choice(
            os.getenv("CHATGPT_LOCAL_ROUTING_STRATEGY", "round-robin"),
            _VALID_ROUTING_STRATEGIES,
            "round-robin",
        ),
        "requestRetry": get_request_retry_limit(),
        "maxRetryInterval": get_max_retry_interval_seconds(),
        "reasoningEffort": _clean_choice(reasoning_effort, _VALID_REASONING_EFFORT, "medium"),
        "reasoningSummary": _clean_choice(reasoning_summary, _VALID_REASONING_SUMMARY, "auto"),
        "reasoningCompat": _clean_choice(reasoning_compat, _VALID_REASONING_COMPAT, "think-tags"),
        "exposeReasoningModels": bool(expose_reasoning_models),
        "enableWebSearch": bool(enable_web_search),
        "verbose": bool(verbose),
        "verboseObfuscation": bool(verbose_obfuscation),
        "httpProxy": os.getenv("HTTP_PROXY", ""),
        "httpsProxy": os.getenv("HTTPS_PROXY", ""),
        "allProxy": os.getenv("ALL_PROXY", ""),
        "noProxy": os.getenv("NO_PROXY", ""),
        "uploadReplaceDefault": _bool_value(stored.get("uploadReplaceDefault"), default=False),
        "authFiles": auth_files,
        "channelsPath": channels_path,
    }


def _set_env_or_clear(name: str, value: str) -> None:
    cleaned = _clean_string(value)
    if cleaned:
        os.environ[name] = cleaned
    else:
        os.environ.pop(name, None)


def _merge_payload_settings(payload: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    incoming = payload if isinstance(payload, dict) else {}

    settings = {
        "routingStrategy": _clean_choice(
            incoming.get("routingStrategy", current["routingStrategy"]),
            _VALID_ROUTING_STRATEGIES,
            current["routingStrategy"],
        ),
        "requestRetry": _clean_int(incoming.get("requestRetry", current["requestRetry"]), current["requestRetry"], 0, 10),
        "maxRetryInterval": _clean_int(
            incoming.get("maxRetryInterval", current["maxRetryInterval"]),
            current["maxRetryInterval"],
            1,
            300,
        ),
        "reasoningEffort": _clean_choice(
            incoming.get("reasoningEffort", current["reasoningEffort"]),
            _VALID_REASONING_EFFORT,
            current["reasoningEffort"],
        ),
        "reasoningSummary": _clean_choice(
            incoming.get("reasoningSummary", current["reasoningSummary"]),
            _VALID_REASONING_SUMMARY,
            current["reasoningSummary"],
        ),
        "reasoningCompat": _clean_choice(
            incoming.get("reasoningCompat", current["reasoningCompat"]),
            _VALID_REASONING_COMPAT,
            current["reasoningCompat"],
        ),
        "exposeReasoningModels": _bool_value(
            incoming.get("exposeReasoningModels", current["exposeReasoningModels"]),
            default=current["exposeReasoningModels"],
        ),
        "enableWebSearch": _bool_value(
            incoming.get("enableWebSearch", current["enableWebSearch"]),
            default=current["enableWebSearch"],
        ),
        "verbose": _bool_value(incoming.get("verbose", current["verbose"]), default=current["verbose"]),
        "verboseObfuscation": _bool_value(
            incoming.get("verboseObfuscation", current["verboseObfuscation"]),
            default=current["verboseObfuscation"],
        ),
        "httpProxy": _clean_string(incoming.get("httpProxy", current["httpProxy"])),
        "httpsProxy": _clean_string(incoming.get("httpsProxy", current["httpsProxy"])),
        "allProxy": _clean_string(incoming.get("allProxy", current["allProxy"])),
        "noProxy": _clean_string(incoming.get("noProxy", current["noProxy"])),
        "uploadReplaceDefault": _bool_value(
            incoming.get("uploadReplaceDefault", current["uploadReplaceDefault"]),
            default=current["uploadReplaceDefault"],
        ),
        "authFiles": _parse_auth_files_payload(incoming.get("authFiles"), current["authFiles"]),
        "channelsPath": _clean_string(incoming.get("channelsPath", current["channelsPath"])),
    }
    return settings


def _apply_settings(settings: Dict[str, Any], *, app=None, persist: bool) -> Dict[str, Any]:
    runtime_app = app or _get_runtime_app()
    current = _current_settings_snapshot(app=runtime_app)
    merged = _merge_payload_settings(settings, current)

    os.environ["CHATGPT_LOCAL_ROUTING_STRATEGY"] = merged["routingStrategy"]
    os.environ["CHATGPT_LOCAL_REQUEST_RETRY"] = str(merged["requestRetry"])
    os.environ["CHATGPT_LOCAL_MAX_RETRY_INTERVAL"] = str(merged["maxRetryInterval"])
    os.environ["CHATGPT_LOCAL_AUTH_FILES"] = ",".join(merged["authFiles"])

    os.environ["CHATGPT_LOCAL_REASONING_EFFORT"] = merged["reasoningEffort"]
    os.environ["CHATGPT_LOCAL_REASONING_SUMMARY"] = merged["reasoningSummary"]
    os.environ["CHATGPT_LOCAL_REASONING_COMPAT"] = merged["reasoningCompat"]
    os.environ["CHATGPT_LOCAL_EXPOSE_REASONING_MODELS"] = "1" if merged["exposeReasoningModels"] else "0"
    os.environ["CHATGPT_LOCAL_ENABLE_WEB_SEARCH"] = "1" if merged["enableWebSearch"] else "0"
    os.environ["CHATGPT_LOCAL_VERBOSE"] = "1" if merged["verbose"] else "0"
    os.environ["CHATGPT_LOCAL_VERBOSE_OBFUSCATION"] = "1" if merged["verboseObfuscation"] else "0"
    _set_env_or_clear("CHATGPT_LOCAL_CHANNELS_PATH", merged["channelsPath"])

    _set_env_or_clear("HTTP_PROXY", merged["httpProxy"])
    _set_env_or_clear("HTTPS_PROXY", merged["httpsProxy"])
    _set_env_or_clear("ALL_PROXY", merged["allProxy"])
    _set_env_or_clear("NO_PROXY", merged["noProxy"])

    if runtime_app is not None:
        runtime_app.config["REASONING_EFFORT"] = merged["reasoningEffort"]
        runtime_app.config["REASONING_SUMMARY"] = merged["reasoningSummary"]
        runtime_app.config["REASONING_COMPAT"] = merged["reasoningCompat"]
        runtime_app.config["EXPOSE_REASONING_MODELS"] = merged["exposeReasoningModels"]
        runtime_app.config["DEFAULT_WEB_SEARCH"] = merged["enableWebSearch"]
        runtime_app.config["VERBOSE"] = merged["verbose"]
        runtime_app.config["VERBOSE_OBFUSCATION"] = merged["verboseObfuscation"]

    if persist:
        stored = _read_settings_file()
        stored.update(merged)
        stored["updatedAt"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        _write_settings_file(stored)

    return merged


def apply_persisted_dashboard_settings(app) -> Dict[str, Any]:
    stored = _read_settings_file()
    if not stored:
        return _current_settings_snapshot(app=app)
    stored = dict(stored)
    stored["authFiles"] = _merge_auth_files(
        _parse_auth_files_payload(stored.get("authFiles"), []),
        _discover_auth_files(_auth_storage_root()),
        replace=False,
    )
    return _apply_settings(stored, app=app, persist=False)


def _merge_auth_files(existing: List[str], new_files: List[str], replace: bool) -> List[str]:
    if replace:
        return list(dict.fromkeys(new_files))
    merged = list(existing)
    for path in new_files:
        if path not in merged:
            merged.append(path)
    return merged


def _write_auth_payload(target_path: Path, payload: Dict[str, Any]) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with open(target_path, "w", encoding="utf-8") as fp:
        if hasattr(os, "fchmod"):
            os.fchmod(fp.fileno(), 0o600)
        json.dump(payload, fp, ensure_ascii=False, indent=2)


def _extract_account_id(payload: Dict[str, Any]) -> str:
    tokens = payload.get("tokens") if isinstance(payload.get("tokens"), dict) else {}
    account_id = tokens.get("account_id") if isinstance(tokens.get("account_id"), str) else ""
    if not account_id and isinstance(payload.get("account_id"), str):
        account_id = payload.get("account_id") or ""
    if account_id:
        return str(account_id).strip()
    return ""


def _auth_payload_fingerprint(payload: Dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _extract_acc_index(label: str) -> int:
    match = re.fullmatch(r"acc(\d+)", label.lower())
    if not match:
        return 0
    try:
        return int(match.group(1))
    except Exception:
        return 0


def _next_acc_label(used_labels: set[str]) -> str:
    index = 1
    if used_labels:
        found = [_extract_acc_index(item) for item in used_labels]
        found = [item for item in found if item > 0]
        if found:
            index = max(found) + 1
    while True:
        label = f"acc{index:02d}"
        if label not in used_labels:
            return label
        index += 1


def _read_auth_payload(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        if isinstance(data, dict):
            return data
    except Exception:
        return None
    return None


def _service_status() -> Dict[str, Any]:
    manager = _runtime_codex_manager()
    if manager is not None:
        return manager.status()
    return {"status": "unmanaged", "managed": False, "listening": False}


def _fast_instance_map() -> Dict[str, Dict[str, Any]]:
    service = _service_status()
    instances = service.get("instances") if isinstance(service.get("instances"), list) else []
    out: Dict[str, Dict[str, Any]] = {}
    for item in instances:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        if label:
            out[label] = item
    return out


def _dashboard_model_ids() -> List[str]:
    gateway_manager = _runtime_gateway_manager()
    if gateway_manager is not None and gateway_manager.is_enabled():
        group = gateway_manager.requested_group()
        model_ids: List[str] = []
        for family in ("openai", "anthropic", "ollama"):
            for model_id in gateway_manager.list_public_models(family, group):
                if model_id not in model_ids:
                    model_ids.append(model_id)
        if model_ids:
            return model_ids
    return _model_ids(bool(current_app.config.get("EXPOSE_REASONING_MODELS")))


@dashboard_bp.get("/dashboard")
@dashboard_bp.get("/dashboard/")
def dashboard_index():
    return send_from_directory(_DASHBOARD_DIR, "index.html")


@dashboard_bp.get("/dashboard/app.js")
def dashboard_js():
    return send_from_directory(_DASHBOARD_DIR, "app.js")


@dashboard_bp.get("/dashboard/styles.css")
def dashboard_css():
    return send_from_directory(_DASHBOARD_DIR, "styles.css")


@dashboard_bp.get("/api/health")
def dashboard_health():
    records = get_chatgpt_auth_records()
    models = _dashboard_model_ids()
    service = _service_status()
    gateway_manager = _runtime_gateway_manager()
    gateway_snapshot = gateway_manager.config_snapshot() if gateway_manager is not None else {"path": "", "channels": [], "api_keys": []}
    payload = {
        "now": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "service": service,
        "listening": bool(service.get("listening")),
        "models": {"count": len(models), "ids": models, "error": ""},
        "accounts": {"count": len(records)},
        "gateway": {
            "enabled": bool(gateway_manager is not None and gateway_manager.is_enabled()),
            "path": gateway_snapshot.get("path", ""),
            "channels": len(gateway_snapshot.get("channels") or []),
            "apiKeys": len(gateway_snapshot.get("api_keys") or []),
        },
        "routing": {
            "strategy": (os.getenv("CHATGPT_LOCAL_ROUTING_STRATEGY") or "round-robin"),
            "request_retry": get_request_retry_limit(),
            "max_retry_interval": get_max_retry_interval_seconds(),
        },
    }
    return jsonify(payload)


@dashboard_bp.get("/api/dashboard/auth-status")
def dashboard_auth_status():
    return jsonify({"ok": True, "required": bool(_dashboard_admin_token())})


@dashboard_bp.get("/api/accounts")
def dashboard_accounts():
    records = get_chatgpt_auth_records()
    instance_map = _fast_instance_map()
    for record in records:
        if not isinstance(record, dict):
            continue
        source = str(record.get("source") or "")
        parent_label = Path(source).parent.name.strip() if source else ""
        instance = instance_map.get(parent_label) if parent_label else None
        if isinstance(instance, dict):
            record["fast_status"] = instance.get("status")
            record["fast_port"] = instance.get("port")
            record["fast_url"] = instance.get("url")
            record["fast_pid"] = instance.get("pid")
            record["fast_cooldown_remaining"] = instance.get("cooldownRemaining")
            record["fast_request_count"] = instance.get("requestCount")
            record["fast_request_successes"] = instance.get("requestSuccesses")
    return jsonify({"count": len(records), "accounts": records})


@dashboard_bp.get("/api/models")
def dashboard_models():
    ids = _dashboard_model_ids()
    return jsonify({"count": len(ids), "ids": ids})


@dashboard_bp.get("/api/config")
def dashboard_config():
    settings = _current_settings_snapshot()
    service = _service_status()
    gateway_manager = _runtime_gateway_manager()
    control_plane = _runtime_control_plane_manager()
    gateway_snapshot = gateway_manager.config_snapshot() if gateway_manager is not None else {"path": "", "channels": [], "api_keys": []}
    local = {
        "CHATGPT_LOCAL_HOME": os.getenv("CHATGPT_LOCAL_HOME", ""),
        "CHATGPT_LOCAL_AUTH_FILES": os.getenv("CHATGPT_LOCAL_AUTH_FILES", ""),
        "CHATGPT_LOCAL_CHANNELS_PATH": settings["channelsPath"],
        "CHATGPT_LOCAL_ROUTING_STRATEGY": settings["routingStrategy"],
        "CHATGPT_LOCAL_REQUEST_RETRY": str(settings["requestRetry"]),
        "CHATGPT_LOCAL_MAX_RETRY_INTERVAL": str(settings["maxRetryInterval"]),
        "CHATGPT_LOCAL_REASONING_EFFORT": settings["reasoningEffort"],
        "CHATGPT_LOCAL_REASONING_SUMMARY": settings["reasoningSummary"],
        "CHATGPT_LOCAL_REASONING_COMPAT": settings["reasoningCompat"],
        "CHATGPT_LOCAL_EXPOSE_REASONING_MODELS": str(settings["exposeReasoningModels"]),
        "CHATGPT_LOCAL_ENABLE_WEB_SEARCH": str(settings["enableWebSearch"]),
        "CHATGPT_LOCAL_VERBOSE": str(settings["verbose"]),
        "CHATGPT_LOCAL_VERBOSE_OBFUSCATION": str(settings["verboseObfuscation"]),
        "HTTP_PROXY": settings["httpProxy"],
        "HTTPS_PROXY": settings["httpsProxy"],
        "ALL_PROXY": settings["allProxy"],
        "NO_PROXY": settings["noProxy"],
        "CHATMOCK_DASHBOARD_SETTINGS_PATH": str(_settings_path()),
        "CHATMOCK_DATA_DIR": os.getenv("CHATMOCK_DATA_DIR", ""),
        "CHATMOCK_MANAGE_CODEX_APP_SERVER": os.getenv("CHATMOCK_MANAGE_CODEX_APP_SERVER", ""),
        "CHATGPT_LOCAL_UPSTREAM": os.getenv("CHATGPT_LOCAL_UPSTREAM", ""),
        "CHATGPT_LOCAL_CODEX_APP_SERVER_URL": os.getenv("CHATGPT_LOCAL_CODEX_APP_SERVER_URL", ""),
        "CHATMOCK_CONTROL_DB_PATH": (control_plane.db_path if control_plane is not None else default_control_db_path()),
        "CODEX_HOME": os.getenv("CODEX_HOME", ""),
        "gateway": gateway_snapshot,
        "service": service,
    }
    return jsonify(
        {
            "localPath": ".env / runtime env",
            "activePath": "runtime",
            "localConfig": json.dumps(local, ensure_ascii=False, indent=2),
            "activeConfig": json.dumps(local, ensure_ascii=False, indent=2),
        }
    )


@dashboard_bp.get("/api/settings")
def dashboard_settings():
    return jsonify(
        {
            "settings": _current_settings_snapshot(),
            "stored": _read_settings_file(),
            "settingsPath": str(_settings_path()),
        }
    )


@dashboard_bp.post("/api/settings")
def dashboard_save_settings():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return make_response(jsonify({"ok": False, "error": "invalid JSON payload"}), 400)

    settings = _apply_settings(payload, persist=True)
    return jsonify({"ok": True, "settings": settings, "settingsPath": str(_settings_path())})


@dashboard_bp.get("/api/gateway/config")
def dashboard_gateway_config():
    manager = _runtime_gateway_manager()
    current_settings = _current_settings_snapshot()
    path_value = current_settings["channelsPath"]
    if manager is not None:
        raw_config = manager.load_raw_config()
        snapshot = manager.config_snapshot()
        actual_path = manager.current_config_path() or path_value
    else:
        actual_path = path_value
        file_path = Path(actual_path).expanduser() if actual_path else _gateway_channels_default_path()
        try:
            raw_config = json.loads(file_path.read_text(encoding="utf-8")) if file_path.exists() else {}
        except Exception:
            raw_config = {}
        snapshot = {
            "path": actual_path,
            "channels": [item.get("id") for item in raw_config.get("channels", []) if isinstance(item, dict)],
            "api_keys": [item.get("name") for item in raw_config.get("api_keys", []) if isinstance(item, dict)],
        }
    if not isinstance(raw_config, dict):
        raw_config = {}
    raw_config.setdefault("api_keys", [])
    raw_config.setdefault("channels", [])
    validation_errors = manager.validate_raw_config(raw_config) if manager is not None else []
    file_path = Path(actual_path).expanduser() if actual_path else _gateway_channels_default_path()
    return jsonify(
        {
            "ok": True,
            "path": str(file_path),
            "defaultPath": str(_gateway_channels_default_path()),
            "exists": bool(file_path.exists()),
            "summary": snapshot,
            "validationErrors": validation_errors,
            "config": json.dumps(raw_config, ensure_ascii=False, indent=2),
        }
    )


@dashboard_bp.get("/api/gateway/status")
def dashboard_gateway_status():
    manager = _runtime_gateway_manager()
    if manager is None:
        return jsonify({"ok": True, "enabled": False, "path": "", "channels": []})
    snapshot = manager.config_snapshot()
    return jsonify(
        {
            "ok": True,
            "enabled": bool(manager.is_enabled()),
            "path": snapshot.get("path", ""),
            "apiKeys": snapshot.get("api_keys", []),
            "channels": manager.channel_status_snapshot(),
        }
    )


@dashboard_bp.post("/api/gateway/config")
def dashboard_save_gateway_config():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return make_response(jsonify({"ok": False, "error": "invalid JSON payload"}), 400)

    path_value = _clean_string(payload.get("path"), default=str(_gateway_channels_default_path()))
    raw_config = payload.get("config")
    if isinstance(raw_config, str):
        try:
            raw_config = json.loads(raw_config)
        except Exception as exc:
            return make_response(jsonify({"ok": False, "error": f"config is not valid JSON: {exc}"}), 400)
    if raw_config is None:
        raw_config = {}
    if not isinstance(raw_config, dict):
        return make_response(jsonify({"ok": False, "error": "config must be a JSON object"}), 400)

    raw_config.setdefault("api_keys", [])
    raw_config.setdefault("channels", [])

    manager = _runtime_gateway_manager()
    if manager is not None:
        validation_errors = manager.validate_raw_config(raw_config)
    else:
        validation_errors = []
    if validation_errors:
        return make_response(jsonify({"ok": False, "error": "invalid gateway config", "details": validation_errors}), 400)

    _apply_settings({"channelsPath": path_value}, persist=True)
    if manager is not None:
        saved_path = manager.save_raw_config(raw_config, path=path_value)
        snapshot = manager.config_snapshot()
    else:
        target = Path(path_value).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as fp:
            json.dump(raw_config, fp, ensure_ascii=False, indent=2)
        saved_path = str(target)
        snapshot = {
            "path": saved_path,
            "channels": [item.get("id") for item in raw_config.get("channels", []) if isinstance(item, dict)],
            "api_keys": [item.get("name") for item in raw_config.get("api_keys", []) if isinstance(item, dict)],
        }

    return jsonify(
        {
            "ok": True,
            "path": saved_path,
            "settingsPath": str(_settings_path()),
            "summary": snapshot,
        }
    )


@dashboard_bp.get("/api/admin/users")
def dashboard_admin_users():
    manager = _runtime_control_plane_manager()
    if manager is None:
        return make_response(jsonify({"ok": False, "error": "control plane manager unavailable"}), 400)
    users = manager.list_users()
    return jsonify({"ok": True, "dbPath": manager.db_path, "count": len(users), "users": users})


@dashboard_bp.post("/api/admin/users")
def dashboard_admin_save_user():
    manager = _runtime_control_plane_manager()
    if manager is None:
        return make_response(jsonify({"ok": False, "error": "control plane manager unavailable"}), 400)
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return make_response(jsonify({"ok": False, "error": "invalid JSON payload"}), 400)
    try:
        user = manager.save_user(payload)
        _invalidate_gateway_manager()
        return jsonify({"ok": True, "dbPath": manager.db_path, "user": user})
    except ValueError as exc:
        return make_response(jsonify({"ok": False, "error": str(exc)}), 400)
    except Exception as exc:
        return make_response(jsonify({"ok": False, "error": str(exc)}), 500)


@dashboard_bp.delete("/api/admin/users/<int:user_id>")
def dashboard_admin_delete_user(user_id: int):
    manager = _runtime_control_plane_manager()
    if manager is None:
        return make_response(jsonify({"ok": False, "error": "control plane manager unavailable"}), 400)
    try:
        manager.delete_user(user_id)
        _invalidate_gateway_manager()
        return jsonify({"ok": True, "dbPath": manager.db_path, "deletedUserId": int(user_id)})
    except ValueError as exc:
        return make_response(jsonify({"ok": False, "error": str(exc)}), 400)
    except Exception as exc:
        return make_response(jsonify({"ok": False, "error": str(exc)}), 500)


@dashboard_bp.get("/api/admin/keys")
def dashboard_admin_keys():
    manager = _runtime_control_plane_manager()
    if manager is None:
        return make_response(jsonify({"ok": False, "error": "control plane manager unavailable"}), 400)
    keys = manager.list_api_keys()
    return jsonify({"ok": True, "dbPath": manager.db_path, "count": len(keys), "keys": keys})


@dashboard_bp.post("/api/admin/keys")
def dashboard_admin_create_key():
    manager = _runtime_control_plane_manager()
    if manager is None:
        return make_response(jsonify({"ok": False, "error": "control plane manager unavailable"}), 400)
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return make_response(jsonify({"ok": False, "error": "invalid JSON payload"}), 400)
    try:
        item = manager.create_api_key(payload)
        _invalidate_gateway_manager()
        return jsonify({"ok": True, "dbPath": manager.db_path, "apiKey": item})
    except ValueError as exc:
        return make_response(jsonify({"ok": False, "error": str(exc)}), 400)
    except Exception as exc:
        return make_response(jsonify({"ok": False, "error": str(exc)}), 500)


@dashboard_bp.post("/api/admin/keys/<int:key_id>")
def dashboard_admin_update_key(key_id: int):
    manager = _runtime_control_plane_manager()
    if manager is None:
        return make_response(jsonify({"ok": False, "error": "control plane manager unavailable"}), 400)
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return make_response(jsonify({"ok": False, "error": "invalid JSON payload"}), 400)
    try:
        item = manager.update_api_key(key_id, payload)
        _invalidate_gateway_manager()
        return jsonify({"ok": True, "dbPath": manager.db_path, "apiKey": item})
    except ValueError as exc:
        return make_response(jsonify({"ok": False, "error": str(exc)}), 400)
    except Exception as exc:
        return make_response(jsonify({"ok": False, "error": str(exc)}), 500)


@dashboard_bp.delete("/api/admin/keys/<int:key_id>")
def dashboard_admin_delete_key(key_id: int):
    manager = _runtime_control_plane_manager()
    if manager is None:
        return make_response(jsonify({"ok": False, "error": "control plane manager unavailable"}), 400)
    try:
        manager.delete_api_key(key_id)
        _invalidate_gateway_manager()
        return jsonify({"ok": True, "dbPath": manager.db_path, "deletedApiKeyId": int(key_id)})
    except ValueError as exc:
        return make_response(jsonify({"ok": False, "error": str(exc)}), 400)
    except Exception as exc:
        return make_response(jsonify({"ok": False, "error": str(exc)}), 500)


@dashboard_bp.get("/api/admin/usage")
def dashboard_admin_usage():
    manager = _runtime_control_plane_manager()
    if manager is None:
        return make_response(jsonify({"ok": False, "error": "control plane manager unavailable"}), 400)
    try:
        limit = int(request.args.get("limit", "200"))
    except Exception:
        limit = 200
    usage = manager.usage_summary(limit=limit)
    return jsonify({"ok": True, "dbPath": manager.db_path, **usage})


@dashboard_bp.get("/api/logs")
def dashboard_logs():
    raw_lines = request.args.get("lines", "180")
    try:
        lines = int(raw_lines)
    except Exception:
        lines = 180
    lines = max(20, min(lines, 1000))
    log_path = _default_log_path()
    text = _read_log_tail(log_path, lines)
    manager = _runtime_codex_manager()
    if manager is not None:
        manager_logs = manager.tail_logs(lines=lines)
        if manager_logs:
            combined = [f"[chatmock log] {log_path}", text, "", "[codex app-server]", manager_logs]
            text = "\n".join(part for part in combined if isinstance(part, str) and part)
    return jsonify({"lines": lines, "logPath": log_path, "text": text})


@dashboard_bp.post("/api/actions/sync")
def dashboard_action_sync():
    health = dashboard_health().get_json()
    return jsonify({"ok": True, "stdout": "sync not required for ChatMock", "stderr": "", "health": health})


@dashboard_bp.post("/api/actions/service")
def dashboard_action_service():
    action = str((request.get_json(silent=True) or {}).get("action") or "").strip().lower()
    if action not in ("start", "stop", "restart"):
        return make_response(jsonify({"error": "action must be one of start|stop|restart"}), 400)
    manager = _runtime_codex_manager()
    if manager is None:
        return make_response(jsonify({"ok": False, "error": "codex app-server manager is unavailable"}), 400)

    try:
        result = getattr(manager, action)()
        health = dashboard_health().get_json()
        return jsonify(
            {
                "ok": bool(result.get("ok")),
                "action": action,
                "manager": "codex-app-server",
                "stdout": result.get("message", ""),
                "stderr": result.get("error", ""),
                "status": result.get("status"),
                "health": health,
            }
        )
    except Exception as exc:
        return make_response(jsonify({"ok": False, "error": str(exc)}), 500)


@dashboard_bp.post("/api/actions/upload_auths")
def dashboard_action_upload_auths():
    if not _bool_env("CHATMOCK_DASHBOARD_ALLOW_UPLOAD", default=True):
        return make_response(jsonify({"ok": False, "error": "upload is disabled by server config"}), 403)

    replace = str(request.form.get("replace", "0")).strip().lower() in ("1", "true", "yes", "on")
    incoming = request.files.getlist("files")
    if not incoming:
        return make_response(jsonify({"ok": False, "error": "no files uploaded"}), 400)

    auth_root = _auth_storage_root()
    written: List[str] = []
    errors: List[str] = []
    upload_results: List[Dict[str, Any]] = []
    primary_payload: Dict[str, Any] | None = None

    existing_files = [] if replace else _merge_auth_files(
        _current_auth_files(),
        _discover_auth_files(auth_root),
        replace=False,
    )
    used_labels: set[str] = set()
    fingerprint_to_path: Dict[str, str] = {}

    for existing in existing_files:
        existing_path = Path(existing)
        parent_label = existing_path.parent.name.strip().lower()
        if parent_label:
            used_labels.add(parent_label)
        payload = _read_auth_payload(existing_path)
        if isinstance(payload, dict):
            fingerprint_to_path[_auth_payload_fingerprint(payload)] = str(existing_path)

    for storage in incoming:
        try:
            data = storage.read()
            payload = json.loads(data.decode("utf-8-sig"))
            if not isinstance(payload, dict):
                raise ValueError("JSON root must be an object")

            account_id = _extract_account_id(payload)
            fingerprint = _auth_payload_fingerprint(payload)
            target: Optional[Path] = None
            action = "created"
            previous_path = ""

            if not replace and fingerprint in fingerprint_to_path:
                target = Path(fingerprint_to_path[fingerprint])
                action = "updated"
                previous_path = str(target)
            else:
                label = _next_acc_label(used_labels)
                used_labels.add(label)
                target = auth_root / label / "auth.json"
                fingerprint_to_path[fingerprint] = str(target)

            _write_auth_payload(target, payload)
            written.append(str(target))
            upload_results.append(
                {
                    "filename": storage.filename or "unknown",
                    "accountId": account_id,
                    "action": action,
                    "target": str(target),
                    "previousTarget": previous_path,
                }
            )
            if primary_payload is None:
                primary_payload = payload
        except Exception as exc:
            errors.append(f"{storage.filename or 'unknown'}: {exc}")

    if not written:
        return make_response(jsonify({"ok": False, "error": "all files failed", "details": errors}), 400)

    merged = _merge_auth_files(existing_files, written, replace=replace)
    saved = _apply_settings(
        {
            "authFiles": merged,
            "uploadReplaceDefault": replace,
        },
        persist=True,
    )
    if primary_payload is not None:
        write_auth_file(primary_payload)

    service_result: Dict[str, Any] | None = None
    manager = _runtime_codex_manager()
    if manager is not None:
        try:
            service_result = manager.sync_from_auth_files(saved["authFiles"], restart=True)
        except Exception as exc:
            service_result = {"ok": False, "error": str(exc), "status": _service_status()}

    records = get_chatgpt_auth_records()
    return jsonify(
        {
            "ok": True,
            "uploaded": len(written),
            "written": written,
            "results": upload_results,
            "created": sum(1 for item in upload_results if item.get("action") == "created"),
            "updated": sum(1 for item in upload_results if item.get("action") == "updated"),
            "replace": replace,
            "auth_files": os.environ.get("CHATGPT_LOCAL_AUTH_FILES", ""),
            "accounts_count": len(records),
            "errors": errors,
            "settingsPath": str(_settings_path()),
            "savedSettings": saved,
            "service": service_result,
        }
    )
