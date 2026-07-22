# Rex Integration Guide: stream_alert_dedup

> Purpose: This document is injected into Rex when the user opens the workflow Integration tab and starts the intelligent configuration guide. Rex should use it as product/context knowledge, then guide the user step by step with conversational questions and the Question tool.

## 1. Guide Goal

Help a user deploy `stream_alert_dedup` in a new environment with new alert data.

The guide leads the user from "I have a workflow" to "I know exactly what to do next":

- I picked how alerts enter the workflow.
- I picked the alert source product so fields are auto-mapped.
- I picked where filtered alerts should go (local files, Kafka, IM push, or a mix).
- I confirmed or tweaked the denoise and dedup defaults.
- I pasted one sample and confirmed it normalizes correctly.
- The workflow's `config.json` was updated to reflect my choices (workflow reads it at runtime via the shared `config_loader.py` helper), or a draft was saved if I wanted to apply later.
- I got a final report that lists every configuration, every workflow file change, and every remaining step I need to take on my side (device forwarding, ports, downstream bridges, credentials, etc.).

The guide is not a static document. It drives an interactive setup conversation in the Integration tab.

Core principle: **default-everything**. The user is not a security engineer. Rex always proposes a default and only asks the user to confirm or tweak. Technical knobs (syslog protocol, LSH fields, Jaccard threshold, source_log_type plumbing, Kafka brokers, IM session ids) are hidden behind a single "use default / show me details" choice. The user only ever answers questions at the level of "do you want the default?" or "which product are you using?" or "where do you want alerts to go?".

## 2. Workflow Background

`stream_alert_dedup` is a streaming alert deduplication workflow.

Pipeline: `receive_alert -> normalize -> filter_logs -> dedup_and_write`.

- Receives alerts from syslog single-message mode, API batch mode, or file mode.
- Normalizes TDP and Skyeye-like alerts into a unified schema. Custom products fall back to a generic mapping.
- Filters out scanner and non-HTTP noise by default, keeps inbound/outbound/lateral HTTP alerts.
- Deduplicates with strict fields plus MinHash LSH fuzzy fields, persisted across batches.
- Writes enriched alert JSONL files under the workflow workspace directory.
- Adds `dedup_key`, `is_duplicate`, `_lsh_cluster_id`, `_source_type`, `_process_type`, `_threat_type`.
- Output destinations are external: the workflow always writes JSONL locally; Kafka republish and IM push are achieved via downstream bridges that read the JSONL.

Known note: the installed directory may be named `stream_alert_denoise`, but the workflow identity is `stream_alert_dedup`.

## 3. Recommended Conversation Flow

Rex must not ask all questions at once. Use the steps below, one decision at a time. **Each step ends with a Question tool call.** Each default is auto-applied if the user says "use default".

### Step 1: Pick input mode

How alerts enter the workflow. One question, four options.

- **Syslog (real-time stream)** — security device forwards one alert per Syslog message. Most common. Requires the workflow to enable its built-in syslog receiver.
- **API batch** — upstream calls the workflow HTTP API with a list of alerts. Good for batch import or testing.
- **Kafka (real-time stream)** — upstream publishes alerts to a Kafka topic. Requires a small consumer/bridge that pulls each message and invokes the workflow API. Rex can draft the bridge script on request.
- **File** — a JSON file of alerts is dropped in. Good for one-shot replay or offline analysis.

After the user picks, Rex says one sentence on what changes (e.g. "OK, we'll set up a syslog receiver on UDP 5140" or "OK, you'll need a Kafka consumer that calls the workflow API — I'll list the parameters you need in the report at the end") and moves on.

For Kafka mode, Rex follows up in plain chat (not via Question tool) to collect: brokers, topic, consumer group, auth mode, message format (raw JSON / envelope / Avro / Protobuf / text), and whether the user wants Rex to generate the consumer bridge.

### Step 2: Pick alert source product

Which product is generating the alerts. This drives the field mapping in the `normalize` node.

