from __future__ import annotations

import json
import time
from typing import Any, Dict, List

from flask import Blueprint, Response, current_app, jsonify, make_response, request

from .config import BASE_INSTRUCTIONS, GPT5_CODEX_INSTRUCTIONS
from .limits import record_rate_limits_from_response
from .http import build_cors_headers
from .reasoning import (
    allowed_efforts_for_model,
    apply_reasoning_to_message,
    build_reasoning_param,
    extract_reasoning_from_model_name,
    extract_service_tier_from_model_name,
)
from .upstream_errors import (
    build_error_info,
    build_openai_error_response,
    error_info_from_event_response,
    error_info_from_flask_response,
    error_info_from_http_response,
    normalized_error_payload,
    should_retry_next_candidate,
)
from .upstream import normalize_model_name, start_upstream_request
from .thread_sessions import build_thread_session_state
from .utils import (
    RetryableStreamError,
    convert_chat_messages_to_responses_input,
    convert_tools_chat_to_responses,
    sse_translate_chat,
    sse_translate_text,
)


openai_bp = Blueprint("openai", __name__)


def _log_json(prefix: str, payload: Any) -> None:
    try:
        print(f"{prefix}\n{json.dumps(payload, indent=2, ensure_ascii=False)}")
    except Exception:
        try:
            print(f"{prefix}\n{payload}")
        except Exception:
            pass


def _wrap_stream_logging(label: str, iterator, enabled: bool):
    if not enabled:
        return iterator

    def _gen():
        for chunk in iterator:
            try:
                text = (
                    chunk.decode("utf-8", errors="replace")
                    if isinstance(chunk, (bytes, bytearray))
                    else str(chunk)
                )
                print(f"{label}\n{text}")
            except Exception:
                pass
            yield chunk

    return _gen()


def _instructions_for_model(model: str) -> str:
    base = current_app.config.get("BASE_INSTRUCTIONS", BASE_INSTRUCTIONS)
    if "codex" in (model or "").lower():
        codex = current_app.config.get("GPT5_CODEX_INSTRUCTIONS") or GPT5_CODEX_INSTRUCTIONS
        if isinstance(codex, str) and codex.strip():
            return codex
    return base


def _upstream_attempt_limit(is_stream: bool) -> int:
    if is_stream:
        return 1
    manager = current_app.config.get("CODEX_APP_SERVER_MANAGER")
    if manager is not None and hasattr(manager, "get_request_candidates"):
        try:
            return max(1, len(manager.get_request_candidates() or []))
        except Exception:
            return 1
    return 1


def _resolve_service_tier(payload: Dict[str, Any], requested_model: str | None = None) -> str | None:
    request_value = payload.get("service_tier")
    if isinstance(request_value, str):
        normalized = request_value.strip().lower()
        if normalized in ("", "off", "none", "unset"):
            return None
        return normalized
    alias_value = extract_service_tier_from_model_name(requested_model)
    if isinstance(alias_value, str) and alias_value:
        return alias_value
    configured = current_app.config.get("SERVICE_TIER")
    if isinstance(configured, str) and configured.strip():
        normalized = configured.strip().lower()
        if normalized in ("off", "none", "unset"):
            return None
        return normalized
    return None


