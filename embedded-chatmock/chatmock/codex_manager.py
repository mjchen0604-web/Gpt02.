from __future__ import annotations

import atexit
import json
import os
import shlex
import shutil
import socket
import subprocess
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from .codex_app_server import CodexAppServerError
from .surface_names import redact_internal_route_terms
from .upstream_errors import classify_error, error_info_from_event_response, extract_retry_after_unlock_ts
from .utils import (
    _build_candidate_uid,
    _derive_user_id,
    _derive_workspace_id,
    get_max_inflight_per_account,
    handle_chatgpt_candidate_failure,
    mark_chatgpt_auth_result,
    remove_chatgpt_auth_candidate,
)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "1" if default else "0")).strip().lower()
    return raw in ("1", "true", "yes", "on")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_codex_home() -> Path:
    home = os.getenv("CODEX_HOME") or os.getenv("CHATGPT_LOCAL_HOME") or os.path.expanduser("~/.codex")
    return Path(home).expanduser()


def _parse_ws_endpoint(url: str) -> tuple[str, int]:
    parsed = urlparse(url)
    if parsed.scheme not in ("ws", "wss"):
        raise ValueError(f"Unsupported Codex app-server URL: {url}")
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8787
    return host, int(port)


def _build_ws_url(base_url: str, port: int) -> str:
    parsed = urlparse(base_url)
    scheme = parsed.scheme or "ws"
    host = parsed.hostname or "127.0.0.1"
    return f"{scheme}://{host}:{int(port)}"


def _parse_auth_files_env() -> list[str]:
    raw = (os.getenv("CHATGPT_LOCAL_AUTH_FILES") or "").strip()
    if not raw:
        return []
    paths: list[str] = []
    for part in raw.split(","):
        path = part.strip()
        if path and path not in paths:
            paths.append(path)
    return paths


def _has_explicit_auth_files_config() -> bool:
    raw_flag = (os.getenv("CHATGPT_LOCAL_AUTH_FILES_CONFIGURED") or "").strip().lower()
    return raw_flag in ("1", "true", "yes", "on")


def _read_auth_payload(path: Path) -> dict[str, Any] | None:
    try:
        with open(path, "r", encoding="utf-8") as fp:
            payload = json.load(fp)
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _has_auth_tokens(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict) or not payload:
        return False
    tokens = payload.get("tokens") if isinstance(payload.get("tokens"), dict) else {}
    return any(
        isinstance(tokens.get(name), str) and bool(tokens.get(name))
        for name in ("access_token", "refresh_token", "id_token")
    )