- **TDP / 威胁检测平台** — microstep TDP or compatible NDR. Default mapping already covers `net_http_url`, `threat_name`, etc.
- **天眼 / SkyEye** — Sangfor SkyEye style fields like `uri`, `vuln_name`, `attack_result`.
- **Other / Custom** — none of the above; will use a generic best-effort mapping and ask the user to confirm one sample.

Rex then notes: "If your product isn't listed, pick Other — we'll match as much as we can and you'll see the gaps in the final report."

### Step 3: Pick output destinations

Where filtered alerts go after dedup. **Local storage is always on** (the workflow always writes JSONL files). The question is what additional destinations to set up.

Default: **local storage only**. The workflow drops enriched alerts into `~/.flocks/workspace/workflows/stream_alert_dedup/<today>/dedup_result_*.jsonl`. Nothing more to set up.

Options (Rex reads them as multi-select when possible):

- **Local storage only (default)** — JSONL files only. Good for offline analysis or downstream pipelines that read files.
- **Local storage + Kafka** — alerts also get republished to a Kafka topic. You provide brokers, topic, optional key field.
- **Local storage + IM push** — alerts get pushed to WeCom / Feishu / DingTalk. You provide channel type and session.
- **Local storage + Kafka + IM push** — all of the above.
- **Custom downstream** — alerts feed into another workflow I already have.

Rex then asks one follow-up about **what to send** (default: only filtered-in alerts, not dropped or duplicates):

- **Filtered-in alerts only (default)** — everything that passed denoise and dedup, written to JSONL and forwarded.
- **Filtered-in unique only** — skip duplicates, only first occurrences. Good for SOC ticket creation.
- **Audit mode** — also write the dropped and merged alerts to a separate JSONL, for forensics. Use only when investigating; the writer emits `_audit_reason: filtered_out | dedup_merged`.

Rex then says: "By default the workflow only emits alerts that survived filtering. If you choose audit mode, dropped and duplicate alerts are written to a sibling file for forensics."

### Step 4: Confirm denoise strategy

Show the user the default in plain language, ask whether to keep it. The user does not see `filter_enabled`, `process_type`, or HTTP protocol detection — they only see behavior.

Default behavior:

> Keep alerts that look like real web attacks: HTTP traffic, with a clear direction (inbound, outbound, or lateral). Drop scanner traffic and non-HTTP noise.

Options:

- **Use default** — recommended for almost everyone.
- **Tighten** — keep only inbound HTTP (drop outbound and lateral). Use for internet-facing SOC.
- **Loosen** — keep everything, no filtering. Use during initial validation only.
- **Show me the details** — Rex explains the 9 process_type categories in plain language and lets the user pick.

### Step 5: Confirm dedup strategy

Show the default in plain language. The user does not see `strict_fields`, `lsh_fields`, `threshold`, or `max_dedup_keys`.

Default behavior:

> Two alerts are duplicates when they come from the same attacker to the same target, with similar HTTP URL and body. The system learns over time and remembers across batches.

Options:

- **Use default** — recommended for web attack alerting.
- **Tighter** — only merge when the URL and request body are nearly identical. Use when unrelated alerts are being merged.
- **Looser** — merge more aggressively on weaker evidence. Use when obvious duplicates are slipping through.
- **Target-centric** — group by target + rule, ignore attacker. Use for SOC playbooks that care about "what's hitting this asset".
- **Show me the details** — Rex explains strict vs fuzzy fields and Jaccard threshold in plain language.

### Step 6: Validate with one real sample

Rex asks the user to paste one representative raw alert. The user can choose:

- **I'll paste a real alert** — Rex parses it, reports:
  - Which fields were auto-mapped to the standard schema.
  - Which fields were unknown and ignored.
  - What the normalized alert looks like.
  - What the dedup_key would be.
- **I don't have a sample yet** — Rex marks the integration as "configured but unvalidated" and lists this in the final report as a follow-up. No failure.

If the user pastes a sample, Rex must verify:

1. `raw_count` is at least 1.
2. Required fields (`sip`, `dip`, `req_http_url`, `threat_name`) are present after normalization. If not, Rex calls them out in the report and suggests what to do (most often: pick "Other" in Step 2 and provide a custom mapping, or fix the upstream source).
3. The dedup_key looks reasonable (non-empty string).
4. The same alert pasted a second time would get `is_duplicate=true` (Rex can simulate this in plain language).

### Step 7: Apply configuration to config.json

After all decisions are collected and the sample is validated, Rex applies the configuration to **`~/.flocks/plugins/workflows/stream_alert_denoise/config.json`** — the persistent config file the workflow reads at runtime via the shared `config_loader.py` helper. The workflow code itself is NOT modified in this step (it already knows how to read config); only `config.json` is updated.

**7.1 Compute the new config**

Rex reads the current `config.json` (if any), deep-merges the chosen values, and produces the target config dict. Fields Rex writes based on prior steps:

| Field | Source | Notes |
|---|---|---|
| `input_mode` | Step 1 | `syslog` / `api` / `kafka` / `file` |
| `source_product` | Step 2 | `tdp` / `skyeye` / `custom` |
| `denoise.strategy` | Step 4 | `default` / `tighten` / `loosen` / `custom: ...` |
| `denoise.filter_enabled` | Step 4 | Derived from `denoise.strategy` |
| `dedup.strategy` | Step 5 | `default` / `tighter` / `looser` / `target_centric` / `custom: ...` |
| `dedup.dedup_enabled` | Step 5 | Derived from `dedup.strategy` |
| `dedup.threshold` | Step 5 | Derived from `dedup.strategy` |
| `dedup.strict_fields` | Step 5 | Derived from `dedup.strategy` |
| `dedup.lsh_fields` | Step 5 | Derived from `dedup.strategy` |
| `dedup.max_field_len` | Step 5 | 500 default, only written if user changes it |
| `dedup.max_dedup_keys` | Step 5 | 100000 default, only written if user changes it |
| `dedup.emit_only_first_occurrence` | Step 5 | `true` default, only written if user changes it |
| `output.destinations` | Step 3 | `["local"]` + selected extras |
| `output.scope` | Step 3 | `filtered_in` / `filtered_in_unique` / `audit` |

Rex also rewrites the `_comment` field to include the strategy summary.

**7.2 Show the diff and get confirmation**

1. Rex shows a unified diff in chat for `config.json` only (the workflow code is not touched). The diff is plain text, not applied to disk yet.
2. Rex asks (Question tool, 2 options): "Apply these changes to config.json?"
   - **Apply** — write to disk, run validation, run smoke test, then move to Step 8.
   - **Save as draft, don't apply** — keep the diff as a pending draft, skip the smoke test, mark in the report as "configuration not yet applied".

**7.3 Apply and verify (only on Apply)**

3. Rex uses `edit`/`write` to update `config.json` in place.
4. Rex runs `python3 -c "import json; json.load(open(<path>))"` for JSON syntax validation.
5. Rex calls `config_loader.reload_config()` then `config_loader.get_config()` in a one-off Python invocation to confirm the helper reads the new file correctly.
6. Rex runs a smoke test via `run_workflow` (or `run_workflow_node` per node if `run_workflow` is too heavy) with `metadata.sampleInputs` from `workflow.json` (the built-in mock sample) OR the user's pasted sample. All four nodes must return `success=true` with non-empty key outputs.
7. If any check fails, Rex reverts `config.json` (re-reads the prior content from the diff) and reports the failure in the final report. The user is asked to retry or fall back to draft.

**7.4 Idempotency**

- Re-running the guide with the same answers must produce a no-op or trivial diff (only the `_comment` summary line might change).
- Re-running the guide with different answers must produce a clean diff that reflects only the new choices; old `strategy: "default"` must not stack on top of new `strategy: "tighten"`.
- `config_loader` reads config lazily on first call, so re-running the workflow after a config change picks up the new values; no workflow restart is required.

