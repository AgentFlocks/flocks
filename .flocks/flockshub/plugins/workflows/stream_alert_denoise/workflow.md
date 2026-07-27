# stream_alert_denoise

## 1. 功能概述

`stream_alert_denoise` 是一个 HTTP 的告警降噪工作流。

它主要解决三件事：

- 把 TDP / SkyEye 来源的告警整理成统一字段。
- 过滤掉扫描、非 HTTP 或低价值噪声。
- 判断告警是否重复，只把更值得关注的告警输出并写入结果文件。

适用场景：

- 安全设备通过 Syslog 实时推送单条告警。
- 通过 API 批量提交告警列表。
- 从 JSON 文件读取历史告警做批量处理。

不适合做的事：

- 不负责调查告警背后的资产、用户或攻击链。
- 不直接处置攻击，只做告警清洗、过滤、去重和结果落盘。
- 不保存明文密钥；发布鉴权和运行时状态应由配置和数据库管理。

## 2. 总体流程

工作流按下面顺序处理告警：

```text
receive_alert -> normalize -> filter_logs -> dedup_and_write
```

| 顺序 | 节点 | 作用 |
| --- | --- | --- |
| 1 | `receive_alert` | 接收输入，判断输入模式和来源类型。 |
| 2 | `normalize` | 把 TDP / SkyEye 的不同字段统一成标准告警字段。 |
| 3 | `filter_logs` | 按规则过滤扫描、非 HTTP 或低价值日志。 |
| 4 | `dedup_and_write` | 计算去重结果，标记重复告警，写入 JSONL 结果文件。 |

可以把它理解成：

```text
原始告警
  -> 识别来源
  -> 字段统一
  -> 噪声过滤
  -> 相似告警去重
  -> 返回增强告警 + 写入结果文件
```

## 3. 输入说明

### 3.1 输入方式

三种输入方式按优先级解析：

| 优先级 | 字段 | 用途 |
| --- | --- | --- |
| 1 | `syslog_message` | Syslog 实时单条告警。 |
| 2 | `alerts` | API 批量传入的告警列表。 |
| 3 | `alert_file` | 指向 JSON 告警文件的路径。 |

如果同时传了多个输入字段，工作流优先处理 `syslog_message`。

### 3.2 常用输入参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `source_log_type` | 自动识别 | 可手动指定 `tdp` 或 `skyeye`。 |
| `filter_enabled` | `true` | 是否启用过滤阶段。 |
| `dedup_enabled` | `true` | 是否启用跨批次去重。 |
| `threshold` | `0.7` | 相似度阈值；越高越严格。 |
| `strict_fields` | `["sip", "dip"]` | 去重时必须精确匹配的字段。 |
| `lsh_fields` | `["req_http_url", "req_body", "rsp_body"]` | 去重时参与相似度判断的字段。 |
| `max_dedup_keys` | `100000` | 持久化去重 key 的最大数量。 |

### 3.3 Syslog 输入示例

```json
{
  "syslog_message": {
    "hostname": "tdp-sensor",
    "app_name": "tdp",
    "timestamp": "2026-05-12T10:00:00",
    "message": "{\"id\":\"AZtRkZkzj\",\"net\":{\"http\":{\"url\":\"/admin\"}},\"threat\":{\"name\":\"SQL注入\"}}"
  }
}
```

### 3.4 批量输入示例

```json
{
  "source_log_type": "tdp",
  "alerts": [
    {
      "net_real_src_ip": "1.2.3.4",
      "net_dest_ip": "10.0.0.1",
      "net_type": "http",
      "net_http_url": "/admin/login.php?id=1 OR 1=1",
      "threat_name": "SQL注入攻击"
    }
  ]
}
```

## 4. 模块逻辑

### 4.1 receive_alert：接收和识别

这个节点负责回答两个问题：

- 告警是从哪里来的？
- 它应该按 TDP 还是 SkyEye 格式解析？

处理逻辑：

1. 优先读取 `syslog_message`。
2. 如果没有 syslog，则读取 `alerts`。
3. 如果没有告警列表，则读取 `alert_file`。
4. 使用 `source_log_type`、syslog 元数据或字段特征判断来源类型。
5. 如果判断失败，默认按 `tdp` 处理。

你通常会在这里修改：

- 新增输入方式。
- 调整 Syslog 解析逻辑。
- 修改 TDP / SkyEye 自动识别规则。

### 4.2 normalize：统一字段

这个节点负责把不同来源的原始字段翻译成统一字段。

统一后的关键字段包括：

| 字段 | 含义 |
| --- | --- |
| `sip` | 源 IP。 |
| `dip` | 目的 IP。 |
| `req_http_url` | HTTP 请求 URL。 |
| `req_body` | 请求正文。 |
| `rsp_body` | 响应正文。 |
| `threat_name` | 威胁名称。 |
| `_source_type` | 来源类型，`tdp` 或 `skyeye`。 |
| `_syslog_meta` | Syslog 元数据，仅 Syslog 输入时存在。 |

