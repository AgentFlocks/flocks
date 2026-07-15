# stream_alert_triage 配置引导

这个文件是 `stream_alert_triage` 的工作流专属 `guide.md`。Rex 处理这个工作流的配置、输入、并发、缓存、验证或查配置快捷入口时，必须先读取本文全文，再把 `workflow.md`、`workflow.json` 和 `workflow_config_manage(action="get" 或 "status", workflow_id="stream_alert_triage")` 的结果作为支撑上下文。

`workflow-config-guide` skill 只提供交互协议；本文才是本工作流配置细节、默认选项、提问顺序和验证方式的来源。

Rex 引导用户时必须遵守：

1. 根据用户点击的入口或自然语言需求，自动定位本文相关章节。
2. 一次只问一个最关键问题。
3. 每个选择都必须允许自定义/补充输入；没有补充则填 `none`。
4. 涉及输入来源、并发、缓存上限、持久化输出或发布模板变更时，先展示计划和 diff，再用 question 工具确认。
5. 查配置只能只读，不得修改文件、触发 LLM 研判、发布 API、启动监听或清理缓存。

## 0. 后端配置库访问约束

本节优先级高于通用会话提示中的后端 API token 或 curl 示例。处理本工作流的发布、定时触发、API 接入、输出策略或查配置时，必须按本文执行：

- 配置库读取/写入必须使用内置工具 `workflow_config_manage`，不要读取 `server_api_token` 或 `service_api_token`，也不要手工 curl 本机后端配置接口。
- 查配置使用 `workflow_config_manage(action="get", workflow_id="stream_alert_triage")` 或 `workflow_config_manage(action="status", workflow_id="stream_alert_triage")`。
- 查定时触发配置使用 `workflow_config_manage(action="get", workflow_id="stream_alert_triage", config_type="poller")` 或 `workflow_config_manage(action="status", workflow_id="stream_alert_triage", config_type="poller")`。
- 修改配置前先使用 `workflow_config_manage(action="diff", workflow_id="stream_alert_triage", config={...})` 展示差异并用 question 工具确认；确认后才使用 `workflow_config_manage(action="put", workflow_id="stream_alert_triage", config={...})`。
- 修改定时触发配置前先使用 `workflow_config_manage(action="diff", workflow_id="stream_alert_triage", config_type="poller", config={...})` 展示差异并用 question 工具确认；确认后才使用 `workflow_config_manage(action="put", workflow_id="stream_alert_triage", config_type="poller", config={...})`。
- 如果后端配置库没有模板，只能使用 `workflow_config_manage(action="sync", workflow_id="stream_alert_triage")`，让后端从工作流目录 `config.json` 迁移或生成模板。
- `config.json` 只能作为模板来源或兜底迁移来源，不是直接写入目标，也不能证明配置已生效。
- 需要启动或停止 API 服务、定时触发或其它运行态能力时，必须使用对应运行态接口；不要通过修改模板字段冒充运行态状态。
- 如果 `workflow_config_manage` 不可用、返回未授权、拒绝访问、连接失败或后端不可达，必须停止配置流程，明确说明本次未应用、未发布、未启动；如已生成目标配置，只能保存草稿到 outputs，不要继续读取 token 或改写 `config.json`。

## 1. 工作流定位

- 工作流 ID：`stream_alert_triage`
- 工作流名称：`stream_alert_denoise` 的下游批量研判 Pipeline。
- 主要用途：读取 `stream_alert_denoise` 写出的 `dedup_result_NNN.jsonl`，按 `dedup_key` 做 leader/follower 分组，只对每组 leader 执行研判，followers 复用 leader 结果。
- 当前状态：`meta.json` 标记为 `active`。
- 当前发布状态：`workflow.json` 中 `triggers` 为空；工作流目录有 `config.json` 用于声明默认持久化策略。默认按手动运行或 API run 输入来引导。

本工作流适合：