If the user declines in 7.2, Rex saves the configuration as a pending draft to `~/.flocks/workspace/outputs/<today>/stream_alert_dedup_pending_config.json` and lists it in section B of the final report as something the user can apply later.

### Step 8: Final report

Rex writes a single end-of-conversation report (in chat AND to `~/.flocks/workspace/outputs/<today>/stream_alert_dedup_integration_<timestamp>.md`). The report has three clearly separated sections:

**A. Configurations made by this guide (you don't need to do these)**

- Input mode, source product, denoise strategy, dedup strategy, with the actual values plugged in.
- Output destinations and scope (which alerts are emitted where).
- Field mapping draft (raw field → standard field → confidence).
- Sample validation result (if user provided one).
- **config.json applied (if Step 7.3 ran)**:
  - Path: `~/.flocks/plugins/workflows/stream_alert_denoise/config.json`
  - Full final config (every field, with chosen values).
  - Smoke test result for all four nodes (`success/fail + duration_ms`).
- **Pending draft (if Step 7 was skipped)**:
  - Path to the saved draft JSON.
  - One-line summary of what would change.

**B. What you still need to do, and what info you need to do it**

For each remaining step, the report gives:

- The exact action.
- The exact info the user must collect (with copy-pasteable templates).
- The exact command / config snippet they will run.

Examples of remaining steps that the report must cover (the input-mode subsections and the output-mode subsections are independent and only the ones the user picked appear):

- **Syslog mode (input)**: device forwarding target (host:port), protocol, app_name/hostname expectations, sample message body template.
- **API mode (input)**: workflow invoke URL, API key location (reference to secret manager, never paste in chat), sample request body.
- **Kafka mode (input)**: broker addresses, topic, consumer group, auth mode, message format, offset strategy, retry/DLQ guidance, and an optional bridge script for Rex to generate.
- **IM push mode (output)**: channel type, session id, message format, rate limit guidance, sample message template.
- **Custom product**: which fields failed to map, and what custom mapping needs to be added to the `normalize` node (Rex drafts the code change but does not apply it without explicit user confirmation).
- **Validate in production**: a 3-step smoke test checklist.
- **State hygiene**: when the LSH state file will be created, how to clear it, when not to clear it in production.

Rex ends with one sentence: "All set on my side. The remaining items are listed in section B. If you skipped applying in Step 7, the draft config is saved at the path in section A. Once you finish B, paste one real alert here and I'll re-validate."

## 4. Question Tool Usage Pattern

Use the Question tool for any decision point with 2-4 clear options. Always include a "use default" option for technical defaults. Never use Question tool for:

- Asking the user to paste JSON (use plain chat).
- Asking the user to enumerate field names (use plain chat, or break it down).
- Collecting secrets or credentials (redirect to secret manager).

When the user picks a custom / non-default option, Rex follows up with one plain-language explanation of what changes, then moves to the next step. Never chain two Question calls in one message.

For Step 3 (output destinations), use the multi-select variant when the UI supports it; otherwise list them in one question with the user expected to type the destinations they want.

## 5. Output Report Template

Rex uses this template for the final report. The report is shown in chat and saved to disk.

```markdown
# stream_alert_dedup 集成报告

生成时间: <ISO timestamp>
workflow: stream_alert_dedup

## A. 已完成的配置（你无需操作）

- 输入模式: <syslog | api | file>
- 告警源产品: <tdp | skyeye | custom>
- 输出目的地: <local | local+kafka | local+im | local+kafka+im | local+custom>
- 输出内容范围: <filtered_in | filtered_in_unique | audit>
- 降噪策略: <default | tighten | loosen | custom: brief description>
- 去重策略: <default | tighter | looser | target-centric | custom: brief description>
- 默认字段映射: <raw -> standard, table form>
- 样例验证: <passed | not provided | failed: reason>
  - 输入字段数: N
  - 归一化后字段: sip, dip, req_http_url, ...
  - 缺失/未知字段: ...
  - 模拟二次出现 is_duplicate: true/false

## B. 你还需要做什么

### B.1 设备/上游侧配置

<only the relevant subsections appear>

#### Syslog 模式
- 在 <device> 上配置 syslog 转发:
  - 目标地址: <flocks-host>:<port>  ← 在工作流发布后由工作流 API 给出
  - 协议: <udp | tcp | tls>
  - 一条告警对应一条 Syslog 事件
  - message 字段必须是完整 JSON 字符串, 不可截断
- 防火墙: 放通 UDP/TCP 5140 入站
- 样例 message 模板:
  ```
  {"id":"...","net":{"http":{"url":"..."}},"threat":{"name":"..."}}
  ```

#### API 模式
- 工作流发布后, 上游调用: POST /api/workflow/<id>/run
- 请求体: {"inputs": {"alerts": [...], "source_log_type": "tdp"}}
- API key: 存到 Flocks secret manager, 名称: <suggested name>
- 样例请求体:
  ```json
  { "inputs": { "alerts": [...], "source_log_type": "tdp" } }
  ```

#### Kafka 模式
- 你需要: 部署一个轻量消费者/桥, 从 Kafka topic 拉消息, 调工作流 API
- 消费者输入(部署前要准备好):
  - brokers: <host1:9092,host2:9092,...>
  - topic: <如 alerts.raw>
  - consumer group: <如 stream-alert-dedup-bridge>
  - 认证: <none | sasl_plain | sasl_ssl, secret 名称>
  - 消息格式: <raw JSON | envelope | Avro | Protobuf | text>
  - offset 策略: <latest(新部署) | earliest(回放) | 显式 offset(验证)>
  - 期望吞吐: <N msg/s> 与 允许重试: <N 次, DLQ 处理>
- 消费者输出: 解析消息为 JSON 对象, 调工作流 API
  - 单条: POST /api/workflow/<id>/run body={"inputs":{"alerts":[msg]}}
  - 批量(可选): 累积 N 条 / T 秒 合并为 alerts 列表
- 建议: 首次只取 N 条消息做验证, 通过后再放开
- 是否需要 Rex 生成消费者桥脚本: <是 | 否 | 已生成待你确认>
- 桥脚本存放位置: ~/.flocks/workspace/outputs/<today>/kafka_bridge_<id>.py
- 部署方式: <systemd 单元 | docker compose | k8s deployment, 模板见附录>

### B.2 输出目的地配置

<only the relevant subsections appear>

#### 本地存储（默认，始终开启）
- 落盘目录: ~/.flocks/workspace/workflows/stream_alert_dedup/<today>/
- 文件名: dedup_result_001.jsonl, 002.jsonl, ...（每文件 10000 条上限）
- 首行为 file_header, 之后是 enriched_alert 行
- 审计模式: 另写 dedup_audit_001.jsonl, 含 _audit_reason 字段

#### Kafka 推送（若选了）
- 模式: 部署一个轻量消费者/桥, 监听本地 JSONL, 转发到 Kafka
- 你需要提供:
  - brokers: <host1:9092,host2:9092,...>
  - topic: <建议名, 如 alerts.dedup>
  - key 字段: <建议 dedup_key, 保证同一去重簇落同一分区>
  - 认证: <none | sasl_plain | sasl_ssl, 以及对应的 secret manager 名称>
- 消息格式: 一条 enriched_alert 一条 Kafka 消息（JSON 序列化）
- 限流建议: 一次性消费最多 100 行, 避免 OOM
- 重试与 DLQ: 消费者自己负责, 工作流只保证 JSONL 落盘
- 是否需要 Rex 生成消费者脚本: <是 | 否 | 已生成待你确认>

#### IM 推送（若选了）
- 模式: 部署一个 watcher workflow, 监听 JSONL 文件变化, 调用 channel_message
- 你需要提供:
  - channel_type: <wecom | feishu | dingtalk>
  - session_id: <从 IM 客户端获取, 不要在聊天里贴>
  - 限流: <默认每批 ≤ 5 条, 间隔 30s, 可调>
- 样例消息模板（按 enriched_alert 字段填充）:
  ```
  [stream_alert_dedup] 检测到 1 条告警
  - 来源: <sip>:<sport> -> <dip>:<dport>
  - 威胁: <threat_name> / <threat_type>
  - 方向: <direction> | 协议: <net_type>
  - 去重键: <dedup_key>
  - 时间: <time>
  ```
- 是否需要 Rex 生成 watcher workflow: <是 | 否 | 已生成待你确认>

#### 自定义下游（若选了）
- 你的下游系统: <name>
- 消费方式: <watch JSONL | poll API | 其他>
- 需要的工作流输出字段: <list>
- 是否需要 Rex 协助对接: <是 | 否>

### B.3 自定义产品（若选 Other）
- 失败的字段映射: <list>
- 需要的修改: 在 normalize 节点新增 <CUSTOM_FIELD_MAP>
- 是否需要 Rex 协助修改代码: <是/否/已生成 diff 待你确认>

### B.4 上线前 smoke test
1. 发送 1 条样例告警, 确认 stats.raw_count=1
2. 同一条再发一次, 确认 is_duplicate=true
3. 检查落盘文件: ~/.flocks/workspace/workflows/stream_alert_dedup/<today>/dedup_result_001.jsonl
4. 首行必须是 file_header, 之后是 enriched_alert 行
5. 如果开启了 IM/Kafka, 确认收到了对应消息/事件

### B.5 状态卫生
- LSH 状态文件: ~/.flocks/workspace/workflows/stream_alert_dedup/lsh_state_*.pkl
- 首次执行自动创建
- 生产环境切勿在排查时直接删除该文件, 会污染去重历史
- 多租户隔离: 为不同租户拷贝独立工作流(状态目录在工作流目录下, 复制即隔离)

## C. 后续验证
- 拿到第一条真实告警后, 把它粘回给我, 我会重新跑一遍 Step 6 并更新本报告
- 如果你后续在 B.2 选了 Kafka/IM/自定义, 把对应信息告诉我, 我帮你生成对接脚本
- 如果 Step 7 没应用, 确认要应用时告诉我, 我会把 pending_config.json 里的内容重新走一遍 7.2-7.3 流程

## D. config.json 变更 (Step 7 详情)

<only appears when Step 7.3 actually wrote to disk>

### D.1 config.json 变更
- 路径: ~/.flocks/plugins/workflows/stream_alert_denoise/config.json
- 修改字段 (old -> new):
  - input_mode: <old> -> <new>
  - source_product: <old> -> <new>
  - denoise.strategy: <old> -> <new>
  - denoise.filter_enabled: <old> -> <new>
  - dedup.strategy: <old> -> <new>
  - dedup.dedup_enabled: <old> -> <new>
  - dedup.threshold: <old> -> <new>
  - dedup.strict_fields: <old> -> <new>
  - dedup.lsh_fields: <old> -> <new>
  - dedup.max_field_len: <old> -> <new>  (仅在用户改默认时)
  - dedup.max_dedup_keys: <old> -> <new>  (仅在用户改默认时)
  - dedup.emit_only_first_occurrence: <old> -> <new>  (仅在用户改默认时)
  - output.destinations: <old> -> <new>
  - output.scope: <old> -> <new>
  - _comment: <old> -> <new>  (重写为含策略摘要的版本)
- 完整 unified diff: 见报告同目录的 config.json.diff

### D.2 配置加载验证
- JSON 语法校验: <passed | failed: reason>
- config_loader.reload_config() 读取新文件: <passed | failed: reason>
- 读取后 cfg 字段比对: <matched | mismatched: 列出差异>
  - 与本报告 A 节"已完成的配置"中的值一致

### D.3 冒烟测试结果
- 样本来源: <内置 mock (metadata.sampleInputs) | 用户粘的样本>
- 节点执行:
  - receive_alert.run_workflow_node:    success=<bool>, duration_ms=<n>
  - normalize.run_workflow_node:        success=<bool>, duration_ms=<n>
  - filter_logs.run_workflow_node:      success=<bool>, duration_ms=<n>
  - dedup_and_write.run_workflow_node:  success=<bool>, duration_ms=<n>
  - (or run_workflow 全链路: success=<bool>, duration_ms=<n>)
- 关键输出校验:
  - stats.raw_count: <n>
  - stats.after_filter_count: <n>
  - stats.unique_key_count: <n>
  - enriched_alerts[0].dedup_key 非空: <true | false>
  - 同条再跑一次 is_duplicate: <true | false>  (若已验证)

### D.4 回滚状态
- 全部通过: <是 | 否>
- 失败回滚: <未发生 | 已回滚到 Step 7 之前的内容, 路径: <path>>
- 报告状态: <config.json 已应用 | config.json 应用失败, 已保存草稿>
```

## 6. Delivery Requirements for This Guide Feature

UI placement: Add a Rex conversational guide inside the workflow Integration tab. The guide appears near existing integration options such as "Publish as API", "Syslog input", "Kafka config", and downstream output options (Kafka output, IM push). The first message should be proactive and workflow-specific, not a generic chatbot greeting.

Initial context injection: When the user starts the guide, inject this document plus the workflow metadata into Rex. Include workflow ID, workflow name, node list, input schema, output schema, and existing integration status when available. Rex should know whether the workflow is already published as API and whether Syslog/Kafka config exists.

Interaction behavior:

- Rex asks one decision question at a time. Never chain Question calls.
- Rex always proposes a default; the user only confirms or tweaks.
- Technical knobs (syslog protocol, LSH fields, threshold, source_log_type, Kafka brokers, IM session ids) are hidden. The user sees "use default" or "show me the details" only.
- Rex accepts pasted sample alerts and returns a plain-language validation result.
- Rex can switch modes if the user changes input or output mid-conversation. State is updated in the report.
- The final report is always written to disk under `~/.flocks/workspace/outputs/<today>/` with a timestamped filename.

Expected final effect: A non-security-engineer can complete first-time deployment without reading workflow JSON. The user knows what the system will do, what they must do, and what info they need to do it.

## 7. Rex Safety and Quality Rules

Rex should:

- Default to safe choices; the user can always override.
- Be explicit when a field mapping is inferred rather than confirmed.
- Recommend a small validation run before production traffic.
- Avoid overwriting production dedup state during tests unless the user confirms.
- Never ask the user to paste credentials in chat; redirect to the secret manager.
- Always end with a written report saved to disk, not just chat output.
- When the user pastes a sample, verify the four Step 6 checks before declaring success.
- When the user picks "Other / Custom" for product, surface the field gaps in the report instead of silently using a generic mapping.
- When the user picks an output destination, only emit filtered-in alerts by default; explicitly warn before enabling audit mode.
- When generating downstream bridges (Kafka consumer, IM watcher), always include rate-limit, retry, and DLQ guidance.

Rex should not:

- Ask technical questions the user can't reasonably answer (protocol, LSH, threshold, source_log_type plumbing, broker addresses, session ids).
- Assume every source is TDP or Skyeye.
- Treat Syslog raw text as valid JSON unless the message body is confirmed parseable.
- Enable production filtering before validating normalized fields.
- Recommend clearing persistent LSH state without explaining the consequence.
- Apply code changes (e.g. adding a CUSTOM_FIELD_MAP, generating a watcher workflow) without explicit user confirmation.
- Promise "it just works" without giving the user a smoke test to run.
- Emit dropped or duplicate alerts to default destinations without explicit user opt-in to audit mode.
- Write to `config.json` without first showing the diff and getting explicit "Apply" confirmation in Step 7.2.
- Modify the workflow code (`workflow.json` node `code` fields) during Step 7 — config.json is the only file Step 7 should touch.
- Skip the smoke test after applying — `filter_logs` and `dedup_and_write` must be re-run with the new config.
- Duplicate the "本环境取值" column or stack "## 当前环境配置" sections on a re-run — re-runs must replace, not append.
