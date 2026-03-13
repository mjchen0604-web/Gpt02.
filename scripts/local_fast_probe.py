import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import requests


REPO_ROOT = Path(__file__).resolve().parent.parent
CHATMOCK_DIR = REPO_ROOT / "embedded-chatmock"
DEFAULT_AUTH_ROOT = Path(r"C:\Users\Mjaga\Desktop\auth")
DEFAULT_WORK_ROOT = Path(r"C:\Users\Mjaga\Documents\Playground\codex-fast-local")


def wait_http(url: str, timeout: int = 30) -> tuple[bool, str | None]:
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            response = requests.get(url, timeout=1)
            if response.ok:
                return True, None
            last_error = f"http {response.status_code}"
        except Exception as exc:  # pragma: no cover - network probe
            last_error = str(exc)
        time.sleep(1)
    return False, last_error


def wait_port(port: int, timeout: int = 20) -> tuple[bool, str | None]:
    import socket

    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        sock = socket.socket()
        sock.settimeout(1)
        try:
            sock.connect(("127.0.0.1", port))
            return True, None
        except Exception as exc:  # pragma: no cover - socket probe
            last_error = str(exc)
        finally:
            sock.close()
        time.sleep(1)
    return False, last_error


def start_process(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    stdout_path: Path,
    stderr_path: Path,
) -> tuple[subprocess.Popen[str], object, object]:
    stdout_handle = open(stdout_path, "w", encoding="utf-8")
    stderr_handle = open(stderr_path, "w", encoding="utf-8")
    process = subprocess.Popen(
        argv,
        cwd=str(cwd),
        env=env,
        stdout=stdout_handle,
        stderr=stderr_handle,
        text=True,
    )
    return process, stdout_handle, stderr_handle


def stop_process(
    process: subprocess.Popen[str] | None,
    stdout_handle: object | None,
    stderr_handle: object | None,
) -> None:
    if process is not None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    if stdout_handle is not None:
        stdout_handle.close()
    if stderr_handle is not None:
        stderr_handle.close()


def run_probe(base_url: str, probe_name: str, body: dict[str, object]) -> dict[str, object]:
    response = requests.post(f"{base_url}/v1/chat/completions", json=body, timeout=90)
    try:
        parsed = response.json()
    except Exception:
        parsed = response.text[:2000]
    result: dict[str, object] = {
        "probe": probe_name,
        "status": response.status_code,
        "requested": response.headers.get("X-ChatMock-Service-Tier-Requested"),
        "observed": response.headers.get("X-ChatMock-Service-Tier-Observed"),
        "service_tier": parsed.get("service_tier") if isinstance(parsed, dict) else None,
        "body": parsed,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a local Codex fast probe with a selected auth.json."
    )
    parser.add_argument("--auth", default="auth02", help="Auth basename like auth02")
    parser.add_argument(
        "--auth-root",
        default=str(DEFAULT_AUTH_ROOT),
        help="Directory that contains authNN.json files",
    )
    parser.add_argument(
        "--work-root",
        default=str(DEFAULT_WORK_ROOT),
        help="Directory used for temporary CODEX_HOME and logs",
    )
    parser.add_argument("--ws-port", type=int, default=8787, help="Local app-server port")
    parser.add_argument("--http-port", type=int, default=1455, help="Local chatmock port")
    parser.add_argument(
        "--keep-running",
        action="store_true",
        help="Keep both local services running after probes complete",
    )
    args = parser.parse_args()

    auth_root = Path(args.auth_root)
    auth_path = auth_root / f"{args.auth}.json"
    if not auth_path.exists():
        print(json.dumps({"error": f"Missing auth file: {auth_path}"}, ensure_ascii=False))
        return 1

    work_root = Path(args.work_root)
    case_dir = work_root / args.auth
    if case_dir.exists():
        shutil.rmtree(case_dir)
    case_dir.mkdir(parents=True, exist_ok=True)
    codex_home = case_dir / ".codex"
    codex_home.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(auth_path, codex_home / "auth.json")

    app_env = os.environ.copy()
    app_env["CODEX_HOME"] = str(codex_home)

    app_server = None
    chatmock = None
    app_stdout = app_stderr = chat_stdout = chat_stderr = None

    try:
        app_server, app_stdout, app_stderr = start_process(
            [
                "codex",
                "app-server",
                "--listen",
                f"ws://127.0.0.1:{args.ws_port}",
                "--enable",
                "fast_mode",
            ],
            cwd=case_dir,
            env=app_env,
            stdout_path=case_dir / "appserver.stdout.log",
            stderr_path=case_dir / "appserver.stderr.log",
        )
        port_ok, port_error = wait_port(args.ws_port, timeout=25)
        if not port_ok:
            print(
                json.dumps(
                    {
                        "case": args.auth,
                        "stage": "app-server",
                        "ok": False,
                        "error": port_error,
                        "stderr_tail": (case_dir / "appserver.stderr.log").read_text(
                            encoding="utf-8", errors="replace"
                        )[-4000:],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1

        chatmock, chat_stdout, chat_stderr = start_process(
            [
                sys.executable,
                "chatmock.py",
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.http_port),
                "--upstream",
                "codex-app-server",
                "--codex-app-server-url",
                f"ws://127.0.0.1:{args.ws_port}",
                "--verbose",
            ],
            cwd=CHATMOCK_DIR,
            env={**os.environ.copy(), "CHATMOCK_EXPOSE_SERVICE_TIER": "1", "CHATMOCK_EXPOSE_INTERNAL_ERROR_DETAILS": "1"},
            stdout_path=case_dir / "chatmock.stdout.log",
            stderr_path=case_dir / "chatmock.stderr.log",
        )
        http_ok, http_error = wait_http(f"http://127.0.0.1:{args.http_port}/health", timeout=25)
        if not http_ok:
            print(
                json.dumps(
                    {
                        "case": args.auth,
                        "stage": "chatmock",
                        "ok": False,
                        "error": http_error,
                        "stderr_tail": (case_dir / "chatmock.stderr.log").read_text(
                            encoding="utf-8", errors="replace"
                        )[-4000:],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1

        base_url = f"http://127.0.0.1:{args.http_port}"
        probes = [
            run_probe(
                base_url,
                "fast_alias",
                {
                    "model": "gpt-5.4-fast-low",
                    "messages": [{"role": "user", "content": "reply with ok"}],
                    "stream": False,
                },
            ),
            run_probe(
                base_url,
                "explicit_fast",
                {
                    "model": "gpt-5.4",
                    "service_tier": "fast",
                    "messages": [{"role": "user", "content": "reply with ok"}],
                    "stream": False,
                },
            ),
        ]

        print(
            json.dumps(
                {
                    "case": args.auth,
                    "ok": True,
                    "base_url": base_url,
                    "probes": probes,
                    "logs": {
                        "appserver_stdout": str(case_dir / "appserver.stdout.log"),
                        "appserver_stderr": str(case_dir / "appserver.stderr.log"),
                        "chatmock_stdout": str(case_dir / "chatmock.stdout.log"),
                        "chatmock_stderr": str(case_dir / "chatmock.stderr.log"),
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )

        if args.keep_running:
            print("Keeping local services running. Press Ctrl+C to stop.")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
        return 0
    finally:
        if not args.keep_running:
            stop_process(chatmock, chat_stdout, chat_stderr)
            stop_process(app_server, app_stdout, app_stderr)


if __name__ == "__main__":
    raise SystemExit(main())
