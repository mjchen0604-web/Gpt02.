from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Tuple

import requests
from flask import Response, current_app, jsonify, make_response

from .config import CHATGPT_RESPONSES_URL
from .http import build_cors_headers
from .session import ensure_session_id
from flask import request as flask_request
from .utils import get_effective_chatgpt_auth_candidates


SUPPORTED_MODEL_GROUPS: List[Tuple[str, List[str]]] = [
    ("gpt-5", ["high", "medium", "low", "minimal"]),
    ("gpt-5.1", ["high", "medium", "low"]),
    ("gpt-5.2", ["xhigh", "high", "medium", "low"]),
    ("gpt-5.3-codex", ["xhigh", "high", "medium", "low"]),
    ("gpt-5-codex", ["high", "medium", "low"]),
    ("gpt-5.2-codex", ["xhigh", "high", "medium", "low"]),
    ("gpt-5.1-codex", ["high", "medium", "low"]),
    ("gpt-5.1-codex-max", ["xhigh", "high", "medium", "low"]),
    ("gpt-5.1-codex-mini", []),
]

MODEL_NAME_MAPPING = {
    "gpt5": "gpt-5",
    "gpt-5-latest": "gpt-5",
    "gpt-5": "gpt-5",
    "gpt-5.1": "gpt-5.1",
    "gpt5.2": "gpt-5.2",
    "gpt-5.2": "gpt-5.2",
    "gpt-5.2-latest": "gpt-5.2",
    "gpt5.3-codex": "gpt-5.3-codex",
    "gpt-5.3-codex": "gpt-5.3-codex",
    "gpt-5.3-codex-latest": "gpt-5.3-codex",
    "gpt5.2-codex": "gpt-5.2-codex",
    "gpt-5.2-codex": "gpt-5.2-codex",
    "gpt-5.2-codex-latest": "gpt-5.2-codex",
    "gpt5-codex": "gpt-5-codex",
    "gpt-5-codex": "gpt-5-codex",
    "gpt-5-codex-latest": "gpt-5-codex",
    "gpt-5.1-codex": "gpt-5.1-codex",
    "gpt-5.1-codex-max": "gpt-5.1-codex-max",
    "gpt-5.1-codex-mini": "gpt-5.1-codex-mini",
    # ChatGPT-backed Codex rejects codex-mini-latest, so keep legacy aliases on a supported mini model.
    "codex": "gpt-5.1-codex-mini",
    "codex-mini": "gpt-5.1-codex-mini",
    "codex-mini-latest": "gpt-5.1-codex-mini",
}

PUBLIC_MODEL_ALIASES = ["codex", "codex-mini"]


def _log_json(prefix: str, payload: Any) -> None:
    try:
        print(f"{prefix}\n{json.dumps(payload, indent=2, ensure_ascii=False)}")
    except Exception:
        try:
            print(f"{prefix}\n{payload}")
        except Exception:
            pass


def normalize_model_name(name: str | None, debug_model: str | None = None) -> str:
    if isinstance(debug_model, str) and debug_model.strip():
        return debug_model.strip()
    if not isinstance(name, str) or not name.strip():
        return "gpt-5"
    base = name.split(":", 1)[0].strip()
    for sep in ("-", "_"):
        lowered = base.lower()
        for effort in ("minimal", "low", "medium", "high", "xhigh"):
            suffix = f"{sep}{effort}"
            if lowered.endswith(suffix):
                base = base[: -len(suffix)]
                break
    return MODEL_NAME_MAPPING.get(base, base)


def list_public_model_ids(expose_variants: bool) -> List[str]:
    model_ids: List[str] = []
    for base, efforts in SUPPORTED_MODEL_GROUPS:
        model_ids.append(base)
        if expose_variants:
            model_ids.extend([f"{base}-{effort}" for effort in efforts])
    for alias in PUBLIC_MODEL_ALIASES:
        if alias not in model_ids:
            model_ids.append(alias)
    return model_ids


