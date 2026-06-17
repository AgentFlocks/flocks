# SOC 告警运营 Workflow 数据清单与 SQL 表设计

## 目标

本文梳理 SOC 工作区中“告警运营”页面的全部数据展示需求，并给出一套由 Workflow 驱动的数据表设计。目标是让告警从接入、降噪、研判、深度调查到响应处置都能被结构化记录、可审计、可下钻，并能稳定支撑页面和抽屉页展示。

当前页面参考：

- `webui/src/pages/Soc/Alerts.tsx`
- `webui/src/pages/Soc/mockData.ts`
- `docs/design/soc-alert-denoise-analysis-tab-design.md`

## 页面与抽屉数据清单

### 1. 告警运营顶部漏斗

展示位置：告警运营页顶部。

需要的数据：

| 数据 | 字段 |
|---|---|
| 原始告警数 | `raw_count`, `source_count`, `time_range_start`, `time_range_end` |
| 降噪后告警数 | `triage_count`, `filter_removed_count`, `dedup_removed_count`, `compression_ratio` |
| NDR 告警研判数 | `triage_pending_count`, `triage_done_count`, `triage_source_type` |
| 深度调查数 | `investigation_count`, `investigation_open_count`, `investigation_escalated_count` |
| 处置建议数 | `response_action_count`, `pending_action_count`, `processing_action_count`, `confirm_action_count` |

### 2. 降噪分析 Tab

展示位置：`降噪分析`。

需要的数据：

| 模块 | 字段 |
|---|---|
| 日期与范围 | `report_date`, `time_range_start`, `time_range_end`, `scope`, `source_filter`, `direction_filter`, `asset_scope` |
| 总览指标 | `raw_count`, `normalized_count`, `normalize_failed_count`, `filter_removed_count`, `dedup_removed_count`, `triage_count` |
| Workflow 链路 | `workflow_id`, `workflow_run_id`, `workflow_name`, `workflow_version`, `node_chain`, `status`, `started_at`, `finished_at` |
| 分段条 | `stage`, `stage_label`, `input_count`, `output_count`, `removed_count`, `ratio` |
| 降噪分类 | `category_key`, `category_title`, `removed_count`, `ratio`, `trend`, `description`, `top_reasons` |

### 3. 扫描告警分析页

展示位置：降噪分析二级页 `扫描告警`。

需要的数据：

| 模块 | 字段 |
|---|---|
| 日报结论 | `title`, `report_date`, `conclusion`, `summary_text` |
| 核心指标 | `scan_total`, `external_scan_count`, `internal_scan_count`, `scanned_asset_count`, `scanned_path_count`, `unique_source_ip_count`, `status_200_count`, `status_404_count`, `other_status_count` |
| 扫描来源分布 | `source_type`, `alert_count`, `source_ip_count`, `feature`, `ratio` |
| 来源 IP Top | `source_ip`, `side`, `source_type`, `region`, `asn`, `intel_summary`, `alert_count`, `asset_count`, `status_200_count`, `sensitive_hit_count`, `first_seen`, `last_seen`, `top_path`, `risk_level`, `disposition` |
| 响应码分布 | `status_code_group`, `count`, `ratio`, `explanation` |
| 200 响应资产 | `asset_id`, `asset_name`, `business`, `exposure`, `status_200_count`, `sensitive_interface`, `path_examples`, `risk_level`, `recommendation` |
| 被扫描资产 | `asset_id`, `asset_name`, `business`, `exposure`, `scan_count`, `status_200_count`, `status_404_count`, `other_status_count`, `top_paths` |
| 路径意图 | `path_intent`, `path_examples`, `alert_count`, `risk_explanation` |
| 封禁建议 | `object_value`, `scope`, `reason`, `evidence`, `suggested_action`, `priority`, `owner` |
| 内部扫描器排查 | `source_ip`, `owner`, `authorization_status`, `expected_window`, `finding`, `next_step` |
| 原始样本入口 | `sample_alert_id`, `raw_alert_id`, `sample_reason`, `payload_excerpt`, `response_excerpt` |

