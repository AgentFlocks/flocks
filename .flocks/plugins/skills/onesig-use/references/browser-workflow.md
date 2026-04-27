# OneSIG 浏览器自动化

只在以下情况进入浏览器模式：

- API 不可用（未配置 / 未开通 / 认证失败 / Cookie 持久反复过期 / SSL 校验失败 / 网络不通）
- 任务必须看页面级详情（攻击链、威胁图、报表预览、报文 hex view、PCAP 在线播放）
- 需要图形验证码 / TOTP / 强制改密之类的人工交互
- 用户明确要求使用浏览器，或者已经在浏览器操作过程中

如果走 API 能完成，请回到 [api-reference.md](api-reference.md) —— 浏览器操作不稳定、不可批量、字段不一定完整。

> ⚠️ OneSIG 的 Web 控制台与 OneSEC、青藤是不同产品；不要把 OneSEC / 青藤的页面路径或 API 套用到 OneSIG。

## 零、登录认证

State 文件路径：`~/.flocks/browser/onesig/auth-state.json`（固定，全局唯一）。

### 首次登录 / Session 过期重新登录

```bash
agent-browser close
agent-browser --headed open "https://<onesig-domain>/login"
```

OneSIG 多数部署启用了图形验证码 + TOTP + 强制改密：

- 图形验证码：在登录框内手动输入即可
- TOTP：从用户的 Authenticator 里读取 6 位动态码，必要时让用户出示恢复码
- 强制改密：首次登录或密码到期时会被网关强制要求改密，按页面提示完成

等用户登录结束、收到通知后保存 state：

```bash
agent-browser state save ~/.flocks/browser/onesig/auth-state.json
```

### Session 失效恢复

当出现以下任一情况，优先判定为认证问题：

- 页面被重定向到 `/login`
- 后台请求 HTTP `401`，或响应里 `responseCode` 是 `1019` / `1020` / `1021` / `1022`（会话相关）
- 页面提示"登录失效""请重新登录""未授权"

恢复步骤（最多尝试 1 次）：

```bash
# 1) close 并重新加载 state
agent-browser close
agent-browser state load ~/.flocks/browser/onesig/auth-state.json

# 2) 打开受保护页面验证 session
agent-browser open "https://<onesig-domain>/monitoring/dashboard"
agent-browser wait --load networkidle

# 3) 根据结果决策
URL=$(agent-browser get url)
if [[ "$URL" == *"/login"* ]]; then
  echo "Session 仍无效，需重新登录"
else
  agent-browser state save ~/.flocks/browser/onesig/auth-state.json
  echo "Session 已恢复，可重试页面操作"
fi
```

如果仍然落回登录页，再要求用户重新登录，**不要无限循环重试**。

## 一、控制台导航

> ⚠️ 如果 OneSIG 域名不清楚，请先询问用户，不要擅自填写域名。
> 进入页面首选直接拼接 URL（比菜单点击更稳定）。

```bash
agent-browser open "https://<onesig-domain>/<path>"
agent-browser wait --load networkidle
agent-browser get text body
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

进入浏览器模式后，对于"查询类"诉求，应该**优先回到 API**（除非 API 真的不可用）：

| 任务 | 优先方案 |
|---|---|
| 列威胁事件 / 失陷主机 / 趋势数据 | API（`onesig_monitoring`） —— 浏览器只在需要威胁图、报文 hex 时用 |
| 增删改黑白名单 / IPS 规则 / 多维封锁 | API（`onesig_strategy`），写操作前要二次确认 |
| 看资产清单、增改资产 | API（`onesig_assets`） —— 浏览器仅做导入文件预览 |
| 设备升级 / 重启 / HA 切换 | 浏览器 + API 并用：先在浏览器里走完确认弹窗、备份提示，再用 API 触发；或者全程在浏览器里完成（更稳） |
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

OneSIG 控制台的导出按钮（资产 / 黑白名单 / 报表 / 审计 / coredump / pcap）多数会触发浏览器下载。在 `agent-browser` 模式下：

```bash
# 触发下载
agent-browser click "<导出 / 下载 按钮的 selector>"
# 等下载完成 —— OneSIG 服务端通常 < 30s
agent-browser wait --download
agent-browser download list
```

建议优先用 API 的导出 / 下载 action（参见 [api-reference.md](api-reference.md) "文件类返回 / 上传"小节）。
