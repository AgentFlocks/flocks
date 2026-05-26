---
name: onesec-use
description: 用于处理 OneSEC/OneDNS 终端安全平台相关任务，适合通过API或者结合浏览器进行以下任务: 终端安全调查、威胁事件分析、终端告警检索、行为日志排查、IOC 查询、恶意文件分析、DNS 威胁排查、软件与终端资产查询、任务进度查看、审计日志分析、病毒扫描和常见终端处置场景。只要用户提到 OneSEC、微步 EDR等相关操纵需求时，必须先加载本 skill。本 skill 是 OneSEC 平台操作的唯一决策入口：在未阅读本 skill 并完成模式判断前，不要直接调用任何 `onesec_*` tool。
---

# OneSEC Use

## First

操作模式 API V.S Browser

### 何时使用API
- 默认模式，默认使用API
- !!! important: 如果已经进入了浏览器模式，就不要走 API 了
- 查询类请求与处置类请求要严格区分；用户没有明确要求执行任务时，默认只使用只读查询能力

### 何时使用浏览器
- 现有 API 没有覆盖目标能力
- 未检测到对应 API 工具
- API 当前不可用，例如未配置、未开通、无权限、认证失败或服务不可达
- 任务必须查看页面详情、图谱、概览、复杂交互、弹窗明细或人工确认
- 页面需要人工登录、验证码、多因子认证或手工点击页面
- 用户要求使用浏览器，或者已经在浏览器操作过程中

### 请求确认
除非是用户要求使用浏览器，否则提示用户API不可用，请检查API配置或直接使用浏览器模式。

当确定操作模式后：
- API模式：请阅读API模式使用指南
- 浏览器模式：请阅读浏览器模式使用指南

## API模式使用指南

### 设备定位（首要步骤，不可跳过）

在调用任何 `onesec_*` 工具之前，**必须**先确定目标设备的 `device_id`：

1. **调用 `device_context`** 获取当前所有已接入的 OneSEC 设备列表及其 `device_id`
2. **按用户提到的设备名称匹配**：
   - 用户说出了具体名称（如"OneSEC 生产环境""edr-01"）→ 在列表里找名称匹配的设备，取其 `device_id`
   - 用户未指定具体设备 → 若只有一台则直接使用；若有多台，**必须向用户确认**使用哪台
   - 用户指定了但列表中找不到匹配 → **告知用户没有找到该设备**，列出可用设备供选择
3. **所有后续工具调用必须带上 `device_id`**，即使该参数在 schema 中标注为可选

!!! important: 禁止在不明确 `device_id` 的情况下直接发起 `onesec_*` 工具调用，否则请求可能落到错误的设备上。

- 时间字段硬规则：凡是本次 API 调用涉及 `time_from` / `time_to` / `begin_time` / `end_time` 等时间字段，必须先在 bash 中执行 `uv run python` 动态计算，再调用对应 tool；禁止手动估算、禁止硬编码、禁止把“今天”“最近 7 天”“最近 24 小时”等自然语言时间直接脑补成数字。
- OneSEC 的时间入参默认按 Unix 秒级时间戳处理；如果需要“今天”“本周”“最近 N 天”这类窗口，也必须先用 `uv run python` 算出准确边界后再传参。
- 用户说“威胁事件”“最近有什么事件”“事件详情”“有哪些事件”时，优先走 `onesec_edr` 的事件类 action
- 用户说“终端告警”“告警日志”“查某终端的告警”“查某进程行为”“行为记录”“时间线”“IOC”“恶意文件”时，优先走 `onesec_edr` 的告警/日志类 action
- 用户说“DNS 拦截”“DNS 告警”“解析日志”“受威胁终端”“域名放行/阻断”时，优先走 `onesec_dns`
- 涉及任何时间查询时，必须动态计算 `time_from` / `time_to` / `begin_time` / `end_time`，禁止手动估算时间戳
- 查询单个域名是否被 DNS 拦截时，优先构造 `dns_search_blocked_queries + domain + time_from + time_to`；未显式给 `keyword` 时，工具会默认复用 `domain`
- DNS 查询优先使用 Unix 秒级时间戳；如果用户给的是常见日期字符串（如 `2026-05-08 00:00:00`），工具会自动换算
- 查询指定域名或关键字的 DNS 拦截明细时，优先使用 `dns_search_blocked_queries`
- `dns_get_recent_blocked_queries` 只用于最近 24 小时增量拉取，不支持 `domain` / `keyword` / `private_ip` / `threat_type` / 分页参数
- 用户说“安装了什么软件”“哪些终端装了某软件”时，优先走 `onesec_software`
- 用户说“终端管理”“任务列表”“任务执行进度”“审计日志”“策略范围”时，优先走 `onesec_ops`
- 用户说“病毒库版本”“病毒扫描”“停止扫描”“升级病毒库”时，优先走 `onesec_threat`

高风险写操作要特别谨慎，例如：
- 终端隔离、取消隔离
- 文件隔离、恢复隔离
- 网络阻断、取消阻断
- 卸载 Agent
- 病毒扫描、停止扫描、病毒库升级
- DNS 目标地址列表变更

必须阅读：
各工具的使用说明见 [references/api-reference.md](references/api-reference.md)。

## 浏览器模式使用指南

- ⚠️ 如果 OneSEC 域名不清楚，请先询问用户，不要擅自填写域名。
- ⚠️ 用 --headed 打开浏览器，人工完成登录。

只要进入浏览器模式，就请阅读并按照 browser-workflow 操作，不要直接跳过本 skill 去套用其他通用浏览器 skill。

请严格按照以下文档执行：
- [references/browser-workflow.md](references/browser-workflow.md)
