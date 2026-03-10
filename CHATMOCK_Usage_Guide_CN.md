# ChatMock 使用教学（核心参数版）

说明：以下命令全部只保留核心参数。  
默认你已在 `ChatMock` 项目目录中执行。

## 1. 账号相关

```bash
chatmock.py login
chatmock.py info
```

## 2. 启动服务

```bash
chatmock.py serve
```

推荐（暴露推理强度变体模型）：

```bash
chatmock.py serve --expose-reasoning-models
```

## 3. OpenAI 兼容调用

Base URL:

```text
http://127.0.0.1:8000/v1
```

示例：

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer key" \
  -H "Content-Type: application/json" \
  -d '{
    "model":"gpt-5.2",
    "messages":[{"role":"user","content":"hello"}]
  }'
```

## 4. Anthropic 兼容调用

Endpoint:

```text
http://127.0.0.1:8000/v1/messages
```

示例：

```bash
curl http://127.0.0.1:8000/v1/messages \
  -H "x-api-key: key" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{
    "model":"gpt-5.2",
    "max_tokens":256,
    "messages":[{"role":"user","content":"hello"}]
  }'
```

## 5. Anthropic 工具调用（简版）

```bash
curl http://127.0.0.1:8000/v1/messages \
  -H "x-api-key: key" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{
    "model":"gpt-5.2",
    "messages":[{"role":"user","content":"use get_weather for Beijing"}],
    "tools":[
      {
        "name":"get_weather",
        "description":"Get weather by city",
        "input_schema":{
          "type":"object",
          "properties":{"city":{"type":"string"}},
          "required":["city"]
        }
      }
    ],
    "tool_choice":{"type":"tool","name":"get_weather"}
  }'
```

## 6. 常见问题

看不到 `gpt-5.2-high/xhigh` 等变体：

```bash
chatmock.py serve --expose-reasoning-models
```

查看当前可用模型：

```bash
curl http://127.0.0.1:8000/v1/models
```

停止服务：在运行窗口按 `Ctrl + C`。
