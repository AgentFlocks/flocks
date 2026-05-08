# http_alert_dedup

网络告警去重 Pipeline，三阶段处理：**归一化 → 过滤 → 去重**。

输入 `dict`（原始告警列表 + 配置），输出 `dict`（去重后的告警 + 统计信息），不调用 LLM。

## 工作流图

```
receive_alerts
      │
branch_log_type
  ├─ tdp    ─→ normalize_tdp
  └─ skyeye ─→ normalize_skyeye
                    │
               filter_logs
                    │
          branch_has_alerts
            ├─ true  ─→ dedup_logs   ◀── 终点，输出 dict
            └─ false ─→ dedup_empty  ◀── 终点，输出空 dict
```

## 输入参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `alerts` | `list[dict]` | — | 原始告警列表（必填） |
| `source_log_type` | `str` | `"tdp"` | 日志来源类型，`"tdp"` 或 `"skyeye"` |
| `filter_enabled` | `bool` | `true` | 是否启用过滤阶段 |
| `dedup_enabled` | `bool` | `true` | 是否启用去重阶段（false 时每条告警独立分配 key） |
| `threshold` | `float` | `0.7` | Jaccard 相似度阈值（0–1） |
| `strict_fields` | `list[str]` | `["sip","dip"]` | 严格匹配字段（需完全相同才参与模糊聚类） |
| `lsh_fields` | `list[str]` | `["req_http_url","req_body","rsp_body"]` | 模糊匹配字段（URI 归一化 + Jaccard） |
| `max_field_len` | `int` | `500` | 单字段截断长度 |

## 输出参数（终点节点 `dedup_logs` 的 outputs）

| 字段 | 类型 | 说明 |
|------|------|------|
| `deduped_alerts` | `list[dict]` | 全量告警（经过滤），每条含 `dedup_key`（MD5）和 `dedup_key_already_exists`（是否重复） |
| `unique_alerts` | `list[dict]` | 每个 dedup_key 的代表性告警（去重后唯一集合） |
| `stats` | `dict` | 各阶段统计（见下表） |
| `dedup_summary` | `str` | 一行文字摘要 |

### stats 字段

| 字段 | 说明 |
|------|------|
| `raw_count` | 原始输入告警数 |
| `normalized_count` | 归一化后告警数 |
| `after_filter_count` | 过滤后保留数 |
| `filter_removed_count` | 过滤剔除数 |
| `filter_process_type_counts` | 各 process_type 计数 dict |
| `after_dedup_count` | 去重后告警总数（等于 after_filter_count） |
| `unique_key_count` | 唯一 dedup_key 数（簇数） |
| `dedup_removed_count` | 去重压缩的重复条数 |
| `dedup_ratio` | 压缩率（dedup_removed / after_dedup） |

## 节点说明

### receive_alerts
解析输入，支持 `alerts` / `alert_list` 键，支持 JSON 字符串或 `{"data": [...]}` 包装格式，提取 Pipeline 配置参数。

### branch_log_type
按 `source_log_type` 路由：`"tdp"` → `normalize_tdp`，`"skyeye"` → `normalize_skyeye`。

### normalize_tdp / normalize_skyeye
字段映射，将各来源的原始字段统一为标准字段（`sip`/`dip`/`req_http_url`/`req_body`/`rsp_body`/`threat_name` 等）。对缺失 `id` 的告警使用 UUID v3 生成。

**TDP 关键映射（部分）**

| 标准字段 | TDP 原始字段 |
|----------|-------------|
| `sip` | `net_real_src_ip` |
| `dip` | `net_dest_ip` |
| `req_http_url` | `net_http_url` |
| `req_body` | `net_http_reqs_body` |
| `rsp_body` | `net_http_resp_body` |
| `threat_name` | `threat_name` |

**Skyeye 关键映射（部分）**

| 标准字段 | Skyeye 原始字段 |
|----------|----------------|
| `req_http_url` | `uri` |
| `threat_name` | `vuln_name` |
| `threat_type` | `vuln_type` |
| `threat_result` | `attack_result` |

### filter_logs
基于 `process_type` 的 9 类分类过滤，输出 `_has_alerts` 布尔值供后续分支路由：

| process_type | 保留/过滤 |
|-------------|----------|
| `alert_not_scan_http_direction_in` | ✅ 保留 |
| `alert_not_scan_http_direction_out` | ✅ 保留 |
| `alert_not_scan_http_direction_lateral` | ✅ 保留 |
| `alert_scan_direction_*` | ❌ 过滤（扫描类） |
| `alert_not_scan_not_http_*` | ❌ 过滤（非 HTTP） |
| `alert_not_process` | ❌ 过滤（其他） |

### branch_has_alerts
按 `_has_alerts` 路由：`true` → `dedup_logs`；`false` → `dedup_empty`。

### dedup_empty（终点 — 无告警路径）
过滤后无告警时直接返回空结果 dict，格式与 `dedup_logs` 输出一致（`deduped_alerts=[]`，`unique_alerts=[]`，stats 补零）。

### dedup_logs（终点 — 有告警路径）

**URI 归一化**（减少 LSH 字段噪音）：

| 正则模式 | 替换为 |
|---------|--------|
| 日期时间 | `DATETIME` |
| UUID | `UUID` |
| 6 位以上数字 | `NUM` |
| 路径穿越 | `../` |
| `%00` | `NULL` |
| 连续 URL 编码（≥3 组） | `ENCODED` |

**去重算法**：
1. `strict_fields` 拼接作为严格前缀，不同前缀的告警不归并
2. 对 `lsh_fields`（URI 归一化后）做 **5-gram shingling**
3. 与已注册簇计算 **Jaccard 相似度**，≥ `threshold` 则归入该簇
4. 新簇生成 **MD5 dedup_key**；重复告警标记 `dedup_key_already_exists=True`

