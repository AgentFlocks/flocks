# stream_alert_denoise 配置引导

这个文件是 `stream_alert_denoise` 的工作流专属 `guide.md`。Rex 处理这个工作流的发布、接入、规则、样例或查配置快捷入口时，必须先读取本文全文，再把 `workflow.md`、`workflow.json`、`workflow_config_manage(action="get" 或 "status", workflow_id="stream_alert_denoise")` 的结果和工作流目录下的 `config.json` 作为支撑上下文。

`workflow-config-guide` skill 只提供交互协议；本文才是本工作流配置细节、默认选项、提问顺序和验证方式的来源。

Rex 引导用户时必须遵守：

1. 根据用户点击的入口或自然语言需求，自动定位本文相关章节。
2. 一次只问一个最关键问题。
3. 每个选择都必须允许自定义/补充输入；没有补充则填 `none`。
4. 涉及发布模板、触发器、阈值、字段列表或持久化行为变更时，先展示计划和 diff，再用 question 工具确认。
5. 查配置只能只读，不得修改文件、启动监听、发布 API 或停止运行态服务。

## 0. 后端配置库访问约束

本节优先级高于通用会话提示中的后端 API token 或 curl 示例。处理本工作流的发布、Syslog 接入、API 接入或查配置时，必须按本文执行：

- 配置库读取/写入必须使用内置工具 `workflow_config_manage`，不要读取 `server_api_token` 或 `service_api_token`，也不要手工 curl 本机后端配置接口。
- 查配置使用 `workflow_config_manage(action="get", workflow_id="stream_alert_denoise")` 或 `workflow_config_manage(action="status", workflow_id="stream_alert_denoise")`。
- 修改配置前先使用 `workflow_config_manage(action="diff", workflow_id="stream_alert_denoise", config={...})` 展示差异并用 question 工具确认；确认后才使用 `workflow_config_manage(action="put", workflow_id="stream_alert_denoise", config={...})`。
- 如果后端配置库没有模板，只能使用 `workflow_config_manage(action="sync", workflow_id="stream_alert_denoise")`，让后端从工作流目录 `config.json` 迁移或生成模板。
- `config.json` 只能作为模板来源或兜底迁移来源，不是直接写入目标，也不能证明配置已生效。
- 需要启动或停止 Syslog listener/API 服务时，必须使用对应运行态接口；不要通过修改模板字段冒充运行态状态。
- 如果 `workflow_config_manage` 不可用、返回未授权、拒绝访问、连接失败或后端不可达，必须停止配置流程，明确说明本次未应用、未发布、未启动；如已生成目标配置，只能保存草稿到 outputs，不要继续读取 token 或改写 `config.json`。

## 1. 工作流定位

- 工作流 ID：`stream_alert_denoise`
- 工作流名称：流式 HTTP 告警降噪与去重 Pipeline。
- 主要用途：接收 TDP / SkyEye 告警，统一字段，过滤扫描、非 HTTP 或低价值噪声，再用 URI 归一化 + 5-gram MinHash LSH 做跨批次去重。
- 当前状态：`meta.json` 标记为 `active`。
- 下游关系：`stream_alert_triage` 会读取本工作流写出的 `dedup_result_NNN.jsonl`，并基于 `dedup_key` 做 leader/follower 研判复用。

本工作流适合：

- 安全设备通过 Syslog 实时推送单条告警。
- 上游系统通过 API 提交 `alerts` 批次。
- 用 `alert_file` 回放历史 JSON 告警。
- 为下游 `stream_alert_triage` 提供带 `dedup_key`、`is_duplicate`、`_lsh_cluster_id` 的增强告警文件。

本工作流不负责：

- 调查资产、用户、攻击链或处置结果。
- 生成攻击研判报告。
- 直接阻断、封禁或处置攻击。
- 保存明文 API Key、密码、token。

## 2. AI 引导方式

如果用户点击的是发布/接入入口，优先判断输入模式和运行入口；如果用户点击的是规则入口，优先判断是否需要改过滤或去重参数；如果用户点击的是样例入口，优先让用户提供一条最小告警样例。

推荐提问顺序：