- 对降噪后的 HTTP 告警做攻击研判。
- 复用 `stream_alert_denoise` 的 `dedup_key` 降低 LLM 调用。
- 按日期重放某天全部去重结果。
- 默认只把明确 `is_duplicate=false`、包含 `dedup_key` 且批内首次出现的研判告警写入 `~/.flocks/data/soc.db`，并保留 JSONL 可选输出，供归档或下游工作流消费。

本工作流不适合：

- 直接接收原始 TDP / SkyEye 告警。
- 执行上游过滤、字段归一化或 LSH 去重。
- 调用或嵌入 `tdp_alert_triage` 子工作流。
- 生成每条告警一个独立 markdown 报告文件。

## 2. AI 引导方式

如果用户点击输入入口，优先确认要读哪些 `dedup_result_NNN.jsonl`；如果用户点击规则入口，优先确认并发和缓存策略；如果用户点击样例入口，优先做文件格式检查，不要直接触发 LLM 研判。

推荐提问顺序：

1. 你要读取上游本次输出文件、单个文件、某个日期，还是默认读取今天？
2. 是否显式设置 `concurrency=1`，还是确认提高到 2 到 5？
3. 是否保持 `max_triage_cache_size=100000` 和 `triage_output_mode=soc_db`，还是改为 JSONL / both / none？
4. 是否开启定时触发；默认建议每 3 分钟执行一次，并显式使用 `concurrency=1`。
5. 是否只做轻量文件检查，还是确认执行真实研判？
6. 是否保存配置草稿、应用发布模板，或暂不修改？

如果用户只问“查一下现在怎么配的”，不要提问，直接按第 9 节只读检查。

## 3. 输入模式

工作流代码支持四种输入定位方式，解析优先级固定为：

1. `input_paths`: 显式 JSONL 文件路径列表，推荐直接使用 `stream_alert_denoise.outputs.output_paths`。
2. `input_path`: 单个 JSONL 文件路径，通常来自 `stream_alert_denoise.outputs.output_path`。
3. `input_date`: `YYYY-MM-DD`，自动读取该日 `stream_alert_denoise` 输出目录下全部 `dedup_result_*.jsonl`。
4. 全部不传：默认读取今天目录下全部 `dedup_result_*.jsonl`。

上游默认输出目录：

```text
~/.flocks/workspace/workflows/stream_alert_denoise/<YYYY-MM-DD>/dedup_result_NNN.jsonl
```

输入模式建议：

| 模式 | 适用场景 | 推荐输入 |
| --- | --- | --- |
| 上游本次输出 | 刚跑完 `stream_alert_denoise`，需要立即研判本批首见告警 | `input_paths = denoise.outputs.output_paths` |
| 单文件重放 | 只检查某个文件或某个序号文件 | `input_path = ".../dedup_result_001.jsonl"` |
| 按日期重放 | 对某天所有去重结果统一研判 | `input_date = "YYYY-MM-DD"` |
| 今日默认 | 调试或日常手动执行 | 不传 `input_*`，但先确认今天目录存在文件 |

默认推荐：上游本次输出文件。如果用户没有上游结果，再推荐 `input_date`。

互斥关系：

- `input_paths` 和 `input_path` 可以同时传，但会合并并按顺序去重。
- 只要显式路径存在，就不会再按 `input_date` 自动发现。
- 传入不存在的路径会被跳过，不会报错中止，但 `load_stats` 会显示实际读取为 0。

## 4. 来源形态

真实来源是 `stream_alert_denoise` 的 JSONL 输出。每个文件形态：

- 第一行：`{"_type":"file_header", ...}`，`load_dedup_file` 会跳过。
- 后续每行：一条 JSON 告警。
- 关键字段：`dedup_key`、`is_duplicate`、`_lsh_cluster_id`、`_source_type`、`_process_type`、`sip`、`dip`、`req_http_url`、`req_body`、`rsp_body`、`threat_name`。

`load_dedup_file` 输出：

