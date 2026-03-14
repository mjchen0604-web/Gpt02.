from __future__ import annotations

import datetime
import json
import os
import re
from typing import Any, Mapping

from flask import Response, jsonify, make_response

from .http import build_cors_headers


_INSUFFICIENT_KEYWORDS = (
    "quota",
    "credits",
    "billing",
    "insufficient_quota",
    "insufficient quota",
    "exceeded your current quota",
    "balance",
)

_INVALID_ACCOUNT_KEYWORDS = (
    "invalid",
    "unauthorized",
    "revoked",
    "deactivated",
    "deleted",
    "suspended",
    "forbidden",
    "disabled",
)

_PERMISSION_KEYWORDS = (
    "permission",
    "not allowed",
    "access denied",
    "insufficient permissions",
)

_RATE_LIMIT_KEYWORDS = (
    "usage limit",
    "try again at",
    "upgrade to plus to continue using codex",
    "rate limit",
    "too many requests",
)


def _compact_string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    try:
        return str(value).strip()
    except Exception:
        return ""


def _jsonable(value: Any) -> Any:
    if isinstance(value, (dict, list, str, int, float, bool)) or value is None:
        return value
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except Exception:
        return _compact_string(value)


def _extract_nested_message(payload: Any) -> str:
    if not isinstance(payload, Mapping):
        return ""
    error_block = payload.get("error")
    if isinstance(error_block, Mapping):
        message = _compact_string(error_block.get("message"))
        if message:
            return message
    for key in ("message", "detail", "error_description", "errorMessage"):
        message = _compact_string(payload.get(key))
        if message:
            return message
    return ""


def _extract_nested_code(payload: Any) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    for key in ("code", "error_code", "errorCode"):
        code = _compact_string(payload.get(key))
        if code:
            return code
    error_block = payload.get("error")
    if isinstance(error_block, Mapping):
        code = _compact_string(error_block.get("code"))
        if code:
            return code
    return None


def _error_text_haystack(info: Mapping[str, Any]) -> str:
    text_parts = [
        _compact_string(info.get("raw_message")),
        _compact_string(info.get("raw_code")),
    ]
    raw_body = info.get("raw_body")
    if isinstance(raw_body, (dict, list)):
        try:
            text_parts.append(json.dumps(raw_body, ensure_ascii=False))
        except Exception:
            text_parts.append(_compact_string(raw_body))
    else:
        text_parts.append(_compact_string(raw_body))
    return " ".join(part for part in text_parts if part).lower()


def extract_retry_after_unlock_ts(info: Mapping[str, Any]) -> float | None:
    haystack = _error_text_haystack(info)
    match = re.search(r"try again at\s+([^.]+)", haystack, flags=re.IGNORECASE)
    if not match:
        return None
    raw_value = match.group(1).strip().rstrip(".,;:!?)")
    cleaned = re.sub(r"(\d{1,2})(st|nd|rd|th)", r"\1", raw_value, flags=re.IGNORECASE)
    local_tz = datetime.datetime.now().astimezone().tzinfo or datetime.timezone.utc
    for fmt in ("%b %d, %Y %I:%M %p", "%B %d, %Y %I:%M %p"):
        try:
            dt = datetime.datetime.strptime(cleaned, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=local_tz)
            return dt.timestamp()
        except Exception:
            continue
    return None


def build_error_info(
    *,
    source: str,
    phase: str,
    raw_status: int | None = None,
    raw_code: str | None = None,
    raw_message: str | None = None,
    raw_body: Any = None,
    category_override: str | None = None,
) -> dict[str, Any]:
    return {
        "source": _compact_string(source) or "upstream",
        "phase": _compact_string(phase) or "unknown",
        "raw_status": int(raw_status) if isinstance(raw_status, int) else None,
        "raw_code": _compact_string(raw_code) or None,
        "raw_message": _compact_string(raw_message) or None,
        "raw_body": _jsonable(raw_body),
        "category_override": _compact_string(category_override) or None,
    }


def error_info_from_http_response(source: str, phase: str, response: Any) -> dict[str, Any]:
    raw_status = None
    try:
        raw_status = int(getattr(response, "status_code", 0) or 0) or None
    except Exception:
        raw_status = None

    raw_body: Any = None
    raw_message: str | None = None
    raw_code: str | None = None

    try:
        content = getattr(response, "content", None)
        if content:
            raw_body = json.loads(content.decode("utf-8", errors="ignore"))
        else:
            text = _compact_string(getattr(response, "text", ""))
            raw_body = text or None
    except Exception:
        text = _compact_string(getattr(response, "text", ""))
        raw_body = text or None

    raw_message = _extract_nested_message(raw_body)
    raw_code = _extract_nested_code(raw_body)
    if not raw_message:
        raw_message = _compact_string(getattr(response, "reason", "")) or _compact_string(getattr(response, "text", ""))

    return build_error_info(
        source=source,
        phase=phase,
        raw_status=raw_status,
        raw_code=raw_code,
        raw_message=raw_message,
        raw_body=raw_body,
    )