1. 你要用哪种输入模式：Syslog 实时流、API 批量、文件回放，还是同时保留 Syslog 和 API？
2. 告警来源是否固定为 TDP 或 SkyEye，还是继续自动识别？
3. 是否保持默认过滤和去重规则？
4. 是否需要用样例做轻量验证？
5. 是否应用到发布配置、保存草稿，或暂不修改？

如果用户只问“查一下现在怎么配的”，不要提问，直接按第 9 节只读检查。

## 3. 输入模式

工作流代码支持三种业务输入，解析优先级固定为：

1. `syslog_message` 或 `syslog`：单条 Syslog 告警。
2. `alerts` 或 `alert_list`：批量告警列表。
3. `alert_file`：JSON 告警文件路径。

发布/触发层当前配置来自工作流目录下的 `config.json` 兜底模板：

- `kind`: `workflow.integration-config`
- `publish.api.enabled`: `true`
- `publish.api.path`: `/api/workflow/stream_alert_denoise/run`
- `publish.syslog.enabled`: `true`
- `publish.syslog.host`: `0.0.0.0`
- `publish.syslog.port`: `514`
- `publish.syslog.protocol`: `udp`
- `triggers[0].id`: `syslog_main`
- `triggers[0].type`: `syslog`
- `triggers[0].config.input_field`: `syslog_message`
- `triggers[0].runtime.status`: `stopped`
- `apiKeyConfigured`: `false`

输入模式建议：

| 模式 | 适用场景 | 推荐配置 |
| --- | --- | --- |
| Syslog 实时流 | TDP / SkyEye 等设备持续推送单条告警 | 保留 Syslog trigger，默认 UDP `0.0.0.0:514`，输入字段 `syslog_message` |
| API 批量调用 | 上游系统或测试脚本提交 `alerts` 列表 | 保留 API 发布路径 `/api/workflow/stream_alert_denoise/run` |
| 文件回放 | 已有历史 JSON 文件，需要离线清洗去重 | 可以不发布触发器，使用手动测试或 API 传 `alert_file` |
| Syslog + API | 既要实时流，也要批量补录 | 保留当前模板中的 Syslog 和 API 两条入口 |

默认推荐：保留 Syslog + API。当前 `config.json` 已经声明两者都启用，但运行态状态仍应以后端配置和运行接口为准。

互斥关系：

- 业务输入在单次运行中按优先级互斥：同时传 `syslog_message` 和 `alerts` 时优先处理 `syslog_message`。
- 发布能力可以同时存在：Syslog listener 和 API publish 可以同时声明。

## 4. 来源形态

支持来源：

- TDP 原始 JSON 或扁平字段。
- SkyEye 原始 JSON 或扁平字段。
- Syslog 包裹的 TDP / SkyEye JSON，JSON 放在 `syslog_message.message`。
- 混合批次，单条告警会再次按字段特征识别。

来源识别规则：

| 线索 | 识别结果 |
| --- | --- |
| `source_log_type` 显式为 `tdp` 或 `skyeye` | 使用显式值 |
| Syslog `app_name` 或 `hostname` 包含 `tdp` | TDP |
| Syslog `app_name` 或 `hostname` 包含 `skyeye` | SkyEye |
| JSON 有嵌套 `net`、`behave_uuid`、`flow_id`、`net_real_src_ip`、`net_http_url`、`threat_suuid` | TDP |
| JSON 有 `uri`、`vuln_name`、`attack_result`、`attack_flag` | SkyEye |
| 仍无法判断 | 默认 TDP |

归一化后的关键字段：

| 标准字段 | 含义 |
| --- | --- |
| `sip` | 源 IP |
| `dip` | 目的 IP |
| `sport` / `dport` | 源/目的端口 |
| `net_type` / `net_app_proto` | 网络类型或应用协议 |
| `req_http_url` | HTTP 请求 URL |
| `req_host` / `req_user_agent` | HTTP Host 和 User-Agent |
| `req_body` / `rsp_body` | 请求体和响应体 |
| `threat_name` / `threat_type` | 威胁名称和类型 |
| `_source_type` | `tdp` 或 `skyeye` |
| `_syslog_meta` | Syslog 元数据，仅 Syslog 输入时存在 |