| 字段 | 说明 |
| --- | --- |
| `enriched_alerts` | 从 JSONL 读出的告警列表 |
| `loaded_files` | 实际读取到的文件路径 |
| `load_stats` | 文件数、记录数、跳过 header 数、坏行数 |
| `concurrency` | 下游外层并发参数 |
| `max_triage_cache_size` | 下游研判缓存上限 |
| `input_date` | 实际日期字符串 |

重要约束：

- 如果告警缺少 `dedup_key`，该告警会作为独立 work unit 研判，无法和其它告警复用。
- `is_duplicate` 不决定是否研判。缓存命中策略只依赖 `dedup_key`。
- `is_duplicate` 决定是否允许写入 SOC DB：只有明确为 `false` 的告警才有资格持久化；缺少该字段时按“未证明首见”处理，不写入 SOC DB。
- SOC DB 持久化要求非空 `dedup_key`：批内只接受第一条，数据库再通过唯一索引保证跨执行全局唯一；无 `dedup_key` 的防御性研判结果不会写入 SOC DB。
- `stream_alert_denoise` 只会把跨批次首见告警写入 JSONL；因此常规情况下本工作流读取到的是适合继续研判的首见告警。
- 如果用户手工构造 JSONL，必须保证每行是独立 JSON 对象，不能是整文件 JSON 数组。

## 5. 输出去向

工作流返回：

| 输出字段 | 说明 |
| --- | --- |
| `enriched_alerts_with_triage` | 每条输入告警加上研判字段后的完整列表 |
| `triage_results` | 精简研判结果列表，不含 markdown 正文 |
| `triage_stats` | leader/follower、cache、并发、耗时、verdict 分布等统计 |
| `load_stats` | 输入文件加载统计 |
| `loaded_files` | 实际读取的上游文件 |
| `input_date` | 本次读取日期 |
| `triage_output_mode` | 本次生效的输出模式：`soc_db` / `jsonl` / `both` / `none` |
| `soc_db_result` / `soc_db_path` | 本次写入的 SOC DB 结果和路径；结果区分 `inserted_rows` 与 `updated_rows` |
| `output_paths` | 本次写入的研判 JSONL 文件列表；未启用 JSONL 时为空 |
| `output_dir` | 研判 JSONL 结果目录；未启用 JSONL 时为空 |
| `summary_report` | markdown 总览文本 |
| `summary_path` | 总览 markdown 落盘路径 |
| `top_attack_verdict` / `top_risk_level` / `top_report_title` / `top_triage_report` | top-risk 告警研判字段 |

每条告警追加：

- `has_dedup_key`
- `triage_source`
- `triage_status`
- `attack_verdict`
- `risk_level`
- `report_title`
- `triage_report`
- `attack_success`
- `triage_ms`
- `triage_error`

默认 SOC DB 输出：

```text
~/.flocks/data/soc.db
```

默认写入表：

```text
alert_records
```

可选研判 JSONL 输出目录：

```text
~/.flocks/workspace/workflows/stream_alert_triage/<YYYY-MM-DD>/triage_result_NNN.jsonl
```

写入规则：

- SOC DB 只接受明确 `is_duplicate=false`、包含非空 `dedup_key` 且该 key 在本批次首次出现的告警。
- `is_duplicate=true`、缺少 `is_duplicate`、缺少 `dedup_key` 或批内重复 `dedup_key` 的告警均不会写入 SOC DB。
- `alert_records.dedup_key` 使用部分唯一索引保证跨批次、跨执行只能存在一条告警记录。
- 已存在的 `dedup_key` 再次回放时只更新研判字段和研判运行标记，不覆盖首次告警的时间、来源、行号、标识与原始事件字段。
- SOC DB 建表、迁移或写入失败会使本次工作流失败，不会以“成功但写入 0 条”结束。
- `soc_db_result` 记录本次持久化总数、新增数与更新数；`triage_stats.soc_db_filter_stats` 记录候选数及各类跳过数。
- `triage_output_mode=soc_db`：默认写入 `soc.db`，不写 JSONL。
- `triage_output_mode=jsonl`：只写 `triage_result_NNN.jsonl`，不写 `soc.db`。
- `triage_output_mode=both`：同时写 `soc.db` 和 JSONL。
- `triage_output_mode=none`：不写 `soc.db` 和 JSONL，但仍可能写缓存。
- 旧参数 `persist_triage_output=true` 仍兼容：当 `triage_output_mode=soc_db` 时会额外写 JSONL，相当于 `both`。
- 每个文件第一行是 file header，包含 `workflow`、`seq`、`run_id`、`batch_total`、`batch_triaged`、`batch_followers_reused`、`batch_cache_hit`、`batch_triage_failed`。
- 每个文件最多 10000 条告警记录。
- `.triage_counter.json` 记录当前文件序号和条数。
- 未启用 JSONL 时不写 `triage_result_NNN.jsonl`，但仍可能触发 LLM 和缓存写入，除非全部 cache 命中或没有输入。

