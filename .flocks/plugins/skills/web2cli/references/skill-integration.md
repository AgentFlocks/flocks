# Web2CLI 结果如何接入 Skill

> 本文说明：`web2cli` 生成 CLI 或 device tool 后，怎样把能力沉淀成可长期维护的产品 skill。

## 结论

无论最终主实现是 CLI 还是 device tool，都必须创建或更新对应产品 skill。skill 是长期入口，负责记录触发条件、模式判断、浏览器经验、认证恢复、接口文档和回归方法。

主实现二选一：

- **CLI 主实现**：CLI 放入 skill 的 `scripts/`，skill 直接调用 CLI。
- **device tool 主实现**：device plugin 放入 `tools/device/<plugin_id>/`，skill 不放独立 CLI 主实现，只记录 device tool 的使用、验证和认证恢复方式。

## 命名约定

- skill 目录：`$HOME/.flocks/plugins/skills/<name>-use/`
- 默认认证状态：`~/.flocks/browser/<name>/auth-state.json`
- `<name>` 使用产品或系统的稳定标识，不用一次性任务名
- 目录名可以保留 `-`，例如 `tdp-use`
- Python 文件名统一用 `_`

不要把最终能力命名成 `export_data`、`test_capture`、`web2cli_demo` 这类临时任务名。

## 共用 Skill 结构

CLI 和 device tool 两种场景都必须保留这些文件：

```text
$HOME/.flocks/plugins/skills/<name>-use/
├── SKILL.md
└── references/
    ├── browser-workflow.md
    └── cli-reference.md
```

其中：

- `SKILL.md`：定义触发条件、模式判断、主实现落点和退回浏览器的条件
- `references/browser-workflow.md`：记录登录入口、保存 state、认证恢复、页面操作经验和重新抓包方法
- `references/cli-reference.md`：记录 CLI 或 device tool 的能力、参数、输出字段、验证方式和回归方法

`references/cli-reference.md` 是历史沿用的统一接口文档名。即使主实现是 device tool，也继续使用这个文件承载 device tool 的参数、输出和验证说明。

## CLI 主实现的 Skill 集成

如果第 8 步选择通用 CLI 作为主实现，skill 还必须包含：

```text
$HOME/.flocks/plugins/skills/<name>-use/
└── scripts/
    ├── <name>_cli.py
    └── config.py
```

从临时抓包结果集成到 skill 时：

```bash
SKILL_ROOT="$HOME/.flocks/plugins/skills/<name>-use"

mkdir -p "$SKILL_ROOT/scripts" "$SKILL_ROOT/references"
mkdir -p "$HOME/.flocks/browser/<name>"

cp "$CAPTURE_ROOT/<normalized_capture_name>_cli.py" \
  "$SKILL_ROOT/scripts/<name>_cli.py"

cp "$CAPTURE_ROOT/auth-state.json" \
  "$HOME/.flocks/browser/<name>/auth-state.json"
```

随后补齐：

1. 在 `scripts/config.py` 中把认证状态默认值指向 `~/.flocks/browser/<name>/auth-state.json`
2. 在 `references/cli-reference.md` 中写清楚 CLI 参数、环境变量、输出字段和示例
3. 在 `references/browser-workflow.md` 中写清楚浏览器登录、保存 state、认证恢复和重新抓包步骤
4. 在 `SKILL.md` 中说明什么时候优先走 CLI，什么时候退回浏览器

推荐的配置写法：

```python
import os
from pathlib import Path

AUTH_STATE_FILE = Path(
    os.getenv(
        "<NAME>_AUTH_STATE",
        Path.home() / ".flocks" / "browser" / "<name>" / "auth-state.json",
    )
)
```

## Device Tool 主实现的 Skill 集成

如果第 8 步选择 device plugin 作为主实现，skill 只放维护入口和文档，不放 `scripts/<name>_cli.py` 主实现。

必须补齐：

1. 在 `SKILL.md` 中说明当前能力最终落点是 `tools/device/<plugin_id>/`
2. 在 `references/cli-reference.md` 中写清楚 device tool 的 action、参数、输出字段、验证方式和回归方法
3. 在 `references/browser-workflow.md` 中写清楚浏览器登录、保存 state、认证恢复、重新抓包步骤和 device 配置依赖
4. 将认证状态默认位置统一到 `~/.flocks/browser/<name>/auth-state.json`

device 场景不要在 skill 的 `scripts/` 目录下放一份与 device tool 平行演进的 CLI 主实现。如确实需要 CLI 做调试或回归，只能放在 device plugin 目录下作为可选辅助文件，并在 `references/cli-reference.md` 明确它不是运行时主路径。

## `SKILL.md` 要求

`SKILL.md` 必须遵守 Flocks 的标准 skill 格式：

- 文件开头必须是 YAML frontmatter，第一行必须为 `---`
- frontmatter 至少包含 `name` 和 `description`
- `name` 使用稳定的 skill 标识，推荐与目录名一致，例如 `<name>-use`
- frontmatter 结束后，再写正文标题、触发条件、模式判断和使用说明

最小模板示例：

```md
---
name: test-use
description: 用于查询 Test 平台数据，支持通过 CLI 或 device tool 快速查询，认证失效时退回浏览器模式。
---

# Test Use

## 触发条件

- 用户提到 Test 平台
- 用户需要查询 Test 数据

## 模式判断

### CLI / Device Tool 模式（默认）

- 适用于快速查询和批量读取数据

### 浏览器模式

- 适用于需要页面交互、导出、重新登录或重新抓包的场景
```

不要把 `SKILL.md` 直接写成普通 Markdown 文档，例如下面这种格式是无效的：

```md
# Test Use
```

## `browser-workflow.md` 写作指南

推荐写入：

- 固定的登录入口、首页、详情页、导出页 URL
- 已确认的稳定登录方法
- 认证失效识别与恢复步骤
- 已验证的页面操作路径、等待条件、iframe、虚拟列表或 SPA 特征
- 默认 state 路径，例如 `~/.flocks/browser/<name>/auth-state.json`
- CLI / device tool 与浏览器的分工边界
- web2cli 过程中的踩坑、注意事项

不要写入：

- cookie、token、密码、短信码、TOTP 等敏感信息
- 一次性的 `@eN` ref、临时 tab id、临时 selector、像素坐标
- 本次任务的操作流水账

## 认证失败怎么处理

CLI 或 device tool 调用出现以下情况时，优先按认证失效处理：

- 返回 `401` 或 `403`
- 返回内容出现 `Unauthorized`、`login`、未登录、无权限
- `auth-state.json` 已存在，但请求仍然被重定向到登录页

处理原则：

1. 不要无限重试
2. 请求用户重新通过浏览器登录
3. 登录完成后，重新保存认证状态到默认路径
4. 再重试一次 CLI 或 device tool

默认认证文件路径固定为：

```bash
~/.flocks/browser/<name>/auth-state.json
```

保存方式示例：

```bash
mkdir -p "$HOME/.flocks/browser/<name>"
flocks browser state save "$HOME/.flocks/browser/<name>/auth-state.json"
```

如果用户重新登录并保存 state 后仍然失败，再继续排查：

- `BASE_URL` 是否写错
- 当前账号是否确实有接口权限
- 站点是否还有额外 header / token / csrf 依赖

## 一句话原则

`web2cli` 的临时抓包结果不是最终交付。最终要么沉淀为 skill `scripts/` 下的稳定 CLI，要么沉淀为 device plugin 下的 device tool；两种方式都必须配套产品 skill 文档入口和统一认证状态路径。
