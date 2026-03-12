from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Tuple

from flask import Blueprint, Response, current_app, jsonify, make_response, request

from .config import BASE_INSTRUCTIONS, GPT5_CODEX_INSTRUCTIONS
from .http import build_cors_headers
from .limits import record_rate_limits_from_response
from .reasoning import (
    allowed_efforts_for_model,
    build_reasoning_param,
    extract_reasoning_from_model_name,
    extract_service_tier_from_model_name,
)
from .upstream import normalize_model_name, start_upstream_request


anthropic_bp = Blueprint("anthropic", __name__)


def _log_json(prefix: str, payload: Any) -> None:
    try:
        print(f"{prefix}\n{json.dumps(payload, indent=2, ensure_ascii=False)}")
    except Exception:
        try:
            print(f"{prefix}\n{payload}")
        except Exception:
            pass


def _instructions_for_model(model: str) -> str:
    base = current_app.config.get("BASE_INSTRUCTIONS", BASE_INSTRUCTIONS)
    if "codex" in (model or "").lower():
        codex = current_app.config.get("GPT5_CODEX_INSTRUCTIONS") or GPT5_CODEX_INSTRUCTIONS
        if isinstance(codex, str) and codex.strip():
            return codex
    return base


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


def _error_response(message: str, status: int = 400, err_type: str = "invalid_request_error") -> Response:
    payload = {"type": "error", "error": {"type": err_type, "message": message}}
    resp = make_response(jsonify(payload), status)
    for k, v in build_cors_headers().items():
        resp.headers.setdefault(k, v)
    return resp


def _decode_json_body(raw: str) -> Dict[str, Any] | None:
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        try:
            return json.loads(raw.lstrip("\ufeff")) if raw else {}
        except Exception:
            return None


def _extract_usage(evt: Dict[str, Any]) -> Tuple[int, int]:
    try:
        usage = (evt.get("response") or {}).get("usage")
        if not isinstance(usage, dict):
            return 0, 0
        prompt_tokens = int(usage.get("input_tokens") or 0)
        completion_tokens = int(usage.get("output_tokens") or 0)
        return prompt_tokens, completion_tokens
    except Exception:
        return 0, 0


def _system_to_text(system_payload: Any) -> str:
    if isinstance(system_payload, str):
        return system_payload
    if not isinstance(system_payload, list):
        return ""
    parts: List[str] = []
    for block in system_payload:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "text":
            continue
        text = block.get("text")
        if isinstance(text, str) and text:
            parts.append(text)
    return "\n".join(parts)


def _image_source_to_url(source: Any) -> str | None:
    if not isinstance(source, dict):
        return None
    source_type = source.get("type")
    if source_type == "base64":
        media_type = source.get("media_type")
        data = source.get("data")
        if isinstance(media_type, str) and media_type and isinstance(data, str) and data:
            return f"data:{media_type};base64,{data}"
        return None
    if source_type == "url":
        url = source.get("url")
        return url if isinstance(url, str) and url else None
    return None


