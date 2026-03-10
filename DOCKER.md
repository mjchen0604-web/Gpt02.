# Docker Deployment

## Quick Start
1) Setup env:
   cp .env.example .env

2) Build the image:
   docker compose build

3) Login:
   docker compose run --rm --service-ports chatmock-login login
   - The command prints an auth URL, copy paste it into your browser.
   - If your browser cannot reach the container's localhost callback, copy the full redirect URL from the browser address bar and paste it back into the terminal when prompted.
   - Server should stop automatically once it receives the tokens and they are saved.

4) Start the server:
   docker compose up -d chatmock

5) Free to use it in whichever chat app you like!

## Configuration
Set options in `.env` or pass environment variables:
- `PORT`: Container listening port (default 8000)
- `VERBOSE`: `true|false` to enable request/stream logs
- `CHATGPT_LOCAL_UPSTREAM`: `chatgpt-backend|codex-app-server`
- `CHATGPT_LOCAL_CODEX_APP_SERVER_URL`: WebSocket URL for the local Codex app-server
- `CHATGPT_LOCAL_SERVICE_TIER`: `fast|flex|priority` depending on upstream
- `CHATGPT_LOCAL_REASONING_EFFORT`: minimal|low|medium|high|xhigh
- `CHATGPT_LOCAL_REASONING_SUMMARY`: auto|concise|detailed|none
- `CHATGPT_LOCAL_REASONING_COMPAT`: legacy|o3|think-tags|current
- `CHATGPT_LOCAL_DEBUG_MODEL`: force model override (e.g., `gpt-5`)
- `CHATGPT_LOCAL_CLIENT_ID`: OAuth client id override (rarely needed)
- `CHATGPT_LOCAL_EXPOSE_REASONING_MODELS`: `true|false` to add reasoning model variants to `/v1/models`
- `CHATGPT_LOCAL_ENABLE_WEB_SEARCH`: `true|false` to enable default web search tool

## Render / true-fast deployment

If you want real GPT-5.4 fast mode in production, deploy the container with the official Codex app-server running beside ChatMock:

1. Use the Docker image from this repo.
2. Start the service with `scripts/render-start.sh`.
3. Mount a persistent disk and point `CODEX_HOME` to it.
4. Optionally pre-seed `auth.json` through one of:
   - `CODEX_AUTH_B64`
   - `CODEX_AUTH_JSON`
   - `CODEX_AUTH_JSON_FILE`

Optional Codex config sources:
   - `CODEX_CONFIG_B64`
   - `CODEX_CONFIG_TOML`
   - `CODEX_CONFIG_TOML_FILE`

The included [`render.yaml`](./render.yaml) and [`scripts/render-start.sh`](./scripts/render-start.sh) implement this layout:

- `chatmock` on `0.0.0.0:$PORT` with `--upstream codex-app-server`
- a managed `codex app-server` on `ws://127.0.0.1:8787`

If you do not pre-seed credentials, the service can still boot. Then:

1. Open `/dashboard`
2. Upload one or more `auth.json` files
3. Uploaded credentials start managed Codex fast instances under the dashboard account pool
4. The same uploaded credentials are preserved in the multi-account pool for backend rotation

## Logs
Set `VERBOSE=true` to include extra logging for debugging issues in upstream or chat app requests. Please include and use these logs when submitting bug reports.

## Test

```
curl -s http://localhost:8000/v1/chat/completions \
   -H 'Content-Type: application/json' \
   -d '{"model":"gpt-5-codex","messages":[{"role":"user","content":"Hello world!"}]}' | jq .
```
