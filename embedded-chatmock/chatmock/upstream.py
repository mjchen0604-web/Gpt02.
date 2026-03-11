from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Tuple

import requests
from flask import Response, current_app, jsonify, make_response

from .config import CHATGPT_RESPONSES_URL
from .codex_app_server import connect_codex_app_server
from .http import build_cors_headers
from .reasoning import split_model_alias
from .session import ensure_session_id
from flask import request as flask_request
from .utils import (
    get_effective_chatgpt_auth_candidates,
    get_max_retry_interval_seconds,
    get_request_retry_limit,
    get_retryable_statuses,
    mark_chatgpt_auth_result,
    remove_chatgpt_auth_candidate,
)


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
    base, _, _ = split_model_alias(name)
    mapping = {
        "gpt5": "gpt-5",
        "gpt-5-latest": "gpt-5",
        "gpt-5": "gpt-5",
        "gpt-5.1": "gpt-5.1",
        "gpt5.2": "gpt-5.2",
        "gpt-5.2": "gpt-5.2",
        "gpt-5.2-latest": "gpt-5.2",
        "gpt5.4": "gpt-5.4",
        "gpt-5.4": "gpt-5.4",
        "gpt-5.4-latest": "gpt-5.4",
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
        "codex": "codex-mini-latest",
        "codex-mini": "codex-mini-latest",
        "codex-mini-latest": "codex-mini-latest",
        "gpt-5.1-codex-mini": "gpt-5.1-codex-mini",
    }
    return mapping.get(base, base or "gpt-5")


def _normalize_backend_service_tier(service_tier: str | None) -> str | None:
    if not isinstance(service_tier, str) or not service_tier.strip():
        return None
    normalized = service_tier.strip().lower()
    if normalized in ("off", "none", "unset", "default"):
        return None
    if normalized == "fast":
        return "priority"
    return normalized


def _prefers_codex_app_server(model: str) -> bool:
    normalized_model = str(model or "").strip().lower()
    if "codex" in normalized_model or normalized_model.startswith("codex"):
        return True
    if normalized_model.startswith("gpt-5.4-fast"):
        return True
    return False


def _resolve_upstream_mode(configured_mode: str, model: str) -> str:
    normalized_mode = str(configured_mode or "").strip().lower()
    if normalized_mode in ("", "default"):
        normalized_mode = "auto"
    if normalized_mode != "auto":
        return normalized_mode
    if _prefers_codex_app_server(model):
        return "codex-app-server"
    return "chatgpt-backend"


def _start_codex_app_server_request(
    model: str,
    input_items: List[Dict[str, Any]],
    *,
    instructions: str | None = None,
    tools: List[Dict[str, Any]] | None = None,
    tool_choice: Any | None = None,
    parallel_tool_calls: bool = False,
    reasoning_param: Dict[str, Any] | None = None,
    service_tier: str | None = None,
    web_search_mode: str | None = None,
    verbose: bool = False,
):
    app_server_url = str(current_app.config.get("CODEX_APP_SERVER_URL") or "").strip()
    if not app_server_url:
        resp = make_response(
            jsonify({"error": {"message": "Missing CODEX_APP_SERVER_URL for codex-app-server upstream"}}),
            500,
        )
        for k, v in build_cors_headers().items():
            resp.headers.setdefault(k, v)
        return None, resp

    manager = current_app.config.get("CODEX_APP_SERVER_MANAGER")
    candidates: List[Dict[str, str]] = []
    if manager is not None and hasattr(manager, "get_request_candidates"):
        try:
            candidates = list(manager.get_request_candidates() or [])
        except Exception as exc:
            if verbose:
                print(f"codex app-server pool candidate lookup failed: {exc}")
            candidates = []
    if not candidates:
        candidates = [{"label": "default", "url": app_server_url}]

    last_error = None
    for candidate in candidates:
        candidate_url = str(candidate.get("url") or "").strip() or app_server_url
        candidate_label = str(candidate.get("label") or "default").strip() or "default"
        if verbose:
            print(f"codex app-server candidate -> {candidate_label} @ {candidate_url}")
        try:
            upstream = connect_codex_app_server(
                app_server_url=candidate_url,
                model=model,
                input_items=input_items,
                instructions=instructions,
                tools=tools,
                tool_choice=tool_choice,
                parallel_tool_calls=parallel_tool_calls,
                reasoning_param=reasoning_param,
                service_tier=service_tier,
                web_search_mode=web_search_mode,
                verbose=verbose,
            )
        except Exception as exc:
            last_error = exc
            if manager is not None and hasattr(manager, "mark_request_result"):
                try:
                    manager.mark_request_result(candidate_label, success=False, error_message=str(exc))
                except Exception:
                    pass
            if verbose:
                print(f"codex app-server upstream failed for {candidate_label} ({candidate_url}): {exc}")
            continue
        if manager is not None and hasattr(manager, "wrap_upstream"):
            try:
                upstream = manager.wrap_upstream(candidate_label, upstream)
            except Exception:
                pass
        return upstream, None

    resp = make_response(
        jsonify(
            {
                "error": {
                    "message": f"codex app-server upstream failed for all candidates: {last_error or 'no candidates available'}"
                }
            }
        ),
        502,
    )
    for k, v in build_cors_headers().items():
        resp.headers.setdefault(k, v)
    return None, resp