def _tool_result_output(block: Dict[str, Any]) -> str:
    content = block.get("content")
    is_error = bool(block.get("is_error"))
    output = ""
    if isinstance(content, str):
        output = content
    elif isinstance(content, list):
        texts: List[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                text = part.get("text")
                if isinstance(text, str) and text:
                    texts.append(text)
        if texts:
            output = "\n".join(texts)
        else:
            try:
                output = json.dumps(content, ensure_ascii=False)
            except Exception:
                output = str(content)
    elif content is None:
        output = ""
    else:
        try:
            output = json.dumps(content, ensure_ascii=False)
        except Exception:
            output = str(content)

    if is_error:
        return f"[tool_error]\n{output}" if output else "[tool_error]"
    return output


def _safe_json_object(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {"value": raw}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed}
    except Exception:
        return {"raw": raw}


def _flush_message_input(input_items: List[Dict[str, Any]], role: str, content_items: List[Dict[str, Any]]) -> None:
    if not content_items:
        return
    input_items.append(
        {
            "type": "message",
            "role": "assistant" if role == "assistant" else "user",
            "content": content_items[:],
        }
    )
    content_items.clear()


def _convert_anthropic_messages_to_input(messages: Any) -> tuple[List[Dict[str, Any]] | None, str | None]:
    if not isinstance(messages, list):
        return None, "messages must be an array"

    input_items: List[Dict[str, Any]] = []
    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict):
            return None, f"messages[{idx}] must be an object"
        role = msg.get("role")
        if role not in ("user", "assistant"):
            return None, f"messages[{idx}].role must be 'user' or 'assistant'"

        content = msg.get("content")
        blocks: List[Dict[str, Any]] = []
        if isinstance(content, str):
            blocks = [{"type": "text", "text": content}]
        elif isinstance(content, list):
            blocks = [b for b in content if isinstance(b, dict)]
        else:
            return None, f"messages[{idx}].content must be a string or block array"

        pending_content_items: List[Dict[str, Any]] = []
        for block in blocks:
            block_type = block.get("type")
            if block_type == "text":
                text = block.get("text")
                if isinstance(text, str) and text:
                    pending_content_items.append(
                        {"type": ("output_text" if role == "assistant" else "input_text"), "text": text}
                    )
                continue

            if block_type == "image":
                if role != "user":
                    return None, f"messages[{idx}] image blocks are only supported for user role"
                url = _image_source_to_url(block.get("source"))
                if not url:
                    return None, f"messages[{idx}] invalid image block source"
                pending_content_items.append({"type": "input_image", "image_url": url})
                continue

            if block_type == "tool_use":
                if role != "assistant":
                    return None, f"messages[{idx}] tool_use blocks are only supported for assistant role"
                _flush_message_input(input_items, role, pending_content_items)
                call_id = block.get("id")
                name = block.get("name")
                if not isinstance(call_id, str) or not call_id:
                    return None, f"messages[{idx}] tool_use.id must be a non-empty string"
                if not isinstance(name, str) or not name:
                    return None, f"messages[{idx}] tool_use.name must be a non-empty string"
                try:
                    args = json.dumps(block.get("input") if block.get("input") is not None else {}, ensure_ascii=False)
                except Exception:
                    args = "{}"
                input_items.append(
                    {
                        "type": "function_call",
                        "name": name,
                        "arguments": args,
                        "call_id": call_id,
                    }
                )
                continue

            if block_type == "tool_result":
                if role != "user":
                    return None, f"messages[{idx}] tool_result blocks are only supported for user role"
                _flush_message_input(input_items, role, pending_content_items)
                call_id = block.get("tool_use_id")
                if not isinstance(call_id, str) or not call_id:
                    return None, f"messages[{idx}] tool_result.tool_use_id must be a non-empty string"
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": _tool_result_output(block),
                    }
                )
                continue

            if isinstance(block_type, str) and block_type:
                return None, f"unsupported content block type: {block_type}"
            return None, f"messages[{idx}] includes invalid content block"

        _flush_message_input(input_items, role, pending_content_items)

    return input_items, None


def _convert_anthropic_tools(tools_payload: Any) -> tuple[List[Dict[str, Any]] | None, str | None]:
    if tools_payload is None:
        return [], None
    if not isinstance(tools_payload, list):
        return None, "tools must be an array"

    out: List[Dict[str, Any]] = []
    for idx, tool in enumerate(tools_payload):
        if not isinstance(tool, dict):
            return None, f"tools[{idx}] must be an object"
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            return None, f"tools[{idx}].name must be a non-empty string"
        desc = tool.get("description")
        schema = tool.get("input_schema")
        if not isinstance(schema, dict):
            schema = {"type": "object", "properties": {}}
        out.append(
            {
                "type": "function",
                "name": name,
                "description": desc if isinstance(desc, str) else "",
                "strict": False,
                "parameters": schema,
            }
        )
    return out, None


def _convert_anthropic_tool_choice(choice_payload: Any) -> tuple[Any, bool, str | None]:
    if choice_payload is None:
        return "auto", False, None

    if isinstance(choice_payload, str):
        normalized = choice_payload.strip().lower()
        if normalized in ("auto", "any"):
            return "auto", False, None
        if normalized == "none":
            return "none", False, None
        return None, False, "tool_choice must be auto/any/none or an object"

    if not isinstance(choice_payload, dict):
        return None, False, "tool_choice must be auto/any/none or an object"

    choice_type = str(choice_payload.get("type") or "").strip().lower()
    disable_parallel = bool(choice_payload.get("disable_parallel_tool_use", False))
    parallel = not disable_parallel
    if choice_type in ("auto", "any"):
        return "auto", parallel, None
    if choice_type == "none":
        return "none", parallel, None
    if choice_type == "tool":
        name = choice_payload.get("name")
        if not isinstance(name, str) or not name:
            return None, parallel, "tool_choice.type=tool requires non-empty name"
        return {"type": "function", "name": name}, parallel, None
    return None, parallel, "unsupported tool_choice.type"


def _tool_use_payload_from_item(item: Dict[str, Any]) -> Dict[str, Any] | None:
    item_type = item.get("type")
    if item_type != "function_call":
        return None
    call_id = item.get("call_id") or item.get("id") or f"toolu_{uuid.uuid4().hex[:12]}"
    name = item.get("name") or "tool"
    arguments = item.get("arguments") or "{}"
    return {
        "id": call_id,
        "name": name,
        "input": _safe_json_object(arguments),
    }


