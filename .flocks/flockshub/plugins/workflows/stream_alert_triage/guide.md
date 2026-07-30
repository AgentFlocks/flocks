# stream_alert_triage 配置指南

本文是 `stream_alert_triage` 的工作流专属配置指南。配置、验证或查询该工作流时，先读取本文，再结合 `workflow.md`、`workflow.json` 和后端运行态配置。

## 0. 配置库访问约束

- 查询工作流配置使用 `workflow_config_manage(action="get", workflow_id="stream_alert_triage")` 或 `status`。
- 查询 poller 使用 `workflow_config_manage(action="get", workflow_id="stream_alert_triage", config_type="poller")`。
- 修改前必须先调用 `workflow_config_manage(action="diff", workflow_id="stream_alert_triage", config_type="poller", config={...})` 展示差异并取得确认。
- 用户确认后才调用 `workflow_config_manage(action="put", workflow_id="stream_alert_triage", config_type="poller", config={...})`。
- 如果后端没有模板，可使用 `workflow_config_manage(action="sync", workflow_id="stream_alert_triage")` 从工作流目录迁移模板。
- 不要读取 `server_api_token` 或 `service_api_token`，不要手工调用 `/api/workflow/stream_alert_triage/poller-config`。
- 后端不可用时只能把目标配置保存为 outputs 下的草稿，并明确说明未应用、未发布、未启动。
- 查询配置是只读操作，不得触发 LLM、写缓存、写 SOC DB、写 JSONL 或修改游标。

## 1. 工作流定位

- 工作流 ID：`stream_alert_triage`
- 上游：`stream_alert_denoise`
- 入口：`load_dedup_file`
- 流程：`load_dedup_file -> concurrent_triage -> commit_cursor -> summarize`
- 默认输出：`soc_db`
- 默认批次：最多 10 条、32 MiB
- 默认调度模板：5 分钟，`noOverlap=true`

本工作流只处理上游去重后的 HTTP 告警，不负责原始告警接入、字段归一化、LSH 去重或跨日期积压补偿。

## 2. 引导顺序

一次只确认一个关键问题，推荐顺序：

1. 使用每日自动增量模式，还是显式文件重放？
2. 是否保持 `batch_max_records=10` 和 `batch_max_bytes=33554432`？
3. 是否保持 `concurrency=1` 和 `max_triage_cache_size=100000`？
4. 输出使用 `soc_db`、`jsonl`、`both` 还是 `none`？
5. 是否启用每 5 分钟一次的定时触发？
6. 展示计划、配置 diff 和副作用后，再确认应用或只保存草稿。

如果用户只要求“查配置”，直接执行第 9 节的只读检查，不提问、不修改。

## 3. 输入模式

### 自动目录模式

不传 `input_path` 和 `input_paths`。未显式设置 `input_date` 时，每次运行动态计算当天日期并扫描：

```text
~/.flocks/workspace/workflows/stream_alert_denoise/<YYYY-MM-DD>/dedup_result_NNN.jsonl
```

自动模式读取并在成功后更新：

```text
~/.flocks/workspace/workflows/stream_alert_triage/.triage_cursor.json
```

日期切换会忽略旧日期游标，从新日期第一个文件开始。前一天未消费完的数据会丢弃，这是当前设计的明确取舍。
游标只接受 `version=2`。除序号和偏移量外，还会校验 device ID、file ID、文件头 SHA-256 和游标前最多 4 KiB 内容的 SHA-256；身份或内容不匹配、旧版本以及字段损坏都会设置 `cursor_invalidated=true` 并从当前文件头安全重读。文件打开后会执行二次校验，避免校验与读取之间的同名替换竞态。

### 显式重放模式

传入 `input_path` 或 `input_paths`。显式模式：

- 不读取、不修改生产游标。
- 仍限制每批最多 10 条和 32 MiB。
- 按调用方路径顺序去重处理。
- 路径不存在时记录 `missing_files`，不回退自动目录。
- 将输出的 `next_cursor` 作为下一次输入的 `resume_cursor` 继续读取。

重放示例：

```json
{
  "input_path": "~/.flocks/workspace/workflows/stream_alert_denoise/<YYYY-MM-DD>/dedup_result_001.jsonl",
  "batch_max_records": 10,
  "batch_max_bytes": 33554432,
  "concurrency": 1,
  "triage_output_mode": "soc_db"
}
```

下一批：

```json
{
  "input_path": "~/.flocks/workspace/workflows/stream_alert_denoise/<YYYY-MM-DD>/dedup_result_001.jsonl",
  "resume_cursor": {
    "version": 2,
    "date": "<YYYY-MM-DD>",
    "file_seq": 1,
    "file_name": "dedup_result_001.jsonl",
    "byte_offset": 1837294,
    "device_id": 16777234,
    "file_id": 123456,
    "head_hash": "<64 位 SHA-256>",
    "boundary_start": 1833198,
    "boundary_hash": "<64 位 SHA-256>",
    "file_index": 0,
    "path": "~/.flocks/workspace/workflows/stream_alert_denoise/<YYYY-MM-DD>/dedup_result_001.jsonl"
  }
}
```

`resume_cursor` 应原样使用上一批返回的完整 `next_cursor`；以上数值仅展示字段结构。

## 4. 有界加载规则

| 参数 | 默认值 | 非法值处理 |
| --- | ---: | --- |
| `batch_max_records` | `10` | 回退 10 |
| `batch_max_bytes` | `33554432` | 回退 32 MiB |
| `concurrency` | `1` | 回退 1 |
| `max_triage_cache_size` | `100000` | 回退 100000 |

