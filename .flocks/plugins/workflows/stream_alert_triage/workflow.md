# stream_alert_triage

`stream_alert_dedup` 的下游研判 Pipeline — **完全自包含**，研判逻辑直接内联，不依赖也不嵌入 `tdp_alert_triage`。

## 核心特性

* **跨批次复用**：dedup_key 命中持久化 cache 时直接复用历史 verdict/title/final_report，不调 LLM
* **同批次去重**：批内多条 alert 共享 dedup_key 时，只对 **leader（首条）研判**，follower 广播复用 leader 结果
* **保留 4 并行 LLM 分支**（survey / cve_related / cve_info / payload_analysis）— 与 `tdp_alert_triage` 完全相同的研判语义；**默认仅开启 payload_analysis**，其余三个分支可按需通过 `branch_enables` 参数开启
* **研判产物仅以字段形式附加**到每条 alert（`final_report` 字段含完整 markdown），**不生成任何独立的 per-alert 报告文件**

## 与上游的关系

```
stream_alert_dedup                       stream_alert_triage（本工作流）
  ─────────────────                       ───────────────────────────────
  receive → normalize                       load_dedup_file
  filter  → dedup_and_write   ────►         （一次性读 JSONL）
       │                                        │
       ▼                                       ▼
  ~/.flocks/workspace/workflows/         concurrent_triage
  stream_alert_dedup/<date>/             (leader/follower 分组 + 5×4 并发)
  dedup_result_NNN.jsonl                     │
                                             ▼
                                          summarize
```

## 工作流图

```
load_dedup_file ──► concurrent_triage ──► summarize
```

## Leader/Follower 分组并发模型

```
input: 100 alerts
   │
   ▼ (group by dedup_key)
   ├── dedup_key='A' → leader: alert#0,  followers: alert#3, alert#7
   ├── dedup_key='B' → leader: alert#1,  followers: alert#5
   ├── dedup_key='C' → leader: alert#2   (singleton)
   ├── ...
   └── no_dedup_key  → alert#42, alert#56 (each its own unit)

   ▼ unique work units only

外层 ThreadPoolExecutor(max_workers=5)         处理 unique work units
   │
   └── _process_unit(unit_type, dedup_key, leader_idx)
         ├── cache 命中 → 返回历史结果（0 LLM 调用）
         └── cache 未命中 → _triage_single_alert(leader)
                                 │
                                 ├── _prepare_intel  (情报)
                                 │
                                 ├── _parallel_4_branches  ← 内层 ThreadPoolExecutor(N, N≤4)
                                 │      ├── _llm_survey            ┐ 默认关闭
                                 │      ├── _llm_cve_related       │ 默认关闭  (branch_enables 控制)
                                 │      ├── _llm_cve_info          │ 默认关闭
                                 │      └── _llm_payload_analysis  ┘ 默认开启
                                 │
                                 ├── _llm_attack_analysis
                                 ├── _llm_attack_verdict
                                 ├── _llm_report_title
                                 └── _generate_report (聚合 markdown，仅返回字符串，不写盘)

   ▼ broadcast leader result to all followers in the group

output: 100 enriched_alerts_with_triage (followers 拿到与 leader 相同的研判字段)
```

**稳态 LLM 并发**：默认配置（仅 payload_analysis 开启）全程固定 **5**（= 外层 5 worker × 每阶段 1 个 LLM 调用），无峰值波动。全部 4 分支开启时，分支阶段峰值为 5 × 4 = **20**。

## 输入参数

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `input_paths` | `list[str]` | — | 显式文件路径列表（来自 `stream_alert_dedup.outputs.output_paths`） |
| `input_path` | `str` | — | 单个文件路径（来自 `stream_alert_dedup.outputs.output_path`） |
| `input_date` | `str` | 今天 | `YYYY-MM-DD`；遍历该日目录下所有 `dedup_result_*.jsonl` |
| `concurrency` | `int` | `5` | 外层并发 worker 数（处理 unique work units） |
| `max_triage_cache_size` | `int` | `100000` | 研判缓存 FIFO LRU 上限 |
| `persist_triage_output` | `bool` | `True` | 是否把附加了研判字段的 alerts 落盘为 JSONL（关闭时仅保留内存输出） |
| `branch_enables` | `dict` | `{'survey':false,'cve_related':false,'cve_info':false,'payload_analysis':true}` | 控制 4 个并行 LLM 分支的开关；未传入的 key 沿用默认值，禁用的分支不发起任何 LLM 调用 |

输入优先级：`input_paths` > `input_path` > `input_date` > 当日默认。

## 输出参数

