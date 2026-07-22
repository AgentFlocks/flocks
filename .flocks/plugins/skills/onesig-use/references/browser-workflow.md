# OneSIG 浏览器自动化

只在以下情况进入浏览器模式：

- API 不可用（未配置 / 未开通 / ApiKey 或 Secret 缺失 / 签名失败 / SSL 校验失败 / 网络不通）
- 任务必须看页面级详情（攻击链、威胁图、报表预览、报文 hex view、PCAP 在线播放）
- 需要图形验证码 / TOTP / 强制改密之类的人工交互
- 用户明确要求使用浏览器，或者已经在浏览器操作过程中

如果当前 Strategy API 能完成，请回到 [api-reference.md](api-reference.md)；浏览器操作不稳定、不可批量、字段不一定完整。

> ⚠️ OneSIG 的 Web 控制台与 OneSEC、青藤是不同产品；不要把 OneSEC / 青藤的页面路径或 API 套用到 OneSIG。
> 本文档统一按 `browser-use` 的 `cdp-direct` 流程执行：先 `flocks browser --doctor`，doctor 通过后只使用 `flocks browser`。
> 对后台任务 / 定时任务，或系统不支持可视化，使用 `browser-use` 的 `cdp-headless` 模式。

## 零、登录认证

State 文件路径：`~/.flocks/browser/onesig/auth-state.json`（固定，全局唯一）。

### 首次登录 / Session 过期重新登录

```bash
flocks browser --doctor
```

如果 `flocks browser --doctor` 提示浏览器已运行，但 daemon 或 active browser connection 不可用，必须直接提示用户：

```text
browser: not connected — 请确保 Chrome / Chromium / Edge 已打开，然后访问对应浏览器的 inspect 页面（例如 chrome://inspect/#remote-debugging 或 edge://inspect/#remote-debugging）并勾选 Allow remote debugging
```

然后等待用户进一步指示，不要直接操作。

当用户确认已开启 remote debugging 后：

1. 执行 `flocks browser --setup` 触发交互式 attach，不要用短超时包装该命令。
2. 再运行 `flocks browser --doctor` 做只读确认。
3. 如果还失败，先执行 `flocks browser --reload` 清理旧 daemon，再重新执行 `flocks browser --setup`，避免因为残留 daemon 造成干扰。
4. 只有随后 `--doctor` 通过后，才继续后面的登录或页面操作。

```bash
flocks browser -c '
tid = new_tab("https://<onesig-domain>/login", activate=True)
wait_for_load()
print(tid)
print(page_info())
'
```

OneSIG 多数部署启用了图形验证码 + TOTP + 强制改密：

- 图形验证码：在登录框内手动输入即可
- TOTP：从用户的 Authenticator 里读取 6 位动态码，必要时让用户出示恢复码
- 强制改密：首次登录或密码到期时会被网关强制要求改密，按页面提示完成

等用户登录结束、收到通知后保存 state：

```bash
flocks browser state save ~/.flocks/browser/onesig/auth-state.json
```

### Session 失效恢复

当出现以下任一情况，优先判定为认证问题：

- 页面被重定向到 `/login`
- 后台请求 HTTP `401`，或响应里 `responseCode` 是 `1019` / `1020` / `1021` / `1022`（会话相关）
- 页面提示"登录失效""请重新登录""未授权"

恢复步骤（最多尝试 1 次）：

```bash
# 1) 重新加载 state，并直接带目标 URL 验证
flocks browser state load ~/.flocks/browser/onesig/auth-state.json --url "https://<onesig-domain>/monitoring/dashboard"

# 2) 读取当前页面状态
flocks browser -c '
print(page_info())
'
```

如果输出 URL 仍然落回登录页，再要求用户重新登录；否则重新保存一次 state 并继续：

```bash
flocks browser state save ~/.flocks/browser/onesig/auth-state.json
```

如果仍然落回登录页，再要求用户重新登录，**不要无限循环重试**。

## 一、控制台导航

> ⚠️ 如果 OneSIG 域名不清楚，请先询问用户，不要擅自填写域名。
> 进入页面首选直接拼接 URL（比菜单点击更稳定）。

```bash
flocks browser -c '
tid = new_tab("https://<onesig-domain>/<path>", activate=True)
wait_for_load()
print(page_info())
print(js("document.body.innerText.slice(0, 2000)"))
'
```

