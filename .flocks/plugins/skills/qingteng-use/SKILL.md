---
name: qingteng-use
description: "Handles tasks for the Qingteng cloud security platform through API or browser workflows when needed, including host asset inventory, process and account investigation, port and service queries, website and database asset analysis, suspicious activity detection, brute-force analysis, abnormal login investigation, WebShell and backdoor investigation, honeypot result analysis, patch and vulnerability risk checks, weak password review, baseline task review, compliance checks, authorization management, system auditing, and quick risk assessments. Whenever the user mentions operations related to Qingteng, Qingteng Cloud Security, or Qingteng Host Security, you must load this skill first. This skill is the only decision entry for Qingteng platform operations: do not call any `qingteng_*` tool before reading this skill and determining the mode."
description_cn: "用于处理青藤云安全平台相关任务，适合通过 API 或结合浏览器完成主机资产盘点、进程与账号排查、端口和服务查询、网站与数据库资产分析、可疑操作检测、暴力破解分析、异常登录排查、WebShell 与后门调查、蜜罐结果分析、补丁与漏洞风险检查、弱密码排查、基线任务查看、合规检查、授权管理、系统审计和快速风险体检等场景。只要用户提到青藤、青藤云安全或青藤主机安全相关操作时，必须先加载本 skill。本 skill 是青藤平台操作的唯一决策入口：在未阅读本 skill 并完成模式判断前，不要直接调用任何 `qingteng_*` tool。"
---

# Qingteng Use

## First

操作模式 API V.S Browser

### 何时使用API
- 默认模式，默认使用API
- !!! important: 如果已经进入了浏览器模式，就不要走 API 了
- 查询类和任务类动作要严格区分；用户没有明确要求执行任务时，默认只使用只读查询能力

### 何时使用浏览器
- 现有 API 没有覆盖目标能力
- 未检测到对应 API 工具
- API 当前不可用，例如未配置、未开通、无权限、认证失败或服务不可达
- 任务必须查看页面级详情、导出、人工确认或使用复杂交互
- 页面需要人工登录、验证码、短信、多因子认证或页面级确认
- 用户要求使用浏览器，或者已经在浏览器操作过程中

### 请求确认
除非是用户要求使用浏览器，否则提示用户API不可用，请检查API配置或直接使用浏览器模式。

当确定操作模式后：
- API模式：请阅读API模式使用指南
- 浏览器模式：请阅读浏览器模式使用指南

## API 模式使用指南

- 用户说“主机资产”“进程”“账号”“端口”“网站”“数据库”“安装包”时，优先走 `qingteng_assets`
- 用户说“可疑操作”“暴力破解”“异常登录”“WebShell”“后门”“蜜罐”时，优先走 `qingteng_detect`
- 用户说“补丁”“风险”“弱密码”“风险文件”“漏洞扫描”“作业管理”时，优先走 `qingteng_risk`
- 用户说“合规基线”“基线检查”“授权”“基线任务”时，优先走 `qingteng_baseline`
- 用户说“系统审计”“谁改了配置”“谁执行了操作”时，优先走 `qingteng_system_audit`
- 用户只是想快速做风险体检时，可优先考虑 `qingteng_vul_check`

高风险写操作要特别谨慎，例如：
- 资产刷新、主机删除、业务组调整
- 暴力破解封停、规则增删改、扫描任务下发
- 漏洞/弱密码/风险扫描、作业管理、文件下载任务创建
- 基线任务创建、更新、执行、授权管理

必须阅读：
API使用说明见 [references/api-reference.md](references/api-reference.md)。

## 浏览器模式使用指南

- ⚠️ 如果青藤访问地址不清楚，请先询问用户，不要擅自填写域名。
- ⚠️ 用 --headed 打开浏览器，人工完成登录或页面确认。

只要进入浏览器模式，就请阅读并按照 browser-workflow 操作。

请严格按照以下文档执行：
- [references/browser-workflow.md](references/browser-workflow.md)