def _first_non_empty(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _resolve_thread_session(payload: Dict[str, Any], input_items: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    session_key = _first_non_empty(
        payload.get("session_id"),
        metadata.get("session_id"),
        payload.get("conversation_id"),
        metadata.get("conversation_id"),
        request.headers.get("x-session-id"),
        request.headers.get("session_id"),
        request.headers.get("x-conversation-id"),
        request.headers.get("conversation_id"),
    )
    explicit_thread_id = _first_non_empty(
        payload.get("thread_id"),
        metadata.get("thread_id"),
        request.headers.get("x-thread-id"),
        request.headers.get("thread_id"),
    )
    fork_from_thread_id = _first_non_empty(
        payload.get("fork_from_thread_id"),
        metadata.get("fork_from_thread_id"),
        request.headers.get("x-fork-from-thread-id"),
        request.headers.get("fork_from_thread_id"),
    )
    return build_thread_session_state(
        session_key=session_key,
        input_items=input_items,
        explicit_thread_id=explicit_thread_id,
        fork_from_thread_id=fork_from_thread_id,
    )


def _resolve_web_search_mode(
    payload: Dict[str, Any],
    tools_payload: List[Dict[str, Any]],
    responses_tools_payload: List[Dict[str, Any]],
) -> str:
    request_value = payload.get("web_search_mode")
    if isinstance(request_value, str):
        normalized = request_value.strip().lower()
        if normalized in ("disabled", "off", "none", "unset", "false"):
            return "disabled"
        if normalized in ("cached", "preview", "web_search_preview"):
            return "cached"
        if normalized in ("live", "on", "true", "web_search"):
            return "live"

    responses_tool_choice = payload.get("responses_tool_choice")
    if isinstance(responses_tool_choice, str) and responses_tool_choice.strip().lower() == "none":
        return "disabled"

    requested_modes: List[str] = []
    for tool in list(tools_payload or []) + list(responses_tools_payload or []):
        if not isinstance(tool, dict):
            continue
        tool_type = tool.get("type")
        if tool_type == "web_search":
            requested_modes.append("live")
        elif tool_type == "web_search_preview":
            requested_modes.append("cached")

    if "live" in requested_modes:
        return "live"
    if "cached" in requested_modes:
        return "cached"
    if bool(current_app.config.get("DEFAULT_WEB_SEARCH")):
        return "live"
    return "disabled"


def _should_retry_nonstream_candidate(error_info: Dict[str, Any] | None) -> bool:
    if not isinstance(error_info, dict):
        return False
    if should_retry_next_candidate(error_info):
        return True
    source = str(error_info.get("source") or "").strip().lower()
    return source == "codex-app-server"


def _consume_chat_completion_nonstream(
    upstream: Any,
    *,
    requested_model: str | None,
    model: str,
    created: int,
    reasoning_compat: str,
) -> Dict[str, Any]:
    full_text = ""
    reasoning_summary_text = ""
    reasoning_full_text = ""
    response_id = "chatcmpl"
    tool_calls: List[Dict[str, Any]] = []
    error_message: str | None = None
    error_info: Dict[str, Any] | None = None
    usage_obj: Dict[str, int] | None = None
    observed_service_tier: str | None = None
    completed_ok = False

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
        for raw in upstream.iter_lines(decode_unicode=False):
            if not raw:
                continue
            line = raw.decode("utf-8", errors="ignore") if isinstance(raw, (bytes, bytearray)) else raw
            if not line.startswith("data: "):
                continue
            data = line[len("data: "):].strip()
            if not data:
                continue
            if data == "[DONE]":
                break
            try:
                evt = json.loads(data)
            except Exception:
                continue
            kind = evt.get("type")
            mu = _extract_usage(evt)
            if mu:
                usage_obj = mu
            if isinstance(evt.get("response"), dict) and isinstance(evt["response"].get("id"), str):
                response_id = evt["response"].get("id") or response_id
            if isinstance(evt.get("response"), dict) and isinstance(evt["response"].get("service_tier"), str):
                observed_service_tier = evt["response"].get("service_tier") or observed_service_tier
            if kind == "response.output_text.delta":
                full_text += evt.get("delta") or ""
            elif kind == "response.reasoning_summary_text.delta":
                reasoning_summary_text += evt.get("delta") or ""
            elif kind == "response.reasoning_text.delta":
                reasoning_full_text += evt.get("delta") or ""
            elif kind == "response.output_item.done":
                item = evt.get("item") or {}
                if isinstance(item, dict) and item.get("type") == "function_call":
                    call_id = item.get("call_id") or item.get("id") or ""
                    name = item.get("name") or ""
                    args = item.get("arguments") or ""
                    if not isinstance(args, str):
                        try:
                            args = json.dumps(args, ensure_ascii=False)
                        except Exception:
                            args = "{}"
                    if isinstance(call_id, str) and isinstance(name, str) and isinstance(args, str):
                        tool_calls.append(
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {"name": name, "arguments": args},
                            }
                        )
            elif kind == "response.failed":
                error_info = error_info_from_event_response(
                    getattr(upstream, "chatmock_source", "upstream"),
                    "stream",
                    evt.get("response"),
                )
                error_message = error_info.get("raw_message") or "response.failed"
            elif kind == "response.completed":
                completed_ok = True
                break
    finally:
        if completed_ok and hasattr(upstream, "mark_success"):
            try:
                upstream.mark_success()
            except Exception:
                pass
        elif error_message and hasattr(upstream, "mark_failure"):
            try:
                upstream.mark_failure(error_message)
            except Exception:
                pass
        upstream.close()

    if error_message:
        if error_info is None:
            error_info = build_error_info(
                source=getattr(upstream, "chatmock_source", "upstream"),
                phase="stream",
                raw_status=int(getattr(upstream, "status_code", 502) or 502),
                raw_message=error_message,
                raw_body={"message": error_message},
            )
        return {"ok": False, "error_info": error_info}

    if tool_calls:
        message = {"role": "assistant", "content": None, "tool_calls": tool_calls}
    else:
        message = {"role": "assistant", "content": full_text if full_text else None}
        message = apply_reasoning_to_message(message, reasoning_summary_text, reasoning_full_text, reasoning_compat)

    return {
        "ok": True,
        "response_id": response_id or "chatcmpl",
        "message": message,
        "usage_obj": usage_obj,
        "observed_service_tier": observed_service_tier,
        "created": created,
        "model": requested_model or model,
    }


def _consume_text_completion_nonstream(
    upstream: Any,
    *,
    requested_model: str | None,
    model: str,
    created: int,
) -> Dict[str, Any]:
    full_text = ""
    response_id = "cmpl"
    usage_obj: Dict[str, int] | None = None
    observed_service_tier: str | None = None
    completed_ok = False
    error_message: str | None = None
    error_info: Dict[str, Any] | None = None

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
            if not line.startswith("data: "):
                continue
            data = line[len("data: "):].strip()
            if not data or data == "[DONE]":
                if data == "[DONE]":
                    break
                continue
            try:
                evt = json.loads(data)
            except Exception:
                continue
            if isinstance(evt.get("response"), dict) and isinstance(evt["response"].get("id"), str):
                response_id = evt["response"].get("id") or response_id
            if isinstance(evt.get("response"), dict) and isinstance(evt["response"].get("service_tier"), str):
                observed_service_tier = evt["response"].get("service_tier") or observed_service_tier
            mu = _extract_usage(evt)
            if mu:
                usage_obj = mu
            kind = evt.get("type")
            if kind == "response.output_text.delta":
                full_text += evt.get("delta") or ""
            elif kind == "response.failed":
                error_info = error_info_from_event_response(
                    getattr(upstream, "chatmock_source", "upstream"),
                    "stream",
                    evt.get("response"),
                )
                error_message = error_info.get("raw_message") or "response.failed"
            elif kind == "response.completed":
                completed_ok = True
                break
    finally:
        if completed_ok and hasattr(upstream, "mark_success"):
            try:
                upstream.mark_success()
            except Exception:
                pass
        upstream.close()

    if error_message:
        if error_info is None:
            error_info = build_error_info(
                source=getattr(upstream, "chatmock_source", "upstream"),
                phase="stream",
                raw_status=int(getattr(upstream, "status_code", 502) or 502),
                raw_message=error_message,
                raw_body={"message": error_message},
            )
        return {"ok": False, "error_info": error_info}

    return {
        "ok": True,
        "response_id": response_id or "cmpl",
        "full_text": full_text,
        "usage_obj": usage_obj,
        "observed_service_tier": observed_service_tier,
        "created": created,
        "model": requested_model or model,
    }


@openai_bp.route("/v1/chat/completions", methods=["POST"])
def chat_completions() -> Response:
    verbose = bool(current_app.config.get("VERBOSE"))
    verbose_obfuscation = bool(current_app.config.get("VERBOSE_OBFUSCATION"))
    expose_service_tier = bool(current_app.config.get("EXPOSE_SERVICE_TIER"))
    expose_thread_ids = bool(current_app.config.get("EXPOSE_THREAD_IDS"))
    reasoning_effort = current_app.config.get("REASONING_EFFORT", "medium")
    reasoning_summary = current_app.config.get("REASONING_SUMMARY", "auto")
    reasoning_compat = current_app.config.get("REASONING_COMPAT", "think-tags")
    debug_model = current_app.config.get("DEBUG_MODEL")

    raw = request.get_data(cache=True, as_text=True) or ""
    if verbose:
        try:
            print("IN POST /v1/chat/completions\n" + raw)
        except Exception:
            pass
    try:
        payload = json.loads(raw) if raw else {}
    except Exception:
        try:
            payload = json.loads(raw.replace("\r", "").replace("\n", ""))
        except Exception:
            err = {"error": {"message": "Invalid JSON body"}}
            if verbose:
                _log_json("OUT POST /v1/chat/completions", err)
            return jsonify(err), 400

    requested_model = payload.get("model")
    model = normalize_model_name(requested_model, debug_model)
    messages = payload.get("messages")
    if messages is None and isinstance(payload.get("prompt"), str):
        messages = [{"role": "user", "content": payload.get("prompt") or ""}]
    if messages is None and isinstance(payload.get("input"), str):
        messages = [{"role": "user", "content": payload.get("input") or ""}]
    if messages is None:
        messages = []
    if not isinstance(messages, list):
        err = {"error": {"message": "Request must include messages: []"}}
        if verbose:
            _log_json("OUT POST /v1/chat/completions", err)
        return jsonify(err), 400

    if isinstance(messages, list):
        sys_idx = next((i for i, m in enumerate(messages) if isinstance(m, dict) and m.get("role") == "system"), None)
        if isinstance(sys_idx, int):
            sys_msg = messages.pop(sys_idx)
            content = sys_msg.get("content") if isinstance(sys_msg, dict) else ""
            messages.insert(0, {"role": "user", "content": content})
    is_stream = bool(payload.get("stream"))
    stream_options = payload.get("stream_options") if isinstance(payload.get("stream_options"), dict) else {}
    include_usage = bool(stream_options.get("include_usage", False))

    raw_tools_payload = payload.get("tools") if isinstance(payload.get("tools"), list) else []
    tools_responses = convert_tools_chat_to_responses(raw_tools_payload)
    tool_choice = payload.get("tool_choice", "auto")
    parallel_tool_calls = bool(payload.get("parallel_tool_calls", False))
    responses_tools_payload = payload.get("responses_tools") if isinstance(payload.get("responses_tools"), list) else []
    builtin_search_tools: List[Dict[str, Any]] = []
    had_builtin_search_tools = False
    for _t in raw_tools_payload:
        if not (isinstance(_t, dict) and isinstance(_t.get("type"), str)):
            continue
        if _t.get("type") in ("web_search", "web_search_preview"):
            builtin_search_tools.append({"type": _t.get("type")})
    if isinstance(responses_tools_payload, list):
        for _t in responses_tools_payload:
            if not (isinstance(_t, dict) and isinstance(_t.get("type"), str)):
                continue
            if _t.get("type") not in ("web_search", "web_search_preview"):
                err = {
                    "error": {
                        "message": "Only web_search/web_search_preview are supported in responses_tools",
                        "code": "RESPONSES_TOOL_UNSUPPORTED",
                    }
                }
                if verbose:
                    _log_json("OUT POST /v1/chat/completions", err)
                return jsonify(err), 400
            builtin_search_tools.append({"type": _t.get("type")})

        if not builtin_search_tools and bool(current_app.config.get("DEFAULT_WEB_SEARCH")):
            responses_tool_choice = payload.get("responses_tool_choice")
            if not (isinstance(responses_tool_choice, str) and responses_tool_choice == "none"):
                builtin_search_tools = [{"type": "web_search"}]

        if builtin_search_tools:
            import json as _json
            MAX_TOOLS_BYTES = 32768
            try:
                size = len(_json.dumps(builtin_search_tools))
            except Exception:
                size = 0
            if size > MAX_TOOLS_BYTES:
                err = {"error": {"message": "responses_tools too large", "code": "RESPONSES_TOOLS_TOO_LARGE"}}
                if verbose:
                    _log_json("OUT POST /v1/chat/completions", err)
                return jsonify(err), 400
            had_builtin_search_tools = True
            tools_responses = (tools_responses or []) + builtin_search_tools

    responses_tool_choice = payload.get("responses_tool_choice")
    if isinstance(responses_tool_choice, str) and responses_tool_choice in ("auto", "none"):
        tool_choice = responses_tool_choice

    input_items = convert_chat_messages_to_responses_input(messages)
    if not input_items and isinstance(payload.get("prompt"), str) and payload.get("prompt").strip():
        input_items = [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": payload.get("prompt")}]}
        ]
    thread_session = _resolve_thread_session(payload, input_items)

    model_reasoning = extract_reasoning_from_model_name(requested_model)
    reasoning_overrides = payload.get("reasoning") if isinstance(payload.get("reasoning"), dict) else model_reasoning
    service_tier = _resolve_service_tier(payload, requested_model)
    web_search_mode = _resolve_web_search_mode(payload, raw_tools_payload, responses_tools_payload)
    reasoning_param = build_reasoning_param(
        reasoning_effort,
        reasoning_summary,
        reasoning_overrides,
        allowed_efforts=allowed_efforts_for_model(model),
    )

    attempt_limit = _upstream_attempt_limit(is_stream)
    last_error_info: Dict[str, Any] | None = None
    upstream = None
    created = int(time.time())
    nonstream_result: Dict[str, Any] | None = None
    for attempt_index in range(attempt_limit):
        upstream, error_resp = start_upstream_request(
            model,
            input_items,
            instructions=_instructions_for_model(model),
            tools=tools_responses,
            tool_choice=tool_choice,
            parallel_tool_calls=parallel_tool_calls,
            reasoning_param=reasoning_param,
            service_tier=service_tier,
            web_search_mode=web_search_mode,
            thread_session=thread_session,
        )
        if error_resp is not None:
            error_info = error_info_from_flask_response("chatcore", "request_start", error_resp)
            last_error_info = error_info
            if not is_stream and should_retry_next_candidate(error_info) and attempt_index + 1 < attempt_limit:
                continue
            return build_openai_error_response(error_info)

        record_rate_limits_from_response(upstream)
        created = int(time.time())
        if upstream.status_code >= 400:
            error_info = error_info_from_http_response(getattr(upstream, "chatmock_source", "upstream"), "http", upstream)
            last_error_info = error_info
            if had_builtin_search_tools:
                if verbose:
                    print("[Passthrough] Upstream rejected tools; retrying without extra tools (args redacted)")
                base_tools_only = convert_tools_chat_to_responses(payload.get("tools"))
                safe_choice = payload.get("tool_choice", "auto")
                upstream2, err2 = start_upstream_request(
                    model,
                    input_items,
                    instructions=BASE_INSTRUCTIONS,
                    tools=base_tools_only,
                    tool_choice=safe_choice,
                    parallel_tool_calls=parallel_tool_calls,
                    reasoning_param=reasoning_param,
                    service_tier=service_tier,
                    web_search_mode="disabled",
                    thread_session=thread_session,
                )
                record_rate_limits_from_response(upstream2)
                if err2 is None and upstream2 is not None and upstream2.status_code < 400:
                    upstream = upstream2
                    if is_stream:
                        break
                if err2 is not None:
                    error_info = error_info_from_flask_response("chatcore", "tool_retry", err2)
                elif upstream2 is not None:
                    error_info = error_info_from_http_response(getattr(upstream2, "chatmock_source", "upstream"), "tool_retry", upstream2)
                last_error_info = error_info
            if not is_stream and should_retry_next_candidate(error_info) and attempt_index + 1 < attempt_limit:
                try:
                    upstream.close()
                except Exception:
                    pass
                continue
            return build_openai_error_response(error_info)
        if not is_stream:
            nonstream_result = _consume_chat_completion_nonstream(
                upstream,
                requested_model=requested_model,
                model=model,
                created=created,
                reasoning_compat=reasoning_compat,
            )
            if not nonstream_result.get("ok"):
                error_info = nonstream_result.get("error_info")
                if isinstance(error_info, dict):
                    last_error_info = error_info
                if _should_retry_nonstream_candidate(error_info) and attempt_index + 1 < attempt_limit:
                    upstream = None
                    nonstream_result = None
                    continue
                return build_openai_error_response(error_info or build_error_info(
                    source="chatcore",
                    phase="nonstream",
                    raw_status=502,
                    raw_message="Unknown upstream failure",
                    raw_body={"message": "Unknown upstream failure"},
                ))
            break
        break

    if upstream is None:
        return build_openai_error_response(
            last_error_info
            or build_error_info(
                source="chatcore",
                phase="retry_exhausted",
                raw_status=502,
                raw_message="No candidate succeeded",
                raw_body={"message": "No candidate succeeded"},
            )
        )

    if is_stream:
        if verbose:
            print("OUT POST /v1/chat/completions (streaming response)")

        def _retrying_stream():
            current_upstream = upstream
            current_created = created
            remaining_attempts = max(1, attempt_limit)
            while remaining_attempts > 0:
                try:
                    yield from sse_translate_chat(
                        current_upstream,
                        requested_model or model,
                        current_created,
                        verbose=verbose_obfuscation,
                        vlog=print if verbose_obfuscation else None,
                        reasoning_compat=reasoning_compat,
                        include_usage=include_usage,
                    )
                    return
                except RetryableStreamError as exc:
                    remaining_attempts -= 1
                    if remaining_attempts <= 0:
                        payload = {"error": normalized_error_payload(exc.error_info)}
                        yield f"data: {json.dumps(payload)}\n\n".encode("utf-8")
                        yield b"data: [DONE]\n\n"
                        return
                    next_upstream, next_error = start_upstream_request(
                        model,
                        input_items,
                        instructions=_instructions_for_model(model),
                        tools=tools_responses,
                        tool_choice=tool_choice,
                        parallel_tool_calls=parallel_tool_calls,
                        reasoning_param=reasoning_param,
                        service_tier=service_tier,
                        web_search_mode=web_search_mode,
                        thread_session=thread_session,
                    )
                    if next_error is not None:
                        next_error_info = error_info_from_flask_response("chatcore", "request_start", next_error)
                        payload = {"error": normalized_error_payload(next_error_info)}
                        yield f"data: {json.dumps(payload)}\n\n".encode("utf-8")
                        yield b"data: [DONE]\n\n"
                        return
                    current_upstream = next_upstream
                    current_created = int(time.time())

        stream_iter = _wrap_stream_logging("STREAM OUT /v1/chat/completions", _retrying_stream(), verbose)
        resp = Response(
            stream_iter,
            status=upstream.status_code,
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )
        if expose_service_tier and service_tier:
            resp.headers["X-ChatMock-Service-Tier-Requested"] = service_tier
        if expose_thread_ids and isinstance(getattr(upstream, "chatmock_thread_id", None), str):
            resp.headers["X-ChatMock-Thread-Id"] = upstream.chatmock_thread_id
            resp.headers["X-ChatMock-Thread-Mode"] = str(getattr(upstream, "chatmock_thread_mode", "start"))
        for k, v in build_cors_headers().items():
            resp.headers.setdefault(k, v)
        return resp

    if not isinstance(nonstream_result, dict) or not nonstream_result.get("ok"):
        return build_openai_error_response(
            last_error_info
            or build_error_info(
                source="chatcore",
                phase="nonstream",
                raw_status=502,
                raw_message="No candidate succeeded",
                raw_body={"message": "No candidate succeeded"},
            )
        )

    response_id = nonstream_result.get("response_id") or "chatcmpl"
    message = nonstream_result.get("message") or {"role": "assistant", "content": None}
    usage_obj = nonstream_result.get("usage_obj")
    observed_service_tier = nonstream_result.get("observed_service_tier")
    completion = {
        "id": response_id or "chatcmpl",
        "object": "chat.completion",
        "created": created,
        "model": requested_model or model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }
        ],
        **({"usage": usage_obj} if usage_obj else {}),
    }
    if expose_thread_ids and isinstance(getattr(upstream, "chatmock_thread_id", None), str):
        completion["thread_id"] = upstream.chatmock_thread_id
        completion["thread_mode"] = str(getattr(upstream, "chatmock_thread_mode", "start"))
    if expose_service_tier and observed_service_tier:
        completion["service_tier"] = observed_service_tier
    if verbose:
        _log_json("OUT POST /v1/chat/completions", completion)
    resp = make_response(jsonify(completion), upstream.status_code)
    if expose_service_tier and service_tier:
        resp.headers["X-ChatMock-Service-Tier-Requested"] = service_tier
    if expose_service_tier and observed_service_tier:
        resp.headers["X-ChatMock-Service-Tier-Observed"] = observed_service_tier
    if expose_thread_ids and isinstance(getattr(upstream, "chatmock_thread_id", None), str):
        resp.headers["X-ChatMock-Thread-Id"] = upstream.chatmock_thread_id
        resp.headers["X-ChatMock-Thread-Mode"] = str(getattr(upstream, "chatmock_thread_mode", "start"))
    for k, v in build_cors_headers().items():
        resp.headers.setdefault(k, v)
    return resp


