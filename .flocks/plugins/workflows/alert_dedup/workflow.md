# alert_dedup — 告警处理四阶段 Pipeline 工作流

## 简介

`alert_dedup` 完整实现了 `aisoc_mini` 项目中 `LogProcessPipeline` 的四阶段主流程：

```
原始告警 → 归一化 → 过滤 → 去重 → 研判分析
```

每阶段均可通过配置独立开关，支持 TDP 和 Skyeye 两种日志格式。

## 工作流节点

```
receive_alerts
     │
     ▼
normalize_logs     ← Step 1: TDP/Skyeye 字段映射，扁平化嵌套结构
     │
     ▼
filter_logs        ← Step 2: 过滤扫描类/出站/非 HTTP 告警
     │
     ▼
dedup_logs         ← Step 3: URI 归一化 + 5-gram Jaccard 去重，生成 dedup_key
     │
     ▼
analyze_unique     ← Step 4: LLM 研判（仅对唯一 dedup_key 调用，结果回填重复告警）
     │
     ▼
generate_report    ← 汇总统计，写出 Markdown 报告与 JSONL 数据文件
```

## 输入参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `alerts` | `list[dict]` | 必填 | 原始告警列表 |
| `source_log_type` | `str` | `"tdp"` | 日志来源类型，`"tdp"` 或 `"skyeye"` |
| `normalize_enabled` | `bool` | `true` | 是否执行字段归一化 |
| `filter_enabled` | `bool` | `true` | 是否执行规则过滤 |
| `dedup_enabled` | `bool` | `true` | 是否执行去重 |
| `analyze_enabled` | `bool` | `true` | 是否执行 LLM 研判 |
| `threshold` | `float` | `0.7` | Jaccard 相似度阈值（去重步骤） |
| `strict_fields` | `list[str]` | `["sip","dip"]` | 严格匹配字段 |
| `lsh_fields` | `list[str]` | `["req_http_url","req_body","rsp_body"]` | 近似匹配字段 |
| `max_field_len` | `int` | `500` | 字段截断长度 |

## 四阶段详解

### Step 1 — 归一化（`normalize_logs`）

将 TDP 或 Skyeye 原始字段映射为统一标准字段：

| 标准字段 | TDP 原始字段 | Skyeye 原始字段 |
|----------|-------------|----------------|
| `sip` | `net_real_src_ip` | `sip` |
| `dip` | `net_dest_ip` | `dip` |
| `req_http_url` | `net_http_url` | `uri` |
| `req_body` | `net_http_reqs_body` | `req_body` |
| `rsp_body` | `net_http_resp_body` | `rsp_body` |
| `threat_name` | `threat_name` | `vuln_name` |
| `direction` | `direction` | *(none)* |
| `net_type` | `net_type` | *(none，自动探测 method)* |

支持嵌套结构（自动扁平化），缺失 `id` 时自动生成 UUID。

### Step 2 — 过滤（`filter_logs`）

应用两条 jsonLogic 规则：

| 规则 | 逻辑 | 结果 |
|------|------|------|
| 扫描过滤 | `threat_name/threat_type 含"扫描"` 且 **不含** `"webshell"` | 剔除 |
| 方向+协议 | `direction in ["in","none"]` 且 `net_type in ["http","none"]` | 保留 |

仅保留**不是扫描类**且**满足入站 HTTP 条件**的告警，添加 `_need_analysis_is_attack`、`_is_scan`、`_is_inbound_http` 字段。

### Step 3 — 去重（`dedup_logs`）

1. **URI 归一化**：对 lsh_fields 字段值做正则替换（日期→`DATETIME`、UUID→`UUID`、6位+数字→`NUM`、路径穿越、URL 编码）
2. **相似度计算**：5-gram Character Shingles + Jaccard 相似度
3. **聚类规则**：严格字段完全相同 + lsh_fields Jaccard ≥ threshold → 归为同一簇，复用同一 `dedup_key`
4. **去重键**：新簇时用 MD5(`strict_text + ". " + normalized_lsh_text`) 生成

每条告警新增字段：
- `dedup_key`：MD5 哈希串
- `dedup_key_already_exists`：`true` 表示该告警是重复告警

### Step 4 — 研判分析（`analyze_unique`）

- **LLM 调用策略**：仅对每个 `dedup_key` 的第一条告警（代表）调用 LLM，重复告警直接复用结果 → 节省大量 LLM 调用开销
- **Prompt**：专业安全研判 Prompt，明确区分"成功攻击"与"扫描/误报/正常流量"
- **输出字段**：每条告警新增 `is_attack: bool`

## 输出

| 字段 | 说明 |
|------|------|
| `analyzed_alerts` | 全量含 `is_attack` 字段的告警列表 |
| `attack_alerts` | 判定为真实攻击的告警子集 |
| `stats` | 各阶段统计：raw/normalized/filtered/dedup/analyzed 计数 |
| `report_path` | 最终 Markdown 报告路径 |
| `summary` | 单行执行摘要 |

## 输出文件

```
outputs/<YYYY-MM-DD>/
├── alert_pipeline_report.md               # 主报告
└── artifacts/
    ├── pipeline_all_analyzed.jsonl        # 全量含 is_attack 告警
    ├── pipeline_attack_alerts.jsonl       # 真实攻击告警
    └── pipeline_non_attack_alerts.jsonl   # 非攻击/误报告警
```

## 与 aisoc_mini 的对应关系

| aisoc_mini 类/函数 | 本工作流节点 |
|-------------------|------------|
| `LogNormalization.process()` / `normalize_ndr_log()` | `normalize_logs` |
| `LogFilter.filter()` + `jsonLogic(rule_1, rule_2)` | `filter_logs` |
| `LogDedup.process()` + `LSHProcessor` + `normalize_uri()` | `dedup_logs` |
| `LogAnalysis.process_parallel()` | `analyze_unique` |
| `PipelineResult.stats` | `generate_report` |

## 示例输入

```json
{
  "source_log_type": "tdp",
  "threshold": 0.7,
  "alerts": [
    {
      "net_real_src_ip": "1.2.3.4",
      "net_dest_ip": "10.0.0.1",
      "direction": "in",
      "net_type": "http",
      "net_http_url": "/admin/login.php?id=1 OR 1=1",
      "net_http_reqs_body": "username=admin&password=123456",
      "net_http_resp_body": "root@localhost, MySQL 5.7",
      "threat_name": "SQL注入攻击",
      "threat_type": "web攻击"
    }
  ]
}
```

> **提示**：`analyze_enabled: false` 可跳过 LLM 调用，仅做去重统计，适合纯降噪场景。
