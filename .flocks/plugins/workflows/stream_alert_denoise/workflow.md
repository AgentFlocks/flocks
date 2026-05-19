# stream_alert_dedup

流式 HTTP 告警去重 Pipeline，三阶段处理：**归一化 → 过滤 → 去重**。

与 `http_alert_dedup` 的核心区别：
1. **流式单条输入**：支持 syslog 实时单条（`syslog_message`），也兼容批次列表与文件
2. **输出为原始数据增强**：每条输出告警 = 归一化字段 + 去重字段（`dedup_key`、`is_duplicate`、`_lsh_cluster_id` 等）
3. **结果落盘**：每次执行自动将结果写入 `~/.flocks/workspace/workflows/stream_alert_dedup/<YYYY-MM-DD>/`

## 工作流图

```
receive_alert
      │
   normalize
      │
  filter_logs
      │
dedup_and_write  ◀── 终点，输出增强告警 + 写日期目录 JSON
```

## 输入参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `syslog_message` | `dict` | — | Syslog 消息体（优先级最高，单条流式） |
| `alerts` | `list[dict]` | — | 原始告警列表（批次模式） |
| `alert_file` | `str` | — | JSON 文件路径（文件模式） |
| `source_log_type` | `str` | 自动识别 | 来源类型 `"tdp"` 或 `"skyeye"`，不填则自动检测 |
| `filter_enabled` | `bool` | `true` | 是否启用过滤阶段 |
| `dedup_enabled` | `bool` | `true` | 是否启用跨批次去重（false 时仅批内去重） |
| `threshold` | `float` | `0.7` | Jaccard 相似度阈值（0–1） |
| `strict_fields` | `list[str]` | `["sip","dip"]` | 严格匹配字段 |
| `lsh_fields` | `list[str]` | `["req_http_url","req_body","rsp_body"]` | 模糊匹配字段（URI 归一化 + MinHash） |
| `max_field_len` | `int` | `500` | 单字段截断长度 |
| `max_dedup_keys` | `int` | `100000` | FIFO LRU 上限（持久化 dedup_key 最大数量） |

### syslog_message 格式

Flocks syslog 监听器解析 RFC3164 / RFC5424 后注入的结构体，TDP/Skyeye 原始 JSON 须在 `message` 字段内：

```json
{
  "hostname":  "tdp-sensor",
  "app_name":  "tdp",
  "timestamp": "2026-05-12T10:00:00",
  "severity":  6,
  "facility":  16,
  "format":    "rfc3164",
  "message":   "{\"id\":\"AZtRkZkzj\",\"net\":{\"http\":{\"url\":\"/admin\"}},\"threat\":{\"name\":\"SQL注入\"}}"
}
```

> 开启 syslog 接收：`POST /api/workflow/{id}/syslog-config {"enabled":true,"protocol":"udp","port":5140,"inputKey":"syslog_message"}`

## 输出参数

| 字段 | 类型 | 说明 |
|------|------|------|
| `enriched_alerts` | `list[dict]` | 过滤后全量告警，每条含完整归一化字段 + 去重字段 |
| `unique_alerts` | `list[dict]` | 每个 dedup_key 的代表性告警（首次出现） |
| `dedup_key` | `str` | 第一条告警的 dedup_key（syslog 单条场景直接使用） |
| `is_duplicate` | `bool` | 第一条告警是否为跨批次重复（syslog 单条场景直接使用） |
| `output_path` | `str` | 当次写入的最后一个 JSONL 文件路径 |
| `output_paths` | `list[str]` | 本次写入涉及的所有文件路径（批量超阈值时跨多个文件） |
| `stats` | `dict` | 各阶段统计（见下表） |
| `dedup_summary` | `str` | 一行文字摘要 |
| `input_mode` | `str` | 输入模式：`syslog` / `alerts` / `alert_file` |

### 每条 enriched_alert 的增强字段

