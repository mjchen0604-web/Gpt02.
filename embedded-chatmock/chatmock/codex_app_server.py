from __future__ import annotations

import json
import os
import uuid
from typing import Any, Dict, Iterable, List

from urllib.parse import urlparse, unquote

from websockets.sync.client import ClientConnection, connect as ws_connect

from .thread_sessions import clear_thread_session, save_thread_session
from .upstream_errors import build_error_info

APP_SERVER_BRIDGE_INSTRUCTIONS = """You are serving requests through an OpenAI-compatible API bridge.

Rules:
- Do not call Codex built-in tools, MCP tools, or collaboration tools unless the current end user explicitly asked for them.
- If client-provided tools are declared for this request, only call those declared tools.
- If the conversation already includes tool outputs, treat them as authoritative completed tool results and answer from them instead of calling more tools.
- Never say that a previously used client tool is unavailable when its completed tool result is already present in the conversation.
- When completed tool results are present, answer from those results directly and omit commentary about tool availability.
- Do not add commentary about internal tooling unless the user asked for it.
"""


class CodexAppServerError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, error_info: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_info = error_info or {}


def normalize_service_tier_for_codex(service_tier: str | None) -> str | None:
    if not isinstance(service_tier, str):
        return None
    normalized = service_tier.strip().lower()
    if not normalized or normalized in ("off", "none", "unset", "default"):
        return None
    if normalized in ("fast", "flex"):
        return normalized
    return normalized


def normalize_web_search_mode_for_codex(web_search_mode: str | None) -> str:
    if not isinstance(web_search_mode, str):
        return "disabled"
    normalized = web_search_mode.strip().lower()
    if not normalized or normalized in ("off", "none", "unset", "false", "disabled"):
        return "disabled"
    if normalized in ("preview", "cached", "web_search_preview"):
        return "cached"
    if normalized in ("on", "true", "live", "web_search"):
        return "live"
    return "disabled"


