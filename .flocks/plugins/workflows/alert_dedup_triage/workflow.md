# 告警去重研判串联工作流

## 业务场景

将 `http_alert_dedup`（MinHash LSH 去重）与 `tdp_alert_triage`（LLM 研判）串联为单一工作流：

1. 接收 TDP/HTTP 原始告警（支持 syslog 实时单条 / 批量列表 / 文件三种模式）
2. 逐条调用 http_alert_dedup 服务去重：跨批次已见告警直接跳过，节省研判算力
3. 对首次出现的唯一告警调用 tdp_alert_triage 服务进行 LLM 研判（测绘/CVE/payload 并行）
4. 聚合所有结果输出汇总报告及最高风险告警的研判详情

## 流程结构

```
receive_alerts   (解析输入：syslog_message / alerts 列表 / alert_file 文件)
       ↓
dedup_and_triage (逐条去重 → 唯一告警 → 研判 → 缓存回填)
       ↓
generate_summary (聚合输出，写 pipeline_summary.md)
```

### 内部循环逻辑（dedup_and_triage）

```
for each raw_alert:
    POST /invoke → http_alert_dedup (port 19000)
        ├─ filtered_out             → 跳过（非 HTTP / 扫描告警）
        ├─ duplicate_with_triage    → 回填历史研判缓存（triage_cache.pkl）
        ├─ duplicate_skipped        → 跳过（跨批次已见且无缓存）
        └─ unique                   → POST /invoke → tdp_alert_triage (port 19001)
                                          ↓
                                      collect & persist triage result
```

## 节点详情

### 1. `receive_alerts`

支持三种输入模式（优先级由高到低）：

| 模式 | 触发条件 | 说明 |
|------|---------|------|
| **syslog** | `syslog_message` 字段存在 | flocks syslog 监听器注入，TDP 告警 JSON 在 `.message` 字段；syslog 元数据附加到告警的 `_syslog_meta` 字段 |
| **alerts** | `alerts` 或 `alert_list` 字段为非空列表 | 批量调用，直接传入告警列表 |
| **alert_file** | `alert_file` 为本地 JSON 文件路径 | 离线测试 / 批处理场景 |

- 提取去重配置：`source_log_type`、`filter_enabled`、`dedup_enabled`、`threshold`
- 支持通过 `dedup_service_url` / `triage_service_url` 输入字段覆盖服务地址

### 2. `dedup_and_triage`
逐条处理每条原始告警：
- 单条 POST 到 `http_alert_dedup` 服务（保持跨批次 LSH 状态持久化）
- 根据返回的 `dedup_key_already_exists` 判断是否为首次出现
- 仅对首次出现的告警用**原始 raw alert**（而非归一化字段）调用 `tdp_alert_triage`
- 对所有告警记录处理阶段：`filtered_out` / `duplicate_skipped` / `triage_done` / `dedup_failed` / `triage_failed`

### 3. `generate_summary`
- 聚合所有研判结果，按 `attack_verdict` 风险级别排序
- 生成 Markdown 汇总表（明细 + 统计）+ 最高风险告警的完整报告
- 落盘到 `~/.flocks/workspace/outputs/<date>/artifacts/pipeline_summary.md`
- 主要输出字段与 `tdp_alert_triage` 兼容（`attack_verdict`、`risk_level`、`report_title`、`final_report`），方便单告警场景直接对接下游

## 输入参数

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `syslog_message` | dict | — | syslog 解析结果（由 flocks 监听器注入），TDP JSON 在 `.message` 字段 |
| `alerts` | list | — | 原始告警列表（与 alert_file 二选一） |
| `alert_file` | string | — | JSON 文件路径（替代 alerts 列表） |
| `source_log_type` | string | `"tdp"` | 日志类型（`tdp` / `skyeye`） |
| `filter_enabled` | bool | `true` | 是否启用告警过滤 |
| `dedup_enabled` | bool | `true` | 是否启用去重（含持久化） |
| `threshold` | float | `0.7` | LSH Jaccard 相似度阈值 |
| `max_dedup_keys` | int | `100000` | LSH hash + 研判缓存最大条数，超出后 FIFO 淘汰 |
| `dedup_service_url` | string | `http://127.0.0.1:19000` | http_alert_dedup 服务地址 |
| `triage_service_url` | string | `http://127.0.0.1:19001` | tdp_alert_triage 服务地址 |
| `triage_timeout_s` | int | `300` | 单条研判超时秒数 |
| `dedup_timeout_s` | int | `60` | 单条去重超时秒数 |

