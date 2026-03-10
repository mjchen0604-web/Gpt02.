#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PORT="${PORT:-8000}"
CHATMOCK_DATA_DIR="${CHATMOCK_DATA_DIR:-/app/storage}"
CODEX_HOME="${CODEX_HOME:-$CHATMOCK_DATA_DIR/.codex}"
CHATGPT_LOCAL_HOME="${CHATGPT_LOCAL_HOME:-$CODEX_HOME}"
CHATGPT_LOCAL_CODEX_APP_SERVER_URL="${CHATGPT_LOCAL_CODEX_APP_SERVER_URL:-ws://127.0.0.1:8787}"

export CODEX_HOME
export CHATGPT_LOCAL_HOME
export CHATGPT_LOCAL_UPSTREAM="codex-app-server"
export CHATGPT_LOCAL_CODEX_APP_SERVER_URL
export CHATMOCK_MANAGE_CODEX_APP_SERVER="${CHATMOCK_MANAGE_CODEX_APP_SERVER:-true}"
export CHATMOCK_AUTO_START_CODEX_APP_SERVER="${CHATMOCK_AUTO_START_CODEX_APP_SERVER:-true}"
export CHATMOCK_DASHBOARD_ALLOW_UPLOAD="${CHATMOCK_DASHBOARD_ALLOW_UPLOAD:-true}"

mkdir -p "$CHATMOCK_DATA_DIR" "$CODEX_HOME"

materialize_secret() {
  local target_path="$1"
  local literal_var="$2"
  local b64_var="$3"
  local file_var="$4"
  local required="$5"
  local current_literal="${!literal_var:-}"
  local current_b64="${!b64_var:-}"
  local current_file="${!file_var:-}"

  if [[ -n "$current_file" ]]; then
    if [[ ! -f "$current_file" ]]; then
      echo "[render-start] Missing file for $file_var: $current_file" >&2
      exit 1
    fi
    cp "$current_file" "$target_path"
  elif [[ -n "$current_literal" ]]; then
    printf "%s" "$current_literal" > "$target_path"
  elif [[ -n "$current_b64" ]]; then
    printf "%s" "$current_b64" | base64 -d > "$target_path"
  elif [[ ! -f "$target_path" && "$required" == "required" ]]; then
    echo "[render-start] Missing required secret for $target_path. Set one of $literal_var, $b64_var, or $file_var." >&2
    exit 1
  fi

  if [[ -f "$target_path" ]]; then
    chmod 600 "$target_path"
  fi
}

materialize_secret "$CODEX_HOME/auth.json" "CODEX_AUTH_JSON" "CODEX_AUTH_B64" "CODEX_AUTH_JSON_FILE" "optional"
materialize_secret "$CODEX_HOME/config.toml" "CODEX_CONFIG_TOML" "CODEX_CONFIG_B64" "CODEX_CONFIG_TOML_FILE" "optional"

ARGS=(serve --host 0.0.0.0 --port "$PORT" --upstream codex-app-server --codex-app-server-url "$CHATGPT_LOCAL_CODEX_APP_SERVER_URL")

bool() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

if bool "${VERBOSE:-}" || bool "${CHATGPT_LOCAL_VERBOSE:-}"; then
  ARGS+=(--verbose)
fi
if bool "${VERBOSE_OBFUSCATION:-}" || bool "${CHATGPT_LOCAL_VERBOSE_OBFUSCATION:-}"; then
  ARGS+=(--verbose-obfuscation)
fi
if bool "${CHATGPT_LOCAL_EXPOSE_REASONING_MODELS:-true}"; then
  ARGS+=(--expose-reasoning-models)
fi
if bool "${CHATGPT_LOCAL_ENABLE_WEB_SEARCH:-false}"; then
  ARGS+=(--enable-web-search)
fi
if [[ -n "${CHATGPT_LOCAL_REASONING_EFFORT:-}" ]]; then
  ARGS+=(--reasoning-effort "${CHATGPT_LOCAL_REASONING_EFFORT}")
fi
if [[ -n "${CHATGPT_LOCAL_REASONING_SUMMARY:-}" ]]; then
  ARGS+=(--reasoning-summary "${CHATGPT_LOCAL_REASONING_SUMMARY}")
fi
if [[ -n "${CHATGPT_LOCAL_REASONING_COMPAT:-}" ]]; then
  ARGS+=(--reasoning-compat "${CHATGPT_LOCAL_REASONING_COMPAT}")
fi
if [[ -n "${CHATGPT_LOCAL_DEBUG_MODEL:-}" ]]; then
  ARGS+=(--debug-model "${CHATGPT_LOCAL_DEBUG_MODEL}")
fi
if [[ -n "${CHATGPT_LOCAL_SERVICE_TIER:-}" ]]; then
  ARGS+=(--service-tier "${CHATGPT_LOCAL_SERVICE_TIER}")
fi

echo "[render-start] Starting ChatMock on 0.0.0.0:$PORT"
exec python chatmock.py "${ARGS[@]}"
