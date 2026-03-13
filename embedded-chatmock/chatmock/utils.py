from __future__ import annotations

import base64
import datetime
import hashlib
import json
import os
import secrets
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from .config import CLIENT_ID_DEFAULT, OAUTH_TOKEN_URL
from .upstream_errors import classify_error, error_info_from_event_response, normalized_error_payload, should_retry_next_candidate


_AUTH_POOL_RR_LOCK = threading.Lock()
_AUTH_POOL_RR_INDEX = 0
_AUTH_POOL_STATE_LOCK = threading.RLock()
_AUTH_POOL_STATE: Dict[str, Dict[str, Any]] = {}
_INVALID_AUTH_LOCK = threading.RLock()
_INVALID_AUTH_LABELS: set[str] = set()
_INVALID_AUTH_ACCOUNT_IDS: set[str] = set()
_AUTH_ACCOUNT_COOLDOWN_LOCK = threading.RLock()
_AUTH_ACCOUNT_COOLDOWN_UNTIL: Dict[str, float] = {}


def eprint(*args, **kwargs) -> None:
    print(*args, file=sys.stderr, **kwargs)


class RetryableStreamError(RuntimeError):
    def __init__(self, error_info: Dict[str, Any]) -> None:
        self.error_info = error_info
        super().__init__(str((error_info or {}).get("raw_message") or "retryable stream failure"))


def get_home_dir() -> str:
    home = os.getenv("CHATGPT_LOCAL_HOME") or os.getenv("CODEX_HOME")
    if not home:
        home = os.path.expanduser("~/.chatgpt-local")
    return home


def _candidate_auth_bases() -> List[str]:
    bases: List[str] = []
    explicit_bases = [
        os.getenv("CHATGPT_LOCAL_HOME"),
        os.getenv("CODEX_HOME"),
    ]
    if any(isinstance(base, str) and base for base in explicit_bases):
        source_bases = explicit_bases
    else:
        source_bases = [
            os.path.expanduser("~/.chatgpt-local"),
            os.path.expanduser("~/.codex"),
        ]
    for base in source_bases:
        if not isinstance(base, str) or not base:
            continue
        if base not in bases:
            bases.append(base)
    return bases


def _read_json_file(path: str) -> Dict[str, Any] | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else None
    except FileNotFoundError:
        return None
    except Exception:
        return None


def _write_json_file(path: str, payload: Any) -> bool:
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
    except Exception as exc:
        eprint(f"ERROR: unable to create directory for {path}: {exc}")
        return False
    try:
        with open(path, "w", encoding="utf-8") as fp:
            if hasattr(os, "fchmod"):
                os.fchmod(fp.fileno(), 0o600)
            json.dump(payload, fp, indent=2)
        return True
    except Exception as exc:
        eprint(f"ERROR: unable to write JSON file {path}: {exc}")
        return False


def _delete_file(path: str) -> bool:
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return True
    except Exception as exc:
        eprint(f"ERROR: unable to delete file {path}: {exc}")
        return False


def _read_raw_json_file(path: str) -> Dict[str, Any] | List[Any] | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, (dict, list)) else None
    except FileNotFoundError:
        return None
    except Exception:
        return None


def _find_auth_file_path(filename: str) -> str | None:
    for base in _candidate_auth_bases():
        path = os.path.join(base, filename)
        if os.path.exists(path):
            return path
    return None


def read_auth_file() -> Dict[str, Any] | None:
    path = _find_auth_file_path("auth.json")
    if not path:
        return None
    return _read_json_file(path)


def write_auth_file(auth: Dict[str, Any]) -> bool:
    home = get_home_dir()
    path = os.path.join(home, "auth.json")
    return _write_json_file(path, auth)


def parse_jwt_claims(token: str) -> Dict[str, Any] | None:
    if not token or token.count(".") != 2:
        return None
    try:
        _, payload, _ = token.split(".")
        padded = payload + "=" * (-len(payload) % 4)
        data = base64.urlsafe_b64decode(padded.encode())
        return json.loads(data.decode())
    except Exception:
        return None


def generate_pkce() -> "PkceCodes":
    from .models import PkceCodes

    code_verifier = secrets.token_hex(64)
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return PkceCodes(code_verifier=code_verifier, code_challenge=code_challenge)