### 4. 重复告警分析页

展示位置：降噪分析二级页 `重复告警`。

需要的数据：

| 模块 | 字段 |
|---|---|
| 去重策略 | `strict_fields`, `lsh_fields`, `threshold`, `dedup_algorithm`, `window_seconds` |
| 去重指标 | `cluster_count`, `dedup_key_count`, `dedup_removed_count`, `dedup_ratio` |
| dedup 簇表 | `cluster_id`, `representative_alert_id`, `representative_title`, `duplicate_count`, `source_summary`, `target_summary`, `pattern`, `first_seen`, `last_seen` |
| 簇样本 | `alert_id`, `raw_alert_id`, `dedup_key`, `similarity`, `payload_excerpt`, `created_at` |

### 5. 条件过滤分析页

展示位置：降噪分析二级页 `条件过滤`。

需要的数据：

| 模块 | 字段 |
|---|---|
| process type 汇总 | `process_type`, `alert_count`, `explanation`, `removed_count`, `kept_count` |
| 样本入口 | `sample_alert_id`, `raw_alert_id`, `filter_reason`, `field_snapshot`, `created_at` |

### 6. 规则过滤分析页

展示位置：降噪分析二级页 `规则过滤`。

需要的数据：

| 模块 | 字段 |
|---|---|
| 规则过滤汇总 | `rule_id`, `rule_name`, `removed_count`, `updated_at`, `note`, `owner`, `enabled`, `severity_floor` |
| 样本入口 | `sample_alert_id`, `raw_alert_id`, `rule_id`, `match_fields`, `payload_excerpt` |

### 7. 黑白名单分析页

展示位置：降噪分析二级页 `黑白名单`。

需要的数据：

| 模块 | 字段 |
|---|---|
| 白名单 | `object_value`, `object_type`, `removed_count`, `ttl`, `expires_at`, `note`, `owner`, `audit_status` |
| 黑名单 | `object_value`, `object_type`, `hit_count`, `action`, `note`, `risk_level`, `intel_source` |
| 关联事件入口 | `incident_id`, `alert_id`, `aggregation_reason`, `created_at` |

### 8. 告警研判 Tab

展示位置：`告警研判`。

需要的数据：

| 模块 | 字段 |
|---|---|
| 研判列表 | `incident_id`, `priority`, `title`, `reason`, `owner`, `raw_alert_count`, `confidence`, `triage_status`, `created_at`, `updated_at` |
| 源 IP 情报 | `src_ip`, `intel_verdict`, `intel_location`, `intel_tags`, `intel_summary` |
| 目标资产 | `asset_id`, `asset_name`, `business`, `exposure`, `asset_owner`, `criticality`, `asset_context` |
| 请求信息 | `method`, `host`, `uri`, `payload`, `request_evidence`, `request_llm_analysis` |
| 响应信息 | `status_code`, `response_evidence`, `response_llm_analysis`, `response_excerpt` |
| 结论 | `verdict`, `summary`, `recommendation`, `actions` |

### 9. 告警研判详情 Drawer

展示位置：点击告警研判列表行。

需要的数据：

| 模块 | 字段 |
|---|---|
| Drawer 标题 | `incident_id`, `rule_id`, `rule_name`, `report_title`, `created_at`, `verdict` |
| 分析步骤 | `step_order`, `step_title`, `step_content`, `code_block`, `tool_name`, `tool_result_ref` |
| 研判报告 | `report_title`, `summary`, `payload_analysis`, `response_analysis`, `important_evidence`, `recommendation` |
| 关键事实卡 | `src_ip`, `target_asset`, `status_code`, `confidence`, `url`, `intel_label` |
| 下载/导出 | `report_id`, `export_format`, `generated_at` |
| 转入深度调查 | `incident_id`, `recommended_investigation_type`, `owner`, `priority`, `selected_evidence_ids` |

### 10. 深度调查 Tab

展示位置：`深度调查`。