@openai_bp.route("/v1/completions", methods=["POST"])
def completions() -> Response:
    verbose = bool(current_app.config.get("VERBOSE"))
    verbose_obfuscation = bool(current_app.config.get("VERBOSE_OBFUSCATION"))
    expose_service_tier = bool(current_app.config.get("EXPOSE_SERVICE_TIER"))
    expose_thread_ids = bool(current_app.config.get("EXPOSE_THREAD_IDS"))
    debug_model = current_app.config.get("DEBUG_MODEL")
    reasoning_effort = current_app.config.get("REASONING_EFFORT", "medium")
    reasoning_summary = current_app.config.get("REASONING_SUMMARY", "auto")

    raw = request.get_data(cache=True, as_text=True) or ""
    if verbose:
        try:
            print("IN POST /v1/completions\n" + raw)
        except Exception:
            pass
    try:
        payload = json.loads(raw) if raw else {}
    except Exception:
        err = {"error": {"message": "Invalid JSON body"}}
        if verbose:
            _log_json("OUT POST /v1/completions", err)
        return jsonify(err), 400

    requested_model = payload.get("model")
    model = normalize_model_name(requested_model, debug_model)
    prompt = payload.get("prompt")
    if isinstance(prompt, list):
        prompt = "".join([p if isinstance(p, str) else "" for p in prompt])
    if not isinstance(prompt, str):
        prompt = payload.get("suffix") or ""
    stream_req = bool(payload.get("stream", False))
    stream_options = payload.get("stream_options") if isinstance(payload.get("stream_options"), dict) else {}
    include_usage = bool(stream_options.get("include_usage", False))

    messages = [{"role": "user", "content": prompt or ""}]
    input_items = convert_chat_messages_to_responses_input(messages)
    thread_session = _resolve_thread_session(payload, input_items)

    model_reasoning = extract_reasoning_from_model_name(requested_model)
    reasoning_overrides = payload.get("reasoning") if isinstance(payload.get("reasoning"), dict) else model_reasoning
    service_tier = _resolve_service_tier(payload, requested_model)
    reasoning_param = build_reasoning_param(
        reasoning_effort,
        reasoning_summary,
        reasoning_overrides,
        allowed_efforts=allowed_efforts_for_model(model),
    )
    attempt_limit = _upstream_attempt_limit(stream_req)
    last_error_info: Dict[str, Any] | None = None
    upstream = None
    created = int(time.time())
    nonstream_result: Dict[str, Any] | None = None
    for attempt_index in range(attempt_limit):
        upstream, error_resp = start_upstream_request(
            model,
            input_items,
            instructions=_instructions_for_model(model),
            reasoning_param=reasoning_param,
            service_tier=service_tier,
            thread_session=thread_session,
        )
        if error_resp is not None:
            error_info = error_info_from_flask_response("chatcore", "request_start", error_resp)
            last_error_info = error_info
            if not stream_req and should_retry_next_candidate(error_info) and attempt_index + 1 < attempt_limit:
                continue
            return build_openai_error_response(error_info)

        record_rate_limits_from_response(upstream)
        created = int(time.time())
        if upstream.status_code >= 400:
            error_info = error_info_from_http_response(getattr(upstream, "chatmock_source", "upstream"), "http", upstream)
            last_error_info = error_info
            if not stream_req and should_retry_next_candidate(error_info) and attempt_index + 1 < attempt_limit:
                try:
                    upstream.close()
                except Exception:
                    pass
                continue
            return build_openai_error_response(error_info)
        if not stream_req:
            nonstream_result = _consume_text_completion_nonstream(
                upstream,
                requested_model=requested_model,
                model=model,
                created=created,
            )
            if not nonstream_result.get("ok"):
                error_info = nonstream_result.get("error_info")
                if isinstance(error_info, dict):
                    last_error_info = error_info
                if _should_retry_nonstream_candidate(error_info) and attempt_index + 1 < attempt_limit:
                    upstream = None
                    nonstream_result = None
                    continue
                return build_openai_error_response(error_info or build_error_info(
                    source="chatcore",
                    phase="nonstream",
                    raw_status=502,
                    raw_message="Unknown upstream failure",
                    raw_body={"message": "Unknown upstream failure"},
                ))
            break
        break

    if upstream is None:
        return build_openai_error_response(
            last_error_info
            or build_error_info(
                source="chatcore",
                phase="retry_exhausted",
                raw_status=502,
                raw_message="No candidate succeeded",
                raw_body={"message": "No candidate succeeded"},
            )
        )

    if stream_req:
        if verbose:
            print("OUT POST /v1/completions (streaming response)")
        def _retrying_text_stream():
            current_upstream = upstream
            current_created = created
            remaining_attempts = max(1, attempt_limit)
            while remaining_attempts > 0:
                try:
                    yield from sse_translate_text(
                        current_upstream,
                        requested_model or model,
                        current_created,
                        verbose=verbose_obfuscation,
                        vlog=(print if verbose_obfuscation else None),
                        include_usage=include_usage,
                    )
                    return
                except RetryableStreamError as exc:
                    remaining_attempts -= 1
                    if remaining_attempts <= 0:
                        payload = {"error": normalized_error_payload(exc.error_info)}
                        yield f"data: {json.dumps(payload)}\n\n".encode("utf-8")
                        yield b"data: [DONE]\n\n"
                        return
                    next_upstream, next_error = start_upstream_request(
                        model,
                        input_items,
                        instructions=_instructions_for_model(model),
                        reasoning_param=reasoning_param,
                        service_tier=service_tier,
                        thread_session=thread_session,
                    )
                    if next_error is not None:
                        next_error_info = error_info_from_flask_response("chatcore", "request_start", next_error)
                        payload = {"error": normalized_error_payload(next_error_info)}
                        yield f"data: {json.dumps(payload)}\n\n".encode("utf-8")
                        yield b"data: [DONE]\n\n"
                        return
                    current_upstream = next_upstream
                    current_created = int(time.time())

        stream_iter = sse_translate_text(
            upstream,
            requested_model or model,
            created,
            verbose=verbose_obfuscation,
            vlog=(print if verbose_obfuscation else None),
            include_usage=include_usage,
        )
        stream_iter = _retrying_text_stream()
        stream_iter = _wrap_stream_logging("STREAM OUT /v1/completions", stream_iter, verbose)
        resp = Response(
            stream_iter,
            status=upstream.status_code,
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )
        if expose_service_tier and service_tier:
            resp.headers["X-ChatMock-Service-Tier-Requested"] = service_tier
        if expose_thread_ids and isinstance(getattr(upstream, "chatmock_thread_id", None), str):
            resp.headers["X-ChatMock-Thread-Id"] = upstream.chatmock_thread_id
            resp.headers["X-ChatMock-Thread-Mode"] = str(getattr(upstream, "chatmock_thread_mode", "start"))
        for k, v in build_cors_headers().items():
            resp.headers.setdefault(k, v)
        return resp

    if not isinstance(nonstream_result, dict) or not nonstream_result.get("ok"):
        return build_openai_error_response(
            last_error_info
            or build_error_info(
                source="chatcore",
                phase="nonstream",
                raw_status=502,
                raw_message="No candidate succeeded",
                raw_body={"message": "No candidate succeeded"},
            )
        )

    full_text = nonstream_result.get("full_text") or ""
    response_id = nonstream_result.get("response_id") or "cmpl"
    usage_obj = nonstream_result.get("usage_obj")
    observed_service_tier = nonstream_result.get("observed_service_tier")

    completion = {
        "id": response_id or "cmpl",
        "object": "text_completion",
        "created": created,
        "model": requested_model or model,
        "choices": [
            {"index": 0, "text": full_text, "finish_reason": "stop", "logprobs": None}
        ],
        **({"usage": usage_obj} if usage_obj else {}),
    }
    if expose_thread_ids and isinstance(getattr(upstream, "chatmock_thread_id", None), str):
        completion["thread_id"] = upstream.chatmock_thread_id
        completion["thread_mode"] = str(getattr(upstream, "chatmock_thread_mode", "start"))
    if expose_service_tier and observed_service_tier:
        completion["service_tier"] = observed_service_tier
    if verbose:
        _log_json("OUT POST /v1/completions", completion)
    resp = make_response(jsonify(completion), upstream.status_code)
    if expose_service_tier and service_tier:
        resp.headers["X-ChatMock-Service-Tier-Requested"] = service_tier
    if expose_service_tier and observed_service_tier:
        resp.headers["X-ChatMock-Service-Tier-Observed"] = observed_service_tier
    if expose_thread_ids and isinstance(getattr(upstream, "chatmock_thread_id", None), str):
        resp.headers["X-ChatMock-Thread-Id"] = upstream.chatmock_thread_id
        resp.headers["X-ChatMock-Thread-Mode"] = str(getattr(upstream, "chatmock_thread_mode", "start"))
    for k, v in build_cors_headers().items():
        resp.headers.setdefault(k, v)
    return resp


@openai_bp.route("/v1/models", methods=["GET"])
def list_models() -> Response:
    expose_variants = bool(current_app.config.get("EXPOSE_REASONING_MODELS"))
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
    ]
    model_ids: List[str] = []
    for base, efforts in model_groups:
        model_ids.append(base)
        if expose_variants:
            model_ids.extend([f"{base}-{effort}" for effort in efforts])
    data = [{"id": mid, "object": "model", "owned_by": "owner"} for mid in model_ids]
    models = {"object": "list", "data": data}
    resp = make_response(jsonify(models), 200)
    for k, v in build_cors_headers().items():
        resp.headers.setdefault(k, v)
    return resp
