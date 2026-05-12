# tdp_alert_pull_dedup

**TDP 告警持续拉取 + 去重 Pipeline**

参考 `stream_alert_dedup` 的处理流水线，把"数据源"从 syslog 监听换成主动调用 TDP v3.3.10 的 `tdp_log_search` 工具拉取。

工作流启动后，单个节点内部以 `while` 循环持续运行：每轮从 TDP 拉取一个时间窗口的告警 → 归一化 → 过滤（去扫描/非HTTP）→ MinHash LSH 去重 → 追加写入按日期切分的 JSONL 文件，直到达到 `max_iterations` / `max_runtime_s` 任一停止条件，或被外部取消。

## 工作流图

```
pull_dedup_loop  (循环节点；启动后持续运行)
       │
       ├─ tool.run('tdp_log_search', ...)   ── TDP 告警拉取
       ├─ normalize                          ── 字段映射到统一 schema
       ├─ filter_logs                        ── 9 分类，保留非扫描 HTTP 告警
       ├─ dedup                              ── URI 归一化 + 5-gram MinHash LSH
       └─ append JSONL                       ── 按日期 + 序号分卷写盘
                ▲
                │
       advance time cursor (持久化)
```

## 输入参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `pull_interval_s` | `float` | `60` | 每两次拉取之间的休眠秒数（不含 TDP 响应时间） |
| `initial_lookback_s` | `int` | `300` | 首次启动（无持久化游标时）从 `now - initial_lookback_s` 开始 |
| `max_iterations` | `int` | `0` | 最大循环次数，`0` 表示无限循环直到外部取消 |
| `max_runtime_s` | `float` | `0` | 最长运行时长，`0` 表示无限制 |
| `batch_size` | `int` | `1000` | 单次 TDP 拉取的最大告警数（映射到 `size`，上限 10000） |
| `net_data_types` | `list[str]` | `["attack"]` | 传给 `tdp_log_search` 的 `net_data_type`，可选 `attack` / `risk` / `action` |
| `sql` | `str` | `"threat.level = 'attack'"` | TDP 过滤表达式（**不是完整 SQL**），用于过滤拉取范围 |
| `assets_group` | `list[int]` | `[]` | 业务组 ID 列表，可选 |
| `filter_enabled` | `bool` | `true` | 是否启用 9 分类过滤（去扫描 / 仅留 HTTP） |
| `dedup_enabled` | `bool` | `true` | 是否启用 LSH 去重（关闭后仅记录原始 dedup_key、不跨批次感知） |
| `threshold` | `float` | `0.7` | Jaccard 相似度阈值 |
| `strict_fields` | `list[str]` | `["sip","dip"]` | 精确匹配字段（拼接进 dedup_key） |
| `lsh_fields` | `list[str]` | `["req_http_url","req_body","rsp_body"]` | 模糊匹配字段（URI 归一化后做 MinHash） |
| `max_field_len` | `int` | `500` | 单字段截断长度 |
| `max_dedup_keys` | `int` | `100000` | LSH 状态 FIFO LRU 上限 |
| `reset_cursor` | `bool` | `false` | `true` 时忽略已有游标，重新从 `now - initial_lookback_s` 开始 |
| `log_progress_every` | `int` | `1` | 每隔 N 轮打印一次进度日志（避免日志过于频繁） |

> ⚠️ 启动时**不要**传 `time_from` / `time_to`，工作流会自己用游标推进。要从指定时间开始重拉，把 `reset_cursor` 设为 `true` 并调整 `initial_lookback_s`。

## 输出参数

工作流执行结束（达到停止条件或被取消）后写入：

| 字段 | 类型 | 说明 |
|------|------|------|
| `summary` | `str` | 一行摘要（iters / pulls / raw / unique / files / stop_reason） |
| `stop_reason` | `str` | 退出原因：`completed` / `reached max_iterations=N` / `reached max_runtime_s=X` / `KeyboardInterrupt` / `unhandled error: ...` |
| `final_cursor` | `int` | 最后一次成功推进到的时间戳（下次启动从此处继续） |
| `output_paths` | `list[str]` | 本次运行写入的所有 JSONL 文件路径 |
| `output_path` | `str` | 最后写入的 JSONL 文件路径（便于单值消费） |
| `stats` | `dict` | 完整统计（见下表） |

### stats 字段