总览报告输出：

```text
~/.flocks/workspace/outputs/<YYYY-MM-DD>/artifacts/stream_alert_triage_summary.md
```

注意：

- 每条告警完整 markdown 在 `triage_report` 字段中。
- 不存在 `report_path`。
- 不会生成 `triage_report_*.md` 这类单告警 markdown 文件。

- `triage_report` 是带语义标签的 markdown 字符串，根标签为 `<triage_report version="soc.triage.markdown.v1">`。
- 前端应按 `<report_title>`、`<report_meta>`、`<analysis_steps>`、`<triage_conclusion>`、`<attack_payload>`、`<payload_explanation>`、`<response_evidence>`、`<key_evidence>`、`<disposal_recommendation>` 切块后渲染标签内 markdown。
- 工作流的报告生成 prompt 已包含攻击成功和攻击失败 few-shot；如果 LLM 未按标签输出，会自动使用确定性 fallback。


## 6. 处理规则

默认参数：

| 参数 | 推荐默认 | 代码行为和说明 |
| --- | --- | --- |
| `input_paths` | 无 | 显式路径列表，优先级最高 |
| `input_path` | 无 | 单个显式路径 |
| `input_date` | 今天 | 自动发现该日所有上游 `dedup_result_*.jsonl` |
| `concurrency` | `1` | `workflow.md` 和 metadata 推荐 1；`concurrent_triage` 会限制到 1 到 5 |
| `max_triage_cache_size` | `100000` | 小于 1 时回退 100000 |
| `triage_output_mode` | `soc_db` | 输出模式：`soc_db` / `jsonl` / `both` / `none` |
| `soc_db_path` | `~/.flocks/data/soc.db` | 默认 SOC DB 写入位置 |
| `persist_triage_output` | `false` | 旧兼容参数；设为 `true` 会在 `soc_db` 模式下额外写 JSONL |
| `jsonl_output_dir` | 空 | 可选 JSONL 输出目录；为空时使用工作流默认日期目录 |

并发注意：

- 外层 `ThreadPoolExecutor(max_workers=concurrency)` 处理 unique work units。
- 内层每个 leader 会用 4 路并行 LLM 分支：`survey`、`cve_related`、`cve_info`、`payload_analysis`。
- 稳态 LLM 峰值约为 `concurrency * 4`。
- 配置引导应默认显式给出 `concurrency=1`。如果用户要提高到 2 到 5，先说明 LLM 并发和上游工具压力，再确认。
- 当前 `load_dedup_file` 节点在完全不传 `concurrency` 时会输出 5；因此引导和样例中应显式传 `concurrency=1`，避免与文档推荐值不一致。

leader/follower 规则：

- 按 `dedup_key` 分组。
- 每个分组首条为 leader。
- leader 负责真实研判。
- follower 复制 leader 的 `attack_verdict`、`risk_level`、`report_title`、`triage_report`、`attack_success`。
- 无 `dedup_key` 告警各自独立研判，`triage_source` 会是 `no_dedup_key_triaged` 或 `no_dedup_key_failed`。

研判缓存：

