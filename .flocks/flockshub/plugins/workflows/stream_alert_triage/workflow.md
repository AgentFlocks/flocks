# stream_alert_triage

stream_alert_triage 是 NDR 告警流并发研判 Pipeline。

核心能力：
- 读取上游 stream_alert_denoise 去重输出
- 同批次 dedup_key 相同 → 只研判 leader，follower 复用结果
- 跨批次 dedup_key 命中缓存 → 直接复用历史研判，不调 LLM
- 4 个 LLM 分支（survey / cve_related / cve_info / payload_analysis），共享运行级并发预算
- 研判产物仅写入 triage_report 字段，不生成独立报告文件

**完全自包含**，研判逻辑直接内联，不依赖也不嵌入 `tdp_alert_triage`。

## 核心特性

* **跨批次复用**：dedup_key 命中持久化 cache 时直接复用历史 verdict/title/triage_report，不调 LLM
* **同批次去重**：批内多条 alert 共享 dedup_key 时，只对 **leader（首条）研判**，follower 广播复用 leader 结果
* **保留 4 个 LLM 分支**（survey / cve_related / cve_info / payload_analysis）— 与 `tdp_alert_triage` 完全相同的研判语义；所有 LLM 调用共享运行级并发预算，避免与外层 work unit 并发相乘
* **研判产物仅以字段形式附加**到每条 alert（`triage_report` 字段含带语义标签的完整 markdown），**不生成任何独立的 per-alert 报告文件**

## 与上游的关系

## 1. 功能概览

基本信息:

- 工作流 ID: `stream_alert_triage`
- 工作流目录: `~/.flocks/plugins/workflows/stream_alert_triage/`
- 分类: `default`
- 状态: `active`
- 入口节点: load_dedup_file (Python)
- 终点节点: summarize (Python)
- 生成时间: 2026/6/24 15:29:18

适合在这里写清楚:

- 这个工作流解决什么问题。
- 适合处理什么输入。
- 不负责处理什么边界场景。

## 2. 原理和总体流程

核心原理是把输入按节点顺序逐步加工，每个节点只负责一个清晰职责。流程顺序如下:

```text
load_dedup_file -> concurrent_triage -> summarize
```

流程表:

| 顺序 | 节点 | 做什么 | 下一步 |
| --- | --- | --- | --- |
| 1 | load_dedup_file | 一次性读取 stream_alert_denoise 写入的 JSONL 文件。输入优先级：input_paths > input_path > input_date（自动遍历该日所有 dedup_result_*.jsonl）> 当日默认。跳过 file_header 行，输出 enriched_alerts (list[dict])。 | concurrent_triage |
| 2 | concurrent_triage | Leader/follower 分组并发研判节点（自包含，内联 tdp_alert_triage 逻辑）。先按 dedup_key 把 alerts 分组：每组只对 leader 研判，follower 复用 leader 结果。外层 ThreadPoolExecutor(concurrency) 处理 unique work units（concurrency 取值 1–5，默认 1）；单条告警仍执行 survey / cve_related / cve_info / payload_analysis 4 个分支，但所有 `llm.ask()` 共享运行级 concurrency 预算，因此总 LLM 峰值不超过 1–5，不再与分支数相乘。dedup_key 在 triage_cache.pkl 命中时直接复用历史 verdict/title/triage_report；未命中则 leader 执行完整研判（情报查询 + 4 个 LLM 分支 + attack_analysis + verdict + title + 聚合 markdown），完整研判 markdown 仅写入 alert 的 `triage_report` 字段，**不生成任何独立报告文件**。新结果合并写回 cache（FIFO LRU + 文件锁 + 原子落盘）。SOC DB 持久化只接受明确 `is_duplicate=false`、包含 `dedup_key` 且批内首次出现的告警，并通过数据库唯一索引保证跨执行全局唯一；重复 key 只更新研判字段并保留首次事件元数据，持久化失败会使工作流失败。可通过工作流目录 `config.json` 或运行输入将 `triage_output_mode` 切换为 `jsonl` / `both` / `none`，保留 `triage_result_NNN.jsonl` 可选输出。 | summarize |
| 3 | summarize | 汇总输出：写 pipeline_summary.md 到 ~/.flocks/workspace/outputs/<today>/artifacts/，暴露 top-risk 告警的 verdict/title/triage_report 作为工作流的 final outputs。 | 工作流最终输出 |

编辑流程结构时，要同时确认节点顺序、边关系、字段映射和最终输出是否仍然一致。

## 3. 输入说明

本章用于说明工作流接受什么输入，以及入口节点如何理解这些输入。

当前工作流保存了这些样例输入，可以先照着这些字段测试:

- _comment_input: 三选一：input_paths（来自 stream_alert_denoise.outputs.output_paths）/ input_path（来自 stream_alert_denoise.outputs.output_path）/ ...
- input_date: 2026-05-18
- concurrency: 1
- max_triage_cache_size: 100000
- persist_triage_output: false
- triage_output_mode: soc_db
- _comment_output: 默认只接受明确 is_duplicate=false、包含 dedup_key 且批内首次出现的告警，并由 soc.db 保证 dedup_key 跨执行全局唯一；如需 JSONL，设置 triage_output_mode=jsonl 或 both。
- _comment_dedup: 同批次内多条 alert 共享 dedup_key 时只 LLM 研判 1 次（leader），其余 follower 直接复用结果；跨批次/跨进程的复用由 triage_cache.pkl 提供。
- _comment_cache: 研判缓存位于 ~/.flocks/workspace/workflows/stream_alert_triage/triage_cache.pkl，FIFO LRU，文件锁 + 原子落盘，可跨进程/跨执行复用。dedup_key 即 str...