需要的数据：

| 模块 | 字段 |
|---|---|
| 调查列表 | `investigation_id`, `severity`, `title`, `entities`, `status`, `owner`, `recommendation`, `source_incident_id`, `created_at`, `updated_at` |
| 跨设备证据摘要 | `evidence_source_list`, `evidence_count`, `first_seen`, `last_seen` |

### 11. 深度调查详情 Drawer

展示位置：点击深度调查列表行。

需要的数据：

| 模块 | 字段 |
|---|---|
| 调查头部 | `investigation_id`, `severity`, `status`, `title`, `summary`, `owner` |
| 指标卡 | `owner`, `entities`, `evidence_count`, `recommendation` |
| 跨设备证据链 | `evidence_id`, `source`, `event_time`, `detail`, `entity_type`, `entity_value`, `raw_ref`, `confidence` |
| Rex 调查结论 | `conclusion_title`, `conclusion_text`, `recommendation` |
| 操作 | `agent_session_id`, `report_id`, `response_action_ids` |

### 12. Agent 调查过程 Drawer

展示位置：深度调查详情 Drawer 内点击 `查看 Agent 调查过程`。

需要的数据：

| 模块 | 字段 |
|---|---|
| 会话头部 | `agent_session_id`, `investigation_id`, `title`, `entities`, `status`, `started_at`, `finished_at` |
| 消息流 | `message_id`, `role`, `sender`, `message_time`, `content`, `order_index` |
| 子任务卡 | `delegate_title`, `delegate_description`, `delegate_status`, `elapsed_ms`, `step_count`, `sub_session_id` |
| 工具调用 | `tool_name`, `target`, `status`, `result`, `error`, `started_at`, `finished_at` |
| 结论 | `conclusion`, `generated_actions`, `generated_report_id` |

### 13. 响应处置 Tab

展示位置：`响应处置`。

需要的数据：

| 模块 | 字段 |
|---|---|
| 统计 | `pending_count`, `processing_count`, `confirm_count`, `done_count`, `failed_count` |
| 处置列表 | `action_id`, `source_type`, `source_id`, `priority`, `object_type`, `object_value`, `action`, `evidence`, `owner`, `status`, `ticket_id`, `created_at`, `updated_at` |
| 操作入口 | `generate_ticket_enabled`, `source_url`, `approval_required`, `executor_type` |

## Workflow 产出字段建议

告警运营 Workflow 建议拆成四类节点，节点输出统一写入上面的业务表。

| 阶段 | 节点 | 关键输出 |
|---|---|---|
| 接入 | `receive_alert` | `raw_alert_id`, `source_id`, `received_at`, `raw_payload`, `payload_hash` |
| 归一化 | `normalize_alert` | `alert_id`, `event_time`, `src_ip`, `dst_ip`, `asset_id`, `rule_id`, `http_detail`, `normalize_status` |
| 降噪 | `filter_and_dedup` | `denoise_run_id`, `category_key`, `filter_reason`, `dedup_key`, `dedup_cluster_id`, `kept_for_triage` |
| 研判 | `triage_alert` | `incident_id`, `confidence`, `verdict`, `evidence`, `recommendation`, `response_actions` |
| 深调 | `investigate_incident` | `investigation_id`, `evidence_chain`, `agent_session_id`, `conclusion`, `report_id` |
| 处置 | `generate_response_actions` | `action_id`, `owner`, `priority`, `object_value`, `action`, `ticket_payload` |

## SQL 表设计

建议使用 21 张数据表。聚合表服务页面性能，原始表服务审计和下钻。

### 表 1：`soc_alert_sources`

告警来源配置表。

```sql
CREATE TABLE soc_alert_sources (
  id VARCHAR(64) PRIMARY KEY,
  name VARCHAR(128) NOT NULL,
  source_type VARCHAR(32) NOT NULL,
  vendor VARCHAR(64),
  product VARCHAR(64),
  region VARCHAR(128),
  security_domain VARCHAR(128),
  ingest_method VARCHAR(32) NOT NULL,
  endpoint_ref VARCHAR(256),
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  owner VARCHAR(128),
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL
);
```

