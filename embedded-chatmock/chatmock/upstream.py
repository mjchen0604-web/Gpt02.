from __future__ import annotations

import json
from typing import Any, Dict, List

from .codex_app_server import CodexAppServerError, connect_codex_app_server
from .reasoning import split_model_alias
from .upstream_errors import build_error_info, build_openai_error_response


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
        "gpt-5.1-codex-mini": "gpt-5.1-codex-mini",
    }
    return mapping.get(base, base or "gpt-5")


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
    thread_session: Dict[str, Any] | None = None,
):
    from flask import current_app

    verbose = False
    try:
        verbose = bool(current_app.config.get("VERBOSE"))
    except Exception:
        verbose = False

    app_server_url = str(current_app.config.get("CODEX_APP_SERVER_URL") or "").strip()
    if not app_server_url:
        return None, build_openai_error_response(
            build_error_info(
                source="chatmock",
                phase="config",
                raw_status=500,
                raw_message="Missing CODEX_APP_SERVER_URL for codex-app-server upstream",
                raw_body={"message": "Missing CODEX_APP_SERVER_URL for codex-app-server upstream"},
            )
        )

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

    preferred_label = None
    preferred_url = None
    if isinstance(thread_session, dict):
        preferred_label = str(thread_session.get("candidate_label") or "").strip() or None
        preferred_url = str(thread_session.get("candidate_url") or "").strip() or None
    if preferred_label or preferred_url:
        preferred_candidates = []
        other_candidates = []
        for candidate in candidates:
            candidate_label = str(candidate.get("label") or "").strip()
            candidate_url = str(candidate.get("url") or "").strip()
            if (preferred_label and candidate_label == preferred_label) or (
                preferred_url and candidate_url == preferred_url
            ):
                preferred_candidates.append(candidate)
            else:
                other_candidates.append(candidate)
        candidates = preferred_candidates + other_candidates

    last_error = None
    last_error_info = None
    for candidate in candidates:
        candidate_url = str(candidate.get("url") or "").strip() or app_server_url
        candidate_label = str(candidate.get("label") or "default").strip() or "default"
        if verbose:
            print(f"codex app-server candidate -> {candidate_label} @ {candidate_url}")
        try:
            upstream = connect_codex_app_server(
                app_server_url=candidate_url,
                candidate_label=candidate_label,
                model=model,
                input_items=input_items,
                instructions=instructions,
                tools=tools,
                tool_choice=tool_choice,
                parallel_tool_calls=parallel_tool_calls,
                reasoning_param=reasoning_param,
                service_tier=service_tier,
                web_search_mode=web_search_mode,
                thread_session=thread_session,
                verbose=verbose,
            )
        except Exception as exc:
            last_error = exc
            status_code = exc.status_code if isinstance(exc, CodexAppServerError) else None
            last_error_info = (
                exc.error_info
                if isinstance(exc, CodexAppServerError) and isinstance(exc.error_info, dict)
                else build_error_info(
                    source="codex-app-server",
                    phase="connect",
                    raw_status=status_code,
                    raw_message=str(exc),
                    raw_body={"exception": str(exc)},
                )
            )
            if manager is not None and hasattr(manager, "mark_request_result"):
                try:
                    manager.mark_request_result(
                        candidate_label,
                        success=False,
                        error_message=str(exc),
                        status_code=status_code,
                        error_info=last_error_info,
                    )
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
        try:
            upstream.chatmock_source = "codex-app-server"
        except Exception:
            pass
        return upstream, None

    if last_error_info is None:
        last_error_info = build_error_info(
            source="codex-app-server",
            phase="connect",
            raw_status=502,
            raw_message=f"codex app-server upstream failed for all candidates: {last_error or 'no candidates available'}",
            raw_body={"message": f"codex app-server upstream failed for all candidates: {last_error or 'no candidates available'}"},
        )
    return None, build_openai_error_response(last_error_info)