你通常会在这里修改：

- 字段映射关系。
- TDP / SkyEye 新版本字段兼容。
- 默认值和缺失字段处理。

### 4.3 filter_logs：过滤噪声

这个节点负责判断哪些告警值得继续处理。

默认策略：

- 保留 HTTP 告警。
- 保留方向为 `in`、`out`、`lateral` 的非扫描告警。
- 过滤扫描、非 HTTP 或低价值日志。

如果传入：

```json
{
  "filter_enabled": false
}
```

则跳过过滤，所有归一化后的告警都会进入去重阶段。

你通常会在这里修改：

- 哪些告警应该保留。
- 哪些告警应该丢弃。
- `_process_type` 和 `_threat_type` 的分类规则。

### 4.4 dedup_and_write：去重和写结果

这个节点负责判断告警是否重复，并写入结果文件。

去重分两层：

- 精确层：默认要求 `sip` 和 `dip` 一致。
- 相似层：对 `req_http_url`、`req_body`、`rsp_body` 做相似度判断。

默认阈值是 `threshold=0.7`：

- 阈值越高，越不容易判定重复。
- 阈值越低，越容易把相似告警归为同一类。

结果文件写入：

```text
~/.flocks/workspace/workflows/stream_alert_denoise/<YYYY-MM-DD>/dedup_result_NNN.jsonl
```

注意：

- 首次出现的告警会写入 JSONL。
- 历史重复告警不会再次写入文件，但会在返回结果里标记为重复。
- LSH 去重状态会保存在工作流自己的状态文件中，不建议和其他工作流混用。

你通常会在这里修改：

- 相似度阈值。
- 哪些字段参与去重。
- 结果文件格式。
- 状态文件保存策略。

## 5. 输出说明

工作流主要输出这些字段：

| 字段 | 含义 |
| --- | --- |
| `enriched_alerts` | 过滤和去重处理后的告警列表。 |
| `unique_alerts` | 每个去重 key 的代表性告警。 |
| `dedup_key` | 第一条告警的去重 key。 |
| `is_duplicate` | 第一条告警是否为历史重复。 |
| `stats` | 本次处理统计。 |
| `output_path` | 本次写入的最后一个结果文件。 |
| `output_paths` | 本次涉及的所有结果文件。 |
| `dedup_summary` | 一句话摘要。 |
| `input_mode` | 实际使用的输入模式。 |

每条 `enriched_alert` 里最重要的增强字段：

| 字段 | 含义 |
| --- | --- |
| `dedup_key` | 去重 key。 |
| `is_duplicate` | 是否重复。 |
| `_lsh_cluster_id` | 相似度聚类 ID。 |
| `_source_type` | 来源类型。 |
| `_process_type` | 过滤分类结果。 |

## 6. 发布和配置

发布页面不直接从 `workflow.md` 决定展示什么能力，而是读取 `config.json` 模板和数据库中的运行时状态。

当前常见发布方式：

- API：通过 `/api/workflow/stream_alert_denoise/run` 调用。
- Syslog：监听端口接收实时告警。
- Kafka / Schedule / Webhook：可按模板配置扩展。

编辑发布方式时：

- 改发布模板：看 `config.json`。
- 改运行启停状态：看发布页和后端运行时状态。
- 不要把明文 API Key、密码、token 写进 `workflow.md` 或 `config.json`。

## 7. 怎么编辑这个工作流

按你想改的目标定位：

| 修改目标 | 优先修改 |
| --- | --- |
| 输入来源、Syslog 格式、文件输入 | `receive_alert` |
| TDP / SkyEye 字段映射 | `normalize` |
| 过滤规则、保留规则、分类规则 | `filter_logs` |
| 去重阈值、去重字段、落盘格式 | `dedup_and_write` |
| 发布方式、API / Syslog 展示 | `config.json` |
| 流程结构、节点增删 | `workflow.json` 和本文档同步修改 |

修改时的基本原则：

- 改输入字段，要同步样例输入。
- 改标准字段名，要同步所有下游节点。
- 改过滤规则，要同步 `stats` 和测试样例预期。
- 改去重逻辑，要说明历史去重结果是否会变化。
- 改输出格式，要确认下游系统还能读取。

## 8. 验证方式

最小验证建议：

1. 用一条正常 HTTP 告警跑通，确认有 `enriched_alerts`。
2. 再跑一条相似告警，确认 `dedup_key` 稳定。
3. 如果开启跨批次去重，确认第二次出现时 `is_duplicate=true`。
4. 用一条应被过滤的扫描或非 HTTP 告警，确认过滤统计正确。
5. 检查 `output_path` 指向的 JSONL 文件是否正常写入。

验收清单：

- [ ] 输入能被正确识别为 syslog、alerts 或 alert_file。
- [ ] TDP / SkyEye 字段能统一到标准字段。
- [ ] 过滤逻辑符合预期。
- [ ] 去重结果符合预期。
- [ ] 输出字段和结果文件格式清晰。
- [ ] 发布页只展示当前配置启用的能力。