### 表 2：`soc_raw_alerts`

原始告警审计表。

```sql
CREATE TABLE soc_raw_alerts (
  id VARCHAR(64) PRIMARY KEY,
  source_id VARCHAR(64) NOT NULL REFERENCES soc_alert_sources(id),
  external_alert_id VARCHAR(128),
  received_at TIMESTAMP NOT NULL,
  event_time TIMESTAMP,
  raw_payload JSON NOT NULL,
  payload_hash VARCHAR(128) NOT NULL,
  parser_version VARCHAR(64),
  ingest_batch_id VARCHAR(64),
  normalize_status VARCHAR(32) NOT NULL,
  normalize_error TEXT,
  created_at TIMESTAMP NOT NULL
);
```

### 表 3：`soc_alerts`

规范化告警主表，承接研判队列。

```sql
CREATE TABLE soc_alerts (
  id VARCHAR(64) PRIMARY KEY,
  raw_alert_id VARCHAR(64) REFERENCES soc_raw_alerts(id),
  source_id VARCHAR(64) NOT NULL REFERENCES soc_alert_sources(id),
  alert_type VARCHAR(64),
  rule_id VARCHAR(128),
  rule_name VARCHAR(256),
  severity VARCHAR(16),
  priority VARCHAR(16),
  event_time TIMESTAMP NOT NULL,
  src_ip VARCHAR(64),
  src_port INTEGER,
  dst_ip VARCHAR(64),
  dst_port INTEGER,
  protocol VARCHAR(32),
  direction VARCHAR(32),
  asset_id VARCHAR(64),
  title VARCHAR(256),
  summary TEXT,
  process_type VARCHAR(128),
  dedup_key VARCHAR(256),
  denoise_status VARCHAR(32) NOT NULL,
  triage_status VARCHAR(32) NOT NULL DEFAULT 'pending',
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL
);
```

### 表 4：`soc_alert_http_details`

HTTP/Web 类告警扩展字段。

```sql
CREATE TABLE soc_alert_http_details (
  alert_id VARCHAR(64) PRIMARY KEY REFERENCES soc_alerts(id),
  method VARCHAR(16),
  scheme VARCHAR(16),
  host VARCHAR(256),
  uri TEXT,
  query_string TEXT,
  user_agent TEXT,
  referer TEXT,
  request_headers JSON,
  request_body_excerpt TEXT,
  request_payload TEXT,
  response_status_code INTEGER,
  response_headers JSON,
  response_body_excerpt TEXT,
  request_evidence JSON,
  response_evidence JSON,
  created_at TIMESTAMP NOT NULL
);
```

### 表 5：`soc_assets`

资产上下文表。

```sql
CREATE TABLE soc_assets (
  id VARCHAR(64) PRIMARY KEY,
  name VARCHAR(256) NOT NULL,
  asset_type VARCHAR(64),
  ip VARCHAR(64),
  domain VARCHAR(256),
  business VARCHAR(128),
  exposure VARCHAR(32),
  owner VARCHAR(128),
  criticality VARCHAR(32),
  region VARCHAR(128),
  security_domain VARCHAR(128),
  cmdb_ref VARCHAR(128),
  tags JSON,
  context TEXT,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL
);
```

### 表 6：`soc_threat_intel_observations`

情报查询和命中结果表。

```sql
CREATE TABLE soc_threat_intel_observations (
  id VARCHAR(64) PRIMARY KEY,
  object_type VARCHAR(32) NOT NULL,
  object_value VARCHAR(512) NOT NULL,
  source VARCHAR(64) NOT NULL,
  verdict VARCHAR(32),
  confidence INTEGER,
  severity VARCHAR(32),
  location VARCHAR(128),
  asn VARCHAR(128),
  tags JSON,
  summary TEXT,
  raw_result JSON,
  observed_at TIMESTAMP NOT NULL,
  expires_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL
);
```