def start_upstream_request(
    model: str,
    input_items: List[Dict[str, Any]],
    *,
    instructions: str | None = None,
    tools: List[Dict[str, Any]] | None = None,
    tool_choice: Any | None = None,
    parallel_tool_calls: bool = False,
    reasoning_param: Dict[str, Any] | None = None,
):
    auth_candidates = get_effective_chatgpt_auth_candidates(ensure_fresh=True)
    if not auth_candidates:
        resp = make_response(
            jsonify(
                {
                    "error": {
                        "message": (
                            "Missing ChatGPT credentials. Run 'python3 chatmock.py login' first, "
                            "or configure CHATGPT_LOCAL_AUTH_FILES/auth_pool.json for multi-account mode."
                        ),
                    }
                }
            ),
            401,
        )
        for k, v in build_cors_headers().items():
            resp.headers.setdefault(k, v)
        return None, resp

    include: List[str] = []
    if isinstance(reasoning_param, dict):
        include.append("reasoning.encrypted_content")

    client_session_id = None
    try:
        client_session_id = (
            flask_request.headers.get("X-Session-Id")
            or flask_request.headers.get("session_id")
            or None
        )
    except Exception:
        client_session_id = None
    session_id = ensure_session_id(instructions, input_items, client_session_id)

    responses_payload = {
        "model": model,
        "instructions": instructions if isinstance(instructions, str) and instructions.strip() else instructions,
        "input": input_items,
        "tools": tools or [],
        "tool_choice": tool_choice if tool_choice in ("auto", "none") or isinstance(tool_choice, dict) else "auto",
        "parallel_tool_calls": bool(parallel_tool_calls),
        "store": False,
        "stream": True,
        "prompt_cache_key": session_id,
    }
    if include:
        responses_payload["include"] = include

    if reasoning_param is not None:
        responses_payload["reasoning"] = reasoning_param

    verbose = False
    try:
        verbose = bool(current_app.config.get("VERBOSE"))
    except Exception:
        verbose = False
    if verbose:
        _log_json("OUTBOUND >> ChatGPT Responses API payload", responses_payload)

    retryable_statuses = {401, 403, 429, 500, 502, 503, 504}
    last_error_resp = None
    last_exception = None
    last_upstream = None

    for idx, candidate in enumerate(auth_candidates):
        access_token = candidate.get("access_token")
        account_id = candidate.get("account_id")
        label = candidate.get("label") or f"candidate-{idx + 1}"
        if not access_token or not account_id:
            continue

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "chatgpt-account-id": account_id,
            "OpenAI-Beta": "responses=experimental",
            "session_id": session_id,
        }

        try:
            upstream = requests.post(
                CHATGPT_RESPONSES_URL,
                headers=headers,
                json=responses_payload,
                stream=True,
                timeout=600,
            )
        except requests.RequestException as e:
            last_exception = e
            if verbose:
                print(f"Upstream request failed for {label}: {e}")
            continue

        last_upstream = upstream
        should_retry = (
            upstream.status_code in retryable_statuses
            and idx < len(auth_candidates) - 1
        )
        if should_retry:
            if verbose:
                print(
                    f"Upstream status {upstream.status_code} for {label}; "
                    "retrying with next account."
                )
            try:
                upstream.close()
            except Exception:
                pass
            continue
        return upstream, None

    if last_upstream is not None:
        return last_upstream, None

    if last_exception is not None:
        last_error_resp = make_response(
            jsonify({"error": {"message": f"Upstream ChatGPT request failed: {last_exception}"}}),
            502,
        )
    else:
        last_error_resp = make_response(
            jsonify({"error": {"message": "No valid ChatGPT account is available."}}),
            401,
        )
    for k, v in build_cors_headers().items():
        last_error_resp.headers.setdefault(k, v)
    return None, last_error_resp