| 字段 | 说明 |
|------|------|
| `enriched_alerts_with_triage` | `list[dict]`：每条 = 上游 enriched_alert 字段 + 研判字段（含 `final_report` 完整 markdown） |
| `triage_results` | `list[dict]`：精简版结果（dedup_key / threat_name / sip / dip / verdict / title 等，不含 markdown 正文，markdown 仅在 `enriched_alerts_with_triage[*].final_report` 中） |
| `output_paths` | `list[str]`：研判结果落盘的 JSONL 文件路径列表（带研判字段，单文件最多 10000 条） |
| `output_dir` | `str`：当日研判结果目录 `~/.flocks/workspace/workflows/stream_alert_triage/<YYYY-MM-DD>/` |
| `triage_stats` | `dict`：并发研判统计（含 unique_dedup_keys / followers_reused / cache_hit / triaged） |
| `load_stats` | `dict`：文件加载统计 |
| `loaded_files` | `list[str]`：实际读取到的文件路径 |
| `summary_report` | `str`：markdown 汇总报告 |
| `summary_path` | `str`：summary 落盘路径 |
| `top_attack_verdict` / `top_risk_level` / `top_report_title` / `top_final_report` | top-risk 告警的研判字段 |

### 每条 alert 上追加的研判字段

| 字段 | 说明 |
|------|------|
| `has_dedup_key` | `bool` — 上游是否给出 dedup_key |
| `triage_source` | `cache`（命中持久化 cache）/ `triaged`（leader 新研判）/ `follower_reused`（同批次 follower）/ `failed`（leader 失败）/ `no_dedup_key_triaged`（无 dedup_key 已研判）/ `no_dedup_key_failed` |
| `triage_status` | `cached` / `ok` / `reused_from_leader` / `failed` |
| `attack_verdict` | `attack_success` / `attack_failed` / `attack` / `unknown` / `benign` |
| `risk_level` | `High` / `Medium` / `Low` |
| `report_title` | LLM 生成的中文报告标题 |
| `final_report` | 完整 markdown 报告字符串（leader/follower/cache-hit **全都** 携带完整内容；**不写独立文件**，整份内容就在这个字段里） |
| `attack_success` | 兼容字段，仅当 verdict==attack_success 时为 True |
| `triage_ms` | 单条研判耗时（毫秒，仅 leader 持有） |
| `triage_error` | 研判失败原因（仅 failed 时存在） |

> 设计原则：每条 alert 的完整研判 markdown 直接挂在 `final_report` 字段上，下游可以零成本访问。**没有 `report_path`，也不会在磁盘上生成 `triage_report_*.md` 之类的独立文件**，避免重复存储与跨日期路径失效。工作流仍会写一份**总览** `stream_alert_triage_summary.md`（见 `summary_path`），其中包含 top-risk 告警的 `final_report` 全文。

### `triage_stats` 字段

| 字段 | 说明 |
|------|------|
| `total` | 输入 alert 总数 |
| `unique_dedup_keys` | 输入中 unique dedup_key 数 |
| `followers_reused` | 同批次复用 leader 的 follower 数 = `total - unique_dedup_keys - no_dedup_key_alerts` |
| `no_dedup_key_alerts` | 无 dedup_key 的 alert 数 |
| `work_units` | LLM 实际处理的 work unit 数 = `unique_dedup_keys + no_dedup_key_alerts` |
| `cache_hit` | 跨批次 cache 命中数 |
| `triaged` | 实际跑 LLM 研判的 leader 数（cache miss） |
| `triage_failed` | leader 研判失败数 |
| `verdict_counts` | 各 verdict 类别计数 |
| `cache_size_before` / `cache_size_after` / `evicted` | cache 状态变化 |
| `elapsed_ms` | 节点总耗时 |

## 研判结果落盘 (与上游 dedup 输出对齐的 JSONL)

路径：`~/.flocks/workspace/workflows/stream_alert_triage/<YYYY-MM-DD>/triage_result_NNN.jsonl`

* **格式**：第 1 行为 `{"_type":"file_header", "created_at", "date", "workflow", "seq", "run_id", "batch_total", "batch_triaged", "batch_followers_reused", "batch_cache_hit", "batch_triage_failed"}`；后续每行是一条完整的 `enriched_with_triage` alert（含原始字段 + 全部研判字段）。
* **滚动**：单文件超过 10000 条记录自动创建下一序号（`.triage_counter.json` 跟踪 seq/count，避免每次扫描）。
* **关闭**：传 `persist_triage_output=False` 可只保留内存输出，不写文件（适合自测）。
* **下游消费**：可用 `flocks.workspace.workflows.stream_alert_triage/<date>/` 作为后续 pipeline（LSH 写入、归档、报表）的输入根目录。

## 研判缓存（dedup_key → triage_fields）

路径：`~/.flocks/workspace/workflows/stream_alert_triage/triage_cache.pkl`

* **key**：`dedup_key`（即 stream_alert_dedup 生成的 `MD5(strict_fields + lsh_cluster_id)`）
* **value**：`{attack_verdict, risk_level, report_title, final_report, attack_success}`（**纯数据，无文件路径**）
* **结构**：原生 `dict`（Python 3.7+ 保留插入顺序），按访问顺序 FIFO LRU
* **并发安全**：进程内 leader/follower 分组 + 单次快照读 + 写入合并；进程间 `fcntl`/`msvcrt` 文件锁 + 原子 `os.replace`
* **淘汰策略**：超过 `max_triage_cache_size` 时丢弃最旧条目（仅在写入路径上做 LRU touch）

