# alert_dedup — 告警去重工作流

## 简介

`alert_dedup` 是一个安全告警去重工作流，基于 **aisoc_mini** 项目的去重算法移植而来。
它通过 URI 归一化 + 5-gram Shingling + Jaccard 相似度，将相似告警归入同一去重簇，
有效降低告警噪声，让安全分析师聚焦于真正唯一的威胁事件。

## 使用场景

- 批量告警分析前的预处理（降噪）
- SIEM/NDR 告警规律性分析
- 告警风暴抑制（同一攻击模式产生大量重复告警）
- 与 LLM 研判结合：去重后只对唯一告警调用大模型，节省成本

## 输入参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `alerts` | `list[dict]` | 必填 | 待去重告警列表，每条为 JSON 对象 |
| `strict_fields` | `list[str]` | `["sip", "dip"]` | 严格匹配字段（如源IP/目的IP），这些字段不同则一定不归为同组 |
| `lsh_fields` | `list[str]` | `["req_http_url", "req_body", "rsp_body"]` | 近似匹配字段（URL、请求体、响应体），用于 Jaccard 相似度计算 |
| `threshold` | `float` | `0.7` | Jaccard 相似度阈值，超过此值认为是同类告警 |
| `max_field_len` | `int` | `500` | 字段截断长度，避免超长内容影响性能 |

### 中文字段名支持

如果告警使用中文列名（如来自 CSV），可将字段名配置为中文：

```json
{
  "strict_fields": ["源IP", "目的IP"],
  "lsh_fields": ["请求内容", "响应内容", "载荷_decoded"]
}
```

## 输出

| 字段 | 说明 |
|------|------|
| `unique_alerts` | 去重后的唯一告警列表，每条含 `dedup_key`、`dedup_group_size`、`dedup_key_already_exists=false` |
| `duplicate_alerts` | 被归为重复的告警列表，含 `dedup_key_already_exists=true` |
| `dedup_stats` | 统计信息：总数、唯一数、重复数、去重率、分组数 |
| `report_path` | Markdown 报告路径 |
| `summary` | 单行执行摘要 |

**告警新增字段说明：**
- `dedup_key`：MD5 去重键，同一去重簇的告警共享相同的值
- `dedup_key_already_exists`：`true` 表示该告警是重复的
- `dedup_group_size`：该告警所属分组的总数量

## 工作流节点

```
receive_alerts
     │
     ▼
normalize_alerts       ← URI 归一化（日期/UUID/长数字/路径穿越/编码字符）
     │
     ▼
compute_dedup_keys     ← 严格字段精确匹配 + 5-gram Jaccard 近似相似度
     │
     ▼
group_by_dedup_key     ← 按去重键分组，标记唯一/重复
     │
     ▼
generate_dedup_report  ← 生成报告 + 写出 JSONL/JSON 数据文件
```

### 节点说明

| 节点 ID | 类型 | 职责 |
|---------|------|------|
| `receive_alerts` | python | 解析告警输入（支持 JSON 字符串、列表、`{data:[...]}` 嵌套），提取配置 |
| `normalize_alerts` | python | 对 LSH 字段值进行 URI 风格归一化，去除日期/UUID/数字等噪声 |
| `compute_dedup_keys` | python | 5-gram Shingling + Jaccard 相似度计算，生成 MD5 去重键 |
| `group_by_dedup_key` | python | 按去重键分组，首条为代表（unique），其余标记为 duplicate |
| `generate_dedup_report` | python | 生成 Markdown 报告及 JSONL/JSON 数据文件 |

## 算法说明

### 1. URI 归一化（`normalize_alerts`）

对 URL、请求体等 LSH 字段应用以下替换规则，使内容相同但细节不同的告警在相似度计算上趋近：

| 模式 | 替换为 |
|------|--------|
| `2024-01-15`、`2024/01/15 14:30` | `DATETIME` |
| `550e8400-e29b-41d4-a716-...` (UUID) | `UUID` |
| 6 位及以上纯数字 | `NUM` |
| `../`、`..\` 路径穿越 | `../` |
| URL 编码的 NULL 字节 `%00` | `NULL` |
| 连续 3 个以上 URL 编码字符 | `ENCODED` |

### 2. 去重键计算（`compute_dedup_keys`）

```
strict_text = join(strict_fields values)
lsh_text    = join(normalized lsh_fields values)

→ 在已有簇中找 strict_text 相同、Jaccard(lsh_text) ≥ threshold 的簇
→ 若找到：复用该簇的 dedup_key
→ 若未找到：dedup_key = MD5(strict_text + ". " + lsh_text)
```

Jaccard 相似度基于 **5-gram** 分词（Character-level shingles）。

## 输出文件

所有文件写入 `~/.flocks/workspace/outputs/<YYYY-MM-DD>/`：

```
outputs/
└── <YYYY-MM-DD>/
    ├── alert_dedup_report.md            # 主报告（Markdown）
    └── artifacts/
        ├── dedup_all_alerts.jsonl       # 全量带去重键告警
        ├── dedup_unique_alerts.jsonl    # 唯一告警（去重代表）
        ├── dedup_duplicate_alerts.jsonl # 重复告警
        └── dedup_groups.json           # 分组统计（key + count）
```

## 示例

```json
{
  "alerts": [
    {
      "sip": "1.2.3.4",
      "dip": "10.0.0.1",
      "req_http_url": "/admin/login.php?id=1 OR 1=1",
      "req_body": "username=admin&password=123456",
      "rsp_body": "HTTP/1.1 200 OK"
    },
    {
      "sip": "1.2.3.4",
      "dip": "10.0.0.1",
      "req_http_url": "/admin/login.php?id=2 OR 2=2",
      "req_body": "username=admin&password=654321",
      "rsp_body": "HTTP/1.1 200 OK"
    }
  ],
  "strict_fields": ["sip", "dip"],
  "lsh_fields": ["req_http_url", "req_body", "rsp_body"],
  "threshold": 0.7
}
```

上述两条告警的严格字段相同（同源同目），LSH 字段经归一化后相似度高（SQL 注入 payload 结构一致），
因此会被归为同一去重簇，只保留第一条为代表。

## 与 aisoc_mini 的对应关系

| aisoc_mini 组件 | 本工作流对应节点 |
|-----------------|----------------|
| `LogDecoder.process()` | `normalize_alerts`（子集：URI 归一化） |
| `LogDedup._generate_dedup_key_text()` | `compute_dedup_keys` |
| `LSHProcessor.query_most_similar()` | `compute_dedup_keys`（5-gram Jaccard 简化版） |
| `LogDedup.process()` | `group_by_dedup_key` |
| 报告输出 | `generate_dedup_report` |

> **注意**：本工作流使用标准库实现相似度计算（无需 `datasketch`），
> 采用精确 Jaccard 而非 MinHash 近似。对于超大批量（>10 万条）告警，
> 建议改用 `datasketch` 的 MinHash LSH 以获得更好性能。