### 表 7：`soc_workflow_runs`

SOC 业务对象与 Workflow 执行实例的关联表。

```sql
CREATE TABLE soc_workflow_runs (
  id VARCHAR(64) PRIMARY KEY,
  workflow_id VARCHAR(128) NOT NULL,
  workflow_name VARCHAR(128) NOT NULL,
  workflow_version VARCHAR(64),
  execution_id VARCHAR(128),
  business_type VARCHAR(32) NOT NULL,
  business_id VARCHAR(64),
  status VARCHAR(32) NOT NULL,
  input_summary JSON,
  output_summary JSON,
  node_chain JSON,
  started_at TIMESTAMP,
  finished_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL
);
```

### 表 8：`soc_denoise_runs`

降噪日报/批次主表。

```sql
CREATE TABLE soc_denoise_runs (
  id VARCHAR(64) PRIMARY KEY,
  workflow_run_id VARCHAR(64) REFERENCES soc_workflow_runs(id),
  report_date DATE NOT NULL,
  time_range_start TIMESTAMP NOT NULL,
  time_range_end TIMESTAMP NOT NULL,
  scope VARCHAR(256),
  source_filter VARCHAR(128),
  direction_filter VARCHAR(64),
  asset_scope VARCHAR(128),
  conclusion TEXT,
  status VARCHAR(32) NOT NULL,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL
);
```

### 表 9：`soc_denoise_metrics`

降噪阶段指标表。

```sql
CREATE TABLE soc_denoise_metrics (
  id VARCHAR(64) PRIMARY KEY,
  denoise_run_id VARCHAR(64) NOT NULL REFERENCES soc_denoise_runs(id),
  stage VARCHAR(64) NOT NULL,
  stage_label VARCHAR(128) NOT NULL,
  input_count INTEGER NOT NULL DEFAULT 0,
  output_count INTEGER NOT NULL DEFAULT 0,
  removed_count INTEGER NOT NULL DEFAULT 0,
  failed_count INTEGER NOT NULL DEFAULT 0,
  ratio DECIMAL(8,4),
  extra JSON,
  created_at TIMESTAMP NOT NULL
);
```

### 表 10：`soc_denoise_category_stats`

降噪分类贡献表。

```sql
CREATE TABLE soc_denoise_category_stats (
  id VARCHAR(64) PRIMARY KEY,
  denoise_run_id VARCHAR(64) NOT NULL REFERENCES soc_denoise_runs(id),
  category_key VARCHAR(64) NOT NULL,
  category_title VARCHAR(128) NOT NULL,
  removed_count INTEGER NOT NULL DEFAULT 0,
  kept_count INTEGER NOT NULL DEFAULT 0,
  ratio DECIMAL(8,4),
  trend VARCHAR(32),
  description TEXT,
  top_reasons JSON,
  created_at TIMESTAMP NOT NULL
);
```

### 表 11：`soc_denoise_entity_stats`

降噪二级分析通用聚合表，用于扫描源、资产、路径、状态码、规则、名单对象、process type 等。

```sql
CREATE TABLE soc_denoise_entity_stats (
  id VARCHAR(64) PRIMARY KEY,
  denoise_run_id VARCHAR(64) NOT NULL REFERENCES soc_denoise_runs(id),
  category_key VARCHAR(64) NOT NULL,
  entity_type VARCHAR(64) NOT NULL,
  entity_value VARCHAR(512) NOT NULL,
  entity_label VARCHAR(256),
  alert_count INTEGER NOT NULL DEFAULT 0,
  removed_count INTEGER NOT NULL DEFAULT 0,
  kept_count INTEGER NOT NULL DEFAULT 0,
  asset_count INTEGER,
  source_ip_count INTEGER,
  status_200_count INTEGER,
  status_404_count INTEGER,
  other_status_count INTEGER,
  sensitive_hit_count INTEGER,
  first_seen TIMESTAMP,
  last_seen TIMESTAMP,
  risk_level VARCHAR(32),
  owner VARCHAR(128),
  disposition TEXT,
  explanation TEXT,
  extra JSON,
  created_at TIMESTAMP NOT NULL
);
```