def _anthropic_stream(upstream, model_out: str, verbose: bool):
    def _emit(event: str, payload: Dict[str, Any]):
        data = f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        if verbose:
            try:
                print(f"STREAM OUT /v1/messages\n{data}")
            except Exception:
                pass
        return data

    response_id = f"msg_{uuid.uuid4().hex}"
    stop_reason = "end_turn"
    usage_in = 0
    usage_out = 0
    next_block_index = 0
    text_open = False
    text_index = -1
    try:
        yield _emit(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": response_id,
                    "type": "message",
                    "role": "assistant",
                    "model": model_out,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            },
        )

        for raw_line in upstream.iter_lines(decode_unicode=False):
            if not raw_line:
                continue
            line = raw_line.decode("utf-8", errors="ignore") if isinstance(raw_line, (bytes, bytearray)) else raw_line
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

            prompt_tokens, completion_tokens = _extract_usage(evt)
            if prompt_tokens:
                usage_in = prompt_tokens
            if completion_tokens:
                usage_out = completion_tokens

            kind = evt.get("type")
            if kind == "response.output_text.delta":
                delta = evt.get("delta") or ""
                if isinstance(delta, str) and delta:
                    if not text_open:
                        text_index = next_block_index
                        next_block_index += 1
                        text_open = True
                        yield _emit(
                            "content_block_start",
                            {
                                "type": "content_block_start",
                                "index": text_index,
                                "content_block": {"type": "text", "text": ""},
                            },
                        )
                    yield _emit(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": text_index,
                            "delta": {"type": "text_delta", "text": delta},
                        },
                    )
                continue

            if kind == "response.output_item.done":
                item = evt.get("item") if isinstance(evt.get("item"), dict) else {}
                tool_payload = _tool_use_payload_from_item(item)
                if tool_payload is None:
                    continue
                if text_open:
                    yield _emit("content_block_stop", {"type": "content_block_stop", "index": text_index})
                    text_open = False
                stop_reason = "tool_use"
                tool_index = next_block_index
                next_block_index += 1
                yield _emit(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": tool_index,
                        "content_block": {
                            "type": "tool_use",
                            "id": tool_payload["id"],
                            "name": tool_payload["name"],
                            "input": {},
                        },
                    },
                )
                partial_json = json.dumps(tool_payload["input"], ensure_ascii=False, separators=(",", ":"))
                yield _emit(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": tool_index,
                        "delta": {"type": "input_json_delta", "partial_json": partial_json},
                    },
                )
                yield _emit("content_block_stop", {"type": "content_block_stop", "index": tool_index})
                continue

            if kind == "response.failed":
                message = (evt.get("response", {}) or {}).get("error", {}).get("message", "response.failed")
                yield _emit("error", {"type": "error", "error": {"type": "api_error", "message": message}})
                return

            if kind == "response.completed":
                break

        if text_open:
            yield _emit("content_block_stop", {"type": "content_block_stop", "index": text_index})

        yield _emit(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {"input_tokens": usage_in, "output_tokens": usage_out},
            },
        )
        yield _emit("message_stop", {"type": "message_stop"})
    finally:
        upstream.close()


