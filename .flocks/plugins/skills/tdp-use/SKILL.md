---
name: tdp-use
description: 用于处理 TDP 威胁检测平台相关任务，适合通过API或者结合浏览器进行以下任务：安全态势查看、告警检索、告警获取、威胁事件调查、受害主机排查、资产风险查询、等场景。只要用户提到需要 打开/操作/获取/浏览 TDP、微步 NDR等需求时，必须先加载本 skill。本 skill 是 TDP 平台操作的唯一决策入口：在未阅读本 skill 并完成模式判断前，不要直接调用任何 `tdp_*` tool。
---

# TDP Use

## First

操作模式 API V.S Browser

### 何时使用API
- 默认模式，默认使用API
!!! important: 如果已经进入了浏览器模式，就不要走 API 了

### 何时使用浏览器
- 现有 API 没有覆盖目标能力
- 未检测到对应 API 工具
- API 当前不可用，例如未配置、未开通、无权限、认证失败或服务不可达
- 任务必须查看页面详情、原始报文、PCAP、下载入口或使用交互式筛选
- 页面需要人工登录、验证码、多因子认证或页面级确认
- 用户要求使用浏览器，或者已经在浏览器操作过程中

### 请求确认
除非是用户要求使用浏览器，否则提示用户API不可用，请检查API配置或直接使用浏览器模式。

当确定操作模式后：
- API模式：请阅读API模式使用指南
- 浏览器模式：请阅读浏览器模式使用指南

## API模式使用指南

> 必须阅读：
API 参数和适用场景见 [references/api-reference.md](references/api-reference.md)。

- API 调用必须以当前 tool schema 为准，优先使用 schema 暴露的顶层语义化参数；列表类工具常见 `keyword`、`severity`、`cur_page`、`page_size`、`sort_by`，但 `tdp_log_search.sql` 是过滤表达式不是完整 SQL，禁止 `SELECT/FROM`，控制返回数量用 `size`，`terms` 可不传 `sql`，外部攻击结果筛选用 `result_list`。
- 用户说“告警”“告警记录”“告警日志”“明细记录”“查某 IP 的告警”时，默认走 `tdp_log_search`
- 用户说“看板”“概览”“趋势”“统计”时，先用 `tdp_dashboard_status`
- 用户说“威胁事件”“外部攻击”“攻击事件”“事件总览”“事件趋势”时，优先用 `tdp_incident_list` 或 `tdp_threat_inbound_attack`
- 用户说“告警主机”“受害主机”“主机下的事件”时，优先用 `tdp_host_threat_list`
- 用户说“系统状态”“核心服务状态”“数据库状态”时，优先用 `tdp_system_status`
- 用户说“MDR”“研判结果”“研判统计”时，优先用 `tdp_mdr_alert_list`
- 用户说“脆弱性”“弱口令”“登录入口”“上传接口”“API 风险”“隐私数据”时，走对应资产或风险类工具
- 用户说“云服务”“云实例”“访问云服务的源主机”时，优先用 `tdp_cloud_facilities`
- 用户说“下载 PCAP”“下载恶意文件”时，优先走下载类 API；下载前先确认用户确实要下载
- 用户说“白名单”“资产配置”“策略配置”“联动阻断”“处置状态修改”时，先判断是否涉及写操作；只有用户明确授权后才调用配置类工具

## 浏览器模式使用指南

- ⚠️ 如果 TDP 域名不清楚，请先询问用户，不要擅自填写域名。
- ⚠️ 用 --headed 打开浏览器，人工完成登录

只要进入浏览器模式，就请阅读并按照browser-workflow操作（不要直接使用agent-browser skill）。

请严格按照以下文档执行：
- [references/browser-workflow.md](references/browser-workflow.md)