- 多文件按文件名中的数字序号排序，不使用字符串排序。
- 批次限制针对整个日期目录或整组显式路径，不是每个文件各算一批。
- 有效 JSON 对象计入告警数。
- header、空行、坏 JSON 和非对象不计入告警数，但完整行会推进待提交 offset。
- 未达到单行字节上限且没有换行符的末尾半行不解析、不推进 offset。
- 超大单行可跨多个批次分块跳过到下一换行符，每批实际读取量仍不超过
  `batch_max_bytes`；跨批状态由 `next_cursor` 自动携带，并记录 `oversized_lines`。
- `loaded_files` 只包含本批实际触达的文件。

## 5. 游标与提交条件

自动模式在读取游标前获取批次租约，覆盖完整的 `load -> triage -> persist -> commit`；重叠执行会以 `production_batch_lease_busy` 失败，提交或异常后释放。入口节点只计算 `pending_cursor` 和 `cursor_revision`。生产游标由 `commit_cursor` 在 `concurrent_triage` 成功后，在游标文件锁内通过 revision CAS 和单调性校验原子提交。

提交生产游标的情况：

- 所有告警研判完成，所有启用的持久化目标成功。
- 部分单条研判失败，但失败状态已经形成。
- 本批只消费了 header、空行或完整坏行。

不提交生产游标的情况：

- SOC DB 写入失败。
- 启用 JSONL 时 JSONL 写入失败。
- 研判缓存写入失败。
- 当前游标 revision 与读取时不一致，或新位置违反单调性。
- 节点超时、取消或进程退出。
- 本批没有读取任何新字节。
- 显式重放模式。

游标写入使用包含 run ID 的同目录唯一临时文件、`flush()`、`os.fsync()` 和 `os.replace()`；CAS 失败返回 `stale_cursor_commit`。文件身份失效触发的明确重置仍要求 revision 未变化，但允许偏移回到文件头。单条研判失败不会阻塞流，失败告警保留 `triage_status=failed`、`triage_error`、`triage_attack_verdict=unknown` 和 `triage_attack_success=unknown`。模型判定节点直接生成这两个字段：`triage_attack_verdict` 表示是否攻击，枚举为 `attack | non_attack | unknown`；`triage_attack_success` 表示攻击结果，枚举为 `success | failed | unknown`。原始告警的 `attack_verdict`、`attack_success`、`threat_result` 等字段保持不变，不参与研判结果字段的生成。

## 6. 输出与副作用

默认 SOC DB：

```text
~/.flocks/data/soc.db
```

可选 JSONL：

```text
~/.flocks/workspace/workflows/stream_alert_triage/<YYYY-MM-DD>/triage_result_NNN.jsonl
```

总览报告：

```text
~/.flocks/workspace/outputs/<YYYY-MM-DD>/artifacts/stream_alert_triage_summary.md
```

输出模式：

| 模式 | 行为 |
| --- | --- |
| `soc_db` | 只写 SOC DB |
| `jsonl` | 只写 JSONL |
| `both` | 两者都写，任一失败都会使批次失败且不提交游标 |
| `none` | 不写业务持久化目标，但仍可能调用 LLM 和写研判缓存 |

`persist_triage_output=true` 是兼容参数，会在 `soc_db` 模式下额外启用 JSONL。

主要增量状态输出：`cursor_enabled`、`cursor_before`、`cursor_revision`、`cursor_invalidated`、`pending_cursor`、`next_cursor`、`cursor_committed`、`committed_cursor`、`has_more`、`batch_records`、`batch_bytes` 和 `load_stats`。批次租约句柄与 token 仅用于节点间内部传递，不属于业务输出。

## 7. 调度与吞吐

工作流模板的 schedule trigger 为 5 分钟一次，保持 `noOverlap=true` 和单写者语义。每批最多 10 条时，理论上限为：

```text
10 × 12 × 24 = 2880 条/天
```

LLM、情报查询、缓存 miss 和持久化耗时会降低实际吞吐。超过当天处理能力的积压会在日期切换时丢弃。启用定时触发前必须明确说明这一容量边界。

## 8. 应用配置

应用前展示：

- 自动增量或显式重放的输入模式。
- 批次条数、字节上限、并发和缓存上限。
- 输出模式及会发生的 SOC DB、JSONL、缓存和游标写入。
- poller 的 5 分钟间隔与 `noOverlap=true`。
- 完整 diff。

用户确认后才可调用 `workflow_config_manage(action="put", workflow_id="stream_alert_triage", config_type="poller", config={...})`。不要通过直接修改 `config.json` 冒充运行态配置已经生效，也不要通过删除 `triage_cache.pkl` 重置配置。

## 9. 只读查配置

按以下顺序执行：

1. 读取本文、`workflow.md` 和 `workflow.json`。
2. 调用 `workflow_config_manage(action="get", workflow_id="stream_alert_triage")`。
3. 调用 `workflow_config_manage(action="get", workflow_id="stream_alert_triage", config_type="poller")`。
4. 后端没有模板时，只读检查工作流目录的 `config.json`。
5. 汇总输入模式、批次限制、输出方式、poller 状态、`noOverlap`、游标路径和剩余缺口。

查配置不得修改文件、触发研判、调用情报工具、写缓存、写 SOC DB、写 JSONL、修改生产游标或启动服务。
