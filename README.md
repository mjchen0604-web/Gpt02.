<div align="center">
  <h1>ChatMock
  <div align="center">
<a href="https://github.com/RayBytes/ChatMock/stargazers"><img src="https://img.shields.io/github/stars/RayBytes/ChatMock" alt="Stars Badge"/></a>
<a href="https://github.com/RayBytes/ChatMock/network/members"><img src="https://img.shields.io/github/forks/RayBytes/ChatMock" alt="Forks Badge"/></a>
<a href="https://github.com/RayBytes/ChatMock/pulls"><img src="https://img.shields.io/github/issues-pr/RayBytes/ChatMock" alt="Pull Requests Badge"/></a>
<a href="https://github.com/RayBytes/ChatMock/issues"><img src="https://img.shields.io/github/issues/RayBytes/ChatMock" alt="Issues Badge"/></a>
<a href="https://github.com/RayBytes/ChatMock/graphs/contributors"><img alt="GitHub contributors" src="https://img.shields.io/github/contributors/RayBytes/ChatMock?color=2b9348"></a>
<a href="https://github.com/RayBytes/ChatMock/blob/master/LICENSE"><img src="https://img.shields.io/github/license/RayBytes/ChatMock?color=2b9348" alt="License Badge"/></a>
</div>
  </h1>
  
  <p><b>OpenAI & Ollama compatible API powered by your ChatGPT plan.</b></p>
  <p>Use your ChatGPT Plus/Pro account to call OpenAI models from code or alternate chat UIs.</p>
  <br>
</div>

## What It Does

ChatMock runs a local server that creates an OpenAI/Ollama compatible API, and requests are then fulfilled using your authenticated ChatGPT login with the oauth client of Codex, OpenAI's coding CLI tool. This allows you to use GPT-5, GPT-5-Codex, and other models right through your OpenAI account, without requiring an api key. You are then able to use it in other chat apps or other coding tools. <br>
This does require a paid ChatGPT account.

## Quickstart

### Mac Users

#### GUI Application