| 字段 | 说明 |
|------|------|
| `dedup_key` | MD5 去重键（`strict_fields + cluster_id` 的哈希） |
| `is_duplicate` | 是否已在历史批次中出现过（跨批次持久化感知） |
| `_lsh_cluster_id` | MinHash LSH 簇 ID |
| `_source_type` | 识别出的来源类型（`tdp` / `skyeye`） |
| `_process_type` | 过滤分类（如 `alert_not_scan_http_direction_in`） |
| `_threat_type` | 威胁类型字符串 |
| `_syslog_meta` | syslog 元数据（仅 syslog 模式下存在） |

### stats 字段

| 字段 | 说明 |
|------|------|
| `raw_count` | 原始输入告警数 |
| `normalized_count` | 归一化后告警数 |
| `after_filter_count` | 过滤后保留数 |
| `filter_removed_count` | 过滤剔除数 |
| `after_dedup_count` | 去重处理总数（= after_filter_count） |
| `unique_key_count` | 唯一 dedup_key 数 |
| `dedup_removed_count` | 批内重复数 |
| `dedup_ratio` | 批内压缩率 |
| `output_path` | 结果文件路径 |

## 结果文件格式

写入路径：`~/.flocks/workspace/workflows/stream_alert_dedup/<YYYY-MM-DD>/dedup_result_NNN.jsonl`

- **JSONL 格式**：每行一个 JSON 对象
- **首行**：`file_header`（含时间戳，不计入告警条数）
- **后续行**：每行一条 enriched_alert
- **分卷规则**：每文件最多 **10,000 条**告警（不含 header 行），超出时自动创建 `dedup_result_002.jsonl`、`003.jsonl`…

```jsonl
{"_type": "file_header", "created_at": "2026-05-12T10:00:00.123456", "date": "2026-05-12", "workflow": "stream_alert_dedup", "seq": 1}
{"sip": "1.2.3.4", "dip": "10.0.0.1", "req_http_url": "/admin/login.php?id=1 OR 1=1", "threat_name": "SQL注入攻击", "_source_type": "tdp", "_process_type": "alert_not_scan_http_direction_in", "dedup_key": "a3f9...", "is_duplicate": false, "_lsh_cluster_id": 42}
{"sip": "5.6.7.8", "dip": "10.0.0.2", ...}
```

`output_path` 输出字段为当次写入的**最后一个**文件路径；`output_paths` 为本次写入涉及的所有文件路径列表（批量超过分卷阈值时可能跨多个文件）。

## 节点说明

### receive_alert
解析三种输入格式（syslog > alerts > alert_file）。从以下来源按优先级解析 `source_log_type`：
1. 显式 `source_log_type` 参数
2. Syslog `app_name` / `hostname` 中含 `tdp` 或 `skyeye`
3. 告警 JSON 字段签名（TDP: 嵌套 net 字典 / behave_uuid；Skyeye: uri / vuln_name）
4. 默认 `tdp`

### normalize
字段映射统一为标准 schema（`sip`/`dip`/`req_http_url`/`req_body`/`rsp_body`/`threat_name` 等），自动检测每条告警类型，支持混合批次。保留 `_syslog_meta`。

### filter_logs
基于 `process_type` 9 分类过滤，保留非扫描 HTTP 告警（`in`/`out`/`lateral` 方向）。`filter_enabled=False` 时全量透传。

### dedup_and_write（终点）

**去重算法**（与 http_alert_dedup 相同）：
1. `strict_fields` 拼接作为精确前缀
2. `lsh_fields` URI 归一化后做 **5-gram shingling**
3. MinHash LSH（128 permutations）近似 Jaccard 相似度聚类，阈值 ≥ `threshold`
4. `dedup_key = MD5(strict_prefix + cluster_id)`；`is_duplicate=True` 表示历史已见

**持久化**：LSH 状态存于 `~/.flocks/workspace/workflows/stream_alert_dedup/lsh_state_np128_th*.pkl`，原子写 + 文件锁，FIFO LRU 上限 `max_dedup_keys`，可跨批次/跨进程复用。

> **注意**：`stream_alert_dedup` 维护独立的 LSH 状态，与 `http_alert_dedup` 不共享去重历史。如需共享历史，可修改 `WORKFLOW_NAME = 'http_alert_dedup'`（同时共享 dedup_key 空间）。
