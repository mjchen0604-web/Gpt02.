from __future__ import annotations

from typing import Any, Mapping

from .config import BASE_INSTRUCTIONS, GPT5_CODEX_INSTRUCTIONS, GPT5_HYBRID_INSTRUCTIONS
from .reasoning import split_model_alias


PUBLIC_MODEL_BASES = {
    "gpt-5",
    "gpt-5.1",
    "gpt-5.2",
    "gpt-5.4",
    "gpt-5.3-codex",
    "gpt-5-codex",
    "gpt-5.2-codex",
    "gpt-5.1-codex",
    "gpt-5.1-codex-max",
    "gpt-5.1-codex-mini",
    "codex-mini",
    "codex-mini-latest",
}


def normalized_model_base(model: str | None) -> str:
    base, _, _ = split_model_alias(model)
    return (base or "").strip().lower()


def is_public_chatmock_model(model: str | None) -> bool:
    return normalized_model_base(model) in PUBLIC_MODEL_BASES


def prompt_family_for_model(model: str | None) -> str:
    base = normalized_model_base(model)
    if base.startswith("gpt-5.4"):
        return "hybrid"
    if "codex" in base or base.startswith("codex"):
        return "codex"
    return "base"


def select_instructions_for_model(config: Mapping[str, Any], model: str | None) -> str:
    family = prompt_family_for_model(model)
    base = config.get("BASE_INSTRUCTIONS", BASE_INSTRUCTIONS)
    if family == "hybrid":
        hybrid = config.get("GPT5_HYBRID_INSTRUCTIONS") or GPT5_HYBRID_INSTRUCTIONS
        if isinstance(hybrid, str) and hybrid.strip():
            return hybrid
    if family == "codex":
        codex = config.get("GPT5_CODEX_INSTRUCTIONS") or GPT5_CODEX_INSTRUCTIONS
        if isinstance(codex, str) and codex.strip():
            return codex
    return base
