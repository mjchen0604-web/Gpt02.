# ChatCore 渠道

`ChatCore` 用于把 `new-api` 作为管理层，把 `chat` / `ChatMock` 作为真实的模型执行层。

分工如下：
- `new-api` 负责用户、令牌、分组、额度、计费和管理后台
- `chat` 负责账号轮询、`codex-app-server`、`service_tier`、模型映射和真实上游调用

## 适用场景

适合以下结构：
- 已经有一套可用的 `chat` 服务
- 想保留 `chat` 的账号池和 Codex 能力
- 想把用户、密钥、分组、配额、计费和后台交给 `new-api`

## 创建渠道

在 `new-api` 后台新增一个渠道：

- 渠道类型：`ChatCore (Chat)`
- `base_url`：填写 `chat` 服务根地址
  - 例如：`https://chat.example.com`
  - 不要追加 `/v1`
- `key`：填写 `chat` 的上游访问密钥
  - 如果 `chat` 没开启网关鉴权，可以留空
- `models`：填写要通过 `new-api` 暴露的模型列表
- `group`：按你的 `new-api` 分组策略填写

## Chat 侧要求

`chat` 需要先能独立工作，至少满足：
- OpenAI 兼容入口可用
- `codex-app-server` 已配置
- 如果启用了网关鉴权，`new-api` 使用的 `key` 必须有效

建议先单独验证 `chat`：

```bash
curl https://chat.example.com/v1/models \
  -H "Authorization: Bearer YOUR_CHAT_KEY"
```

返回 `200` 后再接入 `new-api`。

## 推荐设置

建议在渠道额外设置里启用：
- `allow_service_tier`

这样客户端显式传入 `service_tier` 时，不会被 `new-api` 过滤掉。

## 定价与模型列表

`ChatCore` 模型默认遵循 `chat` 的执行能力。

这一版接入额外处理了两件事：
- `ChatCore` 提供的模型，即使没有在 `new-api` 里单独配置 ratio/price，也会正常出现在 `/v1/models`
- 调用这些模型时，不会再因 `model_price_error` 被 `new-api` 提前拦截

也就是说：
- 模型能力以 `chat` 为准
- `new-api` 主要负责管理、鉴权、额度和后台

## 请求链路

请求流转如下：
1. 客户端请求 `new-api`
2. `new-api` 完成用户鉴权、分组、额度与计费判断
3. 请求被路由到 `ChatCore` 渠道
4. `new-api` 用 OpenAI 兼容格式把请求转发给 `chat`
5. `chat` 再用自己的账号轮询和执行内核访问上游

## 注意事项

- `ChatCore` 是管理层集成，不会替代 `chat` 的账号池
- 账号轮询质量仍然取决于 `chat`
- `new-api` 渠道测试通过，只代表它能访问 `chat`
- `gpt-5.4-fast`、Codex、reasoning 是否可用，最终仍取决于 `chat` 那边的上游能力

## 最小建议

如果要先做最清晰的版本，建议：
- `new-api` 只保留管理功能
- 所有执行类渠道统一走 `ChatCore`
- 不再让 `new-api` 直接接 OpenAI / Claude / Gemini 等 provider

这样结构最清晰，也最符合“`new-api` 做管理，`chat` 做能力”的目标。
