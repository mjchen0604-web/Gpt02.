from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from flask import current_app

from .codex_app_server import CodexAppServerError, connect_codex_app_server
from .model_profiles import is_public_chatmock_model
from .reasoning import split_model_alias


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


def _prefers_codex_app_server(model: str) -> bool:
    return is_public_chatmock_model(model)


def _resolve_upstream_mode(configured_mode: str, model: str) -> str:
    return "codex-app-server"


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
            status_code = exc.status_code if isinstance(exc, CodexAppServerError) else None
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
    upstream_mode = str(current_app.config.get("UPSTREAM_MODE") or "codex-app-server").strip().lower()
    verbose = False
    try:
        verbose = bool(current_app.config.get("VERBOSE"))
    except Exception:
        verbose = False
    selected_mode = _resolve_upstream_mode(upstream_mode, model)
    if verbose:
        print(f"auto upstream -> selected {selected_mode} for model {model}")
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