如果用户要接入 TDP 平台做告警检索、下载 PCAP 或页面调查，必须另行遵循 `tdp-use` skill；本文只描述本工作流处理 TDP 格式告警的输入字段。

## 5. 输出去向

工作流返回：

| 输出字段 | 说明 |
| --- | --- |
| `enriched_alerts` | 过滤和去重后的告警列表，去除了部分大体积 header/body 字段以降低运行历史体积 |
| `unique_alerts` | 本批次每个 `dedup_key` 的第一条代表告警 |
| `stats` | 原始数量、归一化数量、过滤数量、去重统计、LSH 状态统计、输出路径等 |
| `dedup_summary` | 一句话处理摘要 |
| `input_mode` | 本次实际输入模式 |
| `dedup_key` | 第一条增强告警的去重 key；无输出时为空字符串 |
| `is_duplicate` | 第一条增强告警是否为历史重复 |
| `output_path` | 本次最后一个写入的 JSONL 文件；如果本批全是历史重复则为空 |
| `output_paths` | 本次实际写入的 JSONL 文件列表 |

每条 `enriched_alert` 追加：

- `dedup_key`
- `is_duplicate`
- `_lsh_cluster_id`
- `_source_type`
- `_process_type`
- `_threat_type`

结果文件路径：

```text
~/.flocks/workspace/workflows/stream_alert_denoise/<YYYY-MM-DD>/dedup_result_NNN.jsonl
```

写入规则：

- 每个文件第一行是 `{ "_type": "file_header", "created_at", "date", "workflow", "seq" }`。
- 每个文件最多 10000 条告警记录，不含首行 header。
- `.dedup_counter.json` 记录当前文件序号和条数。
- 只持久化跨批次首见告警，即 `is_duplicate=false` 的告警。
- 历史重复告警仍会出现在本次 API 返回的 `enriched_alerts` 里，但不会再次写入 JSONL。

## 6. 处理规则

默认参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `source_log_type` | 自动识别，失败默认 `tdp` | 可显式指定 `tdp` 或 `skyeye` |
| `filter_enabled` | `true` | 是否启用过滤 |
| `dedup_enabled` | `true` | 是否启用 LSH 去重和状态持久化 |
| `threshold` | `0.7` | MinHash LSH 相似度阈值，越高越严格 |
| `strict_fields` | `["sip", "dip"]` | 生成 `dedup_key` 前必须精确参与的字段 |
| `lsh_fields` | `["req_http_url", "req_body", "rsp_body"]` | 参与 URI 归一化和 5-gram 相似度的字段 |
| `max_field_len` | `500` | 每个参与字段截断长度 |
| `max_dedup_keys` | `100000` | 持久化去重 key 和 LSH cluster 上限，小于 1 时回退 100000 |

过滤规则：

- TDP 告警：保留非扫描、HTTP、方向为 `in`、`out` 或 `lateral` 的告警。
- SkyEye 告警：扫描类走 `alert_scan_direction_in`，非扫描类按 HTTP 入向处理。
- 扫描判断：`threat_name` 包含“扫描”且不包含 `webshell`。
- HTTP 判断：`application_layer_protocol`、`net_type` 或 `net_app_proto` 任一字段包含 `http`。
- `filter_enabled=false` 时，所有归一化告警进入去重，`_process_type=filter_disabled`。

去重规则：

- URI 会归一化日期、UUID、目录穿越、NULL、`chr$...`、数字比较、32 位十六进制串等高变形片段。
- MinHash 使用 128 permutations 和 5-gram shingles。
- `dedup_key = MD5(strict_fields 文本 + lsh_cluster_id)`。
- LSH 状态保存在：

```text
~/.flocks/workspace/workflows/stream_alert_denoise/lsh_state_np128_th70.pkl
~/.flocks/workspace/workflows/stream_alert_denoise/lsh_state_np128_th70.append.log
~/.flocks/workspace/workflows/stream_alert_denoise/lsh_state_np128_th70.lock
```

低层参数默认隐藏，不主动询问：`max_field_len`、MinHash seed、`NUM_PERM`、counter 文件名、append-log compact 阈值。只有用户明确要求调优或排障时再解释。

## 7. 样例验证

Syslog 最小样例：

