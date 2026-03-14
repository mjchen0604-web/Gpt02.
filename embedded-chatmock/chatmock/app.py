from __future__ import annotations

import os

from flask import Flask, jsonify

from .config import (
    BASE_INSTRUCTIONS,
    CODEX_APP_SERVER_URL_DEFAULT,
    GPT5_CODEX_INSTRUCTIONS,
    UPSTREAM_MODE_DEFAULT,
)
from .codex_manager import CodexAppServerPoolManager
from .http import build_cors_headers
from .routes_anthropic import anthropic_bp
from .routes_dashboard import apply_persisted_dashboard_settings, dashboard_bp
from .routes_openai import openai_bp
from .routes_ollama import ollama_bp


def create_app(
    verbose: bool = False,
    verbose_obfuscation: bool = False,
    reasoning_effort: str = "medium",
    reasoning_summary: str = "auto",
    reasoning_compat: str = "think-tags",
    debug_model: str | None = None,
    expose_reasoning_models: bool = False,
    default_web_search: bool = False,
    service_tier: str | None = None,
    upstream_mode: str = UPSTREAM_MODE_DEFAULT,
    codex_app_server_url: str = CODEX_APP_SERVER_URL_DEFAULT,
) -> Flask:
    app = Flask(__name__)
    expose_service_tier = (
        os.getenv("CHATMOCK_EXPOSE_SERVICE_TIER") or "0"
    ).strip().lower() in ("1", "true", "yes", "on")
    expose_thread_ids = (
        os.getenv("CHATMOCK_EXPOSE_THREAD_IDS") or ""
    ).strip().lower() in ("1", "true", "yes", "on")
    normalized_service_tier = (
        service_tier.strip().lower() if isinstance(service_tier, str) and service_tier.strip() else None
    )
    if normalized_service_tier in ("off", "none", "unset"):
        normalized_service_tier = None
    normalized_upstream_mode = (
        upstream_mode.strip().lower()
        if isinstance(upstream_mode, str) and upstream_mode.strip()
        else UPSTREAM_MODE_DEFAULT
    )
    normalized_codex_app_server_url = (
        codex_app_server_url.strip()
        if isinstance(codex_app_server_url, str) and codex_app_server_url.strip()
        else CODEX_APP_SERVER_URL_DEFAULT
    )

    app.config.update(
        VERBOSE=bool(verbose),
        VERBOSE_OBFUSCATION=bool(verbose_obfuscation),
        REASONING_EFFORT=reasoning_effort,
        REASONING_SUMMARY=reasoning_summary,
        REASONING_COMPAT=reasoning_compat,
        DEBUG_MODEL=debug_model,
        BASE_INSTRUCTIONS=BASE_INSTRUCTIONS,
        GPT5_CODEX_INSTRUCTIONS=GPT5_CODEX_INSTRUCTIONS,
        EXPOSE_REASONING_MODELS=bool(expose_reasoning_models),
        DEFAULT_WEB_SEARCH=bool(default_web_search),
        SERVICE_TIER=normalized_service_tier,
        UPSTREAM_MODE=normalized_upstream_mode,
        CODEX_APP_SERVER_URL=normalized_codex_app_server_url,
        EXPOSE_SERVICE_TIER=bool(expose_service_tier),
        EXPOSE_THREAD_IDS=bool(expose_thread_ids),
    )
    apply_persisted_dashboard_settings(app)
    manager = CodexAppServerPoolManager(str(app.config.get("CODEX_APP_SERVER_URL") or normalized_codex_app_server_url))
    app.config["CODEX_APP_SERVER_MANAGER"] = manager
    manager.autostart_if_possible()

    @app.get("/")
    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

    @app.after_request
    def _cors(resp):
        for k, v in build_cors_headers().items():
            resp.headers.setdefault(k, v)
        return resp

    app.register_blueprint(openai_bp)
    app.register_blueprint(ollama_bp)
    app.register_blueprint(anthropic_bp)
    app.register_blueprint(dashboard_bp)

    return app
