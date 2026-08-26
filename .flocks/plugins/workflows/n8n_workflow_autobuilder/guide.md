# n8n_workflow_autobuilder 配置指南

## 必要配置

- n8n 地址：默认 `http://localhost:5678`
- n8n API key：建议放在环境变量 `N8N_API_KEY`

不要把 API key 写入 `workflow.md`、`workflow.json`、`guide.md`、报告或聊天内容。

## 运行边界

- n8n 是最终运行时，创建好的 workflow 必须可以脱离 Flocks 独立运行。
- Flocks 只负责编排、渲染、发布、激活和管理，不作为业务节点参与输入、输出、查询或研判。
- 目标兼容版本为 n8n `2.35.4` Public API v1。发布 API key 至少需要 `workflow:create`、`workflow:list`、`workflow:read`、`workflow:activate`；使用 `credentialRequirements` 时还需要 `credential:list`、`credential:read`、`credential:create`。
- 不允许把 Flocks MCP、Flocks skill、Flocks tool、Flocks agent、Flocks 工作流 API、Flocks webhook、Flocks 对外 HTTP 包装接口、`/api/mcp`、`/api/tools`、`/api/workflows` 等回调写进 n8n 节点。
- 需要 ThreatBook、Kafka 等能力时，优先生成 n8n 原生节点或 HTTP Request 节点直连非 Flocks 外部服务，并引用 n8n credential。
- 如果某个 Flocks-only 的 skill/tool/agent 能力无法完全迁移为 n8n 原生节点或非 Flocks 外部 API，必须在生成 IR 或发布前停止，并询问用户确认是否终止本次 workflow 创建；不要提供“由 Flocks 暴露 HTTP 接口给 n8n 调用”的方案。
- Flocks 已有密钥或 credential 引用时不追问；在 `credentialRequirements` 声明从哪个 Flocks secret 创建/复用哪个 n8n credential。
- 如果缺少 Flocks 无法自动提供的业务参数、密钥、credential、网络连通性或 n8n 权限，必须作为阻断项询问/报错并停止。前置条件仍不可用时，不继续发布或激活坏 workflow。
- 不允许在 n8n workflow JSON 中写入 `{secret:...}`、`{{secrets.NAME}}` 或明文 token。
- `credentialRequirements[].data` 可使用 `{secret}` 占位符；它只会在服务端创建 n8n credential 时替换，不会写入 workflow JSON。

## 推荐输入

Webhook 示例：

```json
{
  "n8n_base_url": "http://localhost:5678",
  "n8n_api_key_secret_ref": "N8N_API_KEY",
  "publish": true,
  "cleanup_on_success": false,
  "ir": {
    "name": "flocks-test-hello",
    "trigger": {
      "type": "webhook",
      "method": "POST",
      "path": "flocks-test-hello"
    },
    "steps": [
      {
        "id": "build_response",
        "kind": "code",
        "js_code": "const body = $input.first().json.body || {}; return [{ json: { message: `Hello ${body.name || 'World'}`, source: 'n8n' } }];"
      },
      {
        "id": "respond",
        "kind": "respond_to_webhook",
        "response_body": "={{ $json }}"
      }
    ],
    "tests": [
      {
        "name": "hello",
        "input": { "name": "Alice" },
        "expect": {
          "status": 200,
          "jsonContains": { "source": "n8n" }
        }
      }
    ]
  }
}
```

Kafka Trigger 示例：

```json
{
  "n8n_base_url": "http://localhost:5678",
  "n8n_api_key_secret_ref": "N8N_API_KEY",
  "publish": true,
  "cleanup_on_success": false,
  "ir": {
    "name": "flocks-kafka-alerts",
    "trigger": {
      "type": "kafka",
      "topic": "security-alerts",
      "groupId": "flocks-security-alerts",
      "credentialRef": { "name": "Kafka Production" },
      "fromBeginning": false,
      "batchSize": 1,
      "resolveOffset": "onCompletion"
    },
    "steps": [
      {
        "id": "normalize",
        "kind": "code",
        "name": "Normalize",
        "js_code": "return $input.all().map((item) => ({ json: { ...item.json, handledBy: 'n8n' } }));"
      }
    ],
    "tests": []
  }
}
```

ThreatBook HTTP API 示例：

```json
{
  "n8n_base_url": "http://localhost:5678",
  "n8n_api_key_secret_ref": "N8N_API_KEY",
  "publish": true,
  "cleanup_on_success": false,
  "ir": {
    "name": "flocks-test-ioc-lookup",
    "description": "Webhook receives an IOC and queries ThreatBook through n8n HTTP Request.",
    "credentialRequirements": [
      {
        "name": "ThreatBook API",
        "type": "httpQueryAuth",
        "secretRef": "THREATBOOK_API_KEY",
        "data": {
          "name": "apikey",
          "value": "{secret}"
        }
      }
    ],
    "trigger": {
      "type": "webhook",
      "method": "POST",
      "path": "flocks-test-ioc-lookup",
      "responseMode": "responseNode"
    },
    "steps": [
      {
        "id": "lookup",
        "kind": "http_request",
        "method": "GET",
        "url": "https://api.threatbook.cn/v3/scene/ip_reputation?resource={{$json.body.ioc}}",
        "authentication": "genericCredentialType",
        "genericAuthType": "httpQueryAuth",
        "credentials": {
          "httpQueryAuth": {
            "name": "ThreatBook API",
            "type": "httpQueryAuth"
          }
        },
        "next": "respond"
      },
      {
        "id": "respond",
        "kind": "respond_to_webhook",
        "response_body": "={{ $json }}"
      }
    ],
    "tests": [
      {
        "name": "lookup",
        "input": { "ioc": "1.1.1.1" },
        "expect": { "status": 200 }
      }
    ]
  }
}
```

## 运行结果

报告写入：

```text
~/.flocks/workspace/outputs/<YYYY-MM-DD>/n8n_workflow_autobuilder_report.md
```

生成 JSON 写入：

```text
~/.flocks/workspace/outputs/<YYYY-MM-DD>/n8n_workflow_autobuilder.generated.json
```