## 输出参数

| 字段 | 类型 | 说明 |
|------|------|------|
| `final_report` | string | 最高风险告警的完整 Markdown 报告 |
| `report_title` | string | 最高风险告警的标题 |
| `attack_verdict` | enum | 最高风险告警的判定标签 |
| `risk_level` | enum | 最高风险告警的风险等级 |
| `final_reports` | list | 所有研判成功告警的报告列表 |
| `triage_results` | list | 所有研判成功告警的详情 |
| `summary_report` | string | 汇总 Markdown（统计 + 明细表） |
| `report_path` | string | `pipeline_summary.md` 落盘路径 |
| `stats` | dict | 处理统计（total/filtered/dedup/triage 各计数） |

## Syslog 接入配置

flocks 内置了 RFC 3164 / RFC 5424 syslog 监听器（UDP + TCP，默认端口 5140）。只需通过 API 为本工作流开启监听，即可实现 TDP 实时告警接入。

### 启用 syslog 监听

```bash
curl -X POST http://127.0.0.1:8000/api/workflow/alert_dedup_triage/syslog-config \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <your-token>" \
  -d '{
    "enabled":   true,
    "protocol":  "udp",
    "host":      "0.0.0.0",
    "port":      5140,
    "inputKey":  "syslog_message"
  }'
```

| 参数 | 说明 |
|------|------|
| `protocol` | `udp`（推荐）或 `tcp` |
| `port` | syslog 监听端口（默认 5140） |
| `inputKey` | 注入到工作流 inputs 的键名，本工作流固定读取 `syslog_message` |

### TDP 设备 syslog 转发格式

TDP 传感器/探针将告警以 syslog 方式推送时，**消息体（MSG 字段）必须是合法 JSON 格式的 TDP 告警对象**，例如：

```
<134>May 10 16:00:00 tdp-sensor tdp: {"id":"AZtRk...","net":{"http":{"url":"/admin"}},"threat":{"name":"SQL注入"}}
```

- `receive_alerts` 节点会从 `syslog_message.message` 字段提取并解析该 JSON
- syslog 元数据（`hostname`、`severity`、`timestamp` 等）附加到告警的 `_syslog_meta` 字段，可供后续节点溯源但不参与去重计算

### 查询当前配置

```bash
curl http://127.0.0.1:8000/api/workflow/alert_dedup_triage/syslog-config \
  -H "Authorization: Bearer <your-token>"
```

---

## 工程要点

- **三种输入模式**：syslog 实时单条（最高优先级）→ alerts 批次列表 → alert_file 文件，`receive_alerts` 自动检测并切换，`input_mode` 字段记录实际生效模式
- **跨批次去重**：`dedup_and_triage` 每次单条调用 dedup 服务，LSH 状态持久化在 `~/.flocks/workspace/workflows/http_alert_dedup/` 下，syslog 实时模式与批次模式共享同一 LSH 状态
- **研判缓存回填**：重复告警从 `~/.flocks/workspace/workflows/alert_dedup_triage/triage_cache.pkl` 读取历史研判结果（`stage=duplicate_with_triage`），实时 syslog 模式下可做到秒级响应
- **原始告警传给研判**：triage 接收的是原始 raw alert（保留嵌套 `net.http.*` / `threat.*` 字段），syslog 模式下包含 `_syslog_meta` 附加字段
- **节点超时**：`node_timeout_s = 7200`，留出足够余量处理大批量告警（每条研判约 50s × N 条）
- **输出兼容性**：`generate_summary` 的主要输出字段与 `tdp_alert_triage` 相同，单告警（syslog）场景下可无缝替换
