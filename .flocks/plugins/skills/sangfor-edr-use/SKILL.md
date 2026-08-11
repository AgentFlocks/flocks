---
name: sangfor-edr-use
description: 深信服 EDR 登录态管理、首页仪表盘和威胁资产分析 API 采集。用户提到深信服 EDR、EDR 或 sangfor EDR 时必须先加载本 skill。
---

# 深信服 EDR Use

## 核心功能

实现边界：`sangfor_edr_http_login.py` 负责 HTTP 登录、认证探测、
`auth-state.json` Cookie 及 Secret Manager token bundle；
`sangfor_edr_dashboard_api.py` 负责仪表盘 API 请求，只读取 HTTP 登录模块
验证过的同一套 Cookie/token，不从其他状态源拼接凭据。
`sangfor_edr_threat_assets_api.py` 负责威胁资产分析 API 请求，同样只读取
HTTP 登录模块验证过的同一套 Cookie/token。

- 管理同一次登录产生的 Cookie 与 `login_token`。
- 默认使用 HTTP 登录，开始前必须向用户索取并保存 EDR 地址、用户名和密码。
- 仅当用户明确选择“打开页面后手动登录”时，才可不索取账密并直接使用 browser/CDP。
- HTTP 登录连续 3 次失败后，按“browser/CDP 自动化登录（仍需账密）→保留页面供用户手动登录（不需账密）”顺序降级。
- 每次登录或数据采集前探测现有认证，认证有效则跳过登录，失效则重新登录并更新存储。
- 通过 API 采集首页终端概况、受影响终端、漏洞、勒索防护、实时病毒、Top 5 终端和设备资源使用率。
- 通过 API 采集威胁资产分析的风险汇总、资产分组和威胁终端事件列表，支持风险级别、资产分组、终端状态、隔离状态和分页筛选。

## 输入与输出

### 认证工具

调用 `sangfor_edr_auth`：

- `status_auth_state`：返回认证文件、凭据和认证探测状态，不返回敏感值。
- `ensure_auth_state`、`refresh_auth_state`、`http_login`：执行“探测后按需 HTTP 登录”；HTTP 连续 3 次失败时按降级策略进入 browser/CDP，再失败则转为手动登录。
- `browser_login`：用户明确选择自动化登录时，执行“探测后按需 browser/CDP 登录”；该路径需要账密，除非用户明确要求打开页面后自行手动登录。
- `validate_auth_state`：仅验证浏览器 state。
- `complete_manual_login`：保存用户在已打开浏览器中完成的登录。

成功输出包含 `status`、`valid`、`login_skipped` 和非敏感探测结果；失败输出包含稳定的 `reason` 与恢复建议。

### 仪表盘工具

调用 `sangfor_edr_dashboard`，可输入：

- `sections`：可选采集项；省略时采集全部仪表盘数据。
- `days`：统计时间范围，允许 1–90 天。
- `base_url`、`auth_state_path`：可选运行时覆盖。

输出包含 `data`、`errors`、`sections`、`days` 和本次认证是复用还是重登；不得输出 Cookie、密码或 `login_token`。

### 威胁资产分析工具

调用 `sangfor_edr_threat_assets`，可输入：

- `sections`：`risk_summary`、`zones`、`agent_events`，省略时采集全部数据。
- `days`：威胁终端事件时间范围，允许 1–90 天。
- `info`：用户在搜索框输入的终端名称、IP 地址或资产使用人关键词，原样放入 `list_agent_event.filter.info`。
- `risk_level`、`host_type`、`zone_name`、`agent_state`、`isolate_agent`：威胁资产筛选条件；用户使用中文条件时由 Skill 转换为接口枚举值。
- `page`、`limit`、`paginate`：事件列表分页配置；`limit` 仅允许 10、20、50、100、500，默认自动采集至 `total_items`。
- `base_url`、`auth_state_path`：可选运行时覆盖。

