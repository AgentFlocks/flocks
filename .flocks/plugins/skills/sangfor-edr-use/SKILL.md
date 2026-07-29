---
name: sangfor-edr-use
description: 深信服 EDR 登录态管理与首页仪表盘 API 采集。用户提到深信服 EDR、EDR 或 sangfor EDR 时必须先加载本 skill。
---

# 深信服 EDR Use

## 核心功能

- 管理同一次登录产生的 Cookie 与 `login_token`。
- 默认使用 HTTP 登录；仅当用户明确选择自动化登录时使用 browser/CDP。
- 每次登录或数据采集前探测现有认证，认证有效则跳过登录，失效则重新登录并更新存储。
- 通过 API 采集首页终端概况、受影响终端、漏洞、勒索防护、实时病毒、Top 5 终端和设备资源使用率。

## 输入与输出

### 认证工具

调用 `sangfor_edr_auth`：

- `status_auth_state`：返回认证文件、凭据和认证探测状态，不返回敏感值。
- `ensure_auth_state`、`refresh_auth_state`、`http_login`：执行“探测后按需 HTTP 登录”。
- `browser_login`：用户明确选择自动化登录时，执行“探测后按需 browser/CDP 登录”。
- `validate_auth_state`：仅验证浏览器 state。
- `complete_manual_login`：保存用户在已打开浏览器中完成的登录。

成功输出包含 `status`、`valid`、`login_skipped` 和非敏感探测结果；失败输出包含稳定的 `reason` 与恢复建议。

### 仪表盘工具

调用 `sangfor_edr_dashboard`，可输入：

- `sections`：可选采集项；省略时采集全部仪表盘数据。
- `days`：统计时间范围，允许 1–90 天。
- `base_url`、`auth_state_path`：可选运行时覆盖。

输出包含 `data`、`errors`、`sections`、`days` 和本次认证是复用还是重登；不得输出 Cookie、密码或 `login_token`。

## 关键配置

- `base_url`：从用户提供的 EDR 地址提取 scheme、host 和 port；不得使用固定示例地址。
- `username`、`password`：从设备配置或 Secret Manager 读取。
- `auth_state_path`：Cookie state 文件位置。
- `auto_ocr_code`、`max_captcha_retry`：HTTP/browser 登录验证码配置。
- selector 和页面路径配置仅用于 browser/CDP 登录。

## EDR 交互协议

1. 从 Secret Manager 与 `auth-state.json` 加载成套认证，并校验 base URL 和 Cookie 指纹。
2. 使用 Cookie 与 `login_token` 调用威胁终端概览接口 `get_agent_overview`：
   - HTTP 200、无登录页重定向、响应成功且包含终端概览数据：认证有效，跳过登录。
   - Cookie 缺失或不匹配、401/403、重定向、非 200、响应无终端概览数据：认证失效。
3. 默认重新登录流程：访问登录页，获取 RSA 公钥和验证码，提交 `dlogin`，再调用 `launch_login.php`。
4. 登录成功后将 Cookie 写入 `auth-state.json`，将 `login_token` 写入 Secret Manager，并更新配对指纹。
5. 仪表盘 API 只能使用通过上述探测的同一套 Cookie/token；禁止从不同 state 或 Secret 拼接。

## 错误处理

- 缺少地址或账密：返回缺失字段，向用户索取后保存到配置或 Secret Manager。
- 验证码或 HTTP 登录失败：返回 `http_login_failed`，不得自动切换 browser/CDP。
- 用户明确选择自动化登录后，browser/CDP 失败：保留浏览器供用户完成登录，再调用 `complete_manual_login`。
- 认证探测失败：禁止继续业务 API；HTTP 重登并再次探测，仍失败则返回错误。
- 仪表盘部分接口失败：保留成功数据，在 `errors` 中按采集项返回失败原因。
- Cookie、密码和 `login_token` 不得回显、记录日志或混入业务输出。
- 需要诊断 HTTP 登录时，可在当前 Flocks 运行环境设置 `SANGFOR_EDR_DEBUG_HTTP=1`；模块会按阶段输出请求 payload、响应状态/头/体和 Cookie 名称，默认脱敏。仅在隔离环境临时设置 `SANGFOR_EDR_DEBUG_HTTP_SENSITIVE=1` 查看完整敏感值，诊断完成后必须关闭并清理日志。

## 执行约束

- 运行本 Skill 提供的 Python 脚本时，必须使用 Flocks 虚拟环境；禁止使用系统 Python。
- 不得假设 Flocks 项目、插件或虚拟环境的绝对路径；代码必须通过当前运行时加载的模块、`Path.home()`、`~/.flocks` 或显式配置/环境变量解析路径。
- 需要具体 CDP 命令、浏览器启动方式、验证码识别、selector、tab/iframe 处理或页面关键词时，必须先阅读 [references/cdp-workflow.md](references/cdp-workflow.md)，不要在本文件重复展开。
- `bu.port` 是 Flocks browser daemon 的 IPC 端口文件，不是 Chrome remote-debugging 端口；禁止手工创建或修改。
- 默认认证和仪表盘采集不得启动 browser daemon；只有用户明确选择 `browser_login` 或执行 `validate_auth_state`、`complete_manual_login` 时，才允许使用 browser/CDP。
- HTTP 登录失败不得自动切换 browser/CDP；应返回错误并等待用户明确选择自动化登录或补充输入。
- 任何 API 采集前必须完成认证探测；认证探测失败时不得继续调用业务接口。
