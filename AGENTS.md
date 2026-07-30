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

### 何时可以使用其他路径

- 用户在 prompt 中**明确指定**了输出路径（优先尊重用户指定）
- workflow 定义文件（`workflow.json`、`workflow.md`）写入规范目录：`~/.flocks/plugins/workflows/<id>/`（用户级）或 `<workspace>/.flocks/plugins/workflows/<id>/`（项目级）；旧路径 `~/.flocks/workflow/` 等仍可被扫描兼容
- 插件/工具等系统文件仍写入 `~/.flocks/plugins/`

### ⚠️ 明确禁止

- **禁止**将输出文件写入项目代码目录下的 `artifacts/`（污染代码仓库）
- **禁止**硬编码任何用户相关绝对路径（如 `/Users/xxx/...`）
- **禁止**将报告写入 `logs/`、`tests/`、`docs/` 等功能目录

---

## PowerShell 脚本编码规范

新建或修改 `.ps1` 文件后，必须使用 **UTF-8 with BOM**（文件头 `EF BB BF`）+ **CRLF** 换行符。

原因：Windows PowerShell 5.1 读取无 BOM 的 UTF-8 文件时会使用系统代码页（如 GBK）解码，可能导致中文字符字节错位，引号/大括号被吞，产生级联解析错误。

验证编码（PowerShell）：

```powershell
$bytes = [System.IO.File]::ReadAllBytes("script.ps1")
$bytes[0..2] | ForEach-Object { $_.ToString("X2") }
# 正确输出：EF BB BF
```

保存为正确编码：

```powershell
$content = Get-Content "script.ps1" -Raw
[System.IO.File]::WriteAllText("script.ps1", $content, [System.Text.UTF8Encoding]::new($true))
```

## Skill Discovery Protocol

Rex has a dedicated `flocks_skills` tool for managing agent skills.
**Use it proactively** — do not wait for the user to ask.

| Situation | Action |
|---|---|
| User says "find a skill for X" | `flocks_skills(subcommand="find", args="X")` |
| User says "install this skill" | `flocks_skills(subcommand="install", args="<source>")` |
| After any install | `flocks_skills(subcommand="status")` to check deps |
| Status shows unmet deps | `flocks_skills(subcommand="install-deps", args="<name>")` |

---

## Important
- 涉及 `tdp`、`onesec`、`skyeye`、`qingteng` 的任务时，必须先读取并遵循对应的 skill。
- 对上述系统，禁止绕过对应 skill 直接调用相关 tools；也不要直接使用 `browser`。
