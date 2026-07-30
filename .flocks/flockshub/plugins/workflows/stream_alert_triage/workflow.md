# stream_alert_triage

`stream_alert_triage` 是 `stream_alert_denoise` 的下游 HTTP 告警研判工作流。它按游标增量读取去重结果，完成 leader/follower 研判和持久化后再提交生产游标，避免日期目录数据量增长时全量加载导致 OOM。

## 流程

```text
load_dedup_file -> concurrent_triage -> commit_cursor -> summarize
```

节点边使用显式字段映射并启用严格映射模式，只保留下一节点必需的批次、游标、研判和汇总字段，不透传完整上游 payload。

| 节点 | 职责 |
| --- | --- |
| `load_dedup_file` | 以二进制方式有界读取 JSONL，只产生待提交游标，不写生产游标 |
| `concurrent_triage` | 按 `dedup_key` 分组研判、复用缓存，并写入所有启用的持久化目标 |
| `commit_cursor` | 上一步整体成功后原子提交生产游标；显式重放不写游标 |
| `summarize` | 生成总览并暴露最终结构化输出 |

## 加载边界

默认单批限制：

```json
{
  "batch_max_records": 10,
  "batch_max_bytes": 33554432,
  "concurrency": 1,
  "max_triage_cache_size": 100000,
  "triage_output_mode": "soc_db"
}
```

- `batch_max_records` 和 `batch_max_bytes` 必须是大于 0 的整数，非法值分别回退为 10 和 32 MiB。
- 条数只统计有效 JSON 告警对象；header、空行、坏 JSON 和非对象会推进待提交 offset，但不占告警条数。
- 未达到单行字节上限且没有换行符的末尾半行不解析、不推进 offset。
- 超大单行使用固定大小分块跨批跳过，跨批状态保存在游标中；每批实际读取量仍受
  `batch_max_bytes` 限制，不会整体读入内存或永久阻塞消费。
- 多文件按 `dedup_result_NNN.jsonl` 的数字序号排序，可正确跨越 999 → 1000。
- `loaded_files` 只返回本批实际触达的文件。

## 运行模式

### 自动目录模式

未传 `input_path` 和 `input_paths` 时启用。`input_date` 未设置则每次执行动态计算当天日期，并扫描：

```text
~/.flocks/workspace/workflows/stream_alert_denoise/<YYYY-MM-DD>/dedup_result_NNN.jsonl
```

生产游标位于：

```text
~/.flocks/workspace/workflows/stream_alert_triage/.triage_cursor.json
```

日期变化时直接从新日期的第一个文件开始；旧日期未消费完的数据按设计丢弃，不跨天补偿。
游标只接受 `version=2`，除序号和偏移量外还保存 device ID、file ID、文件头 SHA-256 以及游标前最多 4 KiB 内容的 SHA-256。恢复时任一文件身份或内容锚点不匹配都会设置 `cursor_invalidated=true`，并从该文件头重新读取；旧版或损坏游标也按同样方式安全重置。打开文件后会再次校验，避免校验与读取之间发生同名替换而沿用旧偏移。

### 显式重放模式

传入 `input_path` 或 `input_paths` 时进入重放模式：

- 仍受 10 条和 32 MiB 限制。
- 不读取、也不修改生产游标。
- 将返回的 `next_cursor` 作为下一次的 `resume_cursor`，即可继续读取。
- 显式路径不存在时只记录统计，不回退到自动日期目录。

## 游标提交语义

自动模式在读取游标前获取工作流级批次租约，租约覆盖 `load -> triage -> persist -> commit`，异常或提交完成后释放。`load_dedup_file` 只输出 `pending_cursor` 和读取时的 `cursor_revision`。只有 `concurrent_triage` 完成研判、缓存和所有启用的持久化目标成功后，`commit_cursor` 才在独立游标锁内执行 revision CAS 与单调性校验，并通过 run 级唯一临时文件、`flush`、`fsync` 和 `os.replace` 原子写入生产游标。文件身份失效触发的明确重置仍需通过 CAS，但允许偏移回到文件头。