def _start_chatgpt_backend_request(
    model: str,
    input_items: List[Dict[str, Any]],
    *,
    instructions: str | None = None,
    tools: List[Dict[str, Any]] | None = None,
    tool_choice: Any | None = None,
    parallel_tool_calls: bool = False,
    reasoning_param: Dict[str, Any] | None = None,
    service_tier: str | None = None,
    verbose: bool = False,
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
    backend_service_tier = _normalize_backend_service_tier(service_tier)
    if isinstance(backend_service_tier, str) and backend_service_tier:
        responses_payload["service_tier"] = backend_service_tier

    if verbose:
        _log_json("OUTBOUND >> ChatGPT Responses API payload", responses_payload)

    retryable_statuses = get_retryable_statuses()
    request_retry_limit = get_request_retry_limit()
    max_retry_interval = get_max_retry_interval_seconds()
    last_exception = None
    last_upstream = None

    for round_idx in range(request_retry_limit + 1):
        if round_idx > 0:
            sleep_secs = min(max_retry_interval, 2 ** (round_idx - 1))
            if verbose:
                print(f"Retry round {round_idx}/{request_retry_limit} after {sleep_secs}s")
            time.sleep(sleep_secs)

        round_candidates = get_effective_chatgpt_auth_candidates(ensure_fresh=True)
        if not round_candidates:
            break

        for idx, candidate in enumerate(round_candidates):
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
            except requests.RequestException as exc:
                last_exception = exc
                mark_chatgpt_auth_result(label, success=False, error_message=str(exc))
                if verbose:
                    print(f"Upstream request failed for {label}: {exc}")
                continue

            last_upstream = upstream
            status = int(upstream.status_code or 0)
            if status == 402:
                mark_chatgpt_auth_result(label, success=False, status_code=status)
                remove_chatgpt_auth_candidate(
                    candidate,
                    reason=f"HTTP 402 while requesting model {model}",
                )
                try:
                    upstream.close()
                except Exception:
                    pass
                if verbose:
                    print(f"Upstream status 402 for {label}; removed account and trying next candidate.")
                continue
            should_retry = status in retryable_statuses
            has_more_candidates = idx < len(round_candidates) - 1
            has_more_rounds = round_idx < request_retry_limit

            if should_retry:
                mark_chatgpt_auth_result(label, success=False, status_code=status)
                if has_more_candidates or has_more_rounds:
                    if verbose:
                        print(f"Upstream status {status} for {label}; retrying with next account.")
                    try:
                        upstream.close()
                    except Exception:
                        pass
                    continue
                return upstream, None

            mark_chatgpt_auth_result(label, success=True, status_code=status)
            return upstream, None

    if last_upstream is not None:
        return last_upstream, None

    if last_exception is not None:
        resp = make_response(
            jsonify({"error": {"message": f"Upstream ChatGPT request failed: {last_exception}"}}),
            502,
        )
    else:
        resp = make_response(
            jsonify({"error": {"message": "No valid ChatGPT account is available."}}),
            401,
        )
    for k, v in build_cors_headers().items():
        resp.headers.setdefault(k, v)
    return None, resp


def start_upstream_request(
    model: str,
    input_items: List[Dict[str, Any]],
    *,
    instructions: str | None = None,
    tools: List[Dict[str, Any]] | None = None,
    tool_choice: Any | None = None,
    parallel_tool_calls: bool = False,
    reasoning_param: Dict[str, Any] | None = None,
    service_tier: str | None = None,
    web_search_mode: str | None = None,
):
    upstream_mode = str(current_app.config.get("UPSTREAM_MODE") or "chatgpt-backend").strip().lower()
    verbose = False
    try:
        verbose = bool(current_app.config.get("VERBOSE"))
    except Exception:
        verbose = False
    selected_mode = _resolve_upstream_mode(upstream_mode, model)
    if verbose and upstream_mode == "auto":
        print(f"auto upstream -> selected {selected_mode} for model {model}")
    if selected_mode == "codex-app-server":
        return _start_codex_app_server_request(
            model,
            input_items,
            instructions=instructions,
            tools=tools,
            tool_choice=tool_choice,
            parallel_tool_calls=parallel_tool_calls,
            reasoning_param=reasoning_param,
            service_tier=service_tier,
            web_search_mode=web_search_mode,
            verbose=verbose,
        )
    return _start_chatgpt_backend_request(
        model,
        input_items,
        instructions=instructions,
        tools=tools,
        tool_choice=tool_choice,
        parallel_tool_calls=parallel_tool_calls,
        reasoning_param=reasoning_param,
        service_tier=service_tier,
        verbose=verbose,
    )
