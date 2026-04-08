# Flocks Project Instructions

## 文件输出约定（全局强制）

**所有 Agent（Rex 及所有子 Agent）在写文件时，若无明确指定路径，必须遵守以下约定。**

### 默认输出目录

所有输出文件写入 `~/.flocks/workspace/outputs/<YYYY-MM-DD>/`，日期在**执行时**动态获取（不能依赖 session 启动时注入的 `<env>` 值，因为 session 可能跨天运行）。

| 文件类型 | 默认路径 |
|---|---|
| 分析报告、汇总结果、最终输出 | `~/.flocks/workspace/outputs/<today>/` |
| LLM 中间推理落盘（workflow 节点内） | `~/.flocks/workspace/outputs/<today>/artifacts/` |
| 临时调试/草稿文件 | `/tmp/` |

### 代码示例（workflow 节点 / Python 脚本）

```python
import os, datetime
from flocks.workspace.manager import WorkspaceManager

# 在执行时动态取当日日期，不依赖 session 启动时的注入值
ws = WorkspaceManager.get_instance()
output_dir = str(ws.get_workspace_dir() / 'outputs' / datetime.date.today().isoformat())
os.makedirs(output_dir, exist_ok=True)

# 写报告
tool.run('write', filePath=os.path.join(output_dir, 'final_report.md'), content=report)
# 写 LLM 中间输出
artifacts_dir = os.path.join(output_dir, 'artifacts')
os.makedirs(artifacts_dir, exist_ok=True)
tool.run('write', filePath=os.path.join(artifacts_dir, 'payload_analysis.md'), content=llm_output)
```

### 何时可以使用其他路径

- 用户在 prompt 中**明确指定**了输出路径（优先尊重用户指定）
- workflow 定义文件（`workflow.json`、`workflow.md`）写入规范目录：`~/.flocks/plugins/workflows/<id>/`（用户级）或 `<workspace>/.flocks/plugins/workflows/<id>/`（项目级）；旧路径 `~/.flocks/workflow/` 等仍可被扫描兼容
- 插件/工具等系统文件仍写入 `~/.flocks/plugins/`

### ⚠️ 明确禁止

- **禁止**将输出文件写入项目代码目录下的 `artifacts/`（污染代码仓库）
- **禁止**硬编码任何用户相关绝对路径（如 `/Users/xxx/...`）
- **禁止**将报告写入 `logs/`、`tests/`、`docs/` 等功能目录

---

## Capability Gap Resolution Protocol

**This protocol is mandatory for Rex and all primary agents.**

When you encounter a **capability gap** — a situation where you cannot complete an assigned task because a required tool, library, or integration does not exist — you MUST follow this protocol. Do NOT stop and tell the user you cannot do it.

A capability gap is distinct from a tool execution failure. Examples:
- "I cannot send email because there is no email tool" → **capability gap**
- "The email tool failed because the SMTP server rejected the password" → **credentials issue, ask user**
- "I cannot access the database because no DB tool exists" → **capability gap**
- "The bash command failed due to a syntax error" → **execution error, fix and retry**

### When to Trigger This Protocol

Trigger this protocol when you recognize any of these patterns:
- You are about to say "I don't have a tool for..." or "I cannot do X because..."
- A required Python library is not installed
- An external service integration (email, notification, DB, file format) is missing
- You have attempted to solve a problem and the only blocker is a missing capability

### Resolution Steps

**Step 1 — Quick self-check (< 30 seconds)**

Before delegating, ask yourself:
- Can `bash` + Python standard library handle this? (smtplib, urllib, json, csv, sqlite3 are always available)
- Can I write a one-off script with existing tools?
- Is there an installable skill? Use `flocks_skills(subcommand="find", args="<keyword>")` to check.

If yes: solve it directly. No delegation needed.

**Step 2 — Delegate to `self-enhance`**

If the gap requires installing packages or building a new tool, delegate immediately:

```
delegate_task(
    subagent_type="self-enhance",
    prompt="[Describe the exact capability needed and the context]

Context: [What the main task is trying to accomplish]
Capability needed: [Specific description, e.g. 'send email via SMTP or API']
Constraints: [Any relevant constraints, e.g. 'must work without user interaction', 'email server unknown']
",
    run_in_background=False
)
```

**Step 3 — Use the result**

When `self-enhance` returns:
- If it reports `CAPABILITY ACQUIRED`: immediately use the new tool it created to complete the task
- If it reports `CAPABILITY NOT ACQUIRED`: inform the user with the list of attempted approaches and what specific input is needed (e.g., "please provide SMTP credentials")

**Step 4 — Only give up after genuine effort**

You may tell the user "I cannot do this" ONLY after:
1. The `self-enhance` agent has been invoked and also failed
2. You have clearly explained what was tried and why it failed
3. You specify exactly what the user needs to provide for the task to succeed

### What self-enhance Can Do

The `self-enhance` agent is capable of:
- Writing and testing Python scripts using the standard library
- Installing PyPI packages inside the project virtualenv via `source .venv/bin/activate && uv add ...`
- Creating permanent Flocks plugin tools using the `tool-builder` skill
- Configuring MCP servers for complex integrations
- Researching solutions via `websearch` and `webfetch`

### Security Constraints (apply to all agents)

These constraints apply when acquiring new capabilities:

| Allowed | Prohibited |
|---|---|
| `source .venv/bin/activate && uv add ...` from PyPI | `sudo`, `su`, elevated privileges |
| Writing scripts to `/tmp` or project dirs | Downloading binary executables |
| Creating plugins in `~/.flocks/plugins/` | Installing from non-PyPI sources |
| Installing into the project virtualenv | Modifying system Python or `/usr/` |
| Storing secrets via `get_secret_manager()` | Hardcoding credentials in code |

If a capability requires elevated privileges or system-level access: **stop, explain to the user, and ask them to perform that step manually**.

## Skill Discovery Protocol

Rex has a dedicated `flocks_skills` tool for managing agent skills.
**Use it proactively** — do not wait for the user to ask.

| Situation | Action |
|---|---|
| User says "find a skill for X" | `flocks_skills(subcommand="find", args="X")` |
| You are about to say "I can't do X" | Run `find` first; a skill may exist |
| User says "install this skill" | `flocks_skills(subcommand="install", args="<source>")` |
| After any install | `flocks_skills(subcommand="status")` to check deps |
| Status shows unmet deps | `flocks_skills(subcommand="install-deps", args="<name>")` |

---

### Examples

**Example 1: Email notification**
> Task: "After completing the investigation, send an email summary to security@company.com"
>
> Rex detects: no email tool exists.
> Rex delegates: `delegate_task(subagent_type="self-enhance", prompt="Need email sending capability. Task: send investigation summary to security@company.com after analysis is complete.")`
> self-enhance creates: `send_email` plugin tool using smtplib or an email API
> Rex uses: `send_email(to="security@company.com", subject="Investigation Summary", body="...")`

**Example 2: Excel report generation**
> Task: "Export the findings to an Excel file"
>
> Rex delegates to self-enhance → self-enhance installs `openpyxl` → creates `generate_excel_report` tool → Rex uses it.

**Example 3: Slack notification**
> Task: "Post a Slack message when done"
>
> Rex delegates to self-enhance → self-enhance creates YAML-HTTP tool for Slack webhook → Rex calls `slack_send_message(webhook_url="...", text="...")`.
> Note: Rex then asks user for the Slack webhook URL if not in secrets.

## Important
- 涉及 `tdp`、`onesec`、`skyeye`、`qingteng` 的任务时，必须先读取并遵循对应的 skill。
- 对上述系统，禁止绕过对应 skill 直接调用相关 tools；也不要直接使用 `agent-browser`。