@anthropic_bp.route("/v1/messages", methods=["POST"])
def messages() -> Response:
    verbose = bool(current_app.config.get("VERBOSE"))
    debug_model = current_app.config.get("DEBUG_MODEL")
    reasoning_effort = current_app.config.get("REASONING_EFFORT", "medium")
    reasoning_summary = current_app.config.get("REASONING_SUMMARY", "auto")

    raw = request.get_data(cache=True, as_text=True) or ""
    if verbose:
        try:
            print("IN POST /v1/messages\n" + raw)
        except Exception:
            pass

    payload = _decode_json_body(raw)
    if payload is None:
        return _error_response("invalid JSON body", 400, "invalid_request_error")

    requested_model = payload.get("model")
    model = normalize_model_name(requested_model, debug_model)

    input_items, msg_err = _convert_anthropic_messages_to_input(payload.get("messages"))
    if msg_err:
        return _error_response(msg_err, 400, "invalid_request_error")
    assert isinstance(input_items, list)
    if not input_items:
        return _error_response("messages must include at least one content block", 400, "invalid_request_error")

    system_text = _system_to_text(payload.get("system")).strip()
    instructions = _instructions_for_model(model)
    if system_text:
        instructions = (instructions + "\n\n" + system_text).strip() if instructions else system_text

    tools_responses, tools_err = _convert_anthropic_tools(payload.get("tools"))
    if tools_err:
        return _error_response(tools_err, 400, "invalid_request_error")
    assert isinstance(tools_responses, list)

    tool_choice, parallel_tool_calls, tool_choice_err = _convert_anthropic_tool_choice(payload.get("tool_choice"))
    if tool_choice_err:
        return _error_response(tool_choice_err, 400, "invalid_request_error")
    if isinstance(payload.get("parallel_tool_calls"), bool):
        parallel_tool_calls = bool(payload.get("parallel_tool_calls"))

    model_reasoning = extract_reasoning_from_model_name(requested_model)
    reasoning_overrides = payload.get("reasoning") if isinstance(payload.get("reasoning"), dict) else model_reasoning
    service_tier = _resolve_service_tier(payload, requested_model)
    reasoning_param = build_reasoning_param(
        reasoning_effort,
        reasoning_summary,
        reasoning_overrides,
        allowed_efforts=allowed_efforts_for_model(model),
    )

    upstream, error_resp = start_upstream_request(
        model,
        input_items,
        instructions=instructions,
        tools=tools_responses,
        tool_choice=tool_choice,
        parallel_tool_calls=parallel_tool_calls,
        reasoning_param=reasoning_param,
        service_tier=service_tier,
    )
    if error_resp is not None:
        status = int(getattr(error_resp, "status_code", 401) or 401)
        body = error_resp.get_data(as_text=True) if hasattr(error_resp, "get_data") else ""
        message = "upstream auth error"
        try:
            parsed = json.loads(body) if body else {}
            message = (parsed.get("error") or {}).get("message") or message
        except Exception:
            pass
        return _error_response(message, status, "authentication_error" if status in (401, 403) else "api_error")

    record_rate_limits_from_response(upstream)
    if upstream.status_code >= 400:
        message = "upstream error"
        try:
            parsed = json.loads(upstream.content.decode("utf-8", errors="ignore")) if upstream.content else {}
            message = (parsed.get("error") or {}).get("message") or message
        except Exception:
            pass
        try:
            upstream.close()
        except Exception:
            pass
        return _error_response(message, upstream.status_code, "api_error")

    model_out = requested_model or model
    if bool(payload.get("stream")):
        resp = Response(
            _anthropic_stream(upstream, model_out, verbose),
            status=upstream.status_code,
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )
        if service_tier:
            resp.headers["X-ChatMock-Service-Tier-Requested"] = service_tier
        for k, v in build_cors_headers().items():
            resp.headers.setdefault(k, v)
        return resp

    full_text = ""
    tool_calls: List[Dict[str, Any]] = []
    usage_in = 0
    usage_out = 0
    response_id = f"msg_{uuid.uuid4().hex}"
    error_message: str | None = None
    observed_service_tier: str | None = None
    completed_ok = False
    try:
        for raw_line in upstream.iter_lines(decode_unicode=False):
            if not raw_line:
                continue
            line = raw_line.decode("utf-8", errors="ignore") if isinstance(raw_line, (bytes, bytearray)) else raw_line
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

            prompt_tokens, completion_tokens = _extract_usage(evt)
            if prompt_tokens:
                usage_in = prompt_tokens
            if completion_tokens:
                usage_out = completion_tokens
            if isinstance(evt.get("response"), dict) and isinstance(evt["response"].get("id"), str):
                response_id = evt["response"].get("id") or response_id
            if isinstance(evt.get("response"), dict) and isinstance(evt["response"].get("service_tier"), str):
                observed_service_tier = evt["response"].get("service_tier") or observed_service_tier

            kind = evt.get("type")
            if kind == "response.output_text.delta":
                full_text += evt.get("delta") or ""
            elif kind == "response.output_item.done":
                item = evt.get("item") if isinstance(evt.get("item"), dict) else {}
                tool_payload = _tool_use_payload_from_item(item)
                if tool_payload is not None:
                    tool_calls.append(tool_payload)
            elif kind == "response.failed":
                error_message = (evt.get("response", {}) or {}).get("error", {}).get("message", "response.failed")
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
        return _error_response(error_message, 502, "api_error")

    content: List[Dict[str, Any]] = []
    stop_reason = "end_turn"
    if full_text:
        content.append({"type": "text", "text": full_text})
    if tool_calls:
        stop_reason = "tool_use"
        content.extend([{"type": "tool_use", **tool_call} for tool_call in tool_calls])

    message_obj = {
        "id": response_id,
        "type": "message",
        "role": "assistant",
        "model": model_out,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": usage_in, "output_tokens": usage_out},
    }
    if observed_service_tier:
        message_obj["service_tier"] = observed_service_tier
    if verbose:
        _log_json("OUT POST /v1/messages", message_obj)

    resp = make_response(jsonify(message_obj), upstream.status_code)
    if service_tier:
        resp.headers["X-ChatMock-Service-Tier-Requested"] = service_tier
    if observed_service_tier:
        resp.headers["X-ChatMock-Service-Tier-Observed"] = observed_service_tier
    for k, v in build_cors_headers().items():
        resp.headers.setdefault(k, v)
    return resp