### 表 12：`soc_denoise_recommendations`

降噪阶段生成的建议动作，例如封禁、白名单候选、转研判。

```sql
CREATE TABLE soc_denoise_recommendations (
  id VARCHAR(64) PRIMARY KEY,
  denoise_run_id VARCHAR(64) NOT NULL REFERENCES soc_denoise_runs(id),
  category_key VARCHAR(64) NOT NULL,
  object_type VARCHAR(64) NOT NULL,
  object_value VARCHAR(512) NOT NULL,
  scope VARCHAR(128),
  reason TEXT,
  evidence TEXT,
  suggested_action TEXT NOT NULL,
  priority VARCHAR(16),
  owner VARCHAR(128),
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  created_action_id VARCHAR(64),
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL
);
```

### 表 13：`soc_denoise_samples`

降噪样本下钻表。

```sql
CREATE TABLE soc_denoise_samples (
  id VARCHAR(64) PRIMARY KEY,
  denoise_run_id VARCHAR(64) NOT NULL REFERENCES soc_denoise_runs(id),
  category_key VARCHAR(64) NOT NULL,
  entity_stat_id VARCHAR(64) REFERENCES soc_denoise_entity_stats(id),
  alert_id VARCHAR(64) REFERENCES soc_alerts(id),
  raw_alert_id VARCHAR(64) REFERENCES soc_raw_alerts(id),
  sample_reason TEXT,
  filter_reason TEXT,
  match_fields JSON,
  field_snapshot JSON,
  payload_excerpt TEXT,
  response_excerpt TEXT,
  created_at TIMESTAMP NOT NULL
);
```

### 表 14：`soc_dedup_clusters`

重复告警簇表。

```sql
CREATE TABLE soc_dedup_clusters (
  id VARCHAR(64) PRIMARY KEY,
  denoise_run_id VARCHAR(64) REFERENCES soc_denoise_runs(id),
  dedup_key VARCHAR(256) NOT NULL,
  representative_alert_id VARCHAR(64) REFERENCES soc_alerts(id),
  representative_title VARCHAR(256),
  duplicate_count INTEGER NOT NULL DEFAULT 0,
  strict_fields JSON,
  lsh_fields JSON,
  threshold DECIMAL(5,3),
  source_summary VARCHAR(256),
  target_summary VARCHAR(256),
  pattern TEXT,
  first_seen TIMESTAMP,
  last_seen TIMESTAMP,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL
);
```

### 表 15：`soc_incidents`

研判事件/告警簇主表。

```sql
CREATE TABLE soc_incidents (
  id VARCHAR(64) PRIMARY KEY,
  source_alert_id VARCHAR(64) REFERENCES soc_alerts(id),
  workflow_run_id VARCHAR(64) REFERENCES soc_workflow_runs(id),
  title VARCHAR(256) NOT NULL,
  priority VARCHAR(16),
  severity VARCHAR(16),
  confidence INTEGER,
  owner VARCHAR(128),
  raw_alert_count INTEGER NOT NULL DEFAULT 1,
  rule_id VARCHAR(128),
  reason TEXT,
  verdict VARCHAR(64),
  summary TEXT,
  recommendation TEXT,
  actions JSON,
  status VARCHAR(32) NOT NULL DEFAULT 'open',
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL
);
```

### 表 16：`soc_incident_evidence`

研判详情 Drawer 的步骤、证据、报告段落。

```sql
CREATE TABLE soc_incident_evidence (
  id VARCHAR(64) PRIMARY KEY,
  incident_id VARCHAR(64) NOT NULL REFERENCES soc_incidents(id),
  evidence_type VARCHAR(64) NOT NULL,
  step_order INTEGER,
  title VARCHAR(256),
  content TEXT,
  code_block TEXT,
  source VARCHAR(64),
  object_type VARCHAR(64),
  object_value VARCHAR(512),
  confidence INTEGER,
  raw_ref JSON,
  created_at TIMESTAMP NOT NULL
);
```