| 情况 | 推进生产游标 |
| --- | --- |
| 所有告警研判与持久化成功 | 是 |
| 单条研判失败，但失败状态已形成 | 是 |
| 只消费 header、空行或完整坏行 | 是 |
| SOC DB 或启用的 JSONL 写入失败 | 否 |
| 研判缓存写入失败 | 否 |
| 游标 revision 已被其他执行修改 | 否，返回 `stale_cursor_commit` |
| 节点超时、取消或进程退出 | 否 |
| 没有读取任何新字节 | 否 |
| 显式重放模式 | 否 |

单条研判失败会保存 `triage_status=failed`、`triage_error`、`triage_attack_verdict=unknown` 和 `triage_attack_success=unknown`，不会阻塞整个数据流。模型判定节点直接生成这两个字段：`triage_attack_verdict` 表示是否攻击，枚举为 `attack | non_attack | unknown`；`triage_attack_success` 表示攻击结果，枚举为 `success | failed | unknown`。原始告警的 `attack_verdict`、`attack_success`、`threat_result` 等字段保持不变，不参与研判结果字段的生成。整体持久化失败会抛出异常，使游标保持不变。

```json
{
  "triage_attack_verdict": "attack",
  "triage_attack_success": "failed"
}
```

## 研判与持久化

- 同批相同 `dedup_key` 只研判 leader，followers 复用结果。
- 跨批命中 `triage_cache.pkl` 时直接复用，不调用 LLM；缓存锁覆盖 cache miss、LLM 研判和保存阶段，避免并发执行重复研判相同批次。
- 缓存使用 run 级唯一临时文件原子保存；保存失败会抛出异常并阻止生产游标提交。
- 默认 `triage_output_mode=soc_db`；也支持 `jsonl`、`both` 和 `none`。
- SOC DB 只接收明确 `is_duplicate=false`、有非空 `dedup_key` 且批内首次出现的告警。
- 研判正文只保存在 `triage_report` 字段，不生成逐告警 markdown 文件。

## 主要输出

除原有研判输出外，增量加载还返回：

| 字段 | 说明 |
| --- | --- |
| `cursor_enabled` | 是否为自动目录生产模式 |
| `cursor_before` | 本次读取前的生产或重放游标 |
| `cursor_revision` | 本次读取到的生产游标内容摘要，用于提交 CAS |
| `cursor_invalidated` | 文件身份、内容锚点或游标结构失效后是否从文件头重置 |
| `cursor_commit_error` | 游标提交错误；并发 revision 变化时为 `stale_cursor_commit` |
| `pending_cursor` | 本批成功消费字节之后的待提交位置 |
| `next_cursor` | 显式重放调用方可回传的续读位置 |
| `cursor_committed` | 本次是否实际写入生产游标 |
| `committed_cursor` | 提交后的生产游标 |
| `has_more` | 当前输入中是否还有未消费字节 |
| `batch_records` | 本批有效告警数，最大 10 |
| `batch_bytes` | 本批受字节预算约束的读取量 |

## 调度与容量

工作流内置的 schedule trigger 间隔为 5 分钟，且保持 `noOverlap=true`。默认每批 10 条时理论最大吞吐为：

```text
10 × 12 × 24 = 2880 条/天
```

实际吞吐还受 LLM、情报查询、持久化耗时和 cache miss 数量影响。超过当天处理能力的积压会在日期切换时丢弃。

## 验证清单

- 单文件 20 条分两批读取，提交后第二批不重复第一批。
- 当前文件追加数据后从旧 EOF 继续。
- 文件 001 剩余 6 条、002 有更多数据时，本批读取 6 + 4。
- header、空行、坏 JSON、非对象、半行和超大行符合各自 offset 规则。
- SOC DB 或 JSONL 写失败时游标不变。
- 缓存写失败、重叠生产执行或 stale cursor commit 均不会推进游标。
- 同名文件替换及同 inode 重写会使 v2 游标失效并从文件头重读。
- 显式重放可通过 `next_cursor` 续读且不污染生产游标。
- 工作流 JSON 可解析，所有节点 Python 代码可通过 AST 解析，相关测试通过。