| 字段 | 说明 |
|------|------|
| `iterations` | 实际执行的循环轮次数 |
| `pulls_succeeded` / `pulls_failed` | TDP API 调用成功 / 失败次数 |
| `raw_total` | 从 TDP 拉到的原始告警数总和 |
| `normalized_total` | 归一化后告警数总和 |
| `filtered_total` | 过滤后保留的告警数总和（filter_enabled=true 时） |
| `enriched_total` | 经过去重处理的告警总数（含重复） |
| `unique_total` | 唯一 dedup_key 数总和 |
| `duplicates_total` | 被识别为重复的告警数 |
| `written_files` | 本次运行追加写入的所有文件路径列表 |
| `last_window_from` / `last_window_to` | 最近一次拉取的时间窗口 |
| `last_error` | 最近一次错误描述（无错误时为 `null`） |

## 文件落盘

### 告警结果（每轮追加）

```
~/.flocks/workflows/tdp_alert_pull_dedup/<YYYY-MM-DD>/alerts_NNN.jsonl
```

- **JSONL 格式**：每行一个 JSON 对象。
- **首行**：`{"_type":"file_header", "created_at":..., "date":..., "workflow":"tdp_alert_pull_dedup", "seq":N}`（不计入告警条数）。
- **后续行**：每行一条 enriched_alert（归一化字段 + 去重字段）。
- **分卷规则**：每文件最多 **10,000 条**告警，超出时自动新建 `alerts_002.jsonl`、`003.jsonl`…
- **跨天滚动**：每轮检测当前日期，自动写入新的 `<YYYY-MM-DD>/` 目录。

### 时间游标（断点续传）

```
~/.flocks/workflows/tdp_alert_pull_dedup/cursor.json
```

```json
{
  "next_from":  1715501234,
  "updated_at": "2026-05-12T15:43:54.123456",
  "iter":       42,
  "workflow":   "tdp_alert_pull_dedup"
}
```

- 每轮成功完成后原子写入。
- 重启工作流时自动加载，继续从 `next_from` 推进，**无重叠也无空洞**。
- TDP 调用失败时不推进游标，下一轮会重试同一个时间窗口。

### LSH 去重状态

```
~/.flocks/workflows/tdp_alert_pull_dedup/lsh_state_np128_th{int(threshold*100)}.pkl
~/.flocks/workflows/tdp_alert_pull_dedup/lsh_state_np128_th{int(threshold*100)}.lock
```

- 原子写入 + 文件锁（跨进程安全）。
- FIFO LRU 淘汰：达到 `max_dedup_keys` 阈值后逐出最早条目。
- 不同 `threshold` 互相独立（避免不同阈值之间状态混淆）。

### 每条 enriched_alert 的增强字段

| 字段 | 说明 |
|------|------|
| `dedup_key` | MD5 去重键（`strict_fields + cluster_id` 的哈希） |
| `is_duplicate` | 是否已在历史轮次中出现过（跨轮持久化感知） |
| `_lsh_cluster_id` | MinHash LSH 簇 ID（`dedup_enabled=false` 时为 `null`） |
| `_source_type` | 固定为 `tdp`（数据源） |
| `_process_type` | 过滤分类（如 `alert_not_scan_http_direction_in`） |
| `_threat_type` | 威胁类型字符串（同 `threat_name`） |

## 节点说明

### `pull_dedup_loop`（唯一节点，长时间运行）

`type: python`，`metadata.node_timeout_s = 2,592,000`（30 天）。

主循环步骤（每轮）：
1. **时间窗口计算**：`time_from = 上次 time_to`（首次为 `now - initial_lookback_s`），`time_to = 当前时间戳`。
2. **TDP 拉取**：`tool.run('tdp_log_search', action='search', time_from, time_to, net_data_type, sql, size)`，失败时 `pulls_failed++` 且**不**推进游标，下轮重试同一窗口。
3. **响应解包**：自动识别 `list` / `{"log":[...]}` / `{"list":[...]}` / `{"data":[...]}` 等常见 TDP 返回结构。
4. **归一化**：仅 TDP，复用 `stream_alert_dedup` 的 `TDP_FIELD_MAP`（嵌套字段也支持）。
5. **过滤**：9 分类，保留 `alert_not_scan_http_direction_{in|out|lateral}`。
6. **去重**：URI 归一化 + 5-gram MinHash LSH，跨轮 / 跨进程持久化。
7. **写盘**：JSONL 追加，达到 10,000 条自动滚卷。
8. **推进游标**：成功完成后 atomic 写入 `cursor.json`。
9. **休眠**：`pull_interval_s` 秒。