def _account_id_from_payload(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return ""
    tokens = payload.get("tokens") if isinstance(payload.get("tokens"), dict) else {}
    account_id = tokens.get("account_id") if isinstance(tokens.get("account_id"), str) else ""
    if not account_id and isinstance(payload.get("account_id"), str):
        account_id = payload.get("account_id") or ""
    return account_id.strip()


def _candidate_uid_from_payload(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return ""
    tokens = payload.get("tokens") if isinstance(payload.get("tokens"), dict) else {}
    account_id = _account_id_from_payload(payload)
    id_token = tokens.get("id_token") if isinstance(tokens.get("id_token"), str) else None
    access_token = tokens.get("access_token") if isinstance(tokens.get("access_token"), str) else None
    workspace_id = _derive_workspace_id(id_token, access_token) or account_id
    user_id = _derive_user_id(id_token, access_token) or ""
    return (_build_candidate_uid(workspace_id, user_id) or account_id or "").strip()


def _instance_label_for_auth_path(path: Path, *, fallback: str) -> str:
    parent = path.parent.name.strip()
    if parent:
        return parent
    stem = path.stem.strip()
    if stem:
        return stem
    return fallback


def _copy_text_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    try:
        os.chmod(dst, 0o600)
    except Exception:
        pass


def _same_file_contents(left: Path, right: Path) -> bool:
    try:
        if not left.exists() or not right.exists():
            return False
        return left.read_bytes() == right.read_bytes()
    except Exception:
        return False


def _clear_codex_runtime_state(codex_home: Path) -> None:
    if not codex_home.exists():
        return
    patterns = ("state*",)
    for pattern in patterns:
        for path in codex_home.glob(pattern):
            try:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
            except Exception:
                pass


class ManagedCodexUpstream:
    def __init__(self, upstream: Any, pool: "CodexAppServerPoolManager", label: str) -> None:
        self._upstream = upstream
        self._pool = pool
        self._label = label
        self._marked = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._upstream, name)

    def close(self) -> None:
        return self._upstream.close()

    def mark_success(self) -> None:
        self._mark(True)

    def mark_failure(self, error_message: str | None = None, status_code: int | None = None) -> None:
        self._mark(False, error_message=error_message, status_code=status_code)

    def mark_failure_info(self, error_info: dict[str, Any]) -> None:
        self._mark(
            False,
            error_message=str(error_info.get("raw_message") or ""),
            status_code=error_info.get("raw_status") if isinstance(error_info.get("raw_status"), int) else None,
            error_info=error_info,
        )

    def _mark(
        self,
        success: bool,
        *,
        error_message: str | None = None,
        status_code: int | None = None,
        error_info: dict[str, Any] | None = None,
    ) -> None:
        if self._marked:
            return
        self._marked = True
        self._pool.mark_request_result(
            self._label,
            success=success,
            error_message=error_message,
            status_code=status_code,
            error_info=error_info,
        )

    def iter_lines(self, decode_unicode: bool = False):
        saw_completed = False
        error_message: str | None = None
        status_code: int | None = None
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
                                response = event.get("response") if isinstance(event.get("response"), dict) else {}
                                error_message = (
                                    ((response.get("error") or {}).get("message"))
                                    or "response.failed"
                                )
                                maybe_status = response.get("status")
                                if isinstance(maybe_status, int):
                                    status_code = maybe_status
                                self.mark_failure_info(
                                    error_info_from_event_response("codex-app-server", "stream", response)
                                )
                            elif kind == "response.completed":
                                saw_completed = True
                yield raw
            if not self._marked:
                if saw_completed:
                    self._mark(True)
                else:
                    self._mark(False, error_message=error_message or "stream ended before response.completed", status_code=status_code)
        except Exception as exc:
            if isinstance(exc, CodexAppServerError) and isinstance(getattr(exc, "error_info", None), dict):
                self.mark_failure_info(exc.error_info)
            else:
                self._mark(False, error_message=str(exc), status_code=status_code)
            raise


class CodexAppServerManager:
    def __init__(
        self,
        app_server_url: str,
        *,
        codex_home: str | Path | None = None,
        source_auth_path: str | Path | None = None,
        label: str = "default",
        managed: bool | None = None,
        autostart: bool | None = None,
        binary: str | None = None,
        flags: str | None = None,
        on_log_line: Any | None = None,
    ) -> None:
        self._url = app_server_url.strip() or "ws://127.0.0.1:8787"
        self._host, self._port = _parse_ws_endpoint(self._url)
        self._codex_home = Path(codex_home).expanduser() if codex_home is not None else _default_codex_home()
        self._source_auth_path = (
            Path(source_auth_path).expanduser() if source_auth_path is not None else (self._codex_home / "auth.json")
        )
        self._binary = (binary or os.getenv("CODEX_BIN") or "codex").strip() or "codex"
        self._flags = (flags or os.getenv("CODEX_APP_SERVER_FLAGS") or "--enable fast_mode").strip()
        self._managed = _env_flag("CHATMOCK_MANAGE_CODEX_APP_SERVER", default=False) if managed is None else bool(managed)
        self._autostart = _env_flag("CHATMOCK_AUTO_START_CODEX_APP_SERVER", default=True) if autostart is None else bool(autostart)
        self._label = label
        self._lock = threading.RLock()
        self._proc: subprocess.Popen[str] | None = None
        self._started_at: str | None = None
        self._last_error: str = ""
        self._last_exit_code: int | None = None
        self._log_lines: deque[str] = deque(maxlen=400)
        self._on_log_line = on_log_line
        atexit.register(self._atexit_stop)

    @property
    def label(self) -> str:
        return self._label

    @property
    def url(self) -> str:
        return self._url

    @property
    def managed(self) -> bool:
        return self._managed

    @property
    def auth_path(self) -> Path:
        return self._codex_home / "auth.json"

    @property
    def source_auth_path(self) -> Path:
        return self._source_auth_path

    @property
    def config_path(self) -> Path:
        return self._codex_home / "config.toml"

    def has_auth(self) -> bool:
        return _has_auth_tokens(_read_auth_payload(self.auth_path))

    def tail_logs(self, lines: int = 120) -> str:
        with self._lock:
            items = list(self._log_lines)[-max(1, lines) :]
        return "\n".join(items)

    def status(self) -> dict[str, Any]:
        with self._lock:
            running_owned = self._proc is not None and self._proc.poll() is None
            listening = self._is_port_open(timeout=0.15)
            external = listening and not running_owned
            if not self._managed:
                state = "external" if external else "disabled"
            elif running_owned and listening:
                state = "running"
            elif running_owned:
                state = "starting"
            elif external:
                state = "external"
            elif not self.has_auth():
                state = "awaiting_auth"
            elif self._last_error:
                state = "error"
            else:
                state = "stopped"
            pid = self._proc.pid if self._proc is not None and self._proc.poll() is None else None
            return {
                "label": self._label,
                "status": state,
                "managed": self._managed,
                "autostart": self._autostart,
                "listening": listening,
                "pid": pid,
                "host": self._host,
                "port": self._port,
                "url": self._url,
                "binary": self._binary,
                "flags": self._flags,
                "startedAt": self._started_at,
                "lastError": self._last_error,
                "lastExitCode": self._last_exit_code,
                "authPath": str(self.auth_path),
                "sourceAuthPath": str(self.source_auth_path),
                "configPath": str(self.config_path),
                "authPresent": self.has_auth(),
            }

    def start(self) -> dict[str, Any]:
        with self._lock:
            if not self._managed:
                return {"ok": False, "error": "managed codex app-server is disabled", "status": self.status()}
            if self._proc is not None and self._proc.poll() is None:
                if self._is_port_open(timeout=0.15):
                    return {"ok": True, "message": "codex app-server already running", "status": self.status()}
            elif self._is_port_open(timeout=0.15):
                return {
                    "ok": True,
                    "message": "codex app-server already listening externally",
                    "status": self.status(),
                }
            if not self.has_auth():
                self._last_error = "auth.json is missing or invalid"
                return {"ok": False, "error": self._last_error, "status": self.status()}

            command = [self._binary, "app-server", "--listen", self._url]
            if self._flags:
                command.extend(shlex.split(self._flags))
            self._codex_home.mkdir(parents=True, exist_ok=True)
            child_env = os.environ.copy()
            child_env["CODEX_HOME"] = str(self._codex_home)
            child_env["CHATGPT_LOCAL_HOME"] = str(self._codex_home)

            try:
                self._proc = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    cwd=str(self._codex_home),
                    env=child_env,
                    bufsize=1,
                    universal_newlines=True,
                )
            except Exception as exc:
                self._proc = None
                self._last_error = str(exc)
                self._last_exit_code = None
                return {"ok": False, "error": self._last_error, "status": self.status()}

            self._started_at = _utc_now()
            self._last_error = ""
            self._last_exit_code = None
            self._append_log(f"[manager] starting codex app-server: {' '.join(command)}")
            threading.Thread(target=self._drain_output, args=(self._proc,), daemon=True).start()

        ready = self._wait_until_ready(timeout_seconds=45)
        if ready:
            return {"ok": True, "message": "codex app-server started", "status": self.status()}

        with self._lock:
            exit_code = self._proc.poll() if self._proc is not None else None
            if exit_code is not None:
                self._last_exit_code = exit_code
            if not self._last_error:
                self._last_error = "codex app-server did not become ready in time"
        return {"ok": False, "error": self._last_error, "status": self.status()}

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                if self._is_port_open(timeout=0.15):
                    return {
                        "ok": False,
                        "error": "codex app-server is running externally and cannot be stopped here",
                        "status": self.status(),
                    }
                self._proc = None
                return {"ok": True, "message": "codex app-server already stopped", "status": self.status()}

            proc = self._proc
            proc.terminate()

        deadline = time.time() + 10
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            time.sleep(0.2)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

        with self._lock:
            self._last_exit_code = proc.returncode
            self._append_log(f"[manager] codex app-server stopped with exit code {proc.returncode}")
            self._proc = None
        return {"ok": True, "message": "codex app-server stopped", "status": self.status()}

    def restart(self) -> dict[str, Any]:
        stop_result = self.stop()
        if not stop_result.get("ok") and "already stopped" not in str(stop_result.get("message", "")):
            return stop_result
        return self.start()

    def autostart_if_possible(self) -> None:
        if not self._managed or not self._autostart:
            return
        if not self.has_auth():
            self._append_log("[manager] auth.json not present; skipping codex app-server autostart")
            return
        result = self.start()
        if not result.get("ok"):
            self._append_log(f"[manager] autostart failed: {result.get('error') or result.get('message')}")

    def _atexit_stop(self) -> None:
        try:
            self.stop()
        except Exception:
            pass

    def _append_log(self, line: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        entry = f"[{stamp}] [{self._label}] {redact_internal_route_terms(line.rstrip())}"
        with self._lock:
            self._log_lines.append(entry)
        print(entry)

    def _drain_output(self, proc: subprocess.Popen[str]) -> None:
        stream = proc.stdout
        if stream is None:
            return
        try:
            for line in iter(stream.readline, ""):
                if not line:
                    break
                self._append_log(f"[codex] {line.rstrip()}")
                callback = self._on_log_line
                if callable(callback):
                    try:
                        callback(self._label, line.rstrip())
                    except Exception:
                        pass
        finally:
            try:
                stream.close()
            except Exception:
                pass
            exit_code = proc.poll()
            with self._lock:
                if self._proc is proc:
                    self._last_exit_code = exit_code
                    self._proc = None
                    if exit_code not in (None, 0):
                        self._last_error = f"codex app-server exited with code {exit_code}"

    def _wait_until_ready(self, timeout_seconds: int) -> bool:
        deadline = time.time() + max(1, timeout_seconds)
        while time.time() < deadline:
            with self._lock:
                proc = self._proc
                if proc is not None and proc.poll() is not None:
                    self._last_exit_code = proc.returncode
                    self._last_error = f"codex app-server exited with code {proc.returncode}"
                    self._proc = None
                    return False
            if self._is_port_open(timeout=0.3):
                return True
            time.sleep(0.25)
        return False

    def _is_port_open(self, timeout: float = 0.25) -> bool:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect((self._host, self._port))
            return True
        except OSError:
            return False
        finally:
            try:
                sock.close()
            except Exception:
                pass


class CodexAppServerPoolManager:
    def __init__(self, app_server_url: str) -> None:
        self._base_url = app_server_url.strip() or "ws://127.0.0.1:8787"
        self._host, self._base_port = _parse_ws_endpoint(self._base_url)
        self._managed = _env_flag("CHATMOCK_MANAGE_CODEX_APP_SERVER", default=False)
        self._autostart = _env_flag("CHATMOCK_AUTO_START_CODEX_APP_SERVER", default=True)
        self._binary = (os.getenv("CODEX_BIN") or "codex").strip() or "codex"
        self._flags = (os.getenv("CODEX_APP_SERVER_FLAGS") or "--enable fast_mode").strip()
        self._lock = threading.RLock()
        self._instances: dict[str, CodexAppServerManager] = {}
        self._instance_order: list[str] = []
        self._request_state: dict[str, dict[str, Any]] = {}
        self._rr_index = 0
        self._log_lines: deque[str] = deque(maxlen=400)
        atexit.register(self._atexit_stop)

    @property
    def managed(self) -> bool:
        return self._managed

    def _append_log(self, line: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        entry = f"[{stamp}] [pool] {redact_internal_route_terms(line.rstrip())}"
        with self._lock:
            self._log_lines.append(entry)
        print(entry)

    def _default_config_seed(self) -> Path | None:
        paths = [
            _default_codex_home() / "config.toml",
            Path(__file__).resolve().parent.parent / "config.toml",
        ]
        for path in paths:
            if path.exists():
                return path
        return None

    def _desired_entries(self, auth_files: list[str] | None = None) -> list[dict[str, Any]]:
        paths = auth_files if auth_files is not None else _parse_auth_files_env()
        entries: list[dict[str, Any]] = []
        seen_labels: set[str] = set()
        seen_candidate_uids: set[str] = set()

        for idx, raw in enumerate(paths):
            auth_path = Path(raw).expanduser()
            if not auth_path.exists():
                continue
            payload = _read_auth_payload(auth_path)
            candidate_uid = _candidate_uid_from_payload(payload)
            if candidate_uid and candidate_uid in seen_candidate_uids:
                continue
            label = _instance_label_for_auth_path(auth_path, fallback=f"acc{idx + 1:02d}")
            base_label = label
            suffix = 2
            while label in seen_labels:
                label = f"{base_label}-{suffix}"
                suffix += 1
            seen_labels.add(label)
            if candidate_uid:
                seen_candidate_uids.add(candidate_uid)
            entries.append(
                {
                    "label": label,
                    "auth_path": auth_path,
                    "codex_home": auth_path.parent / ".codex",
                }
            )

        if entries:
            return entries

        if auth_files is not None or _has_explicit_auth_files_config():
            return entries

        default_home = _default_codex_home()
        default_auth = default_home / "auth.json"
        if default_auth.exists():
            entries.append({"label": "default", "auth_path": default_auth, "codex_home": default_home})
        return entries

    def _materialize_instance_files(self, auth_path: Path, codex_home: Path) -> None:
        payload = _read_auth_payload(auth_path)
        if not _has_auth_tokens(payload):
            return
        codex_home.mkdir(parents=True, exist_ok=True)
        target_auth = codex_home / "auth.json"
        auth_changed = False
        if auth_path.resolve() != target_auth.resolve():
            auth_changed = not _same_file_contents(auth_path, target_auth)
            _copy_text_file(auth_path, target_auth)
        if auth_changed:
            _clear_codex_runtime_state(codex_home)
        config_seed = self._default_config_seed()
        if config_seed is not None and config_seed.exists():
            target_config = codex_home / "config.toml"
            if config_seed.resolve() != target_config.resolve():
                _copy_text_file(config_seed, target_config)

    def _sync_instances(self, auth_files: list[str] | None = None) -> list[str]:
        desired_entries = self._desired_entries(auth_files)
        desired_labels = [entry["label"] for entry in desired_entries]
        instances_to_start: list[CodexAppServerManager] = []
        with self._lock:
            for label in list(self._instances.keys()):
                if label not in desired_labels:
                    try:
                        self._instances[label].stop()
                    except Exception:
                        pass
                    self._instances.pop(label, None)
                    self._request_state.pop(label, None)
            self._instance_order = desired_labels[:]

            for idx, entry in enumerate(desired_entries):
                label = entry["label"]
                auth_path = entry["auth_path"]
                codex_home = entry["codex_home"]
                self._materialize_instance_files(auth_path, codex_home)
                url = _build_ws_url(self._base_url, self._base_port + idx)
                current = self._instances.get(label)
                needs_new = True
                if current is not None:
                    current_status = current.status()
                    if (
                        str(current_status.get("url") or "") == url
                        and str(current_status.get("authPath") or "").lower() == str((codex_home / "auth.json")).lower()
                    ):
                        needs_new = False
                if needs_new:
                    if current is not None:
                        try:
                            current.stop()
                        except Exception:
                            pass
                    self._instances[label] = CodexAppServerManager(
                        url,
                        codex_home=codex_home,
                        source_auth_path=auth_path,
                        label=label,
                        managed=self._managed,
                        autostart=self._autostart,
                        binary=self._binary,
                        flags=self._flags,
                        on_log_line=self._handle_instance_log_line,
                    )
                instance = self._instances[label]
                if self._managed and self._autostart and instance.has_auth():
                    status = instance.status()
                    if status.get("status") not in ("running", "starting", "external"):
                        instances_to_start.append(instance)
        for instance in instances_to_start:
            try:
                instance.start()
            except Exception as exc:
                self._append_log(f"failed to start {instance.label}: {exc}")
        return desired_labels

    def _handle_instance_log_line(self, label: str, line: str) -> None:
        if not isinstance(label, str) or not label or not isinstance(line, str):
            return
        lowered = line.lower()
        if "deactivated_workspace" not in lowered:
            return
        now = time.time()
        with self._lock:
            state = dict(self._request_state.get(label) or {})
            state["failures"] = int(state.get("failures") or 0) + 1
            state["cooldown_until"] = now + float(24 * 60 * 60)
            state["last_error"] = "deactivated_workspace"
            state["last_status"] = 402
            state["status"] = "removed_invalid"
            state["last_classification"] = "account_invalid"
            state["last_raw_code"] = "deactivated_workspace"
            state["last_raw_message"] = line
            state["updated_at"] = now
            state["last_failure_at"] = _utc_now()
            self._request_state[label] = state
        self._append_log(f"detected deactivated_workspace for {label}; evicting candidate")
        threading.Thread(
            target=self.remove_auth_for_label,
            args=(label,),
            kwargs={"reason": "deactivated_workspace"},
            daemon=True,
        ).start()

    def status_all(self) -> list[dict[str, Any]]:
        self._sync_instances()
        with self._lock:
            labels = list(self._instance_order)
            instances = [self._instances[label] for label in labels if label in self._instances]
        statuses: list[dict[str, Any]] = []
        now = time.time()
        for instance in instances:
            status = instance.status()
            label = str(status.get("label") or instance.label)
            state = dict(self._request_state.get(label) or {})
            cooldown_until = float(state.get("cooldown_until") or 0.0)
            status["cooldownRemaining"] = max(0, int(cooldown_until - now))
            status["cooldownUntil"] = cooldown_until
            status["unlockAt"] = (
                datetime.fromtimestamp(cooldown_until, timezone.utc).isoformat()
                if cooldown_until > now
                else ""
            )
            status["inflight"] = int(state.get("inflight") or 0)
            status["requestFailures"] = int(state.get("failures") or 0)
            status["requestCount"] = int(state.get("requests_total") or 0)
            status["requestSuccesses"] = int(state.get("successes_total") or 0)
            status["lastRequestError"] = state.get("last_error") or ""
            status["lastRequestAt"] = state.get("last_request_at") or ""
            status["lastSuccessAt"] = state.get("last_success_at") or ""
            status["lastFailureAt"] = state.get("last_failure_at") or ""
            statuses.append(status)
        return statuses

    def status(self) -> dict[str, Any]:
        statuses = self.status_all()
        running = [item for item in statuses if item.get("status") == "running" and item.get("listening")]
        starting = [item for item in statuses if item.get("status") == "starting"]
        awaiting = [item for item in statuses if item.get("status") == "awaiting_auth"]
        external = [item for item in statuses if item.get("status") == "external"]
        errored = [item for item in statuses if item.get("status") == "error"]
        if not self._managed:
            state = "external" if external else "disabled"
        elif running:
            state = "running"
        elif starting:
            state = "starting"
        elif awaiting and not statuses:
            state = "awaiting_auth"
        elif awaiting:
            state = "awaiting_auth"
        elif errored:
            state = "error"
        elif statuses:
            state = "stopped"
        else:
            state = "awaiting_auth"
        primary = running[0] if running else (statuses[0] if statuses else None)
        return {
            "status": state,
            "managed": self._managed,
            "autostart": self._autostart,
            "listening": bool(running),
            "host": self._host,
            "port": self._base_port,
            "url": self._base_url,
            "binary": self._binary,
            "flags": self._flags,
            "instanceCount": len(statuses),
            "activeCount": len(running),
            "instances": statuses,
            "authPresent": bool(primary and primary.get("authPresent")) if primary else False,
            "authPath": primary.get("authPath") if primary else str(_default_codex_home() / "auth.json"),
            "configPath": primary.get("configPath") if primary else str(_default_codex_home() / "config.toml"),
            "pid": primary.get("pid") if primary else None,
            "startedAt": primary.get("startedAt") if primary else None,
            "lastError": primary.get("lastError") if primary else "",
            "lastExitCode": primary.get("lastExitCode") if primary else None,
        }

    def tail_logs(self, lines: int = 120) -> str:
        own_lines = list(self._log_lines)[-max(1, lines) :]
        instance_lines: list[str] = []
        for status in self.status_all():
            label = status.get("label")
            if not isinstance(label, str):
                continue
            instance = self._instances.get(label)
            if instance is None:
                continue
            text = instance.tail_logs(lines=max(10, lines // max(1, len(self._instances))))
            if text:
                instance_lines.append(text)
        combined = own_lines + instance_lines
        return "\n".join(line for line in combined if isinstance(line, str) and line)

    def start(self) -> dict[str, Any]:
        labels = self._sync_instances()
        results: list[dict[str, Any]] = []
        for label in labels:
            instance = self._instances.get(label)
            if instance is None:
                continue
            if not instance.has_auth():
                continue
            results.append(instance.start())
        if not labels:
            self._append_log("no auths available for codex app-server pool start")
        return {
            "ok": any(bool(result.get("ok")) for result in results) or not labels,
            "message": "codex app-server pool started" if labels else "no auths available",
            "status": self.status(),
            "results": results,
        }

    def stop(self) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        for label in list(self._instance_order):
            instance = self._instances.get(label)
            if instance is None:
                continue
            results.append(instance.stop())
        return {"ok": True, "message": "codex app-server pool stopped", "status": self.status(), "results": results}

    def restart(self) -> dict[str, Any]:
        self.stop()
        return self.start()

    def sync_from_auth_files(self, auth_files: list[str] | None = None, *, restart: bool = False) -> dict[str, Any]:
        labels = self._sync_instances(auth_files)
        results: list[dict[str, Any]] = []
        if restart:
            for label in labels:
                instance = self._instances.get(label)
                if instance is None or not instance.has_auth():
                    continue
                results.append(instance.restart())
        elif self._autostart:
            for label in labels:
                instance = self._instances.get(label)
                if instance is None or not instance.has_auth():
                    continue
                status = instance.status()
                if status.get("status") not in ("running", "external"):
                    results.append(instance.start())
        return {"ok": True, "message": "codex app-server pool synced", "status": self.status(), "results": results}

    def autostart_if_possible(self) -> None:
        if not self._managed or not self._autostart:
            return
        result = self.start()
        if not result.get("ok"):
            self._append_log(f"autostart result: {result.get('message') or result.get('error')}")

    def _ordered_labels(self, labels: list[str]) -> list[str]:
        if len(labels) <= 1:
            return labels
        with self._lock:
            start = self._rr_index % len(labels)
            self._rr_index = (self._rr_index + 1) % len(labels)
        if start == 0:
            return labels
        return labels[start:] + labels[:start]

    def _available_labels(self) -> list[str]:
        statuses = self.status_all()
        now = time.time()
        available: list[str] = []
        limit = get_max_inflight_per_account()
        for status in statuses:
            label = status.get("label")
            if not isinstance(label, str):
                continue
            if status.get("status") != "running" or not status.get("listening"):
                continue
            state = self._request_state.get(label) or {}
            cooldown_until = float(state.get("cooldown_until") or 0.0)
            if cooldown_until > now:
                continue
            inflight = int(state.get("inflight") or 0)
            if inflight < limit:
                available.append(label)
        if available:
            return self._ordered_labels(available)
        return []

    def get_request_candidates(self) -> list[dict[str, str]]:
        self._sync_instances()
        labels = self._available_labels()
        if labels:
            self._append_log(f"request candidates: {', '.join(labels)}")
        out: list[dict[str, str]] = []
        for label in labels:
            instance = self._instances.get(label)
            if instance is None:
                continue
            payload = _read_auth_payload(instance.source_auth_path)
            out.append({
                "label": label,
                "url": instance.url,
                "auth_path": str(instance.auth_path),
                "account_id": _account_id_from_payload(payload),
                "candidate_uid": _candidate_uid_from_payload(payload),
            })
        return out

    def claim_request_candidate(
        self,
        *,
        excluded_labels: set[str] | None = None,
        preferred_label: str | None = None,
        preferred_url: str | None = None,
    ) -> dict[str, str] | None:
        self._sync_instances()
        excluded = excluded_labels or set()
        statuses = self.status_all()
        preferred_candidates: list[dict[str, str]] = []
        saturated_candidates: list[dict[str, str]] = []
        limit = get_max_inflight_per_account()

        for status in statuses:
            label = status.get("label")
            if not isinstance(label, str) or label in excluded:
                continue
            if status.get("status") != "running" or not status.get("listening"):
                continue
            cooldown_until = float(status.get("cooldownUntil") or 0.0)
            if cooldown_until > time.time():
                continue
            instance = self._instances.get(label)
            if instance is None:
                continue
            payload = _read_auth_payload(instance.source_auth_path)
            candidate = {
                "label": label,
                "url": instance.url,
                "auth_path": str(instance.auth_path),
                "account_id": _account_id_from_payload(payload),
            }
            inflight = int(status.get("inflight") or 0)
            if inflight < limit:
                preferred_candidates.append(candidate)
            else:
                saturated_candidates.append(candidate)

        def _reorder(items: list[dict[str, str]]) -> list[dict[str, str]]:
            if not items:
                return items
            if preferred_label or preferred_url:
                matched: list[dict[str, str]] = []
                rest: list[dict[str, str]] = []
                for item in items:
                    if (preferred_label and item.get("label") == preferred_label) or (
                        preferred_url and item.get("url") == preferred_url
                    ):
                        matched.append(item)
                    else:
                        rest.append(item)
                items = matched + rest
            labels = [str(item.get("label") or "") for item in items]
            ordered_labels = self._ordered_labels(labels)
            by_label = {str(item.get("label") or ""): item for item in items}
            return [by_label[label] for label in ordered_labels if label in by_label]

        pool = _reorder(preferred_candidates) or _reorder(saturated_candidates)
        if not pool:
            return None
        selected = pool[0]
        label = str(selected.get("label") or "")
        with self._lock:
            state = dict(self._request_state.get(label) or {})
            state["inflight"] = int(state.get("inflight") or 0) + 1
            self._request_state[label] = state
        return selected

    def release_request_slot(self, label: str) -> None:
        if not isinstance(label, str) or not label:
            return
        with self._lock:
            state = dict(self._request_state.get(label) or {})
            state["inflight"] = max(0, int(state.get("inflight") or 0) - 1)
            self._request_state[label] = state

    def remove_auth_for_label(self, label: str, *, reason: str = "") -> bool:
        instance = self._instances.get(label)
        auth_path = str(instance.source_auth_path) if instance is not None else ""
        payload = _read_auth_payload(Path(auth_path)) if auth_path else None
        account_id = _account_id_from_payload(payload)
        candidate_uid = _candidate_uid_from_payload(payload)
        if instance is not None:
            try:
                instance.stop()
            except Exception:
                pass
        removed = remove_chatgpt_auth_candidate(
            {
                "label": label,
                "source_kind": "auth_file",
                "source_path": auth_path,
                "source_index": None,
                "account_id": account_id,
                "candidate_uid": candidate_uid,
            },
            reason=reason,
        )
        if removed:
            with self._lock:
                self._instances.pop(label, None)
                self._request_state.pop(label, None)
                self._instance_order = [item for item in self._instance_order if item != label]
            self._sync_instances()
        return removed

    def mark_request_result(
        self,
        label: str,
        *,
        success: bool,
        error_message: str | None = None,
        status_code: int | None = None,
        error_info: dict[str, Any] | None = None,
    ) -> None:
        if not isinstance(label, str) or not label:
            return
        now = time.time()
        max_retry_interval = max(5, int(os.getenv("CHATGPT_LOCAL_MAX_RETRY_INTERVAL") or "5"))
        classification = classify_error(error_info or {"raw_status": status_code, "raw_message": error_message})
        retry_at_until = extract_retry_after_unlock_ts(error_info or {"raw_status": status_code, "raw_message": error_message})
        effective_classification = classification
        if retry_at_until is not None and effective_classification == "generic_failure":
            effective_classification = "rate_limited"
        instance = self._instances.get(label)
        payload = _read_auth_payload(instance.source_auth_path) if instance is not None else None
        account_id = _account_id_from_payload(payload)
        candidate_uid = _candidate_uid_from_payload(payload)
        with self._lock:
            state = dict(self._request_state.get(label) or {})
            state["requests_total"] = int(state.get("requests_total") or 0) + 1
            state["last_request_at"] = _utc_now()
            state["inflight"] = max(0, int(state.get("inflight") or 0) - 1)
            if success:
                state["failures"] = 0
                state["cooldown_until"] = 0.0
                state["last_error"] = ""
                state["updated_at"] = now
                state["successes_total"] = int(state.get("successes_total") or 0) + 1
                state["last_success_at"] = _utc_now()
                state["status"] = "ready"
                state["last_classification"] = "ready"
                self._request_state[label] = state
                mark_chatgpt_auth_result(
                    label,
                    success=True,
                    status_code=status_code,
                    account_id=account_id,
                    candidate_uid=candidate_uid,
                )
                self._append_log(f"request result: {label} success")
                return
            if effective_classification in ("insufficient_balance", "rate_limited"):
                state["failures"] = int(state.get("failures") or 0) + 1
                state["cooldown_until"] = float(retry_at_until) if retry_at_until is not None else now + float(5 * 60 * 60)
                state["last_error"] = error_message or ""
                state["last_status"] = status_code
                state["status"] = (
                    "cooldown_insufficient_balance"
                    if effective_classification == "insufficient_balance"
                    else "cooldown_rate_limited"
                )
                state["last_classification"] = effective_classification
                state["last_raw_code"] = (error_info or {}).get("raw_code") or ""
                state["last_raw_message"] = (error_info or {}).get("raw_message") or error_message or ""
                state["updated_at"] = now
                state["last_failure_at"] = _utc_now()
                self._request_state[label] = state
                mark_chatgpt_auth_result(
                    label,
                    success=False,
                    status_code=status_code,
                    account_id=account_id,
                    candidate_uid=candidate_uid,
                    error_message=error_message,
                    classification=effective_classification,
                    cooldown_seconds=None if retry_at_until is not None else 5 * 60 * 60,
                    cooldown_until_ts=retry_at_until,
                    raw_code=(error_info or {}).get("raw_code"),
                    raw_message=(error_info or {}).get("raw_message"),
                )
                self._append_log(f"request result: {label} {effective_classification}")
                return
            if effective_classification == "account_invalid":
                state["failures"] = int(state.get("failures") or 0) + 1
                state["cooldown_until"] = 0.0
                state["last_error"] = error_message or ""
                state["last_status"] = status_code
                state["status"] = "removed_invalid"
                state["last_classification"] = effective_classification
                state["last_raw_code"] = (error_info or {}).get("raw_code") or ""
                state["last_raw_message"] = (error_info or {}).get("raw_message") or error_message or ""
                state["updated_at"] = now
                state["last_failure_at"] = _utc_now()
                self._request_state[label] = state
            else:
                failures = int(state.get("failures") or 0) + 1
                cooldown = min(max_retry_interval, 2 ** max(1, failures - 1))
                state["failures"] = failures
                state["cooldown_until"] = now + float(cooldown)
                state["last_error"] = error_message or ""
                state["last_status"] = status_code
                state["status"] = "temporary_failure"
                state["last_classification"] = effective_classification
                state["last_raw_code"] = (error_info or {}).get("raw_code") or ""
                state["last_raw_message"] = (error_info or {}).get("raw_message") or error_message or ""
                state["updated_at"] = now
                state["last_failure_at"] = _utc_now()
                self._request_state[label] = state
                mark_chatgpt_auth_result(
                    label,
                    success=False,
                    status_code=status_code,
                    account_id=account_id,
                    candidate_uid=candidate_uid,
                    error_message=error_message,
                    classification=effective_classification,
                    raw_code=(error_info or {}).get("raw_code"),
                    raw_message=(error_info or {}).get("raw_message"),
                )
            self._append_log(f"request result: {label} failure ({error_message or 'unknown error'})")
        if effective_classification == "account_invalid":
            self.remove_auth_for_label(label, reason=error_message or "Invalid account from codex app-server")

    def wrap_upstream(self, label: str, upstream: Any) -> ManagedCodexUpstream:
        return ManagedCodexUpstream(upstream, self, label)

    def _atexit_stop(self) -> None:
        try:
            self.stop()
        except Exception:
            pass