```text
~/.flocks/workspace/workflows/stream_alert_triage/triage_cache.pkl
~/.flocks/workspace/workflows/stream_alert_triage/triage_cache.lock
```

- key：`dedup_key`。
- value：`attack_verdict`、`risk_level`、`report_title`、`triage_report`、`attack_success`。
- cache 命中时只有在 `triage_report` 为 `soc.triage.markdown.v1` 标签化 markdown 时才直接复用，不调用 LLM。
- 旧 `final_report` 缓存会按 miss 重新研判并写回新版字段。
- cache 未命中时 leader 执行完整内联研判。
- 新结果会合并写回 cache，文件锁 + 原子落盘。
- 淘汰策略是 FIFO LRU，超过 `max_triage_cache_size` 时丢弃最旧条目。

研判逻辑：

- 本工作流不调用 `tdp_alert_triage` 子工作流。
- 研判逻辑是内联实现，语义同源于 `tdp_alert_triage` 文档版本。
- 单条 leader 会执行情报准备、4 路 LLM 分析、攻击状态判断、verdict 归一化、标题生成和 markdown 聚合。
- 如果后续要接入真实 TDP 平台检索或页面调查，必须按 `tdp-use` skill 处理，不得绕过对应 skill 直接调用 TDP 工具。

低层参数默认隐藏，不主动询问：LLM timeout、retry、verdict 映射、JSONL counter 文件名、file lock 细节。只有用户明确排障时再解释。

## 7. 样例验证

推荐样例 1：上游本次输出文件。

```json
{
  "input_paths": [
    "~/.flocks/workspace/workflows/stream_alert_denoise/2026-05-18/dedup_result_001.jsonl"
  ],
  "concurrency": 1,
  "max_triage_cache_size": 100000,
  "triage_output_mode": "soc_db"
}
```

推荐样例 2：按日期重放。

```json
{
  "input_date": "2026-05-18",
  "concurrency": 1,
  "max_triage_cache_size": 100000,
  "triage_output_mode": "soc_db"
}
```

轻量验证优先做只读检查：

1. 路径是否存在。
2. 首行是否为 `_type=file_header` 或第一条 JSON 告警。
3. 后续每行是否能按 JSON 对象解析。
4. 是否至少有 `dedup_key`、`sip`、`dip`、`req_http_url`、`threat_name` 中的关键字段。
5. 按 `dedup_key` 估算 unique work units 和 follower 数。
6. 预估 `concurrency * 4` 的 LLM 峰值。

真实执行验证注意：

- 只要存在 cache miss，就会触发 LLM 和情报工具调用。
- `triage_output_mode=none` 只是不写 SOC DB 和 JSONL，不代表不会调用 LLM，也不代表不会写 `triage_cache.pkl`。
- `persist_triage_output=false` 只是旧 JSONL 开关为关；默认仍会按 `triage_output_mode=soc_db` 写入 SOC DB。
- 如果要避免外部副作用，先只做文件解析和字段检查，不运行工作流。
- 如果用户确认运行，建议用 1 到 3 条样例告警、`concurrency=1`、明确是否允许写缓存和输出文件。

最小期望输出：

- `load_stats.record_count > 0`
- `triage_stats.total == load_stats.record_count`
- `triage_stats.work_units <= triage_stats.total`
- `enriched_alerts_with_triage[*].triage_report` 存在于已研判或缓存命中的告警上
- `soc_db_result.rows` 等于本次持久化的候选数（`inserted_rows + updated_rows`）；应与 `triage_stats.soc_db_first_seen_rows` 一致，除非 `triage_output_mode=jsonl/none`
- `triage_stats.soc_db_skipped_rows` 等于输入总数减去首见唯一告警数，并可通过 `triage_stats.soc_db_filter_stats` 查看具体跳过原因
- `output_paths` 指向当日 `triage_result_NNN.jsonl`，仅在 `triage_output_mode=jsonl/both` 或旧参数 `persist_triage_output=true` 时存在
- `summary_path` 指向 `outputs/<today>/artifacts/stream_alert_triage_summary.md`