修改输入时，至少同步检查:

- 入口节点是否能读取新字段。
- 样例输入是否覆盖主要场景。
- 下游节点是否还在引用旧字段名。
- 发布方式中的参数说明是否需要更新。

## 4. 模块逻辑

本章按执行顺序解释每个节点。修改内部逻辑时，优先定位到对应节点，再检查它的上下游关系。

### 4.1 load_dedup_file

职责: 一次性读取 stream_alert_denoise 写入的 JSONL 文件。输入优先级：input_paths > input_path > input_date（自动遍历该日所有 dedup_result_*.jsonl）> 当日默认。跳过 file_header 行，输出 enriched_alerts (list[dict])。

- 节点类型: Python
- 输入来源: 工作流输入 / 触发器输入
- 输出去向: concurrent_triage
- 编辑重点: 修改去重阈值、状态保存、结果落盘路径或输出格式时，优先编辑这里。
- 上游关系: 从工作流输入开始
- 下游关系: load_dedup_file -> concurrent_triage

### 4.2 concurrent_triage

职责: Leader/follower 分组并发研判节点（自包含，内联 tdp_alert_triage 逻辑）。先按 dedup_key 把 alerts 分组：每组只对 leader 研判，follower 复用 leader 结果。外层 ThreadPoolExecutor(concurrency) 处理 unique work units（concurrency 取值 1–5，默认 1）；单条告警仍执行 survey / cve_related / cve_info / payload_analysis 4 个分支，但所有 `llm.ask()` 共享运行级 concurrency 预算，因此总 LLM 峰值不超过 1–5，不再与分支数相乘。dedup_key 在 triage_cache.pkl 命中时直接复用历史 verdict/title/triage_report；未命中则 leader 执行完整研判（情报查询 + 4 个 LLM 分支 + attack_analysis + verdict + title + 聚合 markdown），完整研判 markdown 仅写入 alert 的 `triage_report` 字段，**不生成任何独立报告文件**。新结果合并写回 cache（FIFO LRU + 文件锁 + 原子落盘）。SOC DB 持久化只接受明确 `is_duplicate=false`、包含 `dedup_key` 且批内首次出现的告警，并通过数据库唯一索引保证跨执行全局唯一；重复 key 只更新研判字段并保留首次事件元数据，持久化失败会使工作流失败。可通过工作流目录 `config.json` 或运行输入将 `triage_output_mode` 切换为 `jsonl` / `both` / `none`，保留 `triage_result_NNN.jsonl` 可选输出。

- 节点类型: Python
- 输入来源: load_dedup_file
- 输出去向: summarize
- 编辑重点: 修改去重阈值、状态保存、结果落盘路径或输出格式时，优先编辑这里。
- 上游关系: load_dedup_file -> concurrent_triage
- 下游关系: concurrent_triage -> summarize

### 4.3 summarize

职责: 汇总输出：写 pipeline_summary.md 到 ~/.flocks/workspace/outputs/<today>/artifacts/，暴露 top-risk 告警的 verdict/title/triage_report 作为工作流的 final outputs。

- 节点类型: Python
- 输入来源: concurrent_triage
- 输出去向: 工作流最终输出
- 编辑重点: 修改此步骤的输入、输出或执行逻辑时，先确认上下游字段是否同步变化。
- 上游关系: concurrent_triage -> summarize
- 下游关系: 输出工作流结果

## 5. 输出说明

本章用于维护工作流最终返回什么，以及是否产生额外副作用。

输出说明建议包含:

- 返回给用户或调用方的核心字段。
- 给下游系统继续消费的结构化字段。
- 是否写文件、发通知、调用外部系统或更新状态。
- 没有结果、部分失败、完全失败时分别返回什么。

如果还不确定输出格式，先用一条样例跑通，再把真实返回字段补到这里。

## 6. 发布方式

发布页会根据 `config.json` 模板和运行时状态决定展示哪些能力；`workflow.md` 只负责解释这些能力的用途。

当前 `workflow.json` 里配置了这些触发器:

- syslog-default: syslog，启用

发布相关编辑原则:

- 改展示模板: 修改 `config.json`。
- 改运行启停状态: 通过发布页或后端运行时状态处理。
- 改参数语义: 同步更新本章、输入说明和相关节点。
- 不要把明文密钥、长期 token 或私人路径写进 `workflow.md` 或 `config.json`。

## 7. 编辑指南

先判断你要改哪一类内容，再去找对应位置:

| 修改目标 | 优先查看 |
| --- | --- |
| 输入格式、来源、样例 | 第 3 章和入口节点 |
| 字段映射、清洗、分类 | 第 4 章对应节点 |
| 分支、循环、节点增删 | `workflow.json` 和第 2 章流程表 |
| 输出字段、落盘、通知 | 第 5 章和终点节点 |
| API、Syslog、Kafka 等发布方式 | `config.json` 和第 6 章 |
| 字段重命名 | 所有上下游节点、样例输入和输出说明 |

编辑后建议把改动说明写回相应章节，让下一个人可以直接看懂为什么这样改。

## 8. 验证方式

最小验收清单:

- [ ] 用一条正常样例能跑通。
- [ ] 输出字段符合你的预期。
- [ ] 如果改了字段名，下游节点没有继续引用旧字段。
- [ ] 如果改了发布方式，发布页只展示应该出现的能力。
- [ ] 没有明文密钥、长期 token 或私人路径写进工作流目录。