| 模块 | 子功能 | URL 路径 | 主要用途 |
|---|---|---|---|
| **监控** | 仪表盘 | `/monitoring/dashboard` | 总览 / 出入站 / 零日数据 |
| | 威胁防护大屏 | `/monitoring/overview` | 事件、资产、趋势、占比可视化 |
| | 设备状态 | `/monitoring/status` | CPU / 内存 / 网口 / 平台运行状态 |
| | 失陷主机 | `/monitoring/hosts` | 失陷主机列表 |
| | 失陷主机详情 | `/monitoring/hostdetail` | 单台主机的关联事件、命中规则、趋势 |
| | 入站威胁 | `/monitoring/inbound_threat` | 入站事件列表与详情 |
| | 出站威胁 | `/monitoring/outbound_threat` | 出站事件列表与详情 |
| | 报告管理 | `/monitoring/report` | 报表表单、任务配置与下载 |
| **防护策略** | 策略首页 | `/strategy/strategy` | 总览所有防护策略 |
| | 白名单 | `/strategy/whitelist` | 全局白名单（多方向、按条件） |
| | 黑名单 | `/strategy/blacklist` | 全局黑名单（地理位置、批量校验） |
| | 多维封锁 | `/strategy/multi_block` | 多维封锁规则与执行日志 |
| | 新建多维封锁 | `/strategy/add_multi_block` | 创建多维封锁规则的向导页 |
| | API 联动 | `/strategy/api` | API Key 管理（看 secret 时需当前用户密码） |
| | Syslog 自动封禁 | `/strategy/syslog` | 基于 syslog 的自动黑名单 |
| | FTP/SFTP 联动 | `/strategy/ftp` | FTP/SFTP 设备联动 |
| | 入侵防护 | `/strategy/intrusion_prevention` | IPS 规则与规则集管理 |
| | HTTP 防护 | `/strategy/httpProtect` | HTTP 黑名单、XFF / 高级配置 |
| | 高危端口防护 | `/strategy/portProtect` | 端口防护组与端口列表 |
| **资产** | 资产管理 | `/assets/segment` | 资产 / 资产组 / 资产类型 / 导入导出 |
| **平台管理** | 通知配置 | `/device/alert` | 告警策略、邮件 / Syslog / Webhook 测试 |
| | 审计日志 | `/device/audit` | 管理日志、清理配置 |
| | 登录管理 | `/device/loginManagement` | 用户、登录策略 |
| | HTTPS 解密 | `/device/httpsDecryption` | 解密策略、证书、检测对象 |
| | 接口 | `/device/interface` | 网口列表、虚拟线、监听口、桥 |
| | 部署引导 | `/device/deployguide` | 网口部署模式向导 |
| | 路由 | `/device/route` | IPv4 / IPv6 静态路由、路由表 |
| | 系统 DNS | `/device/system_dns` | DNS / Hosts / 网络测试 |
| | 代理 | `/device/agent` | HTTP 代理配置 |
| | 高可用 | `/device/high_availability` | HA 状态、模块、配置同步、主备切换 |
| | 集中管控 | `/device/centralized_control` | OneCC 配置与状态 |
| | 设备配置 | `/device/deviceConfig` | 升级、备份、重启、关机、出厂重置、日志外发 |
| | 基本信息 | `/device/system_info` | 设备信息、license、离线情报库、MDR |
| | 设备诊断 | `/device/system_diagnosis` | coredump、pcap |
| **帮助** | 帮助中心 | `/helper/docs` | 帮助文档列表与预览 |
| | 版本更新 | `/helper/update` | 软件版本与更新说明 |
| | 产品反馈 | `/helper/issue` | 提交产品问题 |

> 子页路径来自 OneSIG v2.5.x Web 控制台的当前路由约定；个别版本可能微调，看到 404 时再回到对应模块的根目录手动找一遍。

## 二、浏览器与 API 的互补建议

进入浏览器模式后，只有当前 Strategy API 覆盖的诉求才优先回到 API；未覆盖的控制台能力继续走浏览器：

| 任务 | 优先方案 |
|---|---|
| 看设备平台 / 系统 / 网络状态 | API（`onesig_strategy_api_query`） |
| 看资产组、资产列表、资产类型 | API（`onesig_strategy_api_query`） |
| 增删改资产或资产组 | API（`onesig_strategy_api_ops`），写操作前要二次确认 |
| 看 / 改防护策略、全局白名单、全局黑名单、封禁白名单、HTTP 黑名单 | API（`onesig_strategy_api_query` / `onesig_strategy_api_ops`），写操作前要二次确认 |
| 列威胁事件 / 失陷主机 / 趋势数据 | 浏览器（当前 Strategy API 不覆盖） |
| IPS 规则、多维封锁、Syslog 自动封禁、FTP/SFTP 联动、高危端口防护 | 浏览器（当前 Strategy API 不覆盖） |
| 设备升级 / 重启 / HA 切换 | 浏览器；强破坏性动作必须显式确认 |
| 看证书 / 解密策略详情 | 浏览器（API 返回的字段比页面少） |
| 看页面级图表 / 攻击链 / IOC 关联 | 浏览器（API 没有） |
| 处理图形验证码 / TOTP / 强制改密 | 浏览器（API 不能完成人工交互） |

## 三、写操作的安全护栏

在浏览器里执行下列动作前，必须显式取得用户授权：

- 一键 bypass / 流量直通（`策略首页` 或 `设备配置` 里的"Bypass / 直通"按钮）
- 重启 / 关机 / 出厂重置（`设备配置` 顶部按钮）
- 设备升级 / 系统升级（上传升级包、点"升级"）
- HA 主备切换、HA 配置同步
- 用户增删改密、改当前用户密码
- 黑白名单批量导入 / 批量删除
- IPS 规则集启停、HTTPS 解密策略启停
- 数据库 license / 离线情报库导入
- 备份恢复（会覆盖当前配置）

如果用户只是想"看一眼"，就只点列表 / 详情，不要去碰任何"启停 / 导入 / 删除 / 升级"按钮。

## 四、文件下载与导出

OneSIG 控制台的导出按钮（资产 / 黑白名单 / 报表 / 审计 / coredump / pcap）多数会触发浏览器下载。当前 Strategy API 工具没有导出 / 下载 action；需要导出文件时走浏览器页面。

先用 `js(...)` 或稳定 selector 触发下载，再用浏览器系统的下载目录或页面提示确认下载已开始；不要在 skill 里承诺不存在的“等待下载完成”专有命令。

页面下载最小模板：

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
js("document.querySelector(\"<导出按钮选择器>\")?.click()")
wait(1.0)
print(page_info())
'
```
