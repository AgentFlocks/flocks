---
name: skyeye-use
description: "Handles tasks for the SkyEye / Tianyan / NSFOCUS analysis platform through API or browser workflows when needed, including alert list queries, threat-level filtering, attack-stage analysis, attack-result investigation, dashboard and trend analysis, system status checks, alert report export, PCAP download, and sample file retrieval. Whenever the user mentions operations related to SkyEye, Tianyan, or the NSFOCUS analysis platform, you must load this skill first. This skill is the only decision entry for SkyEye platform operations: do not call any `skyeye_*` tool before reading this skill and determining the mode."
description_cn: "用于处理 SkyEye/天眼/网神分析平台相关任务，适合通过 API 或结合浏览器完成告警列表查询、威胁级别筛选、攻击阶段分析、攻击结果排查、看板和趋势分析、系统状态查看、告警报告导出、PCAP 下载和样本文件获取等场景。只要用户提到 SkyEye、天眼或网神分析平台相关操作时，必须先加载本 skill。本 skill 是天眼平台操作的唯一决策入口：在未阅读本 skill 并完成模式判断前，不要直接调用任何 `skyeye_*` tool。"
---

# SkyEye Use

## First

操作模式 API V.S Browser

### 何时使用API
- 默认模式，默认使用API
- !!! important: 如果已经进入了浏览器模式，就不要走 API 了

### 何时使用浏览器
- 现有 API 没有覆盖目标能力
- 未检测到对应 API 工具
- API 当前不可用，例如未配置、未开通、无权限、认证失败或服务不可达
- 任务必须查看页面级详情、导出、下载或使用复杂交互
- 页面需要人工登录、验证码、多因子认证或页面级确认
- 用户要求使用浏览器，或者已经在浏览器操作过程中

### 请求确认
除非是用户要求使用浏览器，否则提示用户API不可用，请检查API配置或直接使用浏览器模式。

当确定操作模式后：
- API模式：请阅读API模式使用指南
- 浏览器模式：请阅读浏览器模式使用指南

## API模式使用指南

- 用户说“告警列表”“最近告警”“按威胁级别筛选告警”时，优先走 `skyeye_alarm_list`
- 用户说“告警字段有哪些”“攻击阶段有哪些”“需要枚举值”时，优先走 `skyeye_alarm_params`
- 用户说“看板”“概览”“趋势”“整体视图”“系统状态”时，优先走 `skyeye_dashboard_view`
- 用户说“导出告警报告”“下载 PDF/DOCX 报告”时，优先走 `skyeye_download_alarm_report`
- 用户说“下载 PCAP”时，优先走 `skyeye_download_pcap`
- 用户说“下载样本”“下载上传文件”“取告警关联文件”时，优先走 `skyeye_download_uploadfile`

必须阅读：
API 参数和适用场景见 [references/api-reference.md](references/api-reference.md)。

## 浏览器模式使用指南

- ⚠️ 如果 SkyEye 域名不清楚，请先询问用户，不要擅自填写域名。
- ⚠️ 用 --headed 打开浏览器，人工完成登录

只要进入浏览器模式，就请阅读并按照 browser-workflow 操作。

请严格按照以下文档执行：
- [references/browser-workflow.md](references/browser-workflow.md)