输出包含风险汇总、资产分组、事件列表、分页信息和接口错误；不得输出 Cookie、密码或 `login_token`。

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
3. 默认重新登录流程：访问登录页，获取 RSA 公钥和验证码，提交 `dlogin`，调用 `launch_login.php`，再 GET `/ui`；HTTP 登录连续 3 次失败后进入 browser/CDP 自动化登录，自动化登录仍需账密，自动化登录失败后保留页面供用户手动登录。
4. 登录成功后将 Cookie 写入 `auth-state.json`，将 `login_token` 写入 Secret Manager，并更新配对指纹。
5. 仪表盘 API 只能使用通过上述探测的同一套 Cookie/token；禁止从不同 state 或 Secret 拼接。
6. 威胁资产分析 API 使用以下接口：
   - `POST /launch.php?s={login_token}&opr=list_risk_agent_total_count`：风险级别和隔离终端汇总。
   - `POST /launch.php?s={login_token}&opr=list_zones`：资产分组列表，payload 使用 `data.local=true`。
   - `POST /launch.php?s={login_token}&opr=list_agent_event`：威胁终端事件列表，payload 使用 `filter.info`（终端名称/IP/资产使用人关键词）、其他筛选条件、`day_sum`、`page` 和 `limit`；分页采集直到返回的 `total_items` 收集完成。
   - 三个接口均使用当前登录会话的 Cookie、动态 `query_id` 和同一 `login_token`。
7. 威胁资产筛选枚举映射：
   - `risk_level`：空值=全部，`0`=低风险，`1`=中风险，`2`=高风险。
   - `host_type`：`0`=PC 终端，`1`=服务器终端；用户说“PC/服务器”时转换为对应数字，不能把中文直接放入 payload。
   - `agent_state`：`-1`=全部终端状态，`0`=在线，`1`=离线，`2`=已禁用，`3`=未授权，`4`=已卸载，`6`=已降级。
   - `limit`：只能使用 `10/20/50/100/500`。
   - `zone_name`：先调用 `list_zones`，按返回的 `zone_name` 或 `full_zone_name` 精确匹配，再将对应的设备专属 `zone_id` 放入 `list_agent_event.filter.zone_id`；不能使用固定 zone ID，也不能把中文分组名直接作为 `zone_id`。

## 错误处理

- 缺少地址或账密：返回缺失字段，向用户索取后保存到配置或 Secret Manager。
- 验证码或 HTTP 登录失败：最多进行 3 次独立 HTTP 登录尝试；仍失败则切换 browser/CDP 自动化登录，切换时仍需账密。
- browser/CDP 自动化登录失败或用户明确选择打开页面后手动登录：保留浏览器供用户完成登录，不再索取账密，再调用 `complete_manual_login`。
- 认证探测失败：禁止继续业务 API；先执行 HTTP 重登并再次探测，连续 3 次 HTTP 仍失败则按 browser/CDP 自动化登录→手动登录降级。
- 仪表盘部分接口失败：保留成功数据，在 `errors` 中按采集项返回失败原因。
- 威胁资产分析部分接口或分页请求失败：保留已采集的风险汇总、资产分组和事件数据，在 `errors` 中标明失败项。
- Cookie、密码和 `login_token` 不得回显、记录日志或混入业务输出。

## 执行约束

- 运行本 Skill 提供的 Python 脚本时，必须使用 Flocks 虚拟环境；禁止使用系统 Python。
- 不得假设 Flocks 项目、插件或虚拟环境的绝对路径；代码必须通过当前运行时加载的模块、`Path.home()`、`~/.flocks` 或显式配置/环境变量解析路径。
- 需要具体 CDP 命令、浏览器启动方式、验证码识别、selector、tab/iframe 处理或页面关键词时，必须先阅读 [references/cdp-workflow.md](references/cdp-workflow.md)，不要在本文件重复展开。
- `bu.port` 是 Flocks browser daemon 的 IPC 端口文件，不是 Chrome remote-debugging 端口；禁止手工创建或修改。
- 默认认证和仪表盘采集开始时不得启动 browser daemon；只有用户明确选择 `browser_login`、执行 `validate_auth_state`/`complete_manual_login`，或 HTTP 登录连续 3 次失败进入降级流程时，才允许使用 browser/CDP。
- HTTP 登录连续 3 次失败后必须按 browser/CDP 自动化登录→手动登录顺序降级；自动化登录阶段仍需账密，手动登录阶段不得要求账密。
- 任何 API 采集前必须完成认证探测；认证探测失败时不得继续调用业务接口。
- 威胁资产分析的分页请求必须复用同一套 Cookie/token，不得在分页过程中重新拼接或替换认证参数。
- 用户未提供接口参数名时，必须根据中文语义完成上述映射；无法确认的筛选条件不得猜测数值，应省略筛选或向用户确认。
