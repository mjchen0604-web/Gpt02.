from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, List


_LOCK = threading.RLock()
_SESSIONS: Dict[str, Dict[str, Any]] = {}
_MAX_SESSIONS = 2048


def _serialize_input_items(input_items: List[Dict[str, Any]]) -> List[str]:
    out: List[str] = []
    for item in input_items or []:
        try:
            out.append(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        except Exception:
            out.append(str(item))
    return out


def get_thread_session(session_key: str | None) -> Dict[str, Any] | None:
    if not isinstance(session_key, str) or not session_key.strip():
        return None
    with _LOCK:
        state = _SESSIONS.get(session_key.strip())
        return dict(state) if isinstance(state, dict) else None


def clear_thread_session(session_key: str | None) -> None:
    if not isinstance(session_key, str) or not session_key.strip():
        return
    with _LOCK:
        _SESSIONS.pop(session_key.strip(), None)


def save_thread_session(
    session_key: str | None,
    *,
    thread_id: str,
    candidate_label: str,
    candidate_url: str,
    input_items: List[Dict[str, Any]],
) -> None:
    if not isinstance(session_key, str) or not session_key.strip():
        return
    serialized = _serialize_input_items(input_items)
    record = {
        "thread_id": thread_id,
        "candidate_label": candidate_label,
        "candidate_url": candidate_url,
        "input_items": serialized,
        "updated_at": time.time(),
    }
    with _LOCK:
        _SESSIONS[session_key.strip()] = record
        if len(_SESSIONS) > _MAX_SESSIONS:
            oldest_key = min(_SESSIONS.items(), key=lambda item: item[1].get("updated_at") or 0)[0]
            _SESSIONS.pop(oldest_key, None)


def build_thread_session_state(
    *,
    session_key: str | None,
    input_items: List[Dict[str, Any]],
    explicit_thread_id: str | None = None,
    fork_from_thread_id: str | None = None,
) -> Dict[str, Any] | None:
    normalized_session_key = session_key.strip() if isinstance(session_key, str) and session_key.strip() else None
    normalized_explicit_thread_id = (
        explicit_thread_id.strip()
        if isinstance(explicit_thread_id, str) and explicit_thread_id.strip()
        else None
    )
    normalized_fork_from_thread_id = (
        fork_from_thread_id.strip()
        if isinstance(fork_from_thread_id, str) and fork_from_thread_id.strip()
        else None
    )

    if not any([normalized_session_key, normalized_explicit_thread_id, normalized_fork_from_thread_id]):
        return None

    record = get_thread_session(normalized_session_key) if normalized_session_key else None
    current_serialized = _serialize_input_items(input_items)
    turn_input_items = list(input_items)
    resume_thread_id = normalized_explicit_thread_id
    preferred_label = None
    preferred_url = None
    thread_mode = "start"

    if isinstance(record, dict):
        preferred_label = record.get("candidate_label")
        preferred_url = record.get("candidate_url")
        previous_serialized = record.get("input_items") if isinstance(record.get("input_items"), list) else []
        previous_thread_id = record.get("thread_id") if isinstance(record.get("thread_id"), str) else None
        if not normalized_explicit_thread_id and previous_thread_id:
            resume_thread_id = previous_thread_id

        if previous_serialized and current_serialized[: len(previous_serialized)] == previous_serialized:
            suffix_count = len(current_serialized) - len(previous_serialized)
            if suffix_count > 0:
                turn_input_items = input_items[-suffix_count:]
            else:
                resume_thread_id = None
                preferred_label = None
                preferred_url = None
        elif not normalized_explicit_thread_id and not normalized_fork_from_thread_id:
            resume_thread_id = None
            preferred_label = None
            preferred_url = None

    if normalized_fork_from_thread_id:
        thread_mode = "fork"
    elif resume_thread_id:
        thread_mode = "resume"

    return {
        "session_key": normalized_session_key,
        "thread_id": resume_thread_id,
        "fork_from_thread_id": normalized_fork_from_thread_id,
        "turn_input_items": turn_input_items,
        "full_input_items": list(input_items),
        "candidate_label": preferred_label,
        "candidate_url": preferred_url,
        "thread_mode": thread_mode,
    }