def _json_dumps_compact(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _coerce_function_output_to_text(output: Any) -> str:
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        text_parts: List[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") in ("input_text", "output_text") and isinstance(item.get("text"), str):
                text_parts.append(item.get("text"))
            elif item.get("type") in ("input_image", "output_image"):
                image_url = item.get("image_url") or item.get("url")
                if isinstance(image_url, str) and image_url:
                    text_parts.append(f"[image: {image_url}]")
        if text_parts:
            return "\n".join(text_parts)
    return _json_dumps_compact(output)


def _append_message_to_transcript(
    transcript_parts: List[str],
    role: str,
    content_items: List[Dict[str, Any]],
) -> None:
    text_parts: List[str] = []
    for content in content_items:
        if not isinstance(content, dict):
            continue
        content_type = content.get("type")
        if content_type in ("input_text", "output_text"):
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                text_parts.append(text)
        elif content_type == "input_image":
            image_url = content.get("image_url")
            if isinstance(image_url, str) and image_url:
                if image_url.startswith("http://") or image_url.startswith("https://") or image_url.startswith("data:"):
                    text_parts.append(f"[shared image: {image_url}]")
                else:
                    text_parts.append("[shared local image]")
    if text_parts:
        transcript_parts.append(f"{role.capitalize()}: " + "\n".join(text_parts))


def _input_image_to_codex(image_url: str) -> Dict[str, Any] | None:
    if not isinstance(image_url, str) or not image_url:
        return None
    if image_url.startswith("file://"):
        parsed = urlparse(image_url)
        path = unquote(parsed.path or "")
        if os.name == "nt" and path.startswith("/") and len(path) > 2 and path[2] == ":":
            path = path[1:]
        if path:
            return {"type": "localImage", "path": path}
    if os.path.isabs(image_url) or os.path.exists(image_url):
        return {"type": "localImage", "path": image_url}
    return {"type": "image", "url": image_url}


def convert_responses_input_to_codex_input(input_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    transcript_parts: List[str] = []
    last_user_message_index = -1
    trailing_tool_outputs: List[str] = []

    for index, item in enumerate(input_items):
        if isinstance(item, dict) and item.get("type") == "message" and item.get("role") == "user":
            last_user_message_index = index

    for item in reversed(input_items):
        if not isinstance(item, dict):
            break
        if item.get("type") != "function_call_output":
            break
        call_id = item.get("call_id") or ""
        output = _coerce_function_output_to_text(item.get("output"))
        if isinstance(call_id, str) and call_id:
            trailing_tool_outputs.append(f"- {call_id}: {output}")
        else:
            trailing_tool_outputs.append(f"- {output}")
    trailing_tool_outputs.reverse()

    native_turn_items: List[Dict[str, Any]] = []

    for index, item in enumerate(input_items):
        if not isinstance(item, dict):
            continue

        item_type = item.get("type")
        if item_type == "message":
            role = item.get("role") or "user"
            content_items = item.get("content") if isinstance(item.get("content"), list) else []
            if index == last_user_message_index and role == "user" and not native_turn_items:
                for content in content_items:
                    if not isinstance(content, dict):
                        continue
                    content_type = content.get("type")
                    if content_type == "input_text":
                        text = content.get("text")
                        if isinstance(text, str) and text:
                            native_turn_items.append({"type": "text", "text": text})
                    elif content_type == "input_image":
                        image_url = content.get("image_url")
                        if isinstance(image_url, str) and image_url:
                            native_item = _input_image_to_codex(image_url)
                            if native_item is not None:
                                native_turn_items.append(native_item)
                if native_turn_items:
                    continue

            _append_message_to_transcript(transcript_parts, str(role), content_items)
            continue

        if item_type == "function_call":
            name = item.get("name") or "function"
            arguments = item.get("arguments")
            if not isinstance(arguments, str):
                arguments = _json_dumps_compact(arguments)
            transcript_parts.append(f"Assistant tool call {name}: {arguments}")
            continue

        if item_type == "function_call_output":
            call_id = item.get("call_id") or ""
            output = item.get("output")
            output_text = _coerce_function_output_to_text(output)
            prefix = f"Tool output for {call_id}:" if isinstance(call_id, str) and call_id else "Tool output:"
            transcript_parts.append(f"{prefix} {output_text}")

    if transcript_parts:
        history_block = "Conversation so far:\n" + "\n\n".join(transcript_parts)
    else:
        history_block = ""

    if native_turn_items:
        if history_block:
            return [{"type": "text", "text": history_block}, *native_turn_items]
        return native_turn_items

    if not history_block:
        return [{"type": "text", "text": ""}]
    if trailing_tool_outputs:
        tool_result_instruction = "\n".join(
            [
                history_block,
                "",
                "Completed client-side tool results for this turn:",
                *trailing_tool_outputs,
                "",
                "Use the completed tool results above to answer the user directly. Do not call any tools.",
            ]
        )
        return [{"type": "text", "text": tool_result_instruction}]
    if len(transcript_parts) == 1 and transcript_parts[0].startswith("User: "):
        return [{"type": "text", "text": transcript_parts[0][6:]}]
    return [{"type": "text", "text": history_block}]


def has_trailing_tool_outputs(input_items: List[Dict[str, Any]]) -> bool:
    saw_output = False
    for item in reversed(input_items):
        if not isinstance(item, dict):
            break
        if item.get("type") != "function_call_output":
            break
        saw_output = True
    return saw_output


def convert_responses_tools_to_codex_dynamic_tools(
    tools: List[Dict[str, Any]] | None,
    tool_choice: Any | None = None,
) -> List[Dict[str, Any]]:
    if tool_choice == "none":
        return []

    selected_name: str | None = None
    if isinstance(tool_choice, dict):
        function_block = tool_choice.get("function")
        if tool_choice.get("type") == "function" and isinstance(function_block, dict):
            name = function_block.get("name")
            if isinstance(name, str) and name.strip():
                selected_name = name.strip()

    dynamic_tools: List[Dict[str, Any]] = []
    for tool in tools or []:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            continue
        name = tool.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        if selected_name and name != selected_name:
            continue
        parameters = tool.get("parameters")
        if not isinstance(parameters, dict):
            parameters = {"type": "object", "properties": {}}
        dynamic_tools.append(
            {
                "name": name,
                "description": tool.get("description") or "",
                "inputSchema": parameters,
            }
        )
    return dynamic_tools


def build_codex_bridge_instructions(
    instructions: str | None,
    dynamic_tools: List[Dict[str, Any]],
    web_search_mode: str = "disabled",
) -> str:
    tool_names = [tool.get("name") for tool in dynamic_tools if isinstance(tool.get("name"), str) and tool.get("name")]
    if tool_names:
        tools_line = "Client tools available for this request: " + ", ".join(tool_names) + "."
    else:
        tools_line = "No client tools are available for this request; respond directly."
    if web_search_mode == "live":
        web_search_line = "Native built-in web_search is enabled for this request and may be used when useful."
    elif web_search_mode == "cached":
        web_search_line = "Native built-in web_search is enabled in cached mode for this request and may be used when useful."
    else:
        web_search_line = "Native built-in web_search is disabled for this request."
    base = instructions.strip() if isinstance(instructions, str) and instructions.strip() else ""
    merged_parts = [part for part in [base, APP_SERVER_BRIDGE_INSTRUCTIONS.strip(), tools_line, web_search_line] if part]
    return "\n\n".join(merged_parts)


def _recv_jsonrpc_response(websocket: ClientConnection, request_id: str) -> Dict[str, Any]:
    try:
        while True:
            raw = websocket.recv()
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            message = json.loads(raw)
            if message.get("id") == request_id:
                return message
    except Exception:
        raise


def _rpc_call(websocket: ClientConnection, method: str, params: Dict[str, Any] | None) -> Dict[str, Any]:
    request_id = f"{method.replace('/', '-')}-{uuid.uuid4().hex}"
    websocket.send(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            },
            ensure_ascii=False,
        )
    )
    return _recv_jsonrpc_response(websocket, request_id)


def _extract_error_message(message: Dict[str, Any]) -> str | None:
    error = message.get("error")
    if isinstance(error, dict):
        if isinstance(error.get("message"), str) and error.get("message"):
            return error.get("message")
        data = error.get("data")
        if isinstance(data, dict) and isinstance(data.get("message"), str) and data.get("message"):
            return data.get("message")
    params = message.get("params")
    if isinstance(params, dict):
        nested_error = params.get("error")
        if isinstance(nested_error, dict) and isinstance(nested_error.get("message"), str) and nested_error.get("message"):
            return nested_error.get("message")
    return None


def _extract_error_status(message: Dict[str, Any]) -> int | None:
    return CodexAppServerUpstream._extract_error_status(message)


def _extract_error_code(message: Dict[str, Any]) -> str | None:
    return CodexAppServerUpstream._extract_error_code(message)


def _ensure_success_response(message: Dict[str, Any], *, phase: str) -> Dict[str, Any]:
    if isinstance(message.get("error"), dict):
        status_code = _extract_error_status(message)
        error_message = str(message["error"].get("message") or "").strip() or f"{phase} failed"
        raise CodexAppServerError(
            error_message,
            status_code=status_code,
            error_info=build_error_info(
                source="codex-app-server",
                phase=phase,
                raw_status=status_code,
                raw_code=_extract_error_code(message),
                raw_message=error_message,
                raw_body=message,
            ),
        )
    return message


def _resolve_external_auth_tokens() -> Dict[str, str] | None:
    access_token = str(os.getenv("CHATMOCK_CODEX_ACCESS_TOKEN") or "").strip()
    account_id = str(os.getenv("CHATMOCK_CODEX_ACCOUNT_ID") or "").strip()
    if not access_token or not account_id:
        return None
    out = {
        "accessToken": access_token,
        "chatgptAccountId": account_id,
    }
    plan_type = str(os.getenv("CHATMOCK_CODEX_PLAN_TYPE") or "").strip()
    if plan_type:
        out["chatgptPlanType"] = plan_type
    return out


def _login_with_external_auth_tokens(websocket: ClientConnection) -> None:
    tokens = _resolve_external_auth_tokens()
    if tokens is None:
        return
    response = _rpc_call(
        websocket,
        "account/login/start",
        {
            "type": "chatgptAuthTokens",
            **tokens,
        },
    )
    _ensure_success_response(response, phase="account_login_start")


def read_codex_app_server_config(
    *,
    app_server_url: str,
    cwd: str | None = None,
) -> Dict[str, Any]:
    websocket = ws_connect(app_server_url, open_timeout=30, close_timeout=10)
    try:
        init_response = _rpc_call(
            websocket,
            "initialize",
            {
                "protocolVersion": 2,
                "clientInfo": {"name": "chatmock", "version": "local"},
                "capabilities": {
                    "experimentalApi": True,
                    "optOutNotificationMethods": [],
                },
            },
        )
        _ensure_success_response(init_response, phase="initialize")
        _login_with_external_auth_tokens(websocket)
        config_response = _rpc_call(
            websocket,
            "config/read",
            {
                "cwd": cwd,
                "includeLayers": True,
            },
        )
        config_response = _ensure_success_response(config_response, phase="config_read")
        result = config_response.get("result")
        return result if isinstance(result, dict) else {}
    finally:
        try:
            websocket.close()
        except Exception:
            pass


class CodexAppServerUpstream:
    def __init__(
        self,
        websocket: ClientConnection,
        *,
        thread_id: str,
        model: str,
        input_items: List[Dict[str, Any]],
        reasoning_param: Dict[str, Any] | None,
        service_tier: str | None,
        observed_service_tier: str | None,
        cwd: str,
        approval_policy: str,
        tools: List[Dict[str, Any]] | None = None,
        verbose: bool = False,
    ) -> None:
        self._ws = websocket
        self._thread_id = thread_id
        self._model = model
        self._input_items = input_items
        self._reasoning_param = reasoning_param or {}
        self._service_tier = service_tier
        self._observed_service_tier = observed_service_tier
        self._cwd = cwd
        self._approval_policy = approval_policy
        self._tools = tools or []
        self._verbose = verbose
        self._started = False
        self._closed = False
        self._buffered_messages: List[Dict[str, Any]] = []
        self.headers: Dict[str, str] = {}
        self.status_code = 200
        self.text = ""
        self.content = b""

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._ws.close()
        except Exception:
            pass

    def iter_lines(self, decode_unicode: bool = False):
        if self._started:
            return iter(())
        self._started = True

        def _encode(payload: Dict[str, Any]) -> bytes | str:
            line = f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            if decode_unicode:
                return line
            return line.encode("utf-8")

        def _done() -> bytes | str:
            text = "data: [DONE]\n\n"
            if decode_unicode:
                return text
            return text.encode("utf-8")

        try:
            turn_request_id = f"turn-start-{uuid.uuid4().hex}"
            codex_input = convert_responses_input_to_codex_input(self._input_items)
            turn_params: Dict[str, Any] = {
                "threadId": self._thread_id,
                "cwd": self._cwd,
                "approvalPolicy": self._approval_policy,
                "input": codex_input,
            }
            effort = self._reasoning_param.get("effort")
            if isinstance(effort, str) and effort.strip():
                turn_params["effort"] = effort.strip().lower()
            summary = self._reasoning_param.get("summary")
            if isinstance(summary, str) and summary.strip():
                turn_params["summary"] = summary.strip().lower()
            if isinstance(self._service_tier, str) and self._service_tier:
                turn_params["serviceTier"] = self._service_tier

            self._send_rpc(turn_request_id, "turn/start", turn_params)
            turn_response = self._recv_until_id(turn_request_id)
            turn_result = turn_response.get("result") if isinstance(turn_response, dict) else None
            turn = turn_result.get("turn") if isinstance(turn_result, dict) else None
            turn_id = turn.get("id") if isinstance(turn, dict) and isinstance(turn.get("id"), str) else f"resp_{uuid.uuid4().hex}"
            observed_service_tier = self._observed_service_tier or self._service_tier

            if not isinstance(turn_result, dict) or not isinstance(turn, dict):
                status_code = self._extract_error_status(turn_response)
                error_message = self._extract_error_message(turn_response) or "Invalid turn/start response from codex app-server"
                error_info = build_error_info(
                    source="codex-app-server",
                    phase="turn_start",
                    raw_status=status_code,
                    raw_code=self._extract_error_code(turn_response),
                    raw_message=error_message,
                    raw_body=turn_response,
                )
                yield _encode(
                    {
                        "type": "response.failed",
                        "response": {
                            "id": turn_id,
                            "service_tier": observed_service_tier,
                            "error": {
                                "message": error_message,
                                "raw_status": error_info.get("raw_status"),
                                "raw_code": error_info.get("raw_code"),
                                "raw_message": error_info.get("raw_message"),
                                "raw_body": error_info.get("raw_body"),
                                "source": error_info.get("source"),
                                "phase": error_info.get("phase"),
                            },
                            **({"status": status_code} if isinstance(status_code, int) else {}),
                        },
                    }
                )
                yield _done()
                return

            if turn.get("status") == "failed":
                turn_error = turn.get("error") if isinstance(turn.get("error"), dict) else {}
                status_code = self._extract_error_status({"error": turn_error})
                error_message = turn_error.get("message") or "turn/start failed"
                error_info = build_error_info(
                    source="codex-app-server",
                    phase="turn_start",
                    raw_status=status_code,
                    raw_code=self._extract_error_code({"error": turn_error}),
                    raw_message=error_message,
                    raw_body=turn_error,
                )
                yield _encode(
                    {
                        "type": "response.failed",
                        "response": {
                            "id": turn_id,
                            "service_tier": observed_service_tier,
                            "error": {
                                "message": error_message,
                                "raw_status": error_info.get("raw_status"),
                                "raw_code": error_info.get("raw_code"),
                                "raw_message": error_info.get("raw_message"),
                                "raw_body": error_info.get("raw_body"),
                                "source": error_info.get("source"),
                                "phase": error_info.get("phase"),
                            },
                            **({"status": status_code} if isinstance(status_code, int) else {}),
                        },
                    }
                )
                yield _done()
                return

            yield _encode(
                {
                    "type": "response.created",
                    "response": {
                        "id": turn_id,
                        "service_tier": observed_service_tier,
                    },
                }
            )

            saw_output_delta = False
            usage_obj: Dict[str, int] | None = None
            saw_tool_call = False

            while True:
                message = self._next_message()
                if not isinstance(message, dict):
                    continue

                method = message.get("method")
                if method == "rawResponseItem/completed":
                    item = params = message.get("params") if isinstance(message.get("params"), dict) else {}
                    raw_item = item.get("item") if isinstance(item, dict) else {}
                    if isinstance(raw_item, dict):
                        normalized = self._normalize_output_item(raw_item)
                        if normalized is not None:
                            if normalized.get("type") == "function_call":
                                saw_tool_call = True
                            yield _encode(
                                {
                                    "type": "response.output_item.done",
                                    "item": normalized,
                                    "response": {
                                        "id": turn_id,
                                        "service_tier": observed_service_tier,
                                    },
                                }
                            )
                            if saw_tool_call:
                                yield _encode(
                                    {
                                        "type": "response.completed",
                                        "response": {
                                            "id": turn_id,
                                            "service_tier": observed_service_tier,
                                            **({"usage": usage_obj} if usage_obj else {}),
                                        },
                                    }
                                )
                                yield _done()
                                return
                    continue

                params = message.get("params") if isinstance(message.get("params"), dict) else {}
                if method == "codex/event/raw_response_item":
                    payload = params.get("msg") if isinstance(params.get("msg"), dict) else {}
                    raw_item = payload.get("item") if isinstance(payload, dict) else {}
                    if isinstance(raw_item, dict):
                        normalized = self._normalize_output_item(raw_item)
                        if normalized is not None:
                            if normalized.get("type") == "function_call":
                                saw_tool_call = True
                            yield _encode(
                                {
                                    "type": "response.output_item.done",
                                    "item": normalized,
                                    "response": {
                                        "id": turn_id,
                                        "service_tier": observed_service_tier,
                                    },
                                }
                            )
                            if saw_tool_call:
                                yield _encode(
                                    {
                                        "type": "response.completed",
                                        "response": {
                                            "id": turn_id,
                                            "service_tier": observed_service_tier,
                                            **({"usage": usage_obj} if usage_obj else {}),
                                        },
                                    }
                                )
                                yield _done()
                                return
                    continue

                if method == "item/tool/call":
                    call_id = params.get("callId") or ""
                    tool_name = params.get("tool") or ""
                    arguments = params.get("arguments")
                    normalized = {
                        "type": "function_call",
                        "call_id": call_id if isinstance(call_id, str) else "",
                        "name": tool_name if isinstance(tool_name, str) else "",
                        "arguments": arguments if isinstance(arguments, str) else _json_dumps_compact(arguments),
                    }
                    yield _encode(
                        {
                            "type": "response.output_item.done",
                            "item": normalized,
                            "response": {
                                "id": turn_id,
                                "service_tier": observed_service_tier,
                            },
                        }
                    )
                    yield _encode(
                        {
                            "type": "response.completed",
                            "response": {
                                "id": turn_id,
                                "service_tier": observed_service_tier,
                                **({"usage": usage_obj} if usage_obj else {}),
                            },
                        }
                    )
                    yield _done()
                    return

                if method == "codex/event/agent_message_content_delta":
                    payload = params.get("msg") if isinstance(params.get("msg"), dict) else params
                    delta = payload.get("delta") if isinstance(payload, dict) else None
                    if isinstance(delta, str) and delta:
                        saw_output_delta = True
                        yield _encode(
                            {
                                "type": "response.output_text.delta",
                                "delta": delta,
                                "response": {
                                    "id": turn_id,
                                    "service_tier": observed_service_tier,
                                },
                            }
                        )
                    continue

                if method == "item/agentMessage/delta":
                    delta = params.get("delta")
                    if isinstance(delta, str) and delta and not saw_output_delta:
                        saw_output_delta = True
                        yield _encode(
                            {
                                "type": "response.output_text.delta",
                                "delta": delta,
                                "response": {
                                    "id": turn_id,
                                    "service_tier": observed_service_tier,
                                },
                            }
                        )
                    continue

                if method == "codex/event/reasoning_text_delta":
                    payload = params.get("msg") if isinstance(params.get("msg"), dict) else params
                    delta = payload.get("delta") if isinstance(payload, dict) else None
                    if isinstance(delta, str) and delta:
                        yield _encode(
                            {
                                "type": "response.reasoning_text.delta",
                                "delta": delta,
                                "response": {
                                    "id": turn_id,
                                    "service_tier": observed_service_tier,
                                },
                            }
                        )
                    continue

                if method == "codex/event/reasoning_summary_text_delta":
                    payload = params.get("msg") if isinstance(params.get("msg"), dict) else params
                    delta = payload.get("delta") if isinstance(payload, dict) else None
                    if isinstance(delta, str) and delta:
                        yield _encode(
                            {
                                "type": "response.reasoning_summary_text.delta",
                                "delta": delta,
                                "response": {
                                    "id": turn_id,
                                    "service_tier": observed_service_tier,
                                },
                            }
                        )
                    continue

                if method == "item/completed":
                    item = params.get("item") if isinstance(params.get("item"), dict) else {}
                    if item.get("type") == "agentMessage" and not saw_output_delta:
                        text = item.get("text")
                        if isinstance(text, str) and text:
                            saw_output_delta = True
                            yield _encode(
                                {
                                    "type": "response.output_text.delta",
                                    "delta": text,
                                    "response": {
                                        "id": turn_id,
                                        "service_tier": observed_service_tier,
                                    },
                                }
                            )
                    continue

                if method == "thread/tokenUsage/updated":
                    token_usage = params.get("tokenUsage") if isinstance(params.get("tokenUsage"), dict) else {}
                    total_usage = token_usage.get("total") if isinstance(token_usage.get("total"), dict) else {}
                    try:
                        usage_obj = {
                            "input_tokens": int(total_usage.get("inputTokens") or 0),
                            "output_tokens": int(total_usage.get("outputTokens") or 0),
                            "total_tokens": int(total_usage.get("totalTokens") or 0),
                        }
                    except Exception:
                        usage_obj = usage_obj
                    continue

                if method == "turn/completed":
                    completed_turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
                    if completed_turn.get("status") == "failed":
                        turn_error = completed_turn.get("error") if isinstance(completed_turn.get("error"), dict) else {}
                        error_message = turn_error.get("message") or "Turn failed"
                        yield _encode(
                            {
                                "type": "response.failed",
                                "response": {
                                    "id": turn_id,
                                    "service_tier": observed_service_tier,
                                    "error": {"message": error_message},
                                },
                            }
                        )
                        yield _done()
                        return

                    yield _encode(
                        {
                            "type": "response.completed",
                            "response": {
                                "id": turn_id,
                                "service_tier": observed_service_tier,
                                **({"usage": usage_obj} if usage_obj else {}),
                            },
                        }
                    )
                    yield _done()
                    return

                if method == "codex/event/error":
                    status_code = self._extract_error_status(message)
                    error_message = self._extract_error_message(message) or "codex app-server error"
                    error_info = build_error_info(
                        source="codex-app-server",
                        phase="event_error",
                        raw_status=status_code,
                        raw_code=self._extract_error_code(message),
                        raw_message=error_message,
                        raw_body=message,
                    )
                    yield _encode(
                        {
                            "type": "response.failed",
                            "response": {
                                "id": turn_id,
                                "service_tier": observed_service_tier,
                                "error": {
                                    "message": error_message,
                                    "raw_status": error_info.get("raw_status"),
                                    "raw_code": error_info.get("raw_code"),
                                    "raw_message": error_info.get("raw_message"),
                                    "raw_body": error_info.get("raw_body"),
                                    "source": error_info.get("source"),
                                    "phase": error_info.get("phase"),
                                },
                                **({"status": status_code} if isinstance(status_code, int) else {}),
                            },
                        }
                    )
                    yield _done()
                    return

                if method == "error" and params.get("willRetry") is False:
                    status_code = self._extract_error_status(message)
                    error_message = self._extract_error_message(message) or "codex app-server error"
                    error_info = build_error_info(
                        source="codex-app-server",
                        phase="event_error",
                        raw_status=status_code,
                        raw_code=self._extract_error_code(message),
                        raw_message=error_message,
                        raw_body=message,
                    )
                    yield _encode(
                        {
                            "type": "response.failed",
                            "response": {
                                "id": turn_id,
                                "service_tier": observed_service_tier,
                                "error": {
                                    "message": error_message,
                                    "raw_status": error_info.get("raw_status"),
                                    "raw_code": error_info.get("raw_code"),
                                    "raw_message": error_info.get("raw_message"),
                                    "raw_body": error_info.get("raw_body"),
                                    "source": error_info.get("source"),
                                    "phase": error_info.get("phase"),
                                },
                                **({"status": status_code} if isinstance(status_code, int) else {}),
                            },
                        }
                    )
                    yield _done()
                    return
        except Exception as exc:
            error_info = exc.error_info if isinstance(exc, CodexAppServerError) and isinstance(exc.error_info, dict) else build_error_info(
                source="codex-app-server",
                phase="stream",
                raw_status=exc.status_code if isinstance(exc, CodexAppServerError) and isinstance(exc.status_code, int) else None,
                raw_message=f"codex app-server stream failed: {exc}",
                raw_body={"exception": str(exc)},
            )
            yield _encode(
                {
                    "type": "response.failed",
                    "response": {
                        "id": f"resp_{uuid.uuid4().hex}",
                        "service_tier": self._observed_service_tier or self._service_tier,
                        "error": {
                            "message": error_info.get("raw_message") or f"codex app-server stream failed: {exc}",
                            "raw_status": error_info.get("raw_status"),
                            "raw_code": error_info.get("raw_code"),
                            "raw_message": error_info.get("raw_message"),
                            "raw_body": error_info.get("raw_body"),
                            "source": error_info.get("source"),
                            "phase": error_info.get("phase"),
                        },
                        **({"status": error_info.get("raw_status")} if isinstance(error_info.get("raw_status"), int) else {}),
                    },
                }
            )
            yield _done()
        finally:
            self.close()

    def _next_message(self) -> Dict[str, Any]:
        if self._buffered_messages:
            return self._buffered_messages.pop(0)
        raw = self._ws.recv()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        return json.loads(raw)

    def _send_rpc(self, request_id: str, method: str, params: Dict[str, Any] | None) -> None:
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
        self._ws.send(json.dumps(payload, ensure_ascii=False))

    def _recv_until_id(self, request_id: str) -> Dict[str, Any]:
        while True:
            raw = self._ws.recv()
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            message = json.loads(raw)
            if message.get("id") == request_id:
                return message
            self._buffered_messages.append(message)

    @staticmethod
    def _extract_error_message(message: Dict[str, Any]) -> str | None:
        error = message.get("error")
        if isinstance(error, dict):
            if isinstance(error.get("message"), str) and error.get("message"):
                return error.get("message")
            data = error.get("data")
            if isinstance(data, dict) and isinstance(data.get("message"), str) and data.get("message"):
                return data.get("message")
        params = message.get("params")
        if isinstance(params, dict):
            nested_error = params.get("error")
            if isinstance(nested_error, dict) and isinstance(nested_error.get("message"), str) and nested_error.get("message"):
                return nested_error.get("message")
        return None

    @staticmethod
    def _extract_error_status(message: Dict[str, Any]) -> int | None:
        def _coerce(value: Any) -> int | None:
            if isinstance(value, int) and 100 <= value <= 599:
                return value
            if isinstance(value, str) and value.isdigit():
                numeric = int(value)
                if 100 <= numeric <= 599:
                    return numeric
            return None

        for container in (
            message.get("error"),
            message.get("params"),
            (message.get("params") or {}).get("error") if isinstance(message.get("params"), dict) else None,
        ):
            if not isinstance(container, dict):
                continue
            for key in ("status", "statusCode", "code"):
                status = _coerce(container.get(key))
                if status is not None:
                    return status
            data = container.get("data")
            if isinstance(data, dict):
                for key in ("status", "statusCode", "code"):
                    status = _coerce(data.get(key))
                    if status is not None:
                        return status
        message_text = CodexAppServerUpstream._extract_error_message(message) or ""
        for token in message_text.replace("(", " ").replace(")", " ").replace(",", " ").split():
            status = _coerce(token)
            if status is not None:
                return status
        return None

    @staticmethod
    def _extract_error_code(message: Dict[str, Any]) -> str | None:
        for container in (
            message.get("error"),
            message.get("params"),
            (message.get("params") or {}).get("error") if isinstance(message.get("params"), dict) else None,
        ):
            if not isinstance(container, dict):
                continue
            for key in ("code", "errorCode", "error_code"):
                value = container.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            data = container.get("data")
            if isinstance(data, dict):
                for key in ("code", "errorCode", "error_code"):
                    value = data.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
        return None

    @staticmethod
    def _normalize_output_item(item: Dict[str, Any]) -> Dict[str, Any] | None:
        item_type = item.get("type")
        if item_type == "function_call":
            name = item.get("name")
            if not isinstance(name, str) or not name:
                return None
            arguments = item.get("arguments")
            if not isinstance(arguments, str):
                arguments = _json_dumps_compact(arguments)
            call_id = item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex}"
            return {
                "type": "function_call",
                "name": name,
                "arguments": arguments,
                "call_id": call_id,
            }
        if item_type == "function_call_output":
            call_id = item.get("call_id")
            if not isinstance(call_id, str) or not call_id:
                return None
            output = item.get("output")
            return {
                "type": "function_call_output",
                "call_id": call_id,
                "output": _coerce_function_output_to_text(output),
            }
        if item_type == "web_search_call":
            call_id = item.get("call_id") or item.get("id") or f"web_search_{uuid.uuid4().hex}"
            if not isinstance(call_id, str) or not call_id:
                call_id = f"web_search_{uuid.uuid4().hex}"
            action = item.get("action") if isinstance(item.get("action"), dict) else {}
            arguments_payload: Dict[str, Any] = {}
            if isinstance(action, dict) and action:
                arguments_payload.update(action)
            for key in ("query", "q", "domains", "include_domains", "recency", "time_range", "days", "max_results"):
                value = item.get(key)
                if value is not None and key not in arguments_payload:
                    arguments_payload[key] = value
            arguments = _json_dumps_compact(arguments_payload or item)
            return {
                "type": "web_search_call",
                "name": "web_search",
                "call_id": call_id,
                "arguments": arguments,
                "parameters": arguments_payload or item,
            }
        return None