```json
{
  "syslog_message": {
    "hostname": "tdp-sensor",
    "app_name": "tdp",
    "timestamp": "2026-05-12T10:00:00",
    "severity": 6,
    "facility": 16,
    "format": "rfc3164",
    "message": "{\"id\":\"AZtRkZkzj\",\"net\":{\"http\":{\"url\":\"/admin\"}},\"threat\":{\"name\":\"SQL注入\"}}"
  }
}
```

API 批量最小样例：

```json
{
  "source_log_type": "tdp",
  "filter_enabled": true,
  "dedup_enabled": true,
  "threshold": 0.7,
  "alerts": [
    {
      "net_real_src_ip": "1.2.3.4",
      "net_dest_ip": "10.0.0.1",
      "direction": "in",
      "net_type": "http",
      "net_http_url": "/admin/login.php?id=1 OR 1=1",
      "net_http_reqs_body": "username=admin&password=123456",
      "net_http_resp_body": "root@localhost",
      "threat_name": "SQL注入攻击",
      "threat_type": "web攻击"
    }
  ]
}
```

轻量验证优先检查：

1. `receive_alert` 是否识别为 `syslog`、`alerts` 或 `alert_file`。
2. `source_log_type_reason` 是否合理。
3. `normalize` 是否产出 `sip`、`dip`、`req_http_url`、`threat_name`。
4. `filter_logs` 是否符合保留/过滤预期。
5. `dedup_and_write` 是否生成稳定 `dedup_key` 和 `_lsh_cluster_id`。
6. `output_paths` 是否指向当日 `dedup_result_NNN.jsonl`，或者在全重复时为空。

验证注意：

- 如果只想验证字段映射和过滤，建议先用 `dedup_enabled=false`，避免改动生产 LSH 状态。
- 如果要验证跨批次去重，必须说明会更新本工作流的 LSH 状态文件，并用 question 工具确认。
- 不要为了样例验证启动或停止 Syslog/API 服务，除非用户明确要求。

## 8. 应用方式

发布配置模板的生效来源：

1. 优先用 `workflow_config_manage(action="get", workflow_id="stream_alert_denoise")` 读取后端 Storage/SQL 的生效配置。
2. 如果库里没有，调用 `workflow_config_manage(action="sync", workflow_id="stream_alert_denoise")`，由后端读取工作流目录下的 `config.json` 并迁移到 Storage/SQL。
3. `config.json` 是导入/兜底模板，不是运行态开关。
4. 不要直接写 `config.json` 来表示发布、接入或触发配置已经生效。
5. 启停、发布、取消发布等运行态动作必须调用运行时接口，不要通过修改 `config.json` 完成。
6. 如果 `workflow_config_manage` 或后端配置库不可用，只能把目标配置保存为草稿到 outputs，并明确说明未应用、未发布、未启动。

应用变更前必须展示：

- 计划。
- publish / triggers / 参数模板 diff。
- 影响说明，特别是是否会改变 LSH 状态、JSONL 写入或运行态服务。
- question 工具确认：应用、保存草稿或暂不修改。
- 用户确认应用后，使用 `workflow_config_manage(action="put", workflow_id="stream_alert_denoise", config={...})` 写入完整配置。

当前 `config.json` 已经是 runtime 消费的结构：`kind: workflow.integration-config`，顶层包含 `publish` 和 `triggers`。不要生成旧的 `publishTemplates` wrapper。

## 9. 查配置

只读检查顺序：

1. 读取本文。
2. 读取 `workflow.md` 和 `workflow.json`。
3. 调用 `workflow_config_manage(action="get", workflow_id="stream_alert_denoise")` 或 `workflow_config_manage(action="status", workflow_id="stream_alert_denoise")`。
4. 如后端无配置，再查看工作流目录下 `config.json` 是否只是兜底模板。
5. 汇总已配置项、缺失项和最推荐下一步。

查配置时重点报告：

- API 发布是否启用、路径是什么、是否已配置 API Key。
- Syslog trigger 是否存在、host/port/protocol/input_field 是什么、运行态是否 started。
- 当前建议输入模式。
- 默认过滤和去重规则。
- 最近一次输出路径只能作为运行结果线索，不应从 guide 或 config 里臆测。

查配置不得修改文件、启动监听、发布 API、停止服务或写运行态状态。
