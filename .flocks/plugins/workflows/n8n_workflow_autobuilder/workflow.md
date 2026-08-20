# n8n_workflow_autobuilder

## 1. 功能概述

`n8n_workflow_autobuilder` 是一个用于验证和发布 n8n workflow 的 Flocks 工作流。它接收稳定的 n8n IR，生成 n8n 原生 workflow JSON，进行静态校验，按需发布到 n8n 测试实例并执行 Webhook 测试。

它主要解决三件事：

- 将自然语言 Agent 已确认的 n8n IR 转为 n8n 原生 JSON。
- 在发布前执行结构化 lint，拦截节点、连线、表达式、只读字段和密钥泄露风险。
- 发布测试 workflow、激活、调用 webhook 测试，并输出报告。

不适合做的事：

- 不直接负责理解用户自然语言；自然语言理解由 `n8n-workflow-builder` Agent 和 Skill 完成。
- 不保存明文 API key。
- 不默认修改生产 workflow。

## 2. 总体流程

```text
run_autobuilder
  -> render IR
  -> lint workflow
  -> write generated JSON
  -> optionally publish and activate test workflow
  -> run webhook tests
  -> write report
```

## 3. 输入说明

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `ir` | 无 | 必填，n8n IR 对象 |
| `n8n_base_url` | `http://localhost:5678` | n8n 实例地址 |
| `n8n_api_key_secret_ref` | `N8N_API_KEY` | n8n API key 的环境变量/secret 引用 |
| `publish` | `true` | 是否创建并激活测试 workflow |
| `cleanup_on_success` | `false` | 测试成功后是否删除测试 workflow |
| `wait_for_execution` | `false` | 是否尝试等待 execution 摘要 |

## 4. 模块逻辑

### run_autobuilder

类型：Python rule / Tool-driven。

处理逻辑：

1. 校验输入 IR。
2. 使用 `render_ir_to_workflow` 生成 n8n workflow JSON。
3. 使用 `lint_workflow` 做静态校验。
4. 将 JSON 和报告写入 `~/.flocks/workspace/outputs/<YYYY-MM-DD>/`。
5. 如果 `publish=true` 且 lint 无错误，则通过 n8n Public API 创建 workflow。
6. 激活后运行 IR 中的 webhook 测试。
7. 输出 workflow id、编辑 URL、webhook URL、测试结果和报告路径。

## 5. 输出说明

主要输出：

| 字段 | 含义 |
| --- | --- |
| `success` | lint 和测试是否通过 |
| `workflow_id` | n8n workflow id |
| `workflow_url` | n8n 编辑器链接 |
| `webhook_url` | production webhook URL |
| `generated_json_path` | 生成的 n8n JSON |
| `report_path` | Markdown 报告 |
| `lint_issues` | lint 结果 |
| `test_results` | webhook 测试结果 |

## 6. 验证方式

最小验证：

1. 传入包含 Webhook、Code、Respond 的 IR。
2. 确认 lint 无 error。
3. 创建并激活测试 workflow。
4. 调用 webhook，断言 HTTP 200 和 JSON 字段。

