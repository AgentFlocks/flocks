---
name: onboarding
category: system
description: Guide new users through the complete Flocks setup process via conversation — covering ThreatBook MCP/API, security tool APIs, IM channels, and scenario demos. Also runs system health inspection for returning users. Trigger when the user sends "请启动新手引导流程", "start onboarding", or any similar request to begin setup/configuration.
---

# Onboarding — New User Setup Guide

When this skill is triggered, walk the user through Flocks initial configuration step by step via natural conversation.

## Core Principles

- **Language**: Detect the user's language from their messages or system locale. Conduct the **entire conversation in the user's language**. Do not switch languages mid-session.
- **One thing at a time**: Ask only one question per turn. Wait for the user's answer before proceeding.
- **Batch-friendly input**: If the user provides multiple credentials in one message (e.g., `FOFA key: email:key VT: <key>`), extract all recognizable keys immediately and persist each service credential without asking them to resend separately.
- **Skippable**: Every step (except Step 0 Welcome) can be skipped. If the user says "skip", "跳过", or similar, move to the next step immediately.
- **Act immediately**: After receiving credentials or configuration data, call the appropriate tool right away and confirm success before moving on.
- **Use API services correctly**: VirusTotal, FOFA, URLScan, and Shodan are API services. Configure them via `api_services` + secrets only. Do not add extra MCP servers for these services.
- **Be concise**: Keep messages short and friendly. Avoid long walls of text.
- **Track progress**: Internally track which steps were completed and which were skipped, for the final summary.
- **Skip already-configured steps**: Before each step, check whether it is already configured. If yes, briefly acknowledge it and skip ahead (don't re-configure without explicit user request).

---

## Pre-flight Check (run before Step 0)

Before greeting the user, silently run the following check to understand the current configuration state:

```python
# Run via bash tool: uv run python -c "..."
# IMPORTANT: use the unified user config under ~/.flocks/config, not the repo's example .flocks directory.
from flocks.skill.onboarding_status import print_onboarding_preflight_status

print_onboarding_preflight_status()
```

### Decision logic based on pre-flight results

After running the pre-flight check, **always present a full status report** (see "Status Report Format" below) regardless of configuration state. Then immediately begin the guided setup flow, working through each unconfigured item in order.

**Case A — Returning User (everything configured)**

If at least one LLM provider is configured AND `tb_mcp_connected=True` AND all security tools are configured AND all channels are configured:
→ Show status report, say "一切看起来都配置完整了！" and ask if there's anything to update.

**Case B — Partial or Fresh Configuration**

→ Show status report, then sequentially guide the user through each unconfigured item. Skip items that are already ✅.

---

## Status Report Format

After every pre-flight check, present the following status report. Use the actual data from the pre-flight results — do NOT fabricate or assume values.

Use this exact markdown format (bullet lists ensure correct line breaks in the chat UI):

**📋 当前配置状态**

**🔧 LLM 模型**
- ✅/❌ OpenAI — 已配置 / 未配置
- ✅/❌ Anthropic — 已配置 / 未配置
- ✅/❌ ThreatBook — 已配置 / 未配置

**🛠️ 网安工具**
- ✅/⚠️/❌ 微步 MCP — 已连接 / 已配置，未连接 / 未配置
- ✅/❌ 微步 API Key — 已配置 / 未配置
- ✅/❌ VirusTotal — 已配置 / 未配置
- ✅/❌ FOFA — 已配置 / 未配置
- ✅/❌ URLScan — 已配置 / 未配置
- ✅/❌ Shodan — 已配置 / 未配置

**📡 IM 渠道**
- ✅/❌ 飞书 (Feishu) — 已启用 / 未配置
- ✅/❌ 企业微信 (WeCom) — 已启用 / 未配置
- ✅/❌ 钉钉 (DingTalk) — 已启用 / 未配置
- ✅/❌ Telegram — 已启用 / 未配置

**Rendering rules:**
- Replace the status symbol with the actual result from pre-flight data.
- For LLM: use `llm_status[provider_id]` from pre-flight results.
- For 微步 MCP: use `tb_mcp_status` from pre-flight results:
  - `connected` → `✅ 微步 MCP — 已连接`
  - `configured` → `⚠️ 微步 MCP — 已配置，未连接`
  - `disabled` → `⚠️ 微步 MCP — 已配置，未启用`
  - `error` → `⚠️ 微步 MCP — 已配置，连接异常`
  - `not_configured` → `❌ 微步 MCP — 未配置`
- For 微步 API Key: use `tb_api_configured`.
- For security tools: use `security_tool_status[tool_name]`.
- For channels: use `channel_status[channel_name]`.

After presenting the report, say: "我来帮你逐项配置还未完成的部分。" and immediately begin guiding through the first unconfigured item (in the order: LLM → 网安工具 → IM渠道). Do NOT ask the user to choose — proceed in order unless the user says "跳过".

> **LLM 特殊规则**：LLM 只需配置一个即可正常使用。只要 `llm_status` 中有任意一个为 `True`，LLM 整体视为 ✅ 已就绪，**直接跳过 Step 1**，不再引导配置其他 LLM 提供商。

---

## Step 0 — Welcome

Open with a brief friendly greeting that introduces Rex. Then immediately present the **Status Report** (from "Status Report Format" above) based on the pre-flight check results.

After showing the status report, say: "我来帮你逐项完成配置，遇到需要填写 Key 或凭证的地方会提示你。" and proceed to the first unconfigured item.

> Do NOT list the steps again — the status report already shows everything. Skip directly to configuration.

---

## Step 1 — LLM 模型配置

> **只需配置一个 LLM 提供商即可**。如果 `llm_status` 中已有任意一个为 `True`，直接跳过本步骤，无需继续配置其他提供商。

Ask the user which LLM provider they want to use, recommending ThreatBook as the default. Configure only the one they choose, then move on.

### ThreatBook LLM

Tell the user: 微步 LLM 是 Flocks 默认的推理引擎。中国区请前往 https://x.threatbook.cn 获取 API Key，国际区请前往 https://x.threatbook.io 获取 API Key。默认先配置中国区 `threatbook-cn-llm`。

```python
# Run via: uv run python -c "..." from the project root
import json
from flocks.config.config import Config
from flocks.config.config_writer import ConfigWriter

LLM_KEY = '<KEY>'  # substitute actual key

# 1. Save key to .secret.json
secret_file = Config.get_secret_file()
secrets = json.loads(secret_file.read_text()) if secret_file.exists() else {}
secrets['threatbook-cn-llm_llm_key'] = LLM_KEY
secret_file.write_text(json.dumps(secrets, indent=2))

# 2. Register provider in flocks.json so apply_config() can resolve the secret ref
ConfigWriter.add_provider('threatbook-cn-llm', {
    'name': 'ThreatBook-cn-llm',
    'npm': '@ai-sdk/openai-compatible',
    'options': {
        'baseURL': 'https://llm.threatbook.cn/v1',
        'apiKey': '{secret:threatbook-cn-llm_llm_key}'
    }
})
print('ThreatBook CN LLM configured (secret + flocks.json).')
```

### OpenAI LLM

Ask if the user has an OpenAI API Key (https://platform.openai.com/api-keys). If yes:

```python
import json
from flocks.config.config import Config
from flocks.config.config_writer import ConfigWriter

LLM_KEY = '<KEY>'

secret_file = Config.get_secret_file()
secrets = json.loads(secret_file.read_text()) if secret_file.exists() else {}
secrets['openai_llm_key'] = LLM_KEY
secret_file.write_text(json.dumps(secrets, indent=2))

ConfigWriter.add_provider('openai', {
    'name': 'OpenAI',
    'npm': '@ai-sdk/openai',
    'options': {'apiKey': '{secret:openai_llm_key}'}
})
print('OpenAI LLM configured.')
```

### Anthropic LLM

Ask if the user has an Anthropic API Key (https://console.anthropic.com/). If yes:

```python
import json
from flocks.config.config import Config
from flocks.config.config_writer import ConfigWriter

LLM_KEY = '<KEY>'

secret_file = Config.get_secret_file()
secrets = json.loads(secret_file.read_text()) if secret_file.exists() else {}
secrets['anthropic_llm_key'] = LLM_KEY
secret_file.write_text(json.dumps(secrets, indent=2))

ConfigWriter.add_provider('anthropic', {
    'name': 'Anthropic',
    'npm': '@ai-sdk/anthropic',
    'options': {'apiKey': '{secret:anthropic_llm_key}'}
})
print('Anthropic LLM configured.')
```

---

## Step 2 — 微步工具配置

> 微步提供两种接入方式，**功能不同，建议都配置**：
>
> | 类型 | 用途 | 接入方式 |
> |---|---|---|
> | **微步 MCP 工具** | Rex 直接调用威胁情报工具（IP/域名/Hash 查询等），通过 MCP 协议 | `flocks mcp add` CLI 命令 |
> | **微步 API 工具** | 工作流脚本或自定义集成通过 HTTP 调用微步接口 | 保存 API Key 到 secret |
>
> 两者通常使用**同一个 Key**，但注册方式和用途不同。

> Skip individual sub-steps that are already configured: skip 2a only if `tb_mcp_connected=True`, skip 2b if `tb_api_configured=True`. If MCP is merely configured but not connected, treat it as a repair/reconnect task instead of asking for a brand new key. If both MCP and API are already ready, say "微步 MCP 工具和 API 均已配置好 ✅" and move to Step 3.

### Step 2a — 微步 MCP 工具（通过 `flocks_mcp` 工具添加）

Tell the user:

> 微步 MCP 工具让 Rex 直接调用微步在线威胁情报能力（IP/域名/Hash 查询等），推荐优先配置。
> 中国区请前往 [https://x.threatbook.com/flocks/activate](https://x.threatbook.com/flocks/activate) 获取 MCP API Key，国际区请前往 [https://threatbook.io/flocks/activate](https://threatbook.io/flocks/activate) 获取 MCP API Key，然后把 Key 告诉我。
> 如果你还没有微步账号，或者不知道如何获取 Key，请直接告诉我。

If pre-flight reports `tb_mcp_status` as `configured`, `disabled`, or `error`, first explain that the existing ThreatBook MCP entry is already present but not healthy. Try reconnecting / re-enabling the existing `threatbook_mcp` entry before asking the user for a replacement key.

**After receiving the MCP key, call `flocks_mcp` tool to register the MCP server:**

```
flocks_mcp({
  "config": {
    "type": "remote",
    "url": "https://mcp.threatbook.cn/mcp?apikey=<MCP_KEY>",
    "enabled": true
  },
  "name": "threatbook_mcp",
  "subcommand": "add"
})
```

Replace `<MCP_KEY>` with the actual key provided by the user.

**触发连接，让 MCP 工具上线：**

```bash
# 触发连接，让 MCP 工具立即注册
# 默认端口 8000；若用户自定义了端口，先运行 `flocks serve --help` 查看，或让用户告知
curl -s -X POST http://localhost:8000/api/mcp/threatbook_mcp/connect \
  -H "Content-Type: application/json"
```

If the connect call succeeds, MCP tools will be registered in the tool list immediately. If the port differs (e.g. the server was started with `--port XXXX`), replace `8000` with the actual port. If the call fails or the port is unknown, the server will appear as "disconnected" in the Web UI — the user can click "Connect" manually.

**验证：直接调用 MCP 工具**

After connection, **directly call at least one MCP tool** to verify the full chain works. Use a safe, well-known test value: 注意，要调用MCP工具，不要错误地调用微步的API工具。

1. **IP 信誉查询** — 查询 `8.8.8.8`（Google DNS，应返回低风险结果）
2. **域名分析** — 查询 `example.com`（若工具可用）

Call the tool directly in this conversation (not via a script). Report results to the user:
- ✅ 返回了情报数据 → MCP 配置全链路正常
- ⚠️ 返回认证错误 / 配额不足 → 工具链路正常，提示用户检查 Key 或账号配额
- ❌ 工具未找到 / 连接失败 → 检查 connect 步骤，或提示用户在 Web UI Tools 页手动点击 Connect

如果测试不成功，反复调整优化，或者删除了重新添加，要做很多尝试，不能轻易放弃。

Report format after verification:
```
🔍 微步 MCP 验证结果

✅ 连接状态：已连接
✅ 工具注册：{N} 个工具已加载
✅/⚠️ 工具调用（IP 8.8.8.8）：{result summary}

微步 MCP 工具配置完成！
```

---

### Step 2b — 微步 API 工具（保存 API Key）

Tell the user:

> 微步 API Key 供工作流脚本和自定义集成直接调用微步 HTTP 接口使用（与 MCP 工具的 Key 相同）。
> 如果刚才已填写了 MCP Key，直接回复"同上"即可；否则请告诉我你的微步 API Key。

**After receiving the API key:**

```python
# Run via: uv run python -c "..." from the project root
import json
from flocks.config.config import Config

API_KEY = '<API_KEY>'  # substitute actual key provided by user
REGION = 'cn'  # or 'global'

secret_file = Config.get_secret_file()
secrets = json.loads(secret_file.read_text()) if secret_file.exists() else {}
secret_key = 'threatbook_cn_api_key' if REGION == 'cn' else 'threatbook_io_api_key'
secrets[secret_key] = API_KEY
secret_file.write_text(json.dumps(secrets, indent=2))

print(f'ThreatBook API Key saved to .secret.json as {secret_key}.')
```

---

## Step 3 — 安全工具 API 配置

Ask which security tools the user has API keys for. List supported integrations:

- **VirusTotal (VT)** — 恶意软件 / URL / IP / 域名扫描
- **FOFA** — 网络空间搜索（格式：`email:key`）
- **URLScan.io** — URL 沙箱分析
- **Shodan** — 互联网设备搜索

Tip: Tell the user each tool is optional — configure what you have, skip the rest.

> 这些服务都按 **API service** 处理，不要使用 `flocks mcp add`、`ConfigWriter.add_mcp_server(...)`，也不要为了这些服务额外安装或连接 MCP。

**For each tool the user provides, execute the corresponding block:**

```python
# Run via: uv run python -c "..." from the project root
import asyncio
from flocks.server.routes.provider import (
    APIServiceUpdateRequest,
    ProviderCredentialRequest,
    set_service_credentials,
    test_provider_credentials,
    update_api_service,
)


async def configure_api_service(service_id: str, secret_id: str, secret_value: str) -> None:
    await set_service_credentials(
        service_id,
        ProviderCredentialRequest(
            api_key=secret_value,
            secret_id=secret_id,
        ),
    )
    await update_api_service(service_id, APIServiceUpdateRequest(enabled=True))
    result = await test_provider_credentials(service_id)
    print(service_id, result)


async def main() -> None:
    # ── VirusTotal ────────────────────────────────────────────────────────────
    await configure_api_service('virustotal', 'virustotal_api_key', '<VT_KEY>')

    # ── FOFA (key format: email:apikey) ──────────────────────────────────────
    # Save the full compound value as the canonical secret. Runtime will derive
    # fofa_email and fofa_api_key when the YAML tools execute.
    await configure_api_service('fofa', 'fofa_key', '<EMAIL>:<KEY>')

    # ── URLScan ───────────────────────────────────────────────────────────────
    await configure_api_service('urlscan', 'urlscan_api_key', '<URLSCAN_KEY>')

    # ── Shodan ────────────────────────────────────────────────────────────────
    await configure_api_service('shodan', 'shodan_api_key', '<SHODAN_KEY>')


asyncio.run(main())
```

After saving a key, treat the service as **configured + enabled + started/tested**. Report it as an API service startup result, for example:

```text
🔍 安全工具配置结果

✅ VirusTotal — API Key 已保存，API service 已启用并完成测试
✅ FOFA — API Key 已保存，API service 已启用并完成测试
✅ URLScan — API Key 已保存，API service 已启用并完成测试
✅ Shodan — API Key 已保存，API service 已启用并完成测试
```

If a service test fails, report it explicitly instead of silently continuing:

```text
⚠️ VirusTotal — 配置已写入，但启动测试失败：<error>
```

---

## Step 4 — IM 渠道接入

Ask which IM channels the user wants to configure. Supported channels:

- 飞书 / Lark (Feishu)
- 企业微信 (WeCom)
- 钉钉 (DingTalk)
- Telegram

Configure each channel by writing to `flocks.json` via `ConfigWriter`.

After any IM channel is configured, do **not** jump straight to the demo step. First tell the user to open the Web UI `Channels` page, enter the corresponding channel's detail panel, and complete the connection handoff:

1. Click `重启连接` to manually re-establish the long connection
2. If they changed any fields in the page, click `保存` to persist the config
3. Explain that saving will also trigger a reconnect in the background
4. Ask the user to confirm the channel status has recovered before moving on

Use concise Chinese wording such as: `通道配置已经写入。请现在进入 Channels 页面，打开对应通道，点击右上角“重启连接”；如果你刚补充或修改了页面中的字段，再点击“保存”。保存后系统会自动重启连接。确认状态恢复后我再继续下一步。`

### 飞书 (Feishu / Lark)

必填：**App ID**、**App Secret**。接入方式为 WebSocket 长连接，不需要配置回调域名。

获取步骤：
1. 点击 <a href="/feishu-bot-guide.pdf" download="feishu-bot-guide.pdf">下载《飞书配置指引》PDF</a>，按文档指引创建飞书自建应用
2. 前往 [飞书开放平台](https://open.feishu.cn/app)，在「凭证与基础信息」页面获取 App ID 和 App Secret
3. 把 App ID 和 App Secret 告诉我

```python
import json
from flocks.config.config import Config
from flocks.config.config_writer import ConfigWriter

APP_SECRET = '<APP_SECRET>'  # substitute actual value

secret_file = Config.get_secret_file()
secrets = json.loads(secret_file.read_text()) if secret_file.exists() else {}
secrets['channel_feishu_appSecret'] = APP_SECRET
secret_file.write_text(json.dumps(secrets, indent=2))

ConfigWriter.update_config_section('channels.feishu', {
    'enabled': True,
    'defaultAgent': 'rex',
    'appId': '<APP_ID>',
    'appSecret': '{secret:channel_feishu_appSecret}',
    'connectionMode': 'websocket',
    'domain': 'feishu'
})
```

### 企业微信 (WeCom)

必填：**Bot ID**、**Secret**（最重要的两个字段）。

接入方式是企业微信「智能机器人」（AI Bot），基于 WebSocket 长连接，**不需要配置服务器域名或公网 IP**。

获取步骤：
1. 点击 <a href="/wecom-bot-guide.pdf" download="企业微信创建机器人指引.pdf">下载《企业微信创建机器人指引》PDF</a>，按文档指引创建智能机器人
2. 创建完成后，在机器人详情页找到 **Bot ID**（也叫 `botid`）和 **Secret**
3. 把 Bot ID 和 Secret 告诉我

```python
import json
from flocks.config.config import Config
from flocks.config.config_writer import ConfigWriter

SECRET = '<SECRET>'  # substitute actual value

secret_file = Config.get_secret_file()
secrets = json.loads(secret_file.read_text()) if secret_file.exists() else {}
secrets['channel_wecom_secret'] = SECRET
secret_file.write_text(json.dumps(secrets, indent=2))

ConfigWriter.update_config_section('channels.wecom', {
    'enabled': True,
    'defaultAgent': 'rex',
    'botId': '<BOT_ID>',
    'secret': '{secret:channel_wecom_secret}'
})
```

### 钉钉 (DingTalk)

必填：**Client ID**（即 AppKey）、**Client Secret**（即 AppSecret）。

接入方式为钉钉 Stream 长连接，**不需要配置服务器域名或公网 IP**。

获取步骤：
1. 点击 <a href="/dingtalk-channel-guide.pdf" download="dingtalk-channel-guide.pdf">下载《钉钉配置指引》PDF</a>，按文档指引在钉钉开放平台创建应用并开启 Stream 模式
2. 创建完成后，在应用详情页找到 **Client ID**（AppKey）和 **Client Secret**（AppSecret）
3. 把 Client ID 和 Client Secret 告诉我

```python
import json
from flocks.config.config import Config
from flocks.config.config_writer import ConfigWriter

CLIENT_SECRET = '<CLIENT_SECRET>'  # substitute actual value

secret_file = Config.get_secret_file()
secrets = json.loads(secret_file.read_text()) if secret_file.exists() else {}
secrets['channel_dingtalk_clientSecret'] = CLIENT_SECRET
secret_file.write_text(json.dumps(secrets, indent=2))

ConfigWriter.update_config_section('channels.dingtalk', {
    'enabled': True,
    'defaultAgent': 'rex',
    'clientId': '<CLIENT_ID>',
    'clientSecret': '{secret:channel_dingtalk_clientSecret}',
})
```

### Telegram

必填：**Bot Token**（从 [@BotFather](https://t.me/botfather) 获取）。

操作步骤：
1. 打开 Telegram，搜索 @BotFather
2. 发送 `/newbot`，按提示创建 bot
3. 复制 API token 告诉我

```python
import json
from flocks.config.config import Config
from flocks.config.config_writer import ConfigWriter

BOT_TOKEN = '<BOT_TOKEN>'  # substitute actual value

secret_file = Config.get_secret_file()
secrets = json.loads(secret_file.read_text()) if secret_file.exists() else {}
secrets['channel_telegram_botToken'] = BOT_TOKEN
secret_file.write_text(json.dumps(secrets, indent=2))

ConfigWriter.update_config_section('channels.telegram', {
    'enabled': True,
    'botToken': '{secret:channel_telegram_botToken}'
})
```

---

## Step 5 — 场景演示

Tell the user: "配置完成！来看看 Flocks 能帮你做哪些事。"

Present the 3 core scenarios one by one with a brief demo description. Do **not** activate or create workflows unless the user explicitly asks — this step is a showcase only.

---

### 📊 每日情报简报 (Daily Threat Intel)

**描述：** 每天定时汇总威胁情报，推送摘要报告到指定渠道。

**示例效果：**

```
📰 今日威胁情报摘要 — 2026-03-19

🔴 高危事件 (3)
  • APT-29 新型 C2 基础设施被披露，涉及 47 个 IP
  • Log4j 新变种利用工具包流传，CVSS 9.8
  • 金融行业钓鱼活动激增，伪装成主流银行登录页

🟡 中危事件 (12)
  [更多详情...]

📎 IOC 速查
  IP: 192.168.1.1, 10.0.0.5 ...
  域名: malware-c2.example.com ...

— 由 Rex @ Flocks 自动生成
```

**触发方式：** 可配置为每日定时推送到飞书/企微群，或随时对话触发。

---

### 🔍 告警研判 (Alert Triage)

**描述：** 自动分析安全告警，结合威胁情报给出研判结论，减少误报处理时间。

**示例效果：**

```
🚨 告警研判报告

原始告警: [IDS] 内网主机 10.1.2.3 访问可疑域名 update-cdn.top

📊 情报核查结果:
  • 域名 update-cdn.top 注册于 3 天前（高风险）
  • IP 解析至 185.220.x.x（Tor 出口节点）
  • VirusTotal 检出率: 32/89 引擎

🎯 研判结论: 高可信度威胁
  判定: 疑似 C2 通信 / 数据外传
  建议: 立即隔离该主机，检查进程树

🔗 相关 ATT&CK: T1071.001 (Application Layer Protocol)
```

**触发方式：** 接收 SIEM/NDR 告警推送，或直接粘贴告警内容到对话框。

---

### 🎣 钓鱼检测 (Phishing Detection)

**描述：** 分析可疑邮件或 URL，提取 IOC，评估钓鱼风险等级。

**示例效果：**

```
🎣 钓鱼邮件分析报告

发件人: security-alert@paypaI.com (注意: 大写 i 替换 l)
主题: Your account has been limited - Action Required

🔍 分析结果:
  ✅ 发件域名 paypaI.com 与 paypal.com 相差 1 字符（视觉欺骗）
  ✅ 邮件正文包含紧迫性引导语（高频钓鱼特征）
  ✅ 链接指向 http://paypal-secure-login.xyz（非官方域名）
  ✅ URLScan 报告: 页面复刻 PayPal 登录界面

🛡️ 风险评级: ⭐⭐⭐⭐⭐ 高度疑似钓鱼

提取 IOC:
  域名: paypal-secure-login.xyz, paypaI.com
  URL: http://paypal-secure-login.xyz/login

建议: 立即删除该邮件，提醒相关员工。
```

**触发方式：** 粘贴邮件内容/EML 文件/可疑 URL，Rex 自动分析。

---

After showing all 3 demos, ask the user: **"以上哪个场景是你最想用的？我可以帮你进一步配置。"**

If the user wants to set up a specific scenario (e.g., scheduled daily intel), collect the required details (time, target channel) and help configure it. Otherwise, proceed to Step 6.

---

## Step 6 — 完成

Provide a completion summary. Dynamically list what was configured and what was skipped.

Template:
```
🎉 初始配置完成！以下是本次设置摘要：

✅ 已完成：
- 微步 MCP 情报服务（工具测试通过）
- [列出已配置的安全工具]
- [列出已接入的 IM 渠道]

⏭️ 已跳过：
- [列出跳过的项目]

你现在可以：
• 打开 Sessions 与 Rex 开始对话
• 访问 Tools 页查看所有已连接的 MCP 工具
• 访问 Channels 页检查 IM 渠道连接状态；如果刚完成渠道接入，请进入对应通道点击“重启连接”，如有页面改动再点击“保存”，并确认连接已恢复

随时告诉我需要帮什么！
```

---

## Implementation Notes

1. **Python execution**: Always use `bash` tool + `uv run python -c "..."` from the **project root** to ensure correct paths and virtual environment.
2. **Secret file writes**: Write secrets to `Config.get_secret_file()` (normally `~/.flocks/config/.secret.json`) via `json.load/dump` (load existing → merge new key → write back). `SecretManager` also works, but using `Config.get_secret_file()` keeps onboarding scripts aligned with the runtime config directory.
3. **Key naming conventions**:
   - LLM providers: `{provider}_llm_key` (e.g. `openai_llm_key`, `anthropic_llm_key`, `threatbook-cn-llm_llm_key`)
   - ThreatBook MCP 工具: 当前实现会把 key 保存为 `threatbook_mcp_key`（兼容旧项目中的 `threatbook_mcp_api_key`），并在 `mcp.threatbook_mcp.url` 中写入 `{secret:...}` 引用
   - ThreatBook API 工具: 中国区使用 `threatbook-cn` service + `threatbook_cn_api_key`，国际区使用 `threatbook-io` service + `threatbook_io_api_key`（兼容旧项目中的 `threatbook_api_key` / `threatbook_api`）
   - Security tools: 优先使用 `set_service_credentials(...)` 保存凭证，再调用 `update_api_service(enabled=True)` 启用服务，并用 `test_provider_credentials(...)` 立即测试/刷新状态。推荐使用各 provider 的 canonical secret id，例如 `virustotal_api_key`、`fofa_key`、`urlscan_api_key`、`shodan_api_key`；读取状态时兼容 `fofa_api_key`
   - IM channels: 敏感凭据存入 `.secret.json`，命名约定：`channel_feishu_appSecret`、`channel_wecom_secret`、`channel_dingtalk_clientSecret`、`channel_telegram_botToken`；`flocks.json` 中对应字段写 `{secret:<key>}` 引用。非敏感字段（如 `appId`、`botId`、`clientId`）直接写入 `flocks.json` 明文。配置结构为**扁平结构**，字段直接放在 `channels.<name>` 下，不使用 `accounts.default` 嵌套。
4. **Placeholder detection**: A value starting with `<` is a placeholder, not a real credential. Treat such entries as unconfigured.
5. **Error handling**: If a configuration step fails, explain the error and suggest manual config via the WebUI settings page.
6. **Sensitive data**: Never echo full key values back in conversation.
7. **Skip tracking**: Remember skipped steps throughout the session for the final summary.
8. **Idempotency**: If onboarding is re-triggered, check existing config first and only configure what is missing or explicitly being updated.
9. **ConfigWriter API**: Use `ConfigWriter.update_config_section(dot.path, value)` for nested config writes.
10. **MCP tool testing**: Use available MCP tools directly in the conversation to test connectivity. Do not write test scripts — just call the tools and show the response.