### 表 17：`soc_investigations`

深度调查主表。

```sql
CREATE TABLE soc_investigations (
  id VARCHAR(64) PRIMARY KEY,
  source_incident_id VARCHAR(64) REFERENCES soc_incidents(id),
  workflow_run_id VARCHAR(64) REFERENCES soc_workflow_runs(id),
  title VARCHAR(256) NOT NULL,
  severity VARCHAR(16),
  status VARCHAR(32) NOT NULL,
  owner VARCHAR(128),
  entities JSON,
  summary TEXT,
  conclusion_title VARCHAR(256),
  conclusion_text TEXT,
  recommendation TEXT,
  report_id VARCHAR(64),
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL
);
```

### 表 18：`soc_investigation_evidence`

跨设备证据链表。

```sql
CREATE TABLE soc_investigation_evidence (
  id VARCHAR(64) PRIMARY KEY,
  investigation_id VARCHAR(64) NOT NULL REFERENCES soc_investigations(id),
  source VARCHAR(64) NOT NULL,
  event_time TIMESTAMP NOT NULL,
  detail TEXT NOT NULL,
  entity_type VARCHAR(64),
  entity_value VARCHAR(512),
  raw_ref JSON,
  confidence INTEGER,
  order_index INTEGER,
  created_at TIMESTAMP NOT NULL
);
```

### 表 19：`soc_agent_sessions`

Agent 调查会话主表。

```sql
CREATE TABLE soc_agent_sessions (
  id VARCHAR(64) PRIMARY KEY,
  investigation_id VARCHAR(64) REFERENCES soc_investigations(id),
  workflow_run_id VARCHAR(64) REFERENCES soc_workflow_runs(id),
  title VARCHAR(256) NOT NULL,
  status VARCHAR(32) NOT NULL,
  entities JSON,
  started_at TIMESTAMP,
  finished_at TIMESTAMP,
  conclusion TEXT,
  generated_actions JSON,
  generated_report_id VARCHAR(64),
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL
);
```

### 表 20：`soc_agent_session_messages`

Agent 会话消息、子任务和工具调用记录表。

```sql
CREATE TABLE soc_agent_session_messages (
  id VARCHAR(64) PRIMARY KEY,
  session_id VARCHAR(64) NOT NULL REFERENCES soc_agent_sessions(id),
  role VARCHAR(32) NOT NULL,
  sender VARCHAR(128) NOT NULL,
  message_time TIMESTAMP,
  order_index INTEGER NOT NULL,
  content TEXT NOT NULL,
  delegate_title VARCHAR(256),
  delegate_description TEXT,
  delegate_status VARCHAR(32),
  delegate_elapsed_ms INTEGER,
  delegate_step_count INTEGER,
  sub_session_id VARCHAR(64),
  tool_calls JSON,
  conclusion TEXT,
  created_at TIMESTAMP NOT NULL
);
```

### 表 21：`soc_response_actions`

响应处置动作表。

```sql
CREATE TABLE soc_response_actions (
  id VARCHAR(64) PRIMARY KEY,
  source_type VARCHAR(32) NOT NULL,
  source_id VARCHAR(64) NOT NULL,
  priority VARCHAR(16),
  object_type VARCHAR(64),
  object_value VARCHAR(512) NOT NULL,
  action TEXT NOT NULL,
  evidence TEXT,
  owner VARCHAR(128),
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  ticket_id VARCHAR(128),
  approval_required BOOLEAN NOT NULL DEFAULT FALSE,
  executor_type VARCHAR(64),
  executed_at TIMESTAMP,
  result TEXT,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL
);
```

## 表数量汇总

最终建议落地 21 张表：