退出条件：
- `iter > max_iterations`（且 `max_iterations > 0`）
- `elapsed > max_runtime_s`（且 `max_runtime_s > 0`）
- `KeyboardInterrupt` / 节点取消
- 不可恢复异常（已被 catch，会写入 `stats.last_error`）

## 与 stream_alert_dedup 的差异

| 维度 | stream_alert_dedup | tdp_alert_pull_dedup |
|------|--------------------|----------------------|
| 数据来源 | syslog 监听 / `alerts` / `alert_file` 三选一 | 主动调用 `tdp_log_search` 工具 |
| 触发方式 | 外部事件驱动（每收到一条触发一次工作流） | 工作流自身长时间运行（while 循环） |
| 多源支持 | TDP + Skyeye 自动识别 | 仅 TDP（数据来源固定） |
| 时间游标 | 无（事件驱动无需游标） | 持久化游标，断点续传 |
| 落盘路径 | `~/.flocks/workspace/workflows/stream_alert_dedup/<date>/dedup_result_NNN.jsonl` | `~/.flocks/workflows/tdp_alert_pull_dedup/<date>/alerts_NNN.jsonl` |
| LSH 状态 | `~/.flocks/workspace/workflows/stream_alert_dedup/lsh_state_*.pkl` | `~/.flocks/workflows/tdp_alert_pull_dedup/lsh_state_*.pkl` |

> 两个工作流维护**独立**的 LSH 状态与去重历史，不会互相干扰。

## 运行方式

### 1. 通过 webui 启动

打开 webui → Workflows → `tdp_alert_pull_dedup` → 点击运行；可在右侧 RunTab 调整默认 `sampleInputs`。

### 2. 通过 API 启动

```bash
curl -s -X POST http://localhost:8000/api/workflow/tdp_alert_pull_dedup/run \
  -H 'Content-Type: application/json' \
  -d '{
    "inputs": {
      "pull_interval_s":    60,
      "initial_lookback_s": 600,
      "max_iterations":     0,
      "batch_size":         500,
      "net_data_types":     ["attack"],
      "sql":                "threat.level = '\''attack'\''",
      "filter_enabled":     true,
      "dedup_enabled":      true,
      "threshold":          0.7
    }
  }'
```

### 3. 短跑测试

```json
{
  "max_iterations":     5,
  "pull_interval_s":    5,
  "initial_lookback_s": 86400,
  "reset_cursor":       true
}
```

跑 5 轮、每轮拉取过去 24 小时的告警、忽略已有游标，便于快速验证 pipeline。

## 前置条件

1. **TDP 凭据已配置**：`tdp_api_key` / `tdp_secret` / `tdp_host` 已通过 secrets 或 `api_services.tdp_api.base_url` 配置。可用 `python -c "from flocks.tool import ToolRegistry; ToolRegistry.init(); print(ToolRegistry.get('tdp_log_search'))"` 验证工具已注册。
2. **`datasketch` 依赖**：和 `stream_alert_dedup` 共享，已在 flocks 项目依赖中。
3. **写盘权限**：用户对 `~/.flocks/workflows/` 目录有读写权限。

## 工程要点

- **节点超时**：`node_timeout_s = 2,592,000` (30 天)。如需更长运行时间可调高 metadata，或拆成多次有限轮次执行（搭配 cron scheduler）。
- **TDP 调用失败时的语义**：不推进游标，下次重试**同一时间窗口**，避免丢数据。但若 TDP 长时间不可用，建议外部监控 `stats.pulls_failed`。
- **time_from = 上次 time_to**：闭区间还是开区间取决于 TDP 服务端实现。如观察到边界重复，可在 dedup 阶段被 LSH 自动去掉；若不开启 dedup，建议手动 `+1` 偏移。
- **路径根目录**：通过 `Config().get_global().data_dir.parent` 解析 `~/.flocks`，避免硬编码用户目录。
- **不依赖 syslog/Kafka**：与 `stream_alert_dedup` 解耦；如需同时跑两套去重，记得它们**不共享** LSH 历史。
