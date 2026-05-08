# 生成后的 CLI 如何接入 Skill

> 本文只说明一件事：`web2cli` 已经生成出 CLI 之后，怎样把它整理成可长期维护的 skill 资产。

## 命名约定

生成阶段的文件名通常来自抓包名，例如 `<capture_name>_cli.py`。这个名字适合临时验证，不适合直接沉淀到 skill。

落到 skill 时，统一改成**稳定的产品名**：

- skill 目录：`$HOME/.flocks/plugins/skills/<name>-use/`
- CLI 主脚本：`$HOME/.flocks/plugins/skills/<name>-use/scripts/<name>_cli.py`
- 默认认证状态：`~/.flocks/browser/<name>/auth-state.json`

约定说明：

- `<name>` 用产品或系统的稳定标识，不用一次性任务名
- 目录名可以保留 `-`，例如 `tdp-use`
- Python 脚本名统一用 `_`，例如 `tdp_cli.py`
- 不要把最终 CLI 保留成 `export_data_cli.py`、`test_capture_cli.py` 这类临时名字

## 放到已有产品 Skill

如果仓库里已经有对应产品 skill，直接把生成结果并入现有 skill：

```bash
SKILL_ROOT="$HOME/.flocks/plugins/skills/<name>-use"

mkdir -p "$SKILL_ROOT/scripts"
mkdir -p "$HOME/.flocks/browser/<name>"

cp "$CAPTURE_ROOT/<normalized_capture_name>_cli.py" \
  "$SKILL_ROOT/scripts/<name>_cli.py"

cp "$CAPTURE_ROOT/auth-state.json" \
  "$HOME/.flocks/browser/<name>/auth-state.json"
```

然后补齐这几项：

1. 在 `scripts/config.py` 中把认证状态默认值指向 `~/.flocks/browser/<name>/auth-state.json`
2. 在 `references/cli-reference.md` 中写清楚 CLI 用法、环境变量和示例
3. 在 `references/browser-workflow.md` 中写清楚浏览器登录与保存 state 的流程
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

这样做的好处是：

- 默认行为统一，和现有产品 skill 保持一致
- 允许用户用环境变量覆盖
- 生成阶段的临时产物和最终长期使用的认证文件分离

## 生成新的 Skill

如果当前仓库里还没有对应产品 skill，就按下面的最小结构创建：

```text
$HOME/.flocks/plugins/skills/<name>-use/
├── SKILL.md
├── scripts/
│   ├── <name>_cli.py
│   └── config.py
└── references/
    ├── browser-workflow.md
    └── cli-reference.md
```

其中 `SKILL.md` 必须遵守 Flocks 的标准 skill 格式：

- 文件开头必须是 YAML frontmatter，第一行必须为 `---`
- frontmatter 至少包含 `name` 和 `description`
- `name` 使用稳定的 skill 标识，推荐与目录名一致，例如 `<name>-use`
- frontmatter 结束后，再写正文标题、触发条件、模式判断和使用说明

最小模板示例：

```md
---
name: test-use
description: 用于查询 Test 测试平台数据，支持通过 CLI 快速查询，认证失效时退回浏览器模式。
---

# Test Use

## 触发条件

- 用户提到 Test 平台
- 用户需要查询 Test 数据

## 模式判断

### CLI 模式（默认）

- 适用于快速查询和批量读取数据

### 浏览器模式

- 适用于需要页面交互、导出或重新登录的场景
```

不要把 `SKILL.md` 直接写成普通 Markdown 文档，例如下面这种格式是无效的：

```md
# Test Use
```

各文件职责：

- `SKILL.md`：定义触发条件、模式判断、总入口说明
- `scripts/<name>_cli.py`：承载生成并整理后的 CLI 能力
- `scripts/config.py`：集中管理 `BASE_URL`、`AUTH_STATE_FILE`、超时、SSL 等默认配置
- `references/browser-workflow.md`：写浏览器登录、保存 state、认证恢复流程
- `references/cli-reference.md`：写 CLI 参数、命令示例、常见查询

新 skill 的原则也一样：先把生成的 CLI 改成稳定文件名，再把临时 `auth-state.json` 切换到全局默认位置 `~/.flocks/browser/<name>/auth-state.json`。

## 认证失败怎么处理

CLI 调用出现以下情况时，优先按认证失效处理：

- 返回 `401` 或 `403`
- 返回内容出现 `Unauthorized`、`login`、未登录、无权限
- `auth-state.json` 已存在，但请求仍然被重定向到登录页

处理原则：

1. 不要无限重试 CLI
2. 请求用户重新通过浏览器登录
3. 登录完成后，重新保存认证状态到默认路径
4. 再重试一次 CLI

默认认证文件路径固定为：

```bash
~/.flocks/browser/<name>/auth-state.json
```

保存方式示例：

```bash
mkdir -p "$HOME/.flocks/browser/<name>"

# agent-browser 模式
agent-browser state save "$HOME/.flocks/browser/<name>/auth-state.json"

# 或 cdp-direct / flocks browser 模式
flocks browser state save "$HOME/.flocks/browser/<name>/auth-state.json"
```

如果用户重新登录并保存 state 后，CLI 仍然失败，再继续排查：

- `BASE_URL` 是否写错
- 当前账号是否确实有接口权限
- 站点是否还有额外 header / token / csrf 依赖

## 一句话原则

`web2cli` 产出的 `<capture_name>_cli.py` 是临时结果；真正沉淀到 skill 时，要改成稳定产品名脚本，并把认证状态统一落到 `~/.flocks/browser/<name>/auth-state.json`。
