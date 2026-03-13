from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

import yaml


def _override_path() -> Path:
    explicit = (os.getenv("CHATMOCK_PAYLOAD_OVERRIDE_PATH") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    return Path(__file__).resolve().parent.parent / "payload-overrides.yaml"


def _load_override_rules() -> List[Dict[str, Any]]:
    path = _override_path()
    if not path.exists():
        return []
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    root = payload.get("payload") if isinstance(payload, dict) else {}
    overrides = root.get("override") if isinstance(root, dict) else []
    if not isinstance(overrides, list):
        return []
    return [item for item in overrides if isinstance(item, dict)]


def apply_payload_overrides(payload: Dict[str, Any], requested_model: str | None) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return payload
    model_name = str(requested_model or "").strip().lower()
    if not model_name:
        return payload

    merged = dict(payload)
    for rule in _load_override_rules():
        models = rule.get("models")
        if not isinstance(models, list):
            continue
        matched = False
        for entry in models:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip().lower()
            if name and name == model_name:
                matched = True
                break
        if not matched:
            continue
        params = rule.get("params")
        if isinstance(params, dict):
            for key, value in params.items():
                merged[key] = value
    return merged
