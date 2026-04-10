# 批量主机快速巡检（循环子 Agent）

## 业务场景

对多台 Linux 主机依次执行快速安全巡检（首轮研判）：每台主机由子 Agent `host-forensics-fast` 完成轻量 triage 与结论输出。适用于批量资产排查、挖矿/异常快速过筛等场景。

本工作流的关键目标是避免“循环结束后最后一个节点拿到全量巡检正文再做 summary”导致超时，因此策略改为：

- 每巡检完一台主机，立即把完整结果写入 `host_triage/` 下的独立 Markdown 文件。
- 工作流循环态只保留轻量索引信息，不在 `triage_results` 中累计完整正文。
- 末步不再做 LLM 汇总，只生成索引文件、manifest 和轻量结果 JSON，并返回一段很短的执行摘要。

## 输入参数（工作流 `inputs`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `hosts_file` | 可选 string | 主机列表文件绝对路径（或 `~` 开头）。每行一个 SSH 目标（主机名或可解析地址），空行与 `#` 开头行忽略。 |
| `hosts` | 可选 list[str] | 直接内嵌的主机列表；可与文件合并（先读文件再并入列表，去重保序）。 |
| `hosts` 为 string | 少见 | 单主机字符串时视为单元素列表。 |
| `ssh_user` | 可选 string | 指定 SSH 登录用户。若提供且列表项不含 `user@host` 形式，则子 Agent 的 `ssh_run_script` 使用 `ssh_user@<列表中的主机项>` 作为 `host` 参数。若不提供，则使用列表中的主机标识本身（由 Agent/工具默认账户连接，一般为 root）。 |

若两者都未提供有效主机，工作流跳过巡检循环，仅输出无主机的轻量结果。

若 `hosts` 中某项已含 `@`（如 `flocks@web01.example.internal`），则不再与 `ssh_user` 拼接，该项按原样作为 SSH 目标。

## 流程步骤

### 1. 初始化（`init_hosts`）

- 工具/模型：Python（`WorkspaceManager` + 文件系统）
- 输入：`hosts_file`、`hosts`、可选 `ssh_user`
- 处理逻辑：
  - 解析主机列表并去重保序。
  - 规范化 `ssh_user`。
  - 创建输出目录与 `host_triage/` 目录。
  - 初始化 `batch_host_triage_log.md` 文件头。
  - 初始化循环状态：`host_idx=0`、`triage_results=[]`、`should_continue`。
- 输出：`hosts`、`ssh_user`、`host_idx`、`triage_results`、`output_dir`、`per_host_dir`、`batch_report_path`、`should_continue`

说明：这里的 `triage_results` 从现在开始只保存轻量索引，不保存每台机器的完整正文。

### 2. 循环判断（`loop_check`）

- 工具/模型：`loop` 节点，`select_key` 为 `should_continue`
- 决策分支：
  - `continue`：进入单台巡检
  - `exit`：进入末步索引生成

### 3. 单台巡检（`inspect_host`）

- 工具/模型：`task`（`subagent_type=host-forensics-fast`）
- 输入：`hosts`、`host_idx`、`ssh_user`、`per_host_dir`、`batch_report_path`、`triage_results`
- 处理逻辑：
  - 取当前 `hosts[host_idx]`，计算 `ssh_target`。
  - 构造 prompt，明确要求 `ssh_run_script` 的 `host` 参数必须使用 `ssh_target`。
  - 调用 `tool.run_safe('task', ...)` 执行巡检。
  - 将本轮完整输出立即写入 `host_triage/NNNN_slug.md`。
  - 向 `triage_results` 仅追加轻量字段：`{host, ssh_user, ssh_target, success, error, per_host_md}`。
  - 向 `batch_host_triage_log.md` 追加一段索引信息，不再追加完整正文。
- 输出：`triage_results`、`last_host`、`last_ssh_target`、`last_success`、`last_per_host_md`

### 4. 前进下标（`advance_index`）

- 工具/模型：Python
- 处理逻辑：`host_idx += 1`；若仍小于 `len(hosts)` 则 `should_continue='continue'`，否则为 `exit`
- 输出：`host_idx`、`should_continue`

### 5. 生成索引与清单（`finalize_summary`）

- 工具/模型：Python
- 输入：`triage_results`、`hosts`、`ssh_user`、`per_host_dir`、`batch_report_path`
- 处理逻辑：
  - 不调用 LLM。
  - 基于轻量索引生成 `batch_host_triage_index.md`。
  - 生成 `batch_host_triage_manifest.json`。
  - 生成 `batch_host_triage_results.json`，其中 `triage_results` 仅包含轻量字段，不包含每台完整正文。
  - 输出一段短 `executive_summary`，只说明总数、成功失败数和结果文件位置。
- 输出：`executive_summary`、`index_path`、`manifest_path`、`results_json_path`、`batch_report_path`、`per_host_dir`

## 文件输出约定

- 目录：`~/.flocks/workspace/outputs/<执行当日 YYYY-MM-DD>/`
- 逐台完整结果：`host_triage/<序号>_<主机>.md`
- 循环日志：`batch_host_triage_log.md`
- 末步索引：`batch_host_triage_index.md`
- 末步清单：`batch_host_triage_manifest.json`
- 末步轻量结果：`batch_host_triage_results.json`

## 设计说明

- 大字段正文只写磁盘，不在循环状态里累积。
- 最后一个节点只处理轻量索引，因此主机数很多时也不容易超时。
- 若需要查看某台机器的完整分析，直接打开对应的 `host_triage/*.md` 即可。

## 样例 inputs

指定 SSH 用户：

```json
{
  "hosts": ["web01.example.internal", "db01.example.internal"],
  "ssh_user": "flocks"
}
```

不指定用户：

```json
{
  "hosts": ["app01.example.internal"]
}
```

从文件合并列表：

```json
{
  "hosts_file": "/path/to/hosts.txt",
  "hosts": ["svc-a.example.internal", "svc-b.example.internal"]
}
```
