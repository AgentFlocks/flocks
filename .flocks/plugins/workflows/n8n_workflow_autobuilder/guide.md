# n8n_workflow_autobuilder 配置指南

## 必要配置

- n8n 地址：默认 `http://localhost:5678`
- n8n API key：建议放在环境变量 `N8N_API_KEY`

不要把 API key 写入 `workflow.md`、`workflow.json`、`guide.md`、报告或聊天内容。

## 推荐输入

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

## 运行结果

报告写入：

```text
~/.flocks/workspace/outputs/<YYYY-MM-DD>/n8n_workflow_autobuilder_report.md
```

生成 JSON 写入：

```text
~/.flocks/workspace/outputs/<YYYY-MM-DD>/n8n_workflow_autobuilder.generated.json
```