def error_info_from_event_response(source: str, phase: str, response_payload: Any) -> dict[str, Any]:
    payload = response_payload if isinstance(response_payload, Mapping) else {}
    error_block = payload.get("error") if isinstance(payload.get("error"), Mapping) else {}
    raw_status = payload.get("status")
    if not isinstance(raw_status, int):
        maybe_status = error_block.get("raw_status") if isinstance(error_block, Mapping) else None
        raw_status = maybe_status if isinstance(maybe_status, int) else None
    raw_code = (
        _compact_string(error_block.get("raw_code"))
        or _compact_string(error_block.get("code"))
        or _extract_nested_code(payload)
        or None
    )
    raw_message = (
        _compact_string(error_block.get("raw_message"))
        or _compact_string(error_block.get("message"))
        or _extract_nested_message(payload)
        or None
    )
    raw_body = error_block.get("raw_body") if isinstance(error_block, Mapping) and error_block.get("raw_body") is not None else payload
    return build_error_info(
        source=source,
        phase=phase,
        raw_status=raw_status,
        raw_code=raw_code,
        raw_message=raw_message,
        raw_body=raw_body,
    )


def error_info_from_flask_response(source: str, phase: str, response: Response) -> dict[str, Any]:
    raw_status = int(getattr(response, "status_code", 0) or 0) or None
    body_text = ""
    try:
        body_text = response.get_data(as_text=True) or ""
    except Exception:
        body_text = ""
    try:
        body = json.loads(body_text) if body_text else {}
    except Exception:
        body = body_text or None

    if isinstance(body, Mapping):
        error_block = body.get("error") if isinstance(body.get("error"), Mapping) else {}
        if error_block and (
            error_block.get("raw_status") is not None
            or error_block.get("raw_code") is not None
            or error_block.get("raw_message") is not None
        ):
            return build_error_info(
                source=_compact_string(error_block.get("source")) or source,
                phase=_compact_string(error_block.get("phase")) or phase,
                raw_status=error_block.get("raw_status") if isinstance(error_block.get("raw_status"), int) else raw_status,
                raw_code=_compact_string(error_block.get("raw_code")) or None,
                raw_message=_compact_string(error_block.get("raw_message")) or _compact_string(error_block.get("message")) or None,
                raw_body=error_block.get("raw_body"),
                category_override=_compact_string(error_block.get("code")) or None,
            )
        return build_error_info(
            source=source,
            phase=phase,
            raw_status=raw_status,
            raw_code=_extract_nested_code(body),
            raw_message=_extract_nested_message(body) or body_text,
            raw_body=body,
        )

    return build_error_info(
        source=source,
        phase=phase,
        raw_status=raw_status,
        raw_message=body_text,
        raw_body=body,
    )


def classify_error(info: Mapping[str, Any]) -> str:
    category_override = _compact_string(info.get("category_override"))
    if category_override in (
        "insufficient_balance",
        "rate_limited",
        "account_invalid",
        "permission_denied",
        "invalid_request",
        "not_found",
        "request_too_large",
        "generic_failure",
    ):
        return category_override
    raw_status = info.get("raw_status")
    status = int(raw_status) if isinstance(raw_status, int) else None
    haystack = _error_text_haystack(info)

    if status == 429:
        if any(keyword in haystack for keyword in _INSUFFICIENT_KEYWORDS):
            return "insufficient_balance"
        return "rate_limited"
    if status == 401:
        return "account_invalid"
    if status == 403:
        if any(keyword in haystack for keyword in _INVALID_ACCOUNT_KEYWORDS):
            return "account_invalid"
        return "permission_denied"
    if status == 402:
        if any(keyword in haystack for keyword in _INSUFFICIENT_KEYWORDS):
            return "insufficient_balance"
        if any(keyword in haystack for keyword in _INVALID_ACCOUNT_KEYWORDS):
            return "account_invalid"
        return "rate_limited"
    if status in (400, 422):
        return "invalid_request"
    if status == 404:
        return "not_found"
    if status == 413:
        return "request_too_large"
    if any(keyword in haystack for keyword in _RATE_LIMIT_KEYWORDS):
        return "rate_limited"
    if any(keyword in haystack for keyword in _PERMISSION_KEYWORDS):
        return "permission_denied"
    return "generic_failure"


