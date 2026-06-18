# Web2CLI CLI 生成要求

> 本文是生成 WebCLI 工具时必须遵循的参考要求。应根据抓包结果、认证状态和用户目标直接生成 CLI、验证材料和接口文档。

## 输入材料

- 抓包 JSON：`$CAPTURE_ROOT/captures/${CAPTURE_NAME}_api.json`
- 浏览器认证状态：`$CAPTURE_ROOT/auth-state.json`
- 页面入口 URL：来自用户目标或当前浏览器页面
- 用户要复现的操作：来自用户目标和最近页面操作
- 目标 base URL：从抓包 URL 中归纳，必要时结合用户指定值

## 生成目标

生成以下文件：

- CLI 主脚本：`$CAPTURE_ROOT/<normalized_capture_name>_cli.py`
- 验证材料：`$CAPTURE_ROOT/${CAPTURE_NAME}_verify.json`
- 接口文档：`$CAPTURE_ROOT/cli-reference.md`

命名要求：

- `<normalized_capture_name>` 必须是合法 Python 文件名片段
- 将 `-`、空格等不适合作为 Python 模块名的字符替换为 `_`
- 不要把一次性路径、cookie、token 或用户私密信息硬编码到脚本中

## CLI 行为要求

CLI 必须说明并实现：

- 命令名：`<command_name>`
- 目标能力：`<capability>`
- 默认认证策略：`auth-state` / `cookie` / `header` / `public`
- 默认认证输入：优先使用 `--auth-state "$CAPTURE_ROOT/auth-state.json"` 或对应环境变量
- 必填参数：`<required_args>`
- 可选参数与默认值：`<optional_args>`
- 输出格式：默认 `table` 或 `json`，必要时同时支持 `--json`
- 退出码：成功为 `0`，认证失败、参数错误、请求失败和验证失败使用非零退出码

## 请求链路要求

从抓包 JSON 中选出与目标操作直接相关的请求，并写清楚：

- 请求顺序和依赖关系
- method、endpoint、query、body/payload 模板
- 必要 headers，如 `content-type`、`csrf`、`x-requested-with`
- 认证信息从哪里读取，如何注入到请求中
- 分页、排序、过滤、时间范围等参数如何映射到 CLI 参数
- 响应字段路径，以及嵌套列表、空值、错误结构如何处理

不要把无关埋点、静态资源、日志上报、健康检查或页面渲染请求纳入主链路。

## 输出要求

固定输出列必须写清楚：

| column | source_path | required | description |
| --- | --- | --- | --- |
| `<name>` | `<json.path>` | `<true/false>` | `<description>` |

要求：

- 表格输出列顺序稳定
- JSON 输出保留原始字段或清洗后的结构
- 必填列为空时应在验证材料中标记为失败
- 时间、数量、状态等字段需要说明格式化规则

## 验证要求

`verify.json` 至少包含：

- CLI 调用样例
- 认证输入说明
- 最少返回行数
- 必填列列表
- 预期 HTTP 状态码或业务成功字段
- 常见失败场景与判定方式

示例结构：

```json
{
  "command": "uv run python <normalized_capture_name>_cli.py --auth-state auth-state.json",
  "min_rows": 1,
  "required_columns": ["<column>"],
  "success_status": [200],
  "failure_hints": ["authentication expired", "missing required argument"]
}
```

## 接口文档要求

`cli-reference.md` 至少包含：

- 能力说明和适用场景
- 认证方式与刷新登录态的方法
- CLI 参数表
- 请求链路摘要
- 输出字段说明
- 验证方式和常见问题

