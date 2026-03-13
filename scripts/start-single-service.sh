#!/usr/bin/env bash
set -euo pipefail

CHAT_HOST="${CHATCORE_INTERNAL_CHAT_HOST:-127.0.0.1}"
CHAT_PORT="${CHATCORE_INTERNAL_CHAT_PORT:-1455}"
AUTH_DIR="${CHATMOCK_AUTH_DIR:-/data/chatmock-accounts}"
CHAT_ROOT="/app/embedded-chatmock"
SETTINGS_PATH="${CHATMOCK_DASHBOARD_SETTINGS_PATH:-/data/chatmock-dashboard-settings.json}"
CODEX_HOME="${CODEX_HOME:-/data/.codex}"

mkdir -p "$AUTH_DIR" /data "$CODEX_HOME"
export CHATMOCK_DASHBOARD_AUTH_DIR="${CHATMOCK_DASHBOARD_AUTH_DIR:-$AUTH_DIR}"
export CHATMOCK_DASHBOARD_SETTINGS_PATH="$SETTINGS_PATH"
export CODEX_HOME
export CHATGPT_LOCAL_HOME="${CHATGPT_LOCAL_HOME:-$CODEX_HOME}"

build_auth_files_from_env() {
  local -a files=()

  if [[ -n "${CHATGPT_LOCAL_AUTH_FILES:-}" ]]; then
    return 0
  fi

  if [[ -n "${CHATMOCK_AUTH_JSONS_BASE64:-}" ]]; then
    IFS=',' read -r -a b64_items <<< "${CHATMOCK_AUTH_JSONS_BASE64}"
    local idx=1
    for item in "${b64_items[@]}"; do
      [[ -z "$item" ]] && continue
      local acc
      acc="$(printf "acc%02d" "$idx")"
      mkdir -p "$AUTH_DIR/$acc"
      printf "%s" "$item" | base64 -d > "$AUTH_DIR/$acc/auth.json"
      files+=("$AUTH_DIR/$acc/auth.json")
      idx=$((idx + 1))
    done
  fi

  for i in $(seq 1 20); do
    local json_var="CHATMOCK_AUTH_JSON_${i}"
    local b64_var="CHATMOCK_AUTH_B64_${i}"
    local json_val="${!json_var:-}"
    local b64_val="${!b64_var:-}"
    local acc
    acc="$(printf "acc%02d" "$i")"

    if [[ -n "$json_val" ]]; then
      mkdir -p "$AUTH_DIR/$acc"
      printf "%s" "$json_val" > "$AUTH_DIR/$acc/auth.json"
      files+=("$AUTH_DIR/$acc/auth.json")
      continue
    fi

    if [[ -n "$b64_val" ]]; then
      mkdir -p "$AUTH_DIR/$acc"
      printf "%s" "$b64_val" | base64 -d > "$AUTH_DIR/$acc/auth.json"
      files+=("$AUTH_DIR/$acc/auth.json")
    fi
  done

  if [[ ${#files[@]} -gt 0 ]]; then
    CHATGPT_LOCAL_AUTH_FILES="$(IFS=,; echo "${files[*]}")"
    export CHATGPT_LOCAL_AUTH_FILES
  fi
}

shutdown() {
  if [[ -n "${CHAT_PID:-}" ]] && kill -0 "$CHAT_PID" 2>/dev/null; then
    kill "$CHAT_PID" 2>/dev/null || true
    wait "$CHAT_PID" 2>/dev/null || true
  fi
}

trap shutdown EXIT INT TERM

build_auth_files_from_env

if [[ -z "${CHATGPT_LOCAL_AUTH_FILES:-}" ]]; then
  echo "[single-service] No preloaded auth.json found."
  echo "[single-service] Embedded chat will still start; upload auth.json later from the admin panel if needed."
fi

export CHATGPT_LOCAL_ROUTING_STRATEGY="${CHATGPT_LOCAL_ROUTING_STRATEGY:-round-robin}"
export CHATGPT_LOCAL_REQUEST_RETRY="${CHATGPT_LOCAL_REQUEST_RETRY:-0}"
export CHATGPT_LOCAL_MAX_RETRY_INTERVAL="${CHATGPT_LOCAL_MAX_RETRY_INTERVAL:-5}"
export CHATGPT_LOCAL_UPSTREAM="${CHATGPT_LOCAL_UPSTREAM:-codex-app-server}"
export CHATGPT_LOCAL_CODEX_APP_SERVER_URL="${CHATGPT_LOCAL_CODEX_APP_SERVER_URL:-ws://127.0.0.1:8787}"
export CHATMOCK_MANAGE_CODEX_APP_SERVER="${CHATMOCK_MANAGE_CODEX_APP_SERVER:-1}"
export CHATMOCK_AUTO_START_CODEX_APP_SERVER="${CHATMOCK_AUTO_START_CODEX_APP_SERVER:-1}"

(
  cd "$CHAT_ROOT"
  exec python chatmock.py serve --host "$CHAT_HOST" --port "$CHAT_PORT"
) &
CHAT_PID=$!

for _ in $(seq 1 60); do
  if wget -qO- "http://${CHAT_HOST}:${CHAT_PORT}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! wget -qO- "http://${CHAT_HOST}:${CHAT_PORT}/health" >/dev/null 2>&1; then
  echo "[single-service] embedded chat failed to start on ${CHAT_HOST}:${CHAT_PORT}" >&2
  exit 1
fi

exec /new-api