| 类别 | 表 |
|---|---|
| 告警接入与规范化 | `soc_alert_sources`, `soc_raw_alerts`, `soc_alerts`, `soc_alert_http_details` |
| 资产与情报上下文 | `soc_assets`, `soc_threat_intel_observations` |
| Workflow 关联 | `soc_workflow_runs` |
| 降噪分析 | `soc_denoise_runs`, `soc_denoise_metrics`, `soc_denoise_category_stats`, `soc_denoise_entity_stats`, `soc_denoise_recommendations`, `soc_denoise_samples`, `soc_dedup_clusters` |
| 告警研判 | `soc_incidents`, `soc_incident_evidence` |
| 深度调查 | `soc_investigations`, `soc_investigation_evidence` |
| Agent 调查过程 | `soc_agent_sessions`, `soc_agent_session_messages` |
| 响应处置 | `soc_response_actions` |

## 推荐索引

```sql
CREATE INDEX idx_soc_raw_alerts_source_time ON soc_raw_alerts(source_id, event_time);
CREATE INDEX idx_soc_alerts_event_time ON soc_alerts(event_time);
CREATE INDEX idx_soc_alerts_triage_status ON soc_alerts(triage_status, priority, event_time);
CREATE INDEX idx_soc_alerts_src_dst ON soc_alerts(src_ip, dst_ip, event_time);
CREATE INDEX idx_soc_alerts_dedup_key ON soc_alerts(dedup_key);
CREATE INDEX idx_soc_denoise_runs_date ON soc_denoise_runs(report_date, scope);
CREATE INDEX idx_soc_denoise_entity_stats_lookup ON soc_denoise_entity_stats(denoise_run_id, category_key, entity_type);
CREATE INDEX idx_soc_incidents_status ON soc_incidents(status, priority, created_at);
CREATE INDEX idx_soc_investigations_status ON soc_investigations(status, severity, created_at);
CREATE INDEX idx_soc_response_actions_status ON soc_response_actions(status, priority, created_at);
```

## 页面到表的读取关系

| 页面/抽屉 | 主要读取表 |
|---|---|
| 顶部漏斗 | `soc_denoise_metrics`, `soc_incidents`, `soc_investigations`, `soc_response_actions` |
| 降噪分析 | `soc_denoise_runs`, `soc_denoise_metrics`, `soc_denoise_category_stats` |
| 扫描告警分析 | `soc_denoise_entity_stats`, `soc_denoise_recommendations`, `soc_denoise_samples`, `soc_assets` |
| 重复告警分析 | `soc_dedup_clusters`, `soc_denoise_samples` |
| 条件/规则/黑白名单 | `soc_denoise_entity_stats`, `soc_denoise_samples`, `soc_denoise_recommendations` |
| 告警研判列表 | `soc_incidents`, `soc_alerts`, `soc_alert_http_details`, `soc_assets`, `soc_threat_intel_observations` |
| 告警研判 Drawer | `soc_incidents`, `soc_incident_evidence`, `soc_alert_http_details`, `soc_assets`, `soc_threat_intel_observations` |
| 深度调查列表 | `soc_investigations`, `soc_investigation_evidence` |
| 深度调查 Drawer | `soc_investigations`, `soc_investigation_evidence`, `soc_response_actions` |
| Agent 调查过程 Drawer | `soc_agent_sessions`, `soc_agent_session_messages` |
| 响应处置 | `soc_response_actions` |

## 落地建议

1. 第一阶段先落地 `soc_raw_alerts`、`soc_alerts`、`soc_workflow_runs`、降噪 7 张表和 `soc_response_actions`，即可支撑顶部漏斗、降噪分析和响应处置。
2. 第二阶段落地 `soc_incidents`、`soc_incident_evidence`、`soc_assets`、`soc_threat_intel_observations`，支撑告警研判列表与详情 Drawer。
3. 第三阶段落地 `soc_investigations`、`soc_investigation_evidence`、`soc_agent_sessions`、`soc_agent_session_messages`，支撑深度调查和 Agent 调查过程。
4. 原始告警和 Workflow 输出不要直接作为页面接口返回大 JSON；页面读取聚合表，抽屉页按需下钻样本和证据。