## 8. 应用方式

当前工作流目录包含 `config.json` 作为默认运行配置，默认输出到 `~/.flocks/data/soc.db`，不写 JSONL；`workflow.json` 中 `triggers` 为空。配置引导应把它当作“已有输出默认值、尚未声明发布/触发模板”的工作流。

如果用户要配置运行入口：

1. 优先引导为手动运行或 API run 输入参数模板。
2. 如果用户要发布成 API 服务，应使用 `workflow_config_manage(action="get" 或 "sync" 或 "diff" 或 "put", workflow_id="stream_alert_triage")` 流程。
3. 如果用户要开启定时触发，默认建议 3 分钟一次；应用前必须确认触发输入来源、输出模式、是否允许写入 `soc.db` 和是否允许触发 LLM，并使用 `workflow_config_manage(action="get" -> "diff" -> "put", workflow_id="stream_alert_triage", config_type="poller")` 读取和写入 poller 配置。
4. 如果需要扩展工作流目录下的 `config.json`，必须使用 runtime 消费的结构：`kind: workflow.integration-config`，顶层包含 `publish` 和 `triggers`。
5. 不要生成旧的 `publishTemplates` wrapper。
6. 不要直接写 `config.json` 来表示发布、接入或触发配置已经生效。
7. 启停、发布、取消发布等运行态动作必须调用运行时接口。
8. 不要读取 `server_api_token`，不要用 curl 调 `/api/workflow/stream_alert_triage/poller-config` 读取或写入定时配置。
9. 如果后端配置接口不可用，只能把目标配置保存为草稿到 outputs，并明确说明未应用、未发布、未启动。

应用变更前必须展示：

- 计划。
- 输入参数或 publish / triggers 模板 diff。
- poller 配置变更时必须展示 `workflow_config_manage(config_type="poller")` 生成的 diff。
- 是否会触发 LLM、情报工具、`triage_cache.pkl` 写入、`soc.db` 写入、`triage_result_NNN.jsonl` 写入。
- question 工具确认：应用、保存草稿或暂不修改。
- 用户确认应用后，使用 `workflow_config_manage(action="put", workflow_id="stream_alert_triage", config_type="<type>", config={...})` 写入完整配置。

不要通过删除 `triage_cache.pkl` 来“重置配置”。缓存清理是运行数据操作，必须单独说明影响并取得确认。

## 9. 查配置

只读检查顺序：

1. 读取本文。
2. 读取 `workflow.md` 和 `workflow.json`。
3. 调用 `workflow_config_manage(action="get", workflow_id="stream_alert_triage")` 或 `workflow_config_manage(action="status", workflow_id="stream_alert_triage")`。
4. 调用 `workflow_config_manage(action="get", workflow_id="stream_alert_triage", config_type="poller")` 或 `workflow_config_manage(action="status", workflow_id="stream_alert_triage", config_type="poller")`。
5. 如果后端无配置，再检查工作流目录是否有 `config.json`。
6. 汇总已配置项、缺失项和最推荐下一步。

查配置时重点报告：

- 当前是否存在 `config.json` 或后端配置，默认输出方式是否为 `soc_db`。
- `workflow.json.triggers` 是否为空。
- 是否已经配置定时触发；如果没有，说明默认推荐是每 3 分钟一次，但需要用户确认后才应用。
- 推荐输入方式：`input_paths`、`input_path`、`input_date` 或今日默认。
- 推荐显式设置 `concurrency=1`。
- 当前缓存路径和上限配置，但不要读取或修改大型 pickle 内容，除非用户明确要求排障。
- 输出路径：SOC DB 默认在 `~/.flocks/data/soc.db`；triage JSONL 可选在 `~/.flocks/workspace/workflows/stream_alert_triage/<YYYY-MM-DD>/`；总览报告在 `~/.flocks/workspace/outputs/<YYYY-MM-DD>/artifacts/`。

查配置不得修改文件、触发 LLM、调用情报工具、写缓存、启动监听、发布 API 或停止服务。
