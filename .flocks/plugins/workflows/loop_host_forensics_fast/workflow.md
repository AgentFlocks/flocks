# 批量主机快速巡检（循环子 Agent）

## 业务场景

对多台 Linux 主机依次执行**快速安全巡检**（首轮研判）：每台主机由子 Agent `host-forensics-fast` 完成轻量 triage 与结论输出。适用于批量资产排查、挖矿/异常快速过筛等，结果**追加写入**同一份巡检日志文件，最后生成**中文 Markdown 总结报告**。

## 输入参数（工作流 `inputs`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `hosts_file` | 可选 string | 主机列表文件绝对路径（或 `~` 开头）。每行一个主机标识（IP/主机名/SSH 目标），空行与 `#` 开头行忽略。 |
| `hosts` | 可选 list[str] | 直接内嵌的主机列表；可与文件合并（先读文件再并入列表，去重保序）。 |
| `hosts` 为 string | 少见 | 单主机字符串时视为单元素列表。 |

若两者都未提供有效主机，工作流跳过巡检循环，仅输出「无主机」类总结。

## 流程步骤

### 1. 初始化（init_hosts）

- **工具/模型**: Python（WorkspaceManager + 文件系统）
- **输入**: `hosts_file`、`hosts`
- **处理逻辑**:
  - 解析主机列表；写入当日输出目录下的 `batch_host_triage_log.md` 的**文件头**（覆盖写入，仅初始化一次）。
  - 设置 `host_idx=0`、`triage_results=[]`、`batch_report_path`、`should_continue`（有主机则为 `continue`，否则 `exit`）。
- **输出**: `hosts`、`host_idx`、`triage_results`、`batch_report_path`、`should_continue`

### 2. 循环判断（loop_check）

- **工具/模型**: `loop` 节点，`select_key`: `should_continue`
- **决策分支**:
  - `continue`：进入单台巡检
  - `exit`：进入总结

### 3. 单台巡检（inspect_host）

- **工具/模型**: Tool: `task`（`subagent_type=host-forensics-fast`）
- **输入**: `hosts`、`host_idx`、`batch_report_path`、`triage_results`
- **处理逻辑**:
  - 取当前 `hosts[host_idx]`，构造 prompt，要求子 Agent 按 `host-forensics-fast` 规范使用 `ssh_run_script` 执行 `triage_fast.sh` 等步骤。
  - `tool.run_safe('task', description=..., prompt=..., subagent_type='host-forensics-fast', run_in_background=False)`。
  - 将本轮结果追加写入 `batch_report_path`（Markdown 小节）；向 `triage_results` 追加 `{host, success, text, error}`。
- **输出**: `triage_results`、`last_host`、`last_success`

### 4. 前进下标（advance_index）

- **工具/模型**: Python
- **处理逻辑**: `host_idx += 1`；若仍小于 `len(hosts)` 则 `should_continue='continue'`，否则 `'exit'`。
- **输出**: `host_idx`、`should_continue`

### 5. 总结报告（finalize_summary）

- **工具/模型**: LLM: `llm.ask` + Python 写文件
- **输入**: `triage_results`、`hosts`、`batch_report_path`
- **处理逻辑**:
  - 若无结果：生成简短说明性总结。
  - 若有结果：基于逐台摘要与截断后的正文调用 LLM 生成中文 Markdown（执行摘要、逐台要点、整体风险与建议）。
  - **末步必须落盘全部结果**（与循环中追加的日志互补）：
    - `batch_host_triage_summary.md`：LLM 总结（UTF-8）。
    - `batch_host_triage_results.json`：完整结构化数据（`date`、`hosts`、`batch_report_path`、`summary_path`、`triage_results` 全量字段，**不截断**）。
    - `batch_host_triage_raw_all.md`：逐台完整正文与 error 的人类可读汇总（**不截断**）。
- **输出**: `executive_summary`、`summary_path`、`results_json_path`、`raw_all_path`、`batch_report_path`

## 文件输出约定

- 目录：`~/.flocks/workspace/outputs/<执行当日 YYYY-MM-DD>/`
- 循环中追加日志：`batch_host_triage_log.md`
- 末步输出：`batch_host_triage_summary.md`、`batch_host_triage_results.json`、`batch_host_triage_raw_all.md`

## 样例 inputs

```json
{
  "hosts_file": "/path/to/hosts.txt",
  "hosts": ["10.0.0.1", "10.0.0.2"]
}
```

或仅内嵌：

```json
{
  "hosts": ["192.168.1.10", "192.168.1.11"]
}
```