> 物理上**独立**于 `stream_alert_dedup` 维护的 `lsh_state_*.pkl`，避免污染 LSH 状态文件；逻辑上通过 `dedup_key` 与 LSH 持久化层一一对应。

### 冷启动行为

新工作流首次启用、或换了 cache 持久化文件时，所有告警的 `is_duplicate` 都来自**上游 LSH**，但**下游 `triage_cache.pkl` 是空的**。意味着首次执行会跑满 `unique_dedup_keys` 次 LLM 研判（同批次的 follower 仍能复用 leader），后续才能享受跨批次 cache 复用。

## 节点说明

### load_dedup_file

一次性读取上游 JSONL，跳过 `_type: file_header` 行，输出 `enriched_alerts`。`input_*` 全部缺省时自动取**今天**的目录。

### concurrent_triage

**自包含研判节点**，不调用任何子工作流。

1. **预分组**：按 `dedup_key` 把 alerts 分成 group，每组 1+ alert。无 dedup_key 的 alert 各自独立成 work unit。
2. **去重并发**：外层 `ThreadPoolExecutor(max_workers=5)` 处理 unique work units（unique dedup_keys + no-key alerts）。
3. **单条研判**（cache miss 时）：
   1. `_parse_alert`：解析告警，提取 src/dst/url/payload/response，生成统一 `log_text` 与 IOC 列表
   2. `_prepare_intel`：调用 `threatbook_ip_query` / `threatbook_domain_query` / `threatbook_url_query` 与 `__mcp_vuln_query` 工具，预取情报
   3. **`_parallel_4_branches`**：**`ThreadPoolExecutor(max_workers=N)`**（N = 已启用分支数，最大 4）并行调用已启用的 LLM 分支；禁用的分支直接返回空字符串，不占用线程也不发起 LLM 调用：
      - `survey`：测绘信息总结（**默认关闭**）
      - `cve_related`：漏洞编号提取（**默认关闭**）
      - `cve_info`：漏洞详情（**默认关闭**）
      - `payload_analysis`：攻击 payload 分析（**默认开启**）
   4. `_llm_attack_analysis`：5 类攻击状态判定
   5. `_llm_attack_verdict`：归一化为 5 个标签之一
   6. `_llm_report_title`：≤30 字中文报告标题
   7. `_generate_report`：聚合所有上述结果生成最终 markdown 字符串（**仅作为 `final_report` 字段返回，不写盘**）
4. **结果广播**：leader 完成后，把研判字段（含 `final_report` 整篇 markdown）广播到 group 内所有 follower
5. **缓存命中策略**：work unit 进入时先查 `triage_cache.pkl` 快照
   - 命中 → 直接复制历史研判字段（`final_report` 字符串原样复用），**不调用 LLM**，`triage_source='cache'`
6. **写回 cache**：所有 leaders 完成后，merge 进 `triage_cache.pkl`，文件锁 + LRU 淘汰 + 原子写

> ⚠️ 缓存命中策略**仅依赖 dedup_key**，并**不要求 `is_duplicate==True`**。

### summarize

写 `~/.flocks/workspace/outputs/<today>/artifacts/stream_alert_triage_summary.md`，按 verdict 风险排序挑出 top-risk 告警，把它的 final_report 暴露为工作流主输出。

## 典型调用

### 方式 1：用上游本次执行的输出文件

```python
from flocks.workflow.runner import run_workflow
from pathlib import Path

dedup_result = run_workflow(
    workflow=Path('~/.flocks/plugins/workflows/stream_alert_dedup/workflow.json').expanduser(),
    inputs={'alerts': raw_alerts},
)
triage_result = run_workflow(
    workflow=Path('~/.flocks/plugins/workflows/stream_alert_triage/workflow.json').expanduser(),
    inputs={
        'input_paths': dedup_result.outputs['output_paths'],
        'concurrency': 5,
    },
)
```

### 方式 2：按日期重放

```python
inputs = {'input_date': '2026-05-18', 'concurrency': 5}
```

不传任何 `input_*` 时默认取**今天**的所有 dedup_result_*.jsonl。

## 与 tdp_alert_triage 的关系

* **完全独立**：不调用 `tdp_alert_triage`，不依赖其工作流文件存在。
* **逻辑同源**：本工作流的 `_parse_alert` / `_prepare_intel` / 4 个 LLM 分支 / `attack_analysis` / `attack_verdict` / `report_title` / `_generate_report` 都是 `tdp_alert_triage` docs 版本对应节点逻辑的**内联拷贝**。
* **未来更新策略**：如需让两个工作流的研判逻辑保持一致，请在更新 `tdp_alert_triage` 节点逻辑后，同步修改本工作流的 `_node_concurrent_triage.py` 中对应辅助函数（`_llm_survey` 等），再运行 `_build_workflow.py` 重建。

## 重建 workflow.json

节点 Python 代码存放在独立 `_node_*.py` 文件中（避免 JSON 中大段转义字符串难以维护）。修改后运行：

```bash
cd docs/workflows/workflows/stream_alert_triage
python _build_workflow.py
```

会同时更新本目录 `workflow.json` 和运行时 `flocks/.flocks/plugins/workflows/stream_alert_triage/{workflow.json,workflow.md}`。