def convert_chat_messages_to_responses_input(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def _normalize_image_data_url(url: str) -> str:
        try:
            if not isinstance(url, str):
                return url
            if not url.startswith("data:image/"):
                return url
            if ";base64," not in url:
                return url
            header, data = url.split(",", 1)
            try:
                from urllib.parse import unquote

                data = unquote(data)
            except Exception:
                pass
            data = data.strip().replace("\n", "").replace("\r", "")
            data = data.replace("-", "+").replace("_", "/")
            pad = (-len(data)) % 4
            if pad:
                data = data + ("=" * pad)
            try:
                base64.b64decode(data, validate=True)
            except Exception:
                return url
            return f"{header},{data}"
        except Exception:
            return url

    input_items: List[Dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role == "system":
            continue

        if role == "tool":
            call_id = message.get("tool_call_id") or message.get("id")
            if isinstance(call_id, str) and call_id:
                content = message.get("content", "")
                if isinstance(content, list):
                    texts = []
                    for part in content:
                        if isinstance(part, dict):
                            t = part.get("text") or part.get("content")
                            if isinstance(t, str) and t:
                                texts.append(t)
                    content = "\n".join(texts)
                if isinstance(content, str):
                    input_items.append(
                        {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": content,
                        }
                    )
            continue
        if role == "assistant" and isinstance(message.get("tool_calls"), list):
            for tc in message.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                tc_type = tc.get("type", "function")
                if tc_type != "function":
                    continue
                call_id = tc.get("id") or tc.get("call_id")
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                name = fn.get("name") if isinstance(fn, dict) else None
                args = fn.get("arguments") if isinstance(fn, dict) else None
                if isinstance(call_id, str) and isinstance(name, str) and isinstance(args, str):
                    input_items.append(
                        {
                            "type": "function_call",
                            "name": name,
                            "arguments": args,
                            "call_id": call_id,
                        }
                    )

        content = message.get("content", "")
        content_items: List[Dict[str, Any]] = []
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                ptype = part.get("type")
                if ptype == "text":
                    text = part.get("text") or part.get("content") or ""
                    if isinstance(text, str) and text:
                        kind = "output_text" if role == "assistant" else "input_text"
                        content_items.append({"type": kind, "text": text})
                elif ptype == "image_url":
                    image = part.get("image_url")
                    url = image.get("url") if isinstance(image, dict) else image
                    if isinstance(url, str) and url:
                        content_items.append({"type": "input_image", "image_url": _normalize_image_data_url(url)})
        elif isinstance(content, str) and content:
            kind = "output_text" if role == "assistant" else "input_text"
            content_items.append({"type": kind, "text": content})

        if not content_items:
            continue
        role_out = "assistant" if role == "assistant" else "user"
        input_items.append({"type": "message", "role": role_out, "content": content_items})
    return input_items


def convert_tools_chat_to_responses(tools: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not isinstance(tools, list):
        return out
    for t in tools:
        if not isinstance(t, dict):
            continue
        if t.get("type") != "function":
            continue
        fn = t.get("function") if isinstance(t.get("function"), dict) else {}
        name = fn.get("name") if isinstance(fn, dict) else None
        if not isinstance(name, str) or not name:
            continue
        desc = fn.get("description") if isinstance(fn, dict) else None
        params = fn.get("parameters") if isinstance(fn, dict) else None
        if not isinstance(params, dict):
            params = {"type": "object", "properties": {}}
        out.append(
            {
                "type": "function",
                "name": name,
                "description": desc or "",
                "strict": False,
                "parameters": params,
            }
        )
    return out


def load_chatgpt_tokens(ensure_fresh: bool = True) -> tuple[str | None, str | None, str | None]:
    auth = read_auth_file()
    if not isinstance(auth, dict):
        return None, None, None

    tokens = auth.get("tokens") if isinstance(auth.get("tokens"), dict) else {}
    access_token: Optional[str] = tokens.get("access_token")
    account_id: Optional[str] = tokens.get("account_id")
    id_token: Optional[str] = tokens.get("id_token")
    refresh_token: Optional[str] = tokens.get("refresh_token")
    last_refresh = auth.get("last_refresh")

    if ensure_fresh and isinstance(refresh_token, str) and refresh_token and CLIENT_ID_DEFAULT:
        needs_refresh = _should_refresh_access_token(access_token, last_refresh)
        if needs_refresh or not (isinstance(access_token, str) and access_token):
            refreshed = _refresh_chatgpt_tokens(refresh_token, CLIENT_ID_DEFAULT)
            if refreshed:
                access_token = refreshed.get("access_token") or access_token
                id_token = refreshed.get("id_token") or id_token
                refresh_token = refreshed.get("refresh_token") or refresh_token
                account_id = refreshed.get("account_id") or account_id

                updated_tokens = dict(tokens)
                if isinstance(access_token, str) and access_token:
                    updated_tokens["access_token"] = access_token
                if isinstance(id_token, str) and id_token:
                    updated_tokens["id_token"] = id_token
                if isinstance(refresh_token, str) and refresh_token:
                    updated_tokens["refresh_token"] = refresh_token
                if isinstance(account_id, str) and account_id:
                    updated_tokens["account_id"] = account_id

                persisted = _persist_refreshed_auth(auth, updated_tokens)
                if persisted is not None:
                    auth, tokens = persisted
                else:
                    tokens = updated_tokens

    if not isinstance(account_id, str) or not account_id:
        account_id = _derive_account_id(id_token)

    access_token = access_token if isinstance(access_token, str) and access_token else None
    id_token = id_token if isinstance(id_token, str) and id_token else None
    account_id = account_id if isinstance(account_id, str) and account_id else None
    return access_token, account_id, id_token


def _extract_tokens_from_auth_obj(auth_obj: Dict[str, Any]) -> tuple[str | None, str | None, str | None, str | None, Any]:
    tokens = auth_obj.get("tokens") if isinstance(auth_obj.get("tokens"), dict) else {}
    source = tokens if isinstance(tokens, dict) and tokens else auth_obj
    access_token = source.get("access_token") if isinstance(source.get("access_token"), str) else None
    account_id = source.get("account_id") if isinstance(source.get("account_id"), str) else None
    id_token = source.get("id_token") if isinstance(source.get("id_token"), str) else None
    refresh_token = source.get("refresh_token") if isinstance(source.get("refresh_token"), str) else None
    last_refresh = auth_obj.get("last_refresh")
    return access_token, account_id, id_token, refresh_token, last_refresh


def _should_refresh_access_token(access_token: Optional[str], last_refresh: Any) -> bool:
    if not isinstance(access_token, str) or not access_token:
        return True

    claims = parse_jwt_claims(access_token) or {}
    exp = claims.get("exp") if isinstance(claims, dict) else None
    now = datetime.datetime.now(datetime.timezone.utc)
    if isinstance(exp, (int, float)):
        try:
            expiry = datetime.datetime.fromtimestamp(float(exp), datetime.timezone.utc)
        except (OverflowError, OSError, ValueError):
            expiry = None
        if expiry is not None:
            return expiry <= now + datetime.timedelta(minutes=5)

    if isinstance(last_refresh, str):
        refreshed_at = _parse_iso8601(last_refresh)
        if refreshed_at is not None:
            return refreshed_at <= now - datetime.timedelta(minutes=55)
    return False


def _refresh_chatgpt_tokens(refresh_token: str, client_id: str) -> Optional[Dict[str, Optional[str]]]:
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "scope": "openid profile email offline_access",
    }

    try:
        resp = requests.post(OAUTH_TOKEN_URL, json=payload, timeout=30)
    except requests.RequestException as exc:
        eprint(f"ERROR: failed to refresh ChatGPT token: {exc}")
        return None

    if resp.status_code >= 400:
        eprint(f"ERROR: refresh token request returned status {resp.status_code}")
        return None

    try:
        data = resp.json()
    except ValueError as exc:
        eprint(f"ERROR: unable to parse refresh token response: {exc}")
        return None

    id_token = data.get("id_token")
    access_token = data.get("access_token")
    new_refresh_token = data.get("refresh_token") or refresh_token
    if not isinstance(id_token, str) or not isinstance(access_token, str):
        eprint("ERROR: refresh token response missing expected tokens")
        return None

    account_id = _derive_account_id(id_token)
    new_refresh_token = new_refresh_token if isinstance(new_refresh_token, str) and new_refresh_token else refresh_token
    return {
        "id_token": id_token,
        "access_token": access_token,
        "refresh_token": new_refresh_token,
        "account_id": account_id,
    }


def _persist_refreshed_auth(auth: Dict[str, Any], updated_tokens: Dict[str, Any]) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    updated_auth = dict(auth)
    updated_auth["tokens"] = updated_tokens
    updated_auth["last_refresh"] = _now_iso8601()
    if write_auth_file(updated_auth):
        return updated_auth, updated_tokens
    eprint("ERROR: unable to persist refreshed auth tokens")
    return None


def _derive_account_id(id_token: Optional[str]) -> Optional[str]:
    if not isinstance(id_token, str) or not id_token:
        return None
    claims = parse_jwt_claims(id_token) or {}
    auth_claims = claims.get("https://api.openai.com/auth") if isinstance(claims, dict) else None
    if isinstance(auth_claims, dict):
        account_id = auth_claims.get("chatgpt_account_id")
        if isinstance(account_id, str) and account_id:
            return account_id
    return None


def _parse_iso8601(value: str) -> Optional[datetime.datetime]:
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        dt = datetime.datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc)
    except Exception:
        return None


def _now_iso8601() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _dashboard_settings_path() -> str | None:
    explicit = (os.getenv("CHATMOCK_DASHBOARD_SETTINGS_PATH") or "").strip()
    if explicit:
        return explicit
    data_dir = (os.getenv("CHATMOCK_DATA_DIR") or "").strip()
    if data_dir:
        return os.path.join(data_dir, "accounts", "_dashboard_settings.json")
    return None


def _load_dashboard_settings() -> Dict[str, Any] | None:
    path = _dashboard_settings_path()
    if not path:
        return None
    data = _read_raw_json_file(path)
    return data if isinstance(data, dict) else None


def _persist_dashboard_auth_files(paths: List[str]) -> bool:
    path = _dashboard_settings_path()
    if not path:
        return False
    payload = _load_dashboard_settings() or {}
    payload["authFiles"] = list(paths)
    payload["updatedAt"] = _now_iso8601()
    return _write_json_file(path, payload)


def _has_explicit_auth_files_config() -> bool:
    raw_flag = (os.getenv("CHATGPT_LOCAL_AUTH_FILES_CONFIGURED") or "").strip().lower()
    if raw_flag in ("1", "true", "yes", "on"):
        return True
    stored = _load_dashboard_settings()
    return isinstance(stored, dict) and "authFiles" in stored


def _remove_path_from_auth_files_env(path: str) -> List[str]:
    current = _parse_auth_files_env()
    updated = [item for item in current if item != path]
    if updated:
        os.environ["CHATGPT_LOCAL_AUTH_FILES"] = ",".join(updated)
    else:
        os.environ.pop("CHATGPT_LOCAL_AUTH_FILES", None)
    return updated


def _remove_label_state(label: str) -> None:
    with _AUTH_POOL_STATE_LOCK:
        _AUTH_POOL_STATE.pop(label, None)


def _mark_invalid_auth_candidate(*, label: str = "", account_id: str = "") -> None:
    with _INVALID_AUTH_LOCK:
        if isinstance(label, str) and label.strip():
            _INVALID_AUTH_LABELS.add(label.strip())
        if isinstance(account_id, str) and account_id.strip():
            _INVALID_AUTH_ACCOUNT_IDS.add(account_id.strip())


def _clear_invalid_auth_candidate(*, label: str = "", account_id: str = "") -> None:
    with _INVALID_AUTH_LOCK:
        if isinstance(label, str) and label.strip():
            _INVALID_AUTH_LABELS.discard(label.strip())
        if isinstance(account_id, str) and account_id.strip():
            _INVALID_AUTH_ACCOUNT_IDS.discard(account_id.strip())


def _is_invalid_auth_candidate(*, label: str = "", account_id: str = "") -> bool:
    with _INVALID_AUTH_LOCK:
        if isinstance(label, str) and label.strip() and label.strip() in _INVALID_AUTH_LABELS:
            return True
        if isinstance(account_id, str) and account_id.strip() and account_id.strip() in _INVALID_AUTH_ACCOUNT_IDS:
            return True
    return False


def _set_account_cooldown(*, account_id: str = "", until_ts: float = 0.0) -> None:
    if not isinstance(account_id, str) or not account_id.strip():
        return
    with _AUTH_ACCOUNT_COOLDOWN_LOCK:
        if until_ts > 0:
            _AUTH_ACCOUNT_COOLDOWN_UNTIL[account_id.strip()] = float(until_ts)
        else:
            _AUTH_ACCOUNT_COOLDOWN_UNTIL.pop(account_id.strip(), None)


def _get_account_cooldown(account_id: str) -> float:
    if not isinstance(account_id, str) or not account_id.strip():
        return 0.0
    with _AUTH_ACCOUNT_COOLDOWN_LOCK:
        until_ts = float(_AUTH_ACCOUNT_COOLDOWN_UNTIL.get(account_id.strip()) or 0.0)
    now = time.time()
    if until_ts <= now:
        _set_account_cooldown(account_id=account_id, until_ts=0.0)
        return 0.0
    return until_ts


def is_auth_candidate_blocked(candidate: Dict[str, Any]) -> bool:
    if not isinstance(candidate, dict):
        return True
    label = str(candidate.get("label") or "").strip()
    account_id = str(candidate.get("account_id") or "").strip()
    if _is_invalid_auth_candidate(label=label, account_id=account_id):
        return True
    cooldown_until = _get_account_cooldown(account_id)
    if cooldown_until > time.time():
        return True
    return False


def _label_for_auth_file_path(path: str) -> str:
    dirname = os.path.basename(os.path.dirname(path))
    filename = os.path.basename(path)
    return f"{dirname}/{filename}" if dirname else (filename or path)


def _account_id_from_auth_obj(auth_obj: Dict[str, Any]) -> str:
    access_token, account_id, id_token, _, _ = _extract_tokens_from_auth_obj(auth_obj)
    del access_token  # unused in this helper
    if not isinstance(account_id, str) or not account_id:
        account_id = _derive_account_id(id_token) or ""
    return str(account_id).strip()


def _remove_auth_from_pool_file(pool_path: str, index: int) -> bool:
    raw_pool = _read_raw_json_file(pool_path)
    if not isinstance(raw_pool, (dict, list)):
        return False
    removed = False
    if isinstance(raw_pool, list):
        if 0 <= index < len(raw_pool):
            raw_pool.pop(index)
            removed = True
    else:
        accounts = raw_pool.get("accounts")
        if isinstance(accounts, list) and 0 <= index < len(accounts):
            accounts.pop(index)
            raw_pool["accounts"] = accounts
            removed = True
    if not removed:
        return False
    return _write_json_file(pool_path, raw_pool)


def remove_chatgpt_auth_candidate(candidate: Dict[str, Any], *, reason: str = "") -> bool:
    if not isinstance(candidate, dict):
        return False
    label = str(candidate.get("label") or "").strip()
    source_kind = str(candidate.get("source_kind") or "").strip()
    source_path = str(candidate.get("source_path") or "").strip()
    source_index = candidate.get("source_index")
    account_id = str(candidate.get("account_id") or "").strip()

    success = False
    if source_kind in ("auth_file", "default_auth"):
        current_paths = _parse_auth_files_env()
        paths_to_remove: List[str] = []
        if source_path:
            paths_to_remove.append(source_path)
        if account_id:
            for path in current_paths:
                if path in paths_to_remove:
                    continue
                auth_obj = _read_json_file(path)
                if not isinstance(auth_obj, dict):
                    continue
                if _account_id_from_auth_obj(auth_obj) == account_id:
                    paths_to_remove.append(path)
        if paths_to_remove:
            updated = [item for item in current_paths if item not in paths_to_remove]
            if updated:
                os.environ["CHATGPT_LOCAL_AUTH_FILES"] = ",".join(updated)
            else:
                os.environ.pop("CHATGPT_LOCAL_AUTH_FILES", None)
            _persist_dashboard_auth_files(updated)
            for path in paths_to_remove:
                if _delete_file(path):
                    success = True
                _remove_label_state(_label_for_auth_file_path(path))
    elif source_kind == "auth_pool":
        if source_path and isinstance(source_index, int):
            success = _remove_auth_from_pool_file(source_path, source_index)

    if success and label:
        _remove_label_state(label)
        if reason:
            eprint(f"INFO: removed ChatGPT auth candidate {label}: {reason}")
        else:
            eprint(f"INFO: removed ChatGPT auth candidate {label}")
    return success


def get_effective_chatgpt_auth() -> tuple[str | None, str | None]:
    access_token, account_id, id_token = load_chatgpt_tokens()
    if not account_id:
        account_id = _derive_account_id(id_token)
    return access_token, account_id


def _candidate_from_auth_obj(
    auth_obj: Dict[str, Any],
    *,
    label: str,
    ensure_fresh: bool,
    source_kind: str | None = None,
    source_path: str | None = None,
    source_index: int | None = None,
) -> tuple[Dict[str, Any] | None, bool]:
    access_token, account_id, id_token, refresh_token, last_refresh = _extract_tokens_from_auth_obj(auth_obj)
    changed = False
    refreshed = False

    if ensure_fresh and isinstance(refresh_token, str) and refresh_token and CLIENT_ID_DEFAULT:
        needs_refresh = _should_refresh_access_token(access_token, last_refresh)
        if needs_refresh or not (isinstance(access_token, str) and access_token):
            updated = _refresh_chatgpt_tokens(refresh_token, CLIENT_ID_DEFAULT)
            if updated:
                access_token = updated.get("access_token") or access_token
                id_token = updated.get("id_token") or id_token
                refresh_token = updated.get("refresh_token") or refresh_token
                account_id = updated.get("account_id") or account_id
                refreshed = True
                changed = True

    if not isinstance(account_id, str) or not account_id:
        derived = _derive_account_id(id_token)
        if isinstance(derived, str) and derived:
            account_id = derived
            changed = True

    if changed:
        tokens = auth_obj.get("tokens") if isinstance(auth_obj.get("tokens"), dict) else {}
        updated_tokens = dict(tokens) if isinstance(tokens, dict) else {}
        if isinstance(access_token, str) and access_token:
            updated_tokens["access_token"] = access_token
        if isinstance(id_token, str) and id_token:
            updated_tokens["id_token"] = id_token
        if isinstance(refresh_token, str) and refresh_token:
            updated_tokens["refresh_token"] = refresh_token
        if isinstance(account_id, str) and account_id:
            updated_tokens["account_id"] = account_id
        auth_obj["tokens"] = updated_tokens
        if refreshed:
            auth_obj["last_refresh"] = _now_iso8601()

    if not (isinstance(access_token, str) and access_token):
        return None, changed
    if not (isinstance(account_id, str) and account_id):
        return None, changed
    return {
        "label": label,
        "access_token": access_token,
        "account_id": account_id,
        "source_kind": source_kind or "",
        "source_path": source_path or "",
        "source_index": source_index,
    }, changed


def _load_auth_candidates_from_auth_files(ensure_fresh: bool = True) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    paths = _parse_auth_files_env()
    for idx, path in enumerate(paths):
        auth_obj = _read_json_file(path)
        if not isinstance(auth_obj, dict):
            eprint(f"WARNING: skipped invalid auth file: {path}")
            continue
        dirname = os.path.basename(os.path.dirname(path))
        filename = os.path.basename(path)
        label = f"{dirname}/{filename}" if dirname else (filename or f"file-{idx + 1}")
        account_id = _account_id_from_auth_obj(auth_obj)
        if _is_invalid_auth_candidate(label=label, account_id=account_id):
            continue
        candidate, changed = _candidate_from_auth_obj(
            auth_obj,
            label=label,
            ensure_fresh=ensure_fresh,
            source_kind="auth_file",
            source_path=path,
        )
        if changed:
            _write_json_file(path, auth_obj)
        if candidate is not None:
            out.append(candidate)
    return out


def _load_auth_candidates_from_pool_file(ensure_fresh: bool = True) -> List[Dict[str, Any]]:
    path = _find_auth_file_path("auth_pool.json")
    if not path:
        return []
    raw_pool = _read_raw_json_file(path)
    if not isinstance(raw_pool, (dict, list)):
        return []
    accounts = _extract_pool_accounts(raw_pool)
    if not accounts:
        return []

    changed = False
    out: List[Dict[str, Any]] = []
    for idx, account_obj in enumerate(accounts):
        label = ""
        for key in ("name", "alias", "label"):
            value = account_obj.get(key)
            if isinstance(value, str) and value.strip():
                label = value.strip()
                break
        if not label:
            label = f"pool-{idx + 1}"
        account_id = _account_id_from_auth_obj(account_obj)
        if _is_invalid_auth_candidate(label=label, account_id=account_id):
            continue
        candidate, account_changed = _candidate_from_auth_obj(
            account_obj,
            label=label,
            ensure_fresh=ensure_fresh,
            source_kind="auth_pool",
            source_path=path,
            source_index=idx,
        )
        changed = changed or account_changed
        if candidate is not None:
            out.append(candidate)

    if changed:
        _write_json_file(path, raw_pool)
    return out


def _dedupe_candidates_by_account_id(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    seen_account_ids: set[str] = set()
    seen_labels: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        label = str(candidate.get("label") or "").strip()
        account_id = str(candidate.get("account_id") or "").strip()
        dedupe_key = account_id or label
        if not dedupe_key:
            continue
        if dedupe_key in seen_account_ids or label in seen_labels:
            continue
        seen_account_ids.add(dedupe_key)
        if label:
            seen_labels.add(label)
        deduped.append(candidate)
    return deduped


def get_effective_chatgpt_auth_candidates(ensure_fresh: bool = True) -> List[Dict[str, Any]]:
    candidates = _load_auth_candidates_from_auth_files(ensure_fresh=ensure_fresh)
    if not candidates and not _has_explicit_auth_files_config():
        candidates = _load_auth_candidates_from_pool_file(ensure_fresh=ensure_fresh)
    if not candidates and not _has_explicit_auth_files_config():
        access_token, account_id, id_token = load_chatgpt_tokens(ensure_fresh=ensure_fresh)
        if not account_id:
            account_id = _derive_account_id(id_token)
        if (
            isinstance(access_token, str)
            and access_token
            and isinstance(account_id, str)
            and account_id
            and not _is_invalid_auth_candidate(label="default", account_id=account_id)
        ):
            candidates = [{
                "label": "default",
                "access_token": access_token,
                "account_id": account_id,
                "source_kind": "default_auth",
                "source_path": _find_auth_file_path("auth.json") or "auth.json",
                "source_index": None,
            }]
    candidates = _dedupe_candidates_by_account_id(candidates)
    candidates = _apply_account_cooldown(candidates)
    return _ordered_candidates_by_strategy(candidates)


def _ordered_candidates_round_robin(candidates: List[Dict[str, str]]) -> List[Dict[str, str]]:
    if len(candidates) <= 1:
        return candidates
    global _AUTH_POOL_RR_INDEX
    with _AUTH_POOL_RR_LOCK:
        start = _AUTH_POOL_RR_INDEX % len(candidates)
        _AUTH_POOL_RR_INDEX = (_AUTH_POOL_RR_INDEX + 1) % len(candidates)
    if start == 0:
        return candidates
    return candidates[start:] + candidates[:start]


def _routing_strategy() -> str:
    raw = (os.getenv("CHATGPT_LOCAL_ROUTING_STRATEGY") or "round-robin").strip().lower()
    if raw in ("round-robin", "rr"):
        return "round-robin"
    if raw in ("random", "rand"):
        return "random"
    return "first"


def _ordered_candidates_by_strategy(candidates: List[Dict[str, str]]) -> List[Dict[str, str]]:
    if len(candidates) <= 1:
        return candidates
    strategy = _routing_strategy()
    if strategy == "round-robin":
        return _ordered_candidates_round_robin(candidates)
    if strategy == "random":
        out = list(candidates)
        for i in range(len(out) - 1, 0, -1):
            j = secrets.randbelow(i + 1)
            out[i], out[j] = out[j], out[i]
        return out
    return candidates


def get_request_retry_limit() -> int:
    raw = (os.getenv("CHATGPT_LOCAL_REQUEST_RETRY") or "2").strip()
    try:
        value = int(raw)
    except Exception:
        value = 2
    return max(0, min(10, value))


def get_max_retry_interval_seconds() -> int:
    raw = (os.getenv("CHATGPT_LOCAL_MAX_RETRY_INTERVAL") or "5").strip()
    try:
        value = int(raw)
    except Exception:
        value = 5
    return max(1, min(300, value))


def get_retryable_statuses() -> set[int]:
    return {401, 403, 429, 500, 502, 503, 504}


def _get_cooldown_until(label: str) -> float:
    with _AUTH_POOL_STATE_LOCK:
        state = _AUTH_POOL_STATE.get(label) or {}
        try:
            return float(state.get("cooldown_until") or 0.0)
        except Exception:
            return 0.0


def _set_auth_pool_state(
    label: str,
    *,
    status: str,
    cooldown_until: float,
    failures: int,
    last_status: int | None,
    last_error: str,
    classification: str,
    raw_code: str | None = None,
    raw_message: str | None = None,
) -> None:
    with _AUTH_POOL_STATE_LOCK:
        state = dict(_AUTH_POOL_STATE.get(label) or {})
        state["status"] = status
        state["cooldown_until"] = cooldown_until
        state["failures"] = failures
        state["last_status"] = last_status
        state["last_error"] = last_error
        state["last_classification"] = classification
        state["last_raw_code"] = raw_code or ""
        state["last_raw_message"] = raw_message or last_error or ""
        state["updated_at"] = time.time()
        _AUTH_POOL_STATE[label] = state


def _apply_account_cooldown(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    now = time.time()
    available: List[Dict[str, Any]] = []
    for candidate in candidates:
        label = candidate.get("label") or ""
        cooldown_until = _get_cooldown_until(label)
        if cooldown_until <= now:
            available.append(candidate)
    if available:
        return available
    return []


def mark_chatgpt_auth_result(
    label: str,
    *,
    success: bool,
    status_code: int | None = None,
    account_id: str | None = None,
    error_message: str | None = None,
    classification: str | None = None,
    cooldown_seconds: int | None = None,
    raw_code: str | None = None,
    raw_message: str | None = None,
) -> None:
    if not isinstance(label, str) or not label:
        return
    now = time.time()
    max_retry_interval = get_max_retry_interval_seconds()
    with _AUTH_POOL_STATE_LOCK:
        state = dict(_AUTH_POOL_STATE.get(label) or {})
        if success:
            _clear_invalid_auth_candidate(label=label)
            _set_account_cooldown(account_id=account_id or "", until_ts=0.0)
            _set_auth_pool_state(
                label,
                status="ready",
                cooldown_until=0.0,
                failures=0,
                last_status=status_code if isinstance(status_code, int) else 200,
                last_error="",
                classification="ready",
                raw_code=raw_code,
                raw_message=raw_message,
            )
            return

        failures = int(state.get("failures") or 0) + 1
        category = (classification or "").strip() or "generic_failure"
        if isinstance(cooldown_seconds, int) and cooldown_seconds > 0:
            cooldown = cooldown_seconds
            state_status = "cooldown_insufficient_balance" if category == "insufficient_balance" else "temporary_failure"
        elif isinstance(status_code, int) and status_code in (401, 403):
            base = 5
            cooldown = min(max_retry_interval, base * (2 ** max(0, failures - 1)))
            state_status = "temporary_failure"
        elif isinstance(status_code, int) and status_code == 429:
            base = 2
            cooldown = min(max_retry_interval, base * (2 ** max(0, failures - 1)))
            state_status = "temporary_failure"
        else:
            base = 1
            cooldown = min(max_retry_interval, base * (2 ** max(0, failures - 1)))
            state_status = "temporary_failure"
        _set_auth_pool_state(
            label,
            status=state_status,
            cooldown_until=now + float(cooldown),
            failures=failures,
            last_status=status_code,
            last_error=error_message or "",
            classification=category,
            raw_code=raw_code,
            raw_message=raw_message,
        )


def handle_chatgpt_candidate_failure(candidate: Dict[str, Any], info: Dict[str, Any]) -> str:
    label = str(candidate.get("label") or "").strip()
    account_id = str(candidate.get("account_id") or "").strip()
    classification = classify_error(info)
    raw_status = info.get("raw_status") if isinstance(info.get("raw_status"), int) else None
    raw_code = info.get("raw_code") if isinstance(info.get("raw_code"), str) else None
    raw_message = info.get("raw_message") if isinstance(info.get("raw_message"), str) else None

    if classification in ("insufficient_balance", "rate_limited"):
        cooldown_until = time.time() + float(5 * 60 * 60)
        _set_account_cooldown(account_id=account_id, until_ts=cooldown_until)
        mark_chatgpt_auth_result(
            label,
            success=False,
            status_code=raw_status,
            account_id=account_id,
            error_message=raw_message,
            classification=classification,
            cooldown_seconds=5 * 60 * 60,
            raw_code=raw_code,
            raw_message=raw_message,
        )
        return classification

    if classification == "account_invalid":
        _mark_invalid_auth_candidate(label=label, account_id=account_id)
        remove_chatgpt_auth_candidate(candidate, reason=raw_message or "Account invalid")
        return classification

    mark_chatgpt_auth_result(
        label,
        success=False,
        status_code=raw_status,
        account_id=account_id,
        error_message=raw_message,
        classification=classification,
        raw_code=raw_code,
        raw_message=raw_message,
    )
    return classification


def get_chatgpt_auth_pool_state() -> Dict[str, Dict[str, Any]]:
    with _AUTH_POOL_STATE_LOCK:
        return {k: dict(v) for k, v in _AUTH_POOL_STATE.items()}


def _compact_account_id(raw: str | None) -> str:
    if not isinstance(raw, str) or not raw:
        return ""
    if len(raw) <= 12:
        return raw
    return f"{raw[:8]}...{raw[-4:]}"


def _state_for_label(label: str) -> Dict[str, Any]:
    with _AUTH_POOL_STATE_LOCK:
        state = dict(_AUTH_POOL_STATE.get(label) or {})
    now = time.time()
    cooldown_until = float(state.get("cooldown_until") or 0.0)
    remaining = max(0, int(cooldown_until - now))
    return {
        "status": state.get("status") or "ready",
        "failures": int(state.get("failures") or 0),
        "last_status": state.get("last_status"),
        "last_error": state.get("last_error") or "",
        "last_classification": state.get("last_classification") or "",
        "last_raw_code": state.get("last_raw_code") or "",
        "last_raw_message": state.get("last_raw_message") or "",
        "cooldown_remaining": remaining,
        "updated_at": state.get("updated_at"),
    }


def _auth_record_from_obj(
    auth_obj: Dict[str, Any],
    *,
    label: str,
    source: str,
) -> Dict[str, Any]:
    access_token, account_id, id_token, refresh_token, last_refresh = _extract_tokens_from_auth_obj(auth_obj)
    if not isinstance(account_id, str) or not account_id:
        account_id = _derive_account_id(id_token)
    state = _state_for_label(label)
    id_claims = parse_jwt_claims(id_token) or {}
    access_claims = parse_jwt_claims(access_token) or {}
    plan_raw = (access_claims.get("https://api.openai.com/auth") or {}).get("chatgpt_plan_type") or ""
    return {
        "label": label,
        "source": source,
        "account_id": _compact_account_id(account_id),
        "email": id_claims.get("email") or id_claims.get("preferred_username") or "",
        "plan": str(plan_raw).lower() if isinstance(plan_raw, str) else "",
        "last_refresh": last_refresh if isinstance(last_refresh, str) else "",
        "has_access_token": bool(isinstance(access_token, str) and access_token),
        "has_refresh_token": bool(isinstance(refresh_token, str) and refresh_token),
        "has_id_token": bool(isinstance(id_token, str) and id_token),
        **state,
    }


def get_chatgpt_auth_records() -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []

    auth_files = _parse_auth_files_env()
    explicit_auth_files = _has_explicit_auth_files_config()
    if auth_files:
        for idx, path in enumerate(auth_files):
            auth_obj = _read_json_file(path)
            dirname = os.path.basename(os.path.dirname(path))
            filename = os.path.basename(path)
            label = f"{dirname}/{filename}" if dirname else (filename or f"file-{idx + 1}")
            if not isinstance(auth_obj, dict):
                records.append(
                    {
                        "label": label,
                        "source": path,
                        "error": "invalid auth file",
                        **_state_for_label(label),
                    }
                )
                continue
            records.append(_auth_record_from_obj(auth_obj, label=label, source=path))
        return records

    if explicit_auth_files:
        return records

    pool_path = _find_auth_file_path("auth_pool.json")
    if pool_path:
        raw_pool = _read_raw_json_file(pool_path)
        accounts = _extract_pool_accounts(raw_pool) if isinstance(raw_pool, (dict, list)) else []
        for idx, account_obj in enumerate(accounts):
            label = ""
            for key in ("name", "alias", "label"):
                value = account_obj.get(key)
                if isinstance(value, str) and value.strip():
                    label = value.strip()
                    break
            if not label:
                label = f"pool-{idx + 1}"
            records.append(_auth_record_from_obj(account_obj, label=label, source=f"{pool_path}#{idx + 1}"))
        if records:
            return records

    default_path = _find_auth_file_path("auth.json")
    default_auth = read_auth_file()
    if isinstance(default_auth, dict):
        records.append(_auth_record_from_obj(default_auth, label="default", source=default_path or "auth.json"))
    return records


def _parse_auth_files_env() -> List[str]:
    raw = (os.getenv("CHATGPT_LOCAL_AUTH_FILES") or "").strip()
    if not raw:
        return []
    paths: List[str] = []
    for part in raw.split(","):
        path = part.strip()
        if not path:
            continue
        if path not in paths:
            paths.append(path)
    return paths


def _extract_pool_accounts(raw_pool: Dict[str, Any] | List[Any]) -> List[Dict[str, Any]]:
    if isinstance(raw_pool, list):
        return [entry for entry in raw_pool if isinstance(entry, dict)]
    if isinstance(raw_pool, dict):
        accounts = raw_pool.get("accounts")
        if isinstance(accounts, list):
            return [entry for entry in accounts if isinstance(entry, dict)]
    return []


def sse_translate_chat(
    upstream,
    model: str,
    created: int,
    verbose: bool = False,
    vlog=None,
    reasoning_compat: str = "think-tags",
    *,
    include_usage: bool = False,
):
    response_id = "chatcmpl-stream"
    compat = (reasoning_compat or "think-tags").strip().lower()
    think_open = False
    think_closed = False
    saw_output = False
    sent_stop_chunk = False
    sent_tool_finish = False
    saw_any_summary = False
    pending_summary_paragraph = False
    upstream_usage = None
    has_visible_output = False
    ws_state: dict[str, Any] = {}
    ws_index: dict[str, int] = {}
    ws_next_index: int = 0
    
    def _serialize_tool_args(eff_args: Any) -> str:
        """
        Serialize tool call arguments with proper JSON handling.
        
        Args:
            eff_args: Arguments to serialize (dict, list, str, or other)
            
        Returns:
            JSON string representation of the arguments
        """
        if isinstance(eff_args, (dict, list)):
            return json.dumps(eff_args)
        elif isinstance(eff_args, str):
            try:
                parsed = json.loads(eff_args)
                if isinstance(parsed, (dict, list)):
                    return json.dumps(parsed) 
                else:
                    return json.dumps({"query": eff_args})  
            except (json.JSONDecodeError, ValueError):
                return json.dumps({"query": eff_args})
        else:
            return "{}"
    
    def _extract_usage(evt: Dict[str, Any]) -> Dict[str, int] | None:
        try:
            usage = (evt.get("response") or {}).get("usage")
            if not isinstance(usage, dict):
                return None
            pt = int(usage.get("input_tokens") or 0)
            ct = int(usage.get("output_tokens") or 0)
            tt = int(usage.get("total_tokens") or (pt + ct))
            return {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": tt}
        except Exception:
            return None
    try:
        try:
            line_iterator = upstream.iter_lines(decode_unicode=False)
        except requests.exceptions.ChunkedEncodingError as e:
            if verbose and vlog:
                vlog(f"Failed to start stream: {e}")
            yield b"data: [DONE]\n\n"
            return

        for raw in line_iterator:
            try:
                if not raw:
                    continue
                line = (
                    raw.decode("utf-8", errors="ignore")
                    if isinstance(raw, (bytes, bytearray))
                    else raw
                )
                if verbose and vlog:
                    vlog(line)
                if not line.startswith("data: "):
                    continue
                data = line[len("data: ") :].strip()
                if not data:
                    continue
                if data == "[DONE]":
                    break
                try:
                    evt = json.loads(data)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
            except (
                requests.exceptions.ChunkedEncodingError,
                ConnectionError,
                BrokenPipeError,
            ) as e:
                # Connection interrupted mid-stream - end gracefully
                if verbose and vlog:
                    vlog(f"Stream interrupted: {e}")
                yield b"data: [DONE]\n\n"
                return
            kind = evt.get("type")
            if isinstance(evt.get("response"), dict) and isinstance(evt["response"].get("id"), str):
                response_id = evt["response"].get("id") or response_id

            if isinstance(kind, str) and ("web_search_call" in kind):
                continue

            if kind == "response.output_text.delta":
                delta = evt.get("delta") or ""
                if compat == "think-tags" and think_open and not think_closed:
                    close_chunk = {
                        "id": response_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "delta": {"content": "</think>"}, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(close_chunk)}\n\n".encode("utf-8")
                    think_open = False
                    think_closed = True
                saw_output = True
                has_visible_output = True
                chunk = {
                    "id": response_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}],
                }
                yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
            elif kind == "response.output_item.done":
                item = evt.get("item") or {}
                if isinstance(item, dict) and item.get("type") == "function_call":
                    call_id = item.get("call_id") or item.get("id") or ""
                    name = item.get("name") or ""
                    raw_args = item.get("arguments")
                    if isinstance(raw_args, dict):
                        try:
                            ws_state.setdefault(call_id, {}).update(raw_args)
                        except Exception:
                            pass
                    eff_args = ws_state.get(call_id, raw_args if isinstance(raw_args, (dict, list, str)) else {})
                    try:
                        args = _serialize_tool_args(eff_args)
                    except Exception:
                        args = "{}"
                    if call_id not in ws_index:
                        ws_index[call_id] = ws_next_index
                        ws_next_index += 1
                    _idx = ws_index.get(call_id, 0)
                    if isinstance(call_id, str) and isinstance(name, str) and isinstance(args, str):
                        delta_chunk = {
                            "id": response_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {
                                        "tool_calls": [
                                            {
                                                "index": _idx,
                                                "id": call_id,
                                                "type": "function",
                                                "function": {"name": name, "arguments": args},
                                            }
                                        ]
                                    },
                                    "finish_reason": None,
                                }
                            ],
                        }
                        yield f"data: {json.dumps(delta_chunk)}\n\n".encode("utf-8")
                        has_visible_output = True

                        finish_chunk = {
                            "id": response_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
                        }
                        yield f"data: {json.dumps(finish_chunk)}\n\n".encode("utf-8")
                        sent_stop_chunk = True
                        sent_tool_finish = True
            elif kind == "response.reasoning_summary_part.added":
                if compat in ("think-tags", "o3"):
                    if saw_any_summary:
                        pending_summary_paragraph = True
                    else:
                        saw_any_summary = True
            elif kind in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta"):
                delta_txt = evt.get("delta") or ""
                if compat == "o3":
                    if kind == "response.reasoning_summary_text.delta" and pending_summary_paragraph:
                        nl_chunk = {
                            "id": response_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"reasoning": {"content": [{"type": "text", "text": "\n"}]}},
                                    "finish_reason": None,
                                }
                            ],
                        }
                        yield f"data: {json.dumps(nl_chunk)}\n\n".encode("utf-8")
                        pending_summary_paragraph = False
                    chunk = {
                        "id": response_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"reasoning": {"content": [{"type": "text", "text": delta_txt}]}},
                                "finish_reason": None,
                            }
                        ],
                    }
                    yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
                elif compat == "think-tags":
                    if not think_open and not think_closed:
                        open_chunk = {
                            "id": response_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [{"index": 0, "delta": {"content": "<think>"}, "finish_reason": None}],
                        }
                        yield f"data: {json.dumps(open_chunk)}\n\n".encode("utf-8")
                        think_open = True
                    if think_open and not think_closed:
                        if kind == "response.reasoning_summary_text.delta" and pending_summary_paragraph:
                            nl_chunk = {
                                "id": response_id,
                                "object": "chat.completion.chunk",
                                "created": created,
                                "model": model,
                                "choices": [{"index": 0, "delta": {"content": "\n"}, "finish_reason": None}],
                            }
                            yield f"data: {json.dumps(nl_chunk)}\n\n".encode("utf-8")
                            pending_summary_paragraph = False
                        content_chunk = {
                            "id": response_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [{"index": 0, "delta": {"content": delta_txt}, "finish_reason": None}],
                        }
                        yield f"data: {json.dumps(content_chunk)}\n\n".encode("utf-8")
                else:
                    if kind == "response.reasoning_summary_text.delta":
                        chunk = {
                            "id": response_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"reasoning_summary": delta_txt, "reasoning": delta_txt},
                                    "finish_reason": None,
                                }
                            ],
                        }
                        yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
                    else:
                        chunk = {
                            "id": response_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [
                                {"index": 0, "delta": {"reasoning": delta_txt}, "finish_reason": None}
                            ],
                        }
                        yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
            elif isinstance(kind, str) and kind.endswith(".done"):
                pass
            elif kind == "response.output_text.done":
                chunk = {
                    "id": response_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }
                yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
                sent_stop_chunk = True
            elif kind == "response.failed":
                error_info = error_info_from_event_response(
                    getattr(upstream, "chatmock_source", "upstream"),
                    "stream",
                    evt.get("response"),
                )
                if not has_visible_output and should_retry_next_candidate(error_info):
                    raise RetryableStreamError(error_info)
                chunk = {"error": normalized_error_payload(error_info)}
                yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
                yield b"data: [DONE]\n\n"
                break
            elif kind == "response.completed":
                m = _extract_usage(evt)
                if m:
                    upstream_usage = m
                if compat == "think-tags" and think_open and not think_closed:
                    close_chunk = {
                        "id": response_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "delta": {"content": "</think>"}, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(close_chunk)}\n\n".encode("utf-8")
                    think_open = False
                    think_closed = True
                if not sent_stop_chunk:
                    chunk = {
                        "id": response_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    }
                    yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
                    sent_stop_chunk = True

                if include_usage and upstream_usage:
                    try:
                        usage_chunk = {
                            "id": response_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [{"index": 0, "delta": {}, "finish_reason": None}],
                            "usage": upstream_usage,
                        }
                        yield f"data: {json.dumps(usage_chunk)}\n\n".encode("utf-8")
                    except Exception:
                        pass
                yield b"data: [DONE]\n\n"
                break
    finally:
        upstream.close()


