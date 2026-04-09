---
name: onesec-use
description: "Handles tasks for the OneSEC endpoint security platform through API or browser workflows when needed, including endpoint security investigation, threat event analysis, endpoint alert search, behavior log investigation, IOC lookup, malicious file analysis, DNS threat investigation, software and endpoint asset queries, task progress tracking, audit log analysis, virus scanning, and common endpoint response scenarios. Whenever the user mentions operations related to OneSEC or ThreatBook EDR, you must load this skill first. This skill is the only decision entry for OneSEC platform operations: do not call any `onesec_*` tool before reading this skill and determining the mode."
description_cn: "用于处理 OneSEC 终端安全平台相关任务，适合通过 API 或结合浏览器完成终端安全调查、威胁事件分析、终端告警检索、行为日志排查、IOC 查询、恶意文件分析、DNS 威胁排查、软件与终端资产查询、任务进度查看、审计日志分析、病毒扫描和常见终端处置等场景。只要用户提到 OneSEC、微步 EDR 等相关操作需求时，必须先加载本 skill。本 skill 是 OneSEC 平台操作的唯一决策入口：在未阅读本 skill 并完成模式判断前，不要直接调用任何 `onesec_*` tool。"
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

- 用户说“威胁事件”“最近有什么事件”“事件详情”“有哪些事件”时，优先走 `onesec_edr` 的事件类 action
- 用户说“终端告警”“告警日志”“查某终端的告警”“查某进程行为”“行为记录”“时间线”“IOC”“恶意文件”时，优先走 `onesec_edr` 的告警/日志类 action
- 用户说“DNS 拦截”“DNS 告警”“解析日志”“受威胁终端”“域名放行/阻断”时，优先走 `onesec_dns`
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