def normalized_http_status(info: Mapping[str, Any]) -> int:
    category = classify_error(info)
    raw_status = info.get("raw_status")
    status = int(raw_status) if isinstance(raw_status, int) else None
    if category in ("insufficient_balance", "rate_limited"):
        return 429
    if category == "account_invalid":
        return 401
    if category == "permission_denied":
        return 403
    if category == "invalid_request":
        return 400
    if category == "not_found":
        return 404
    if category == "request_too_large":
        return 413
    if status is not None and 500 <= status <= 599:
        return status
    return 502


def normalized_error_type(info: Mapping[str, Any]) -> str:
    category = classify_error(info)
    if category in ("insufficient_balance", "rate_limited"):
        return "rate_limit_error"
    if category == "account_invalid":
        return "authentication_error"
    if category == "permission_denied":
        return "permission_error"
    if category in ("invalid_request", "not_found", "request_too_large"):
        return "invalid_request_error"
    return "server_error"


def normalized_error_code(info: Mapping[str, Any]) -> str | None:
    raw_code = _compact_string(info.get("raw_code"))
    if raw_code.lower() == "deactivated_workspace":
        return None
    if raw_code:
        return raw_code
    category = classify_error(info)
    if category == "insufficient_balance":
        return "insufficient_quota"
    if category == "rate_limited":
        return "rate_limit_exceeded"
    if category == "account_invalid":
        return "invalid_api_key"
    return None


def normalized_error_message(info: Mapping[str, Any]) -> str:
    raw_message = _compact_string(info.get("raw_message"))
    category = classify_error(info)
    expose_internal = (os.getenv("CHATMOCK_EXPOSE_INTERNAL_ERROR_DETAILS") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if raw_message:
        if expose_internal:
            return raw_message
        lowered = raw_message.lower()
        if "deactivated_workspace" in lowered:
            return "Account unavailable"
        if "codex app-server" in lowered:
            return "The server had an error while processing your request."
        if category == "generic_failure" and any(
            marker in lowered
            for marker in (
                "codex app-server",
                "chatmock",
                "candidate",
                "tool_retry",
                "request_start",
                "retry_exhausted",
                "no candidate succeeded",
            )
        ):
            return "The server had an error while processing your request."
        return raw_message
    if category == "insufficient_balance":
        return "Insufficient balance or quota"
    if category == "rate_limited":
        return "Rate limit exceeded"
    if category == "account_invalid":
        return "Account unavailable"
    if category == "permission_denied":
        return "Permission denied"
    if category == "invalid_request":
        return "Invalid request"
    if category == "not_found":
        return "Resource not found"
    if category == "request_too_large":
        return "Request body too large"
    return "Upstream error"


def normalized_error_payload(info: Mapping[str, Any]) -> dict[str, Any]:
    payload = {
        "message": normalized_error_message(info),
        "type": normalized_error_type(info),
        "param": None,
        "code": normalized_error_code(info),
    }
    expose_internal = (os.getenv("CHATMOCK_EXPOSE_INTERNAL_ERROR_DETAILS") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if expose_internal:
        payload.update(
            {
                "raw_status": info.get("raw_status"),
                "raw_code": info.get("raw_code"),
                "raw_message": info.get("raw_message"),
                "raw_body": _jsonable(info.get("raw_body")),
                "source": info.get("source"),
                "phase": info.get("phase"),
            }
        )
    return payload


def should_retry_next_candidate(info: Mapping[str, Any]) -> bool:
    return classify_error(info) in ("insufficient_balance", "rate_limited", "account_invalid") or extract_retry_after_unlock_ts(info) is not None


def build_openai_error_response(info: Mapping[str, Any]) -> Response:
    payload = {"error": normalized_error_payload(info)}
    response = make_response(jsonify(payload), normalized_http_status(info))
    for key, value in build_cors_headers().items():
        response.headers.setdefault(key, value)
    return response


def build_anthropic_error_response(info: Mapping[str, Any]) -> Response:
    payload = {"type": "error", "error": normalized_error_payload(info)}
    response = make_response(jsonify(payload), normalized_http_status(info))
    for key, value in build_cors_headers().items():
        response.headers.setdefault(key, value)
    return response


def build_ollama_error_response(info: Mapping[str, Any]) -> Response:
    payload = {"error": normalized_error_payload(info)}
    response = make_response(jsonify(payload), normalized_http_status(info))
    for key, value in build_cors_headers().items():
        response.headers.setdefault(key, value)
    return response
