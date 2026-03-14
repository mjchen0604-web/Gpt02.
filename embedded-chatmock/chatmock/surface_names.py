from __future__ import annotations

from typing import Any


_UPSTREAM_PUBLIC_NAMES = {
    "chatgpt-backend": "IIfyI",
    "codex-app-server": "IIfyl",
}


def public_upstream_name(name: str | None) -> str | None:
    if not isinstance(name, str) or not name.strip():
        return name
    normalized = name.strip().lower()
    return _UPSTREAM_PUBLIC_NAMES.get(normalized, name)


def redact_internal_route_terms(value: Any) -> Any:
    if isinstance(value, str):
        text = value
        for raw, masked in _UPSTREAM_PUBLIC_NAMES.items():
            text = text.replace(raw, masked)
            text = text.replace(raw.lower(), masked)
            text = text.replace(raw.upper(), masked)
        return text
    return value