def connect_codex_app_server(
    *,
    app_server_url: str,
    candidate_label: str = "default",
    model: str,
    input_items: List[Dict[str, Any]],
    instructions: str | None,
    tools: List[Dict[str, Any]] | None,
    tool_choice: Any | None,
    parallel_tool_calls: bool,
    reasoning_param: Dict[str, Any] | None,
    service_tier: str | None,
    web_search_mode: str | None = None,
    thread_session: Dict[str, Any] | None = None,
    cwd: str | None = None,
    approval_policy: str = "never",
    sandbox_mode: str = "workspace-write",
    verbose: bool = False,
) -> CodexAppServerUpstream:
    normalized_service_tier = normalize_service_tier_for_codex(service_tier)
    normalized_web_search_mode = normalize_web_search_mode_for_codex(web_search_mode)
    trailing_tool_outputs = has_trailing_tool_outputs(input_items)
    dynamic_tools = [] if trailing_tool_outputs else convert_responses_tools_to_codex_dynamic_tools(tools, tool_choice)
    bridge_instructions = build_codex_bridge_instructions(instructions, dynamic_tools, normalized_web_search_mode)
    resolved_cwd = cwd or os.getcwd()
    websocket = ws_connect(app_server_url, open_timeout=30, close_timeout=10)
    try:
        init_response = _rpc_call(
            websocket,
            "initialize",
            {
                "protocolVersion": 2,
                "clientInfo": {"name": "chatmock", "version": "local"},
                "capabilities": {
                    "experimentalApi": True,
                    "optOutNotificationMethods": [],
                },
            },
        )
        _ensure_success_response(init_response, phase="initialize")
        _login_with_external_auth_tokens(websocket)

        thread_start_params: Dict[str, Any] = {
            "cwd": resolved_cwd,
            "model": model,
            "approvalPolicy": approval_policy,
            "sandbox": sandbox_mode,
            "ephemeral": False if isinstance(thread_session, dict) and thread_session.get("session_key") else True,
            "serviceName": "chatmock",
            "personality": "pragmatic",
            "experimentalRawEvents": True,
            "persistExtendedHistory": True,
            "config": {
                "default_tools_enabled": False,
                "web_search": normalized_web_search_mode,
            },
        }
        if normalized_web_search_mode != "disabled":
            thread_start_params["config"]["open_world_enabled"] = True
            thread_start_params["tools"] = {"web_search": True}
        if bridge_instructions:
            thread_start_params["developerInstructions"] = bridge_instructions
        if normalized_service_tier:
            thread_start_params["serviceTier"] = normalized_service_tier
        if dynamic_tools:
            thread_start_params["dynamicTools"] = dynamic_tools

        thread_resume_or_fork_params: Dict[str, Any] = {
            "cwd": resolved_cwd,
            "model": model,
            "approvalPolicy": approval_policy,
            "sandbox": sandbox_mode,
            "personality": "pragmatic",
            "config": {
                "default_tools_enabled": False,
                "web_search": normalized_web_search_mode,
            },
        }
        if bridge_instructions:
            thread_resume_or_fork_params["developerInstructions"] = bridge_instructions
        if normalized_service_tier:
            thread_resume_or_fork_params["serviceTier"] = normalized_service_tier

        result = None
        thread = None
        observed_service_tier = normalized_service_tier
        active_thread_mode = "start"

        session_key = thread_session.get("session_key") if isinstance(thread_session, dict) else None
        resume_thread_id = thread_session.get("thread_id") if isinstance(thread_session, dict) else None
        fork_from_thread_id = thread_session.get("fork_from_thread_id") if isinstance(thread_session, dict) else None
        turn_input_items = (
            thread_session.get("turn_input_items")
            if isinstance(thread_session, dict) and isinstance(thread_session.get("turn_input_items"), list)
            else input_items
        )
        full_input_items = (
            thread_session.get("full_input_items")
            if isinstance(thread_session, dict) and isinstance(thread_session.get("full_input_items"), list)
            else input_items
        )

        thread_attempts: List[tuple[str, Dict[str, Any], str]] = []
        if isinstance(fork_from_thread_id, str) and fork_from_thread_id:
            fork_params = dict(thread_resume_or_fork_params)
            fork_params["threadId"] = fork_from_thread_id
            thread_attempts.append(("thread/fork", fork_params, "thread_fork"))
        elif isinstance(resume_thread_id, str) and resume_thread_id:
            resume_params = dict(thread_resume_or_fork_params)
            resume_params["threadId"] = resume_thread_id
            thread_attempts.append(("thread/resume", resume_params, "thread_resume"))
        thread_attempts.append(("thread/start", thread_start_params, "thread_start"))

        last_thread_error: CodexAppServerError | None = None
        for method, params, phase in thread_attempts:
            try:
                thread_response = _rpc_call(websocket, method, params)
                thread_response = _ensure_success_response(thread_response, phase=phase)
                result = thread_response.get("result") if isinstance(thread_response, dict) else None
                thread = result.get("thread") if isinstance(result, dict) else None
                if not isinstance(thread, dict) or not isinstance(thread.get("id"), str):
                    raise CodexAppServerError(
                        f"Invalid {method} response from codex app-server",
                        error_info=build_error_info(
                            source="codex-app-server",
                            phase=phase,
                            raw_message=f"Invalid {method} response from codex app-server",
                            raw_body=thread_response,
                        ),
                    )
                observed_service_tier = (
                    result.get("serviceTier")
                    if isinstance(result, dict) and isinstance(result.get("serviceTier"), str)
                    else normalized_service_tier
                )
                if method == "thread/resume":
                    active_thread_mode = "resume"
                elif method == "thread/fork":
                    active_thread_mode = "fork"
                else:
                    active_thread_mode = "start"
                break
            except CodexAppServerError as exc:
                last_thread_error = exc
                if method == "thread/start":
                    raise
                if isinstance(session_key, str) and session_key:
                    clear_thread_session(session_key)
                if verbose:
                    print(f"codex app-server {method} failed for {candidate_label}: {exc}")
                continue

        if not isinstance(thread, dict) or not isinstance(thread.get("id"), str):
            raise last_thread_error or CodexAppServerError("Unable to establish codex thread")
    except Exception:
        try:
            websocket.close()
        except Exception:
            pass
        raise

    upstream = CodexAppServerUpstream(
        websocket,
        thread_id=thread["id"],
        model=model,
        input_items=turn_input_items,
        reasoning_param=reasoning_param,
        service_tier=normalized_service_tier,
        observed_service_tier=observed_service_tier,
        cwd=resolved_cwd,
        approval_policy=approval_policy,
        tools=dynamic_tools,
        verbose=verbose,
    )
    upstream.chatmock_source = "codex-app-server"
    upstream.chatmock_thread_id = thread["id"]
    upstream.chatmock_thread_mode = active_thread_mode
    if isinstance(session_key, str) and session_key:
        save_thread_session(
            session_key,
            thread_id=thread["id"],
            candidate_label=candidate_label,
            candidate_url=app_server_url,
            input_items=full_input_items,
        )
    return upstream
