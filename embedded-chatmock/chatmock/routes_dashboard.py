from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Blueprint, current_app, jsonify, make_response, request, send_from_directory

from .utils import (
    get_chatgpt_auth_records,
    get_max_retry_interval_seconds,
    parse_jwt_claims,
    get_request_retry_limit,
)


dashboard_bp = Blueprint("dashboard", __name__)

_DASHBOARD_DIR = Path(__file__).resolve().parent / "dashboard"
_VALID_ROUTING_STRATEGIES = {"round-robin", "random", "first"}
_VALID_REASONING_EFFORT = {"minimal", "low", "medium", "high", "xhigh"}
_VALID_REASONING_SUMMARY = {"auto", "concise", "detailed", "none"}
_VALID_REASONING_COMPAT = {"legacy", "o3", "think-tags", "current"}


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


def _settings_path() -> Path:
    explicit = (os.getenv("CHATMOCK_DASHBOARD_SETTINGS_PATH") or "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    return _auth_storage_root() / "_dashboard_settings.json"


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
        "authFiles": _current_auth_files(),
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

    id_token = tokens.get("id_token") if isinstance(tokens.get("id_token"), str) else ""
    if not id_token and isinstance(payload.get("id_token"), str):
        id_token = payload.get("id_token") or ""
    if id_token:
        claims = parse_jwt_claims(id_token)
        if isinstance(claims, dict):
            sub = claims.get("sub")
            if isinstance(sub, str) and sub.strip():
                return sub.strip()
    return ""


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
    service_name = (os.getenv("CHATMOCK_SERVICE_NAME") or "").strip()
    if not service_name:
        return {"name": "", "status": "running", "raw": "running in foreground/no service configured"}
    try:
        completed = subprocess.run(
            ["systemctl", "is-active", service_name],
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
        )
        status = (completed.stdout or completed.stderr or "unknown").strip()
        if not status:
            status = "unknown"
        return {"name": service_name, "status": status, "raw": status}
    except Exception as exc:
        return {"name": service_name, "status": "error", "raw": str(exc)}


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
    models = _model_ids(bool(current_app.config.get("EXPOSE_REASONING_MODELS")))
    payload = {
        "now": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "service": _service_status(),
        "listening": True,
        "models": {"count": len(models), "ids": models, "error": ""},
        "accounts": {"count": len(records)},
        "routing": {
            "strategy": (os.getenv("CHATGPT_LOCAL_ROUTING_STRATEGY") or "round-robin"),
            "request_retry": get_request_retry_limit(),
            "max_retry_interval": get_max_retry_interval_seconds(),
        },
    }
    return jsonify(payload)


@dashboard_bp.get("/api/accounts")
def dashboard_accounts():
    records = get_chatgpt_auth_records()
    return jsonify({"count": len(records), "accounts": records})


@dashboard_bp.get("/api/models")
def dashboard_models():
    ids = _model_ids(bool(current_app.config.get("EXPOSE_REASONING_MODELS")))
    return jsonify({"count": len(ids), "ids": ids})


@dashboard_bp.get("/api/config")
def dashboard_config():
    settings = _current_settings_snapshot()
    local = {
        "CHATGPT_LOCAL_HOME": os.getenv("CHATGPT_LOCAL_HOME", ""),
        "CHATGPT_LOCAL_AUTH_FILES": os.getenv("CHATGPT_LOCAL_AUTH_FILES", ""),
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

    service_name = (os.getenv("CHATMOCK_SERVICE_NAME") or "").strip()
    if not service_name:
        return make_response(
            jsonify(
                {
                    "ok": False,
                    "error": "CHATMOCK_SERVICE_NAME is not set; service action unavailable.",
                }
            ),
            400,
        )

    try:
        completed = subprocess.run(
            ["systemctl", action, service_name],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        health = dashboard_health().get_json()
        return jsonify(
            {
                "ok": completed.returncode == 0,
                "action": action,
                "manager": "systemd",
                "service": service_name,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
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

    existing_files = [] if replace else _current_auth_files()
    used_labels: set[str] = set()

    for existing in existing_files:
        existing_path = Path(existing)
        parent_label = existing_path.parent.name.strip().lower()
        if parent_label:
            used_labels.add(parent_label)

    for storage in incoming:
        try:
            data = storage.read()
            payload = json.loads(data.decode("utf-8-sig"))
            if not isinstance(payload, dict):
                raise ValueError("JSON root must be an object")

            label = _next_acc_label(used_labels)
            used_labels.add(label)
            target: Optional[Path] = auth_root / label / "auth.json"

            _write_auth_payload(target, payload)
            written.append(str(target))
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

    records = get_chatgpt_auth_records()
    return jsonify(
        {
            "ok": True,
            "uploaded": len(written),
            "written": written,
            "replace": replace,
            "auth_files": os.environ.get("CHATGPT_LOCAL_AUTH_FILES", ""),
            "accounts_count": len(records),
            "errors": errors,
            "settingsPath": str(_settings_path()),
            "savedSettings": saved,
        }
    )