def sse_translate_text(upstream, model: str, created: int, verbose: bool = False, vlog=None, *, include_usage: bool = False):
    response_id = "cmpl-stream"
    upstream_usage = None
    has_visible_output = False
    
    def _extract_usage(evt: Dict[str, Any]) -> Dict[str, int] | None:
        try:
            usage = (evt.get("response") or {}).get("usage")
            if not isinstance(usage, dict):
                return None
            pt = int(usage.get("input_tokens") or 0)
            ct = int(usage.get("output_tokens") or 0)
            tt = int(usage.get("total_tokens") or (pt + ct))
            return {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": tt}
        except Exception:
            return None
    try:
        for raw_line in upstream.iter_lines(decode_unicode=False):
            if not raw_line:
                continue
            line = raw_line.decode("utf-8", errors="ignore") if isinstance(raw_line, (bytes, bytearray)) else raw_line
            if verbose and vlog:
                vlog(line)
            if not line.startswith("data: "):
                continue
            data = line[len("data: "):].strip()
            if not data or data == "[DONE]":
                if data == "[DONE]":
                    chunk = {
                        "id": response_id,
                        "object": "text_completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "text": "", "finish_reason": "stop"}],
                    }
                    yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
                continue
            try:
                evt = json.loads(data)
            except Exception:
                continue
            kind = evt.get("type")
            if isinstance(evt.get("response"), dict) and isinstance(evt["response"].get("id"), str):
                response_id = evt["response"].get("id") or response_id
            if kind == "response.output_text.delta":
                delta_text = evt.get("delta") or ""
                has_visible_output = has_visible_output or bool(delta_text)
                chunk = {
                    "id": response_id,
                    "object": "text_completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "text": delta_text, "finish_reason": None}],
                }
                yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
            elif kind == "response.output_text.done":
                chunk = {
                    "id": response_id,
                    "object": "text_completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "text": "", "finish_reason": "stop"}],
                }
                yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
            elif kind == "response.completed":
                m = _extract_usage(evt)
                if m:
                    upstream_usage = m
                if include_usage and upstream_usage:
                    try:
                        usage_chunk = {
                            "id": response_id,
                            "object": "text_completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [{"index": 0, "text": "", "finish_reason": None}],
                            "usage": upstream_usage,
                        }
                        yield f"data: {json.dumps(usage_chunk)}\n\n".encode("utf-8")
                    except Exception:
                        pass
                yield b"data: [DONE]\n\n"
                break
            elif kind == "response.failed":
                error_info = error_info_from_event_response(
                    getattr(upstream, "chatmock_source", "upstream"),
                    "stream",
                    evt.get("response"),
                )
                if not has_visible_output and should_retry_next_candidate(error_info):
                    raise RetryableStreamError(error_info)
                chunk = {"error": normalized_error_payload(error_info)}
                yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
                yield b"data: [DONE]\n\n"
                break
    finally:
        upstream.close()
