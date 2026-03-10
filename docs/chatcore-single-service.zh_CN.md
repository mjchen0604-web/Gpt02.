# ChatCore 单服务模式

这版会在同一个容器里同时启动：

- `new-api`
- 内嵌 `chat`

对外只暴露 `new-api` 一个端口，所以 Render 只需要一个服务。

## Render 环境变量

至少需要这些：

```env
CHATCORE_INTERNAL_CHAT_HOST=127.0.0.1
CHATCORE_INTERNAL_CHAT_PORT=1455
CHATGPT_LOCAL_ROUTING_STRATEGY=round-robin
CHATGPT_LOCAL_REQUEST_RETRY=0
CHATGPT_LOCAL_MAX_RETRY_INTERVAL=5
```

再提供一组可用的 `auth.json` 凭据，二选一：

```env
CHATMOCK_AUTH_JSONS_BASE64=base64_auth_json_1,base64_auth_json_2
```

或：

```env
CHATMOCK_AUTH_JSON_1={"accessToken":"..."}
CHATMOCK_AUTH_B64_2=base64_auth_json
```

## 后台怎么配

部署完成后，在 `new-api` 后台新建一个 `ChatCore (Chat)` 渠道。

建议直接填：

- `base_url`: `http://127.0.0.1:1455`
- `key`: `internal-chatcore`
- `group`: `default`

然后拉取模型，保存渠道，再去创建给客户端用的令牌。

## 客户端怎么连

客户端只连 `new-api`：

- Base URL: `https://你的域名/v1`
- API Key: `new-api` 后台里创建的令牌

客户端不需要直接连接内嵌 `chat`。