If you're on **macOS**, you can download the GUI app from the [GitHub releases](https://github.com/RayBytes/ChatMock/releases).  
> **Note:** Since ChatMock isn't signed with an Apple Developer ID, you may need to run the following command in your terminal to open the app:
>
> ```bash
> xattr -dr com.apple.quarantine /Applications/ChatMock.app
> ```
>
> *[More info here.](https://github.com/deskflow/deskflow/wiki/Running-on-macOS)*

#### Command Line (Homebrew)

You can also install ChatMock as a command-line tool using [Homebrew](https://brew.sh/):
```
brew tap RayBytes/chatmock
brew install chatmock
```

### Python
If you wish to just simply run this as a python flask server, you are also freely welcome too.

Clone or download this repository, then cd into the project directory. Then follow the instrunctions listed below.

1. Sign in with your ChatGPT account and follow the prompts
```bash
python chatmock.py login
```
You can make sure this worked by running `python chatmock.py info`

2. After the login completes successfully, you can just simply start the local server

```bash
python chatmock.py serve
```
Then, you can simply use the address and port as the baseURL as you require (http://127.0.0.1:8000 by default)

**Reminder:** When setting a baseURL in other applications, make you sure you include /v1/ at the end of the URL if you're using this as a OpenAI compatible endpoint (e.g http://127.0.0.1:8000/v1)

### Docker

Read [the docker instrunctions here](https://github.com/RayBytes/ChatMock/blob/main/DOCKER.md)

### Render

This repo now includes a Render-ready Docker deployment for the official Codex app-server path:

- [`render.yaml`](./render.yaml) defines a Docker web service with a persistent disk.
- [`scripts/render-start.sh`](./scripts/render-start.sh) boots ChatMock first, with an in-process Codex app-server manager.

You can still pre-seed credentials through secrets:

- `CODEX_AUTH_B64` or `CODEX_AUTH_JSON` or `CODEX_AUTH_JSON_FILE`

Optional runtime secret:

- `CODEX_CONFIG_B64` or `CODEX_CONFIG_TOML` or `CODEX_CONFIG_TOML_FILE`

Recommended Render env:

- `CHATMOCK_DATA_DIR=/app/storage`
- `CODEX_HOME=/app/storage/.codex`
- `CHATGPT_LOCAL_UPSTREAM=codex-app-server`
- `CHATGPT_LOCAL_CODEX_APP_SERVER_URL=ws://127.0.0.1:8787`
- `CHATGPT_LOCAL_EXPOSE_REASONING_MODELS=true`
- `CHATMOCK_MANAGE_CODEX_APP_SERVER=true`
- `CHATMOCK_AUTO_START_CODEX_APP_SERVER=true`
- `CHATMOCK_DASHBOARD_ALLOW_UPLOAD=true`

Current recommended workflow on Render:

1. Deploy the service even if no `auth.json` is present yet.
2. Open `/dashboard`.
3. Upload one or more `auth.json` files.
4. Uploaded credentials are written into the dashboard account pool and each one starts a managed Codex app-server fast instance.
5. The same uploaded credentials are also preserved in the multi-account pool (`CHATGPT_LOCAL_AUTH_FILES`) for backend rotation.

If your Git repo root is not this folder, either move `render.yaml` to the repo root or select this file path explicitly in Render when syncing the Blueprint.

# Examples

### Python 

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="key"  # ignored
)

resp = client.chat.completions.create(
    model="gpt-5",
    messages=[{"role": "user", "content": "hello world"}]
)

print(resp.choices[0].message.content)
```

### curl

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-5",
    "messages": [{"role":"user","content":"hello world"}]
  }'
```

# What's supported

- Tool/Function calling 
- Vision/Image understanding
- Thinking summaries (through thinking tags)
- Thinking effort

## Notes & Limits

- Requires an active, paid ChatGPT account.
- Some context length might be taken up by internal instructions (but they dont seem to degrade the model) 
- Use responsibly and at your own risk. This project is not affiliated with OpenAI, and is a educational exercise.

# Supported models
- `gpt-5`
- `gpt-5.1`
- `gpt-5.2`
- `gpt-5.4`
- `gpt-5.4-fast` (ChatMock alias, requires `codex-app-server` upstream for real fast mode)
- `gpt-5-codex`
- `gpt-5.2-codex`
- `gpt-5.3-codex`
- `gpt-5.1-codex`
- `gpt-5.1-codex-max`
- `gpt-5.1-codex-mini`
- `codex-mini`

# Customisation / Configuration

### Upstream mode

- `--upstream` (choice of `chatgpt-backend`, `codex-app-server`)<br>
ChatMock supports two upstream modes:

  - `chatgpt-backend` (default): direct bridge to the private ChatGPT/Codex backend.
  - `codex-app-server`: bridge to an official local `codex app-server` instance over WebSocket.

Use `codex-app-server` if you want the official Codex client protocol, including real `serviceTier:"fast"` support for GPT-5.4 fast aliases.

- `--codex-app-server-url`<br>
WebSocket URL for the Codex app-server upstream. The default is `ws://127.0.0.1:8787`.

### Thinking effort

- `--reasoning-effort` (choice of minimal,low,medium,high,xhigh)<br>
GPT-5 has a configurable amount of "effort" it can put into thinking, which may cause it to take more time for a response to return, but may overall give a smarter answer. Applying this parameter after `serve` forces the server to use this reasoning effort by default, unless overrided by the API request with a different effort set. The default reasoning effort without setting this parameter is `medium`.<br>
    The `gpt-5.1` family (including codex) supports `low`, `medium`, and `high` while `gpt-5.1-codex-max` adds `xhigh`. The `gpt-5.2` and `gpt-5.3` families (including codex) support `low`, `medium`, `high`, and `xhigh`. 

### Thinking summaries

- `--reasoning-summary` (choice of auto,concise,detailed,none)<br>
Models like GPT-5 do not return raw thinking content, but instead return thinking summaries. These can also be customised by you.

### OpenAI Tools

- `--enable-web-search`<br>
You can also access OpenAI tools through this project. Currently, only web search is available.
You can enable it by starting the server with this parameter, which will allow OpenAI to determine when a request requires a web search, or you can use the following parameters during a request to the API to enable web search:
<br><br>
`responses_tools`: supports `[{"type":"web_search"}]` / `{ "type": "web_search_preview" }`<br>
`responses_tool_choice`: `"auto"` or `"none"`

### Service tier / fast mode

- `--service-tier`<br>
This forwards service tier requests to the selected upstream. The exact values depend on the upstream mode:

  - `chatgpt-backend`: use `priority` to probe the backend fast/priority path. This is best-effort only, and the backend may still downgrade the request to `default`.
  - `codex-app-server`: use `fast` or `flex`, which map to the official Codex app-server `serviceTier` field.

This is separate from reasoning effort: lowering reasoning makes the model think less, while service tier asks the upstream to use a different processing mode.

ChatMock also supports a real fast alias for model pickers:

- `gpt-5.4-fast` -> upstream `gpt-5.4` + fast service tier
- `gpt-5.4-fast-low|medium|high|xhigh` -> upstream `gpt-5.4` + fast service tier + matching `reasoning.effort`

If a request explicitly sets `service_tier`, that explicit value overrides the alias.

You can also send it per request:

```json
{
  "model": "gpt-5.4",
  "service_tier": "fast",
  "messages": [{"role":"user","content":"Say ok"}]
}
```

If the upstream accepts it, non-stream responses include a top-level `service_tier` field and ChatMock also returns:

- `X-ChatMock-Service-Tier-Requested`
- `X-ChatMock-Service-Tier-Observed` (when the upstream reports one)

### Local true-fast validation

To validate real GPT-5.4 fast mode locally, run the official Codex app-server and then point ChatMock at it:

1. Start the Codex app-server:
```bash
codex app-server --listen ws://127.0.0.1:8787 --enable fast_mode
```

2. Start ChatMock against that upstream:
```bash
python chatmock.py serve --upstream codex-app-server --codex-app-server-url ws://127.0.0.1:8787 --expose-reasoning-models
```

3. Send a test request with `gpt-5.4-fast-low` (or another `gpt-5.4-fast-*` alias). A successful non-stream response should include:

```json
{
  "model": "gpt-5.4-fast-low",
  "service_tier": "fast"
}
```

Current behavior: the `codex-app-server` adapter is validated for standard text chat, streaming, OpenAI-style function calling (`tool_calls` + follow-up `tool` messages), native image inputs on the latest user turn, and native Codex `web_search` passthrough. Anthropic-compatible `/v1/messages` is also available on top of the same upstream. Legacy multi-account rotation is still available for the `chatgpt-backend` upstream path and through the dashboard account pool.

#### Example usage
```json
{
  "model": "gpt-5",
  "messages": [{"role":"user","content":"Find current METAR rules"}],
  "stream": true,
  "responses_tools": [{"type": "web_search"}],
  "responses_tool_choice": "auto"
}
```

### Expose reasoning models

- `--expose-reasoning-models`<br>
If your preferred app doesn’t support selecting reasoning effort, or you just want a simpler approach, this parameter exposes each reasoning level as a separate, queryable model. Each reasoning level also appears individually under ⁠/v1/models, so model pickers in your favorite chat apps will list all reasoning options as distinct models you can switch between.

## Notes
If you want the official fast path locally, use `--upstream codex-app-server` and a `gpt-5.4-fast-*` alias (or `service_tier=fast`). If you only want less thinking overhead, lower `--reasoning-effort` separately. <br>
All parameters and choices can be seen by sending `python chatmock.py serve --h`<br>
The context size of this route is also larger than what you get access to in the regular ChatGPT app.<br>

When the model returns a thinking summary, the model will send back thinking tags to make it compatible with chat apps. **If you don't like this behavior, you can instead set `--reasoning-compat` to legacy, and reasoning will be set in the reasoning tag instead of being returned in the actual response text.**


## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=RayBytes/ChatMock&type=Timeline)](https://www.star-history.com/#RayBytes/ChatMock&Timeline)

