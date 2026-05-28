# Flocks 安全运营模块与 UI 融入设计

## 目标

本文基于当前分支 `feat/auth_hook2` 和 `main` 分支的产品逻辑，设计“安全运营优先模块”如何融入 Flocks 当前架构，并给出主要 UI 层设计与用户使用路径。

设计目标：

- 保持 Flocks 开源版的定位：面向安全运营的 AI Agent Framework。
- 不在开源版中直接建设完整数据湖和完整 SOC，而是补齐安全运营 Agent 的基础能力。
- 复用当前已有的 Agent、Tool、Skill、Workflow、Task、Hub、Workspace、Provider、Syslog、Audit、Permission 等能力。
- 吸收 `main` 分支新增的设备接入和新版 UI 设计，使安全运营链路更完整。
- 为未来商业版 AISOC Copilot / SOC UI 留出清晰扩展边界。

## 当前架构基础

Flocks 当前已经具备比较完整的产品骨架：

```text
CLI / WebUI / TUI
  -> FastAPI Server
  -> Session / Agent / Tool / Workflow / Skill / Task
  -> PluginLoader / Hub / Provider / Permission
  -> Storage / Config / Secret / Audit
```

与安全运营模块最相关的现有能力：

- `flocks/server/app.py`：FastAPI 服务入口，统一挂载 session、agent、tool、workflow、hub、provider、task、channel、auth、audit 等路由。
- `flocks/plugin/loader.py`：统一插件加载器，是安全工具、Agent、Task、Channel 扩展的基础。
- `flocks/tool/registry.py`：工具注册和执行入口，适合承载安全产品 API、威胁情报、日志查询和处置动作。
- `flocks/agent/registry.py`：Agent 注册入口，适合注册安全运营 Agent，例如 NDR 分析师、主机取证、威胁情报分析师。
- `flocks/workflow/`：Workflow 引擎、编译器、执行历史和发布能力，适合承载告警研判、事件调查、报告生成等场景。
- `flocks/ingest/syslog/`：Syslog 触发 workflow 的事件入口，适合从 SIEM/NDR 接收告警。
- `.flocks/plugins/tools/api/`：已有 TDP、OneSec、SkyEye、青藤等安全产品 API 工具插件。
- `.flocks/plugins/skills/*-use/SKILL.md`：已有安全产品使用规范，适合作为 Agent 调用安全工具前的决策入口。
- `webui/src/pages/Workflow`、`Tool`、`Hub`、`Skill`、`Task`、`Workspace`：已有 UI 页面可复用。

当前分支 `feat/auth_hook2` 的重点是 Pro、审计、认证、权限和会话共享。`main` 分支的重点是安全运营工作台、设备接入和 UI 改版。`soc_ui` 分支提供了一套高保真 SOC 场景页面，重点体现安全运营场景的产品化 UI、跨场景故事线和配置体验。因此安全运营 UI 设计应以 `main` 的运营链路为基础，吸收 `soc_ui` 的 SOC 场景表达，同时保留当前分支的治理能力。

## `soc_ui` 分支可复用的 SOC 设计资产

`soc_ui` 分支新增了纯前端 SOC 场景页面，核心路径集中在：

```text
webui/src/pages/Soc/index.tsx
webui/src/pages/Soc/Alerts.tsx
webui/src/pages/Soc/Assets.tsx
webui/src/pages/Soc/Intel.tsx
webui/src/pages/Soc/Vulnerabilities.tsx
webui/src/pages/Soc/Drills.tsx
webui/src/pages/Soc/AttackSurface.tsx
webui/src/pages/Soc/components.tsx
webui/src/pages/Soc/预置数据文件
webui/src/locales/zh-CN/soc.json
webui/src/locales/en-US/soc.json
```

该分支的 SOC 场景页面不是后端能力实现，而是一套产品化体验原型。它最值得融入当前设计的部分有四类。

### 一、SOC 场景页面

`soc_ui` 通过 `/soc/*` 路由组织了一组 SOC 场景页面：

```text
/soc
/soc/alerts
/soc/assets
/soc/intel
/soc/vulnerabilities
/soc/drills
/soc/attack-surface
```

这些页面覆盖了告警运营、安全设备、态势情报、漏洞排查、钓鱼演练和互联网攻击面。它不适合作为最终生产态信息架构的唯一入口，但非常适合作为：

- 开源版安全运营场景页。
- 新用户 onboarding 的场景样板。
- 商业版 AISOC Copilot 的产品预览。
- Hub 安全场景包的场景页。

### 二、运营视图与配置车间双模式

`soc_ui` 的关键设计是每个场景页都有两种模式：

```text
运营视图
  -> 展示 SOC 分析师和值班人员的日常使用界面

配置车间
  -> 用户用自然语言描述需求
  -> Rex 生成配置蓝图
  -> 映射到 Agent / Tool / Workflow / Channel / 设备接入
```

这个模式非常适合 Flocks，因为 Flocks 的核心价值不是只看告警，而是让用户把自然语言需求转化为可执行 Agent 和 Workflow。建议将该模式沉淀为通用交互范式：

```text
看结果：SOC 运营视图
配能力：Rex 配置车间
```

### 三、告警运营漏斗与深度调查 Drawer

`webui/src/pages/Soc/Alerts.tsx` 是最完整的 SOC 场景页面，包含：

- 原始告警到深度调查的漏斗。
- 事件簇列表。
- 告警研判详情 Drawer。
- 深度调查列表。
- 跨设备证据链。
- Agent 调查过程 Drawer。
- 生成调查报告入口。

这部分可以作为未来 `/security/alerts` 和 `/security/cases/:id` 的 UI 原型。尤其是“事件簇 -> 研判 Drawer -> 深度调查 -> Agent 会话 Drawer”的路径，正好对应本文设计的 `Alert -> Case -> Evidence -> Session -> Report` 链路。

### 四、跨场景故事线

SOC 预置数据文件中用同一事件串联了告警、设备、情报、漏洞、攻击面等页面，例如：

```text
同一事件 ID
  -> 攻击源 IP
  -> DMZ 资产
  -> CVE
  -> NDR / EDR / WAF / 邮件 / OA 证据
  -> 处置建议
```

这对于产品表达非常重要。未来真实实现时，应把这条故事线替换为真实的 `Case` 结构：

```text
Case
  -> Alert Cluster
  -> Related Entities
  -> Evidence
  -> Timeline
  -> Findings
  -> Actions
  -> Report
```

因此，`soc_ui` 的价值不是提供最终数据模型，而是提供了一套安全运营产品应有的叙事方式和交互层次。

## 推荐模块分层

优先推荐融入的安全运营模块分为四层：

```text
接入层
  -> 安全数据连接器
  -> 设备接入
  -> 威胁情报 / SIEM / EDR / NDR / 资产 / 工单

语义层
  -> 安全对象模型
  -> Alert / Incident / Case / Asset / Identity / Entity / Evidence / Action

编排层
  -> 安全 Workflow
  -> Skill / Subagent
  -> 企业安全知识库上下文注入
  -> Agent 调查任务
  -> Syslog / Task 触发

呈现层
  -> SOC 工作台
  -> 告警研判
  -> 调查证据链
  -> 报告和归档
  -> 审计和治理
```

## 模块一：安全数据连接器与设备接入

### 融入方式

优先复用 `main` 分支的设备接入能力，将其从“设备接入”扩展为“安全数据源与设备接入”。

建议保留和合并 `main` 分支中的：

- `webui/src/pages/DeviceIntegration/index.tsx`
- `webui/src/api/device.ts`
- `flocks/server/routes/device.py`
- `flocks/tool/device/`

当前分支需要补齐对应路由挂载和侧栏入口。

### UI 入口

建议导航位置：

```text
Agent 工作区
  -> Agent
  -> Skills
  -> 工具
  -> 数据源与设备
  -> Hub
  -> 模型
  -> 通道
```

页面名称建议从“设备接入”升级为“数据源与设备”，因为安全运营不只接硬件设备，也会接 SIEM、EDR、NDR、威胁情报、资产系统和工单系统。

### 页面结构

`数据源与设备` 页面建议分为四个区域：

```text
顶部总览
  -> 已接入数据源数量
  -> 在线 / 异常 / 未配置数量
  -> 最近同步时间
  -> 最近连通性检测结果

数据源列表
  -> 厂商 / 产品类型 / 名称 / 状态 / 版本 / 所属机房
  -> 测试连接 / 编辑 / 禁用 / 删除

接入向导
  -> 选择类型
  -> 填写地址和凭证
  -> 测试连接
  -> 选择启用的工具能力
  -> 关联 Workflow 模板

能力映射
  -> 该数据源提供哪些工具
  -> 支持哪些对象：Alert / Asset / Identity / Entity
  -> 可触发哪些 Workflow
```

### 后端模型

建议在设备接入模型基础上增加安全运营语义字段：

```text
source_type: siem | edr | ndr | ti | asset | ticket | soar | firewall | waf
capabilities: search_logs | get_alert | get_asset | query_ioc | response_action
object_types: alert | asset | identity | entity | evidence | action
default_workflows: string[]
health_status: healthy | degraded | failed | unknown
```

### 与现有能力关系

- 凭证仍走 Provider / SecretManager。
- 工具仍走 ToolRegistry 和 YAML API tool。
- 插件仍走 Hub / PluginLoader。
- 设备状态、分组和连接测试复用 `main` 的 DeviceIntegration 设计。

## 模块二：安全对象模型

### 融入方式

新增轻量安全对象模型，作为工具、workflow、UI、报告之间的中间语言。

建议新增后端包：

```text
flocks/security_ops/
  models.py
  normalize.py
  evidence.py
  case_store.py
```

核心对象：

```text
Alert
Incident
Case
Asset
Identity
Entity
Evidence
Finding
Action
TimelineEvent
```

### UI 融入

安全对象模型不需要单独作为一级页面，主要体现在三个地方：

- 告警研判页展示 `Alert`。
- 调查详情页展示 `Case`、`Entity`、`Evidence`、`TimelineEvent`。
- 报告页使用 `Finding`、`Evidence`、`Action`、`TimelineEvent` 生成结构化报告。

### API 设计

建议新增轻量 API：

```text
GET  /api/security/alerts
GET  /api/security/alerts/{id}
POST /api/security/alerts/{id}/triage
GET  /api/security/cases
GET  /api/security/cases/{id}
POST /api/security/cases
POST /api/security/cases/{id}/evidence
POST /api/security/cases/{id}/actions
```

这些 API 不替代现有 Workflow API，而是把安全运营对象和 Workflow 执行结果关联起来。

## 模块三：告警研判 Workflow

### 融入方式

告警研判应优先复用当前 Workflow 系统，而不是新建一套执行引擎。

建议将安全 workflow 作为模板包进入 Hub：

```text
.flocks/plugins/workflows/security_alert_triage/
.flocks/plugins/workflows/security_ioc_investigation/
.flocks/plugins/workflows/security_host_forensics/
.flocks/plugins/workflows/security_report_generation/
```

已有 `tdp_alert_triage` 和 `loop_host_forensics_fast` 可作为第一批模板参考。

### UI 融入

在 `Workflow` 页面增加安全运营视角：

```text
Workflow 页面
  -> 全部
  -> 告警研判
  -> 主机取证
  -> 威胁情报
  -> 报告生成
  -> 处置动作
```

在 `告警详情 / Case 详情` 页中，用户不需要理解完整 workflow 编辑器，只需要看到：

```text
推荐 Workflow
  -> 一键运行
  -> 运行进度
  -> 当前执行步骤
  -> 工具调用记录
  -> 证据产出
  -> 结论
```

复杂编辑仍跳转到现有 Workflow Editor。

### 用户路径

```text
告警进入
  -> 选择告警
  -> 系统推荐研判 Workflow
  -> 用户点击开始研判
  -> Agent 自动补充证据
  -> 输出风险等级、证据链、建议动作
  -> 用户确认或继续追问
```

## 模块四：证据链与调查详情

### 融入方式

证据链是安全运营 UI 的核心，不应只存在于聊天记录中。

建议新增 `Investigation Trace` 概念，用来记录一次告警研判或事件调查的结构化过程：

```text
Investigation Trace
  -> 输入对象
  -> 执行的 workflow
  -> 调用的工具
  -> 查询的数据源
  -> 发现的证据
  -> Agent 推理摘要
  -> 不确定性
  -> 建议动作
```

可先复用 `flocks/workflow/execution_store.py` 的执行历史，再逐步抽象到 `flocks/security_ops/evidence.py`。

### UI 页面

建议新增 `Case 详情页`，作为安全运营主视图。

页面布局：

```text
左侧：对象上下文
  -> Alert 摘要
  -> 资产 / 账号 / IP / 域名 / Hash
  -> 风险等级
  -> 当前状态

中间：调查时间线
  -> Workflow 步骤
  -> 工具调用
  -> 证据发现
  -> 人工确认
  -> 处置动作

右侧：AI 分析助手
  -> 继续追问
  -> 补充调查
  -> 解释证据
  -> 生成报告

底部：证据与输出
  -> Evidence 列表
  -> Findings
  -> Recommended Actions
  -> Report Draft
```

### 与 Session 的关系

当前 Flocks 的 Session 是 Agent 交互主入口。安全运营 Case 详情页不应替代 Session，而应关联 Session：

```text
Case
  -> 关联一个或多个 Session
  -> 关联一次或多次 Workflow Execution
  -> 关联 Evidence / Finding / Action / Report
```

这样既保留聊天式调查体验，也能提供 SOC 所需的结构化证据视图。

## 模块五：企业安全知识库

### 融入方式

不建议新增 `Playbook` 这个独立定义。Flocks 已经有 `Skill`、`Workflow`、`Agent` 和 `Subagent`，如果再引入 Playbook，容易与 Skill 的“操作规则/领域方法论”和 Workflow 的“可执行步骤”产生重叠。

更合理的做法是新增“企业安全知识库”模块。知识库不负责定义执行流程，而是为 Agent、Skill、Workflow 和 Subagent 提供企业上下文，让降噪、研判和调查更贴近客户真实环境。

企业安全知识库的核心价值：

- 降噪：识别企业内部正常业务行为、已知误报、白名单资产和例外规则。
- 研判：提供资产重要性、业务归属、历史处置结论、内部系统语义。
- 调查：补充企业内部文档、系统说明、网络区域、账号职责和变更记录。
- 生成能力：从知识库中提炼稳定规则，生成或更新 Skill、Subagent、Workflow 输入模板。

### 知识来源

知识库至少应覆盖三类可沉淀数据，并对接一类外部企业上下文源。

```text
企业文档
  -> 安全制度
  -> 应急响应 SOP
  -> 业务系统说明
  -> 网络区域说明
  -> 资产分级规则
  -> 账号权限说明
  -> 变更发布记录
  -> 合规要求

黑白名单数据
  -> 白名单 IP / 域名 / Hash / 进程 / 账号
  -> 黑名单 IOC
  -> 已知扫描器
  -> 可信运维跳板机
  -> 可信自动化任务
  -> 例外规则
  -> 临时放行记录

历史研判结论
  -> 已关闭告警
  -> 误报原因
  -> 真实事件结论
  -> 处置动作
  -> 复盘报告
  -> 相似事件
  -> 分析师备注

外部企业上下文源
  -> 资产 CMDB
  -> 业务系统和负责人目录
  -> 网络拓扑和安全域系统
  -> 身份和组织架构系统
  -> 数据分级系统
  -> 服务依赖系统
  -> 值班和工单系统
```

边界说明：

- 知识库不应存储 CMDB、OA、IAM、工单系统、拓扑系统的全量数据。
- 知识库应通过连接器按需查询这些外部系统，获取研判所需的企业 Context。
- 可以缓存必要的摘要、引用、查询结果快照和过期时间，但不能把知识库设计成另一个 CMDB 或工单管理系统。
- 对于会随业务频繁变化的数据，例如资产负责人、值班人、服务依赖、组织架构，应优先实时查询或短 TTL 缓存。
- 对于相对稳定且可复用的知识，例如误报规则、白名单规则、研判结论、SOP 摘要，可以沉淀到知识库中。

### 后端设计

建议新增轻量知识库模块：

```text
flocks/security_ops/knowledge/
  models.py
  sources.py
  ingest.py
  retrieval.py
  context_builder.py
  feedback.py
  generators.py
```

职责划分：

```text
models.py
  -> KnowledgeSource
  -> KnowledgeItem
  -> ContextSnippet
  -> KnowledgeFeedback

sources.py
  -> 文档、黑白名单、历史结论和外部 Context Provider 的数据源定义

ingest.py
  -> 文档导入、黑白名单导入、历史 Case 归档，不导入外部系统全量主数据

retrieval.py
  -> 按 Alert / Entity / Asset / Identity / Case 检索知识库，并按需调用外部 Context Provider

context_builder.py
  -> 聚合知识库命中和外部系统查询结果，为 Agent / Workflow / Skill 构造可注入的企业 Context

feedback.py
  -> 分析师采纳、驳回、修正结论后回写知识库

generators.py
  -> 从稳定知识生成 Skill、Subagent 或 Workflow 输入模板
```

### API 设计

建议新增 API：

```text
GET  /api/security/knowledge/sources
POST /api/security/knowledge/sources
POST /api/security/knowledge/import
GET  /api/security/knowledge/items
GET  /api/security/knowledge/search
POST /api/security/knowledge/context
POST /api/security/knowledge/feedback
POST /api/security/knowledge/generate-skill
POST /api/security/knowledge/generate-subagent
GET  /api/security/context-providers
POST /api/security/context-providers/query
```

其中 `/context` 是核心接口，它不只是全文搜索，而是面向安全对象返回可用于研判的企业上下文：

```text
输入：
  Alert / Entity / Asset / Identity / Case

输出：
  相关企业文档片段
  黑白名单命中
  历史相似告警
  已知误报规则
  从外部系统按需查询到的资产重要性和业务归属
  可解释引用
```

`/context-providers/query` 用于查询外部企业上下文源，例如 CMDB、IAM、OA、工单系统和拓扑系统。它返回的是当前研判所需的上下文片段，而不是把外部系统数据同步进知识库。

### UI 融入

建议新增页面“知识库”，放在 Agent 工作区中：

```text
Agent 工作区
  -> Agent
  -> Skills
  -> 知识库
  -> 工具
  -> 数据源与设备
  -> Hub
  -> 模型
  -> 通道
```

知识库页面建议分为五个 Tab：

```text
企业文档
  -> 上传 / 同步文档
  -> 文档类型
  -> 适用业务域
  -> 生效状态
  -> 引用次数

黑白名单
  -> IP / 域名 / Hash / 进程 / 账号
  -> 黑名单 / 白名单 / 灰名单
  -> 生效时间
  -> 来源
  -> 审批状态

历史结论
  -> 历史 Case
  -> 历史告警
  -> 误报原因
  -> 处置动作
  -> 相似事件

上下文检索
  -> 输入告警 / 实体 / 资产 / 账号
  -> 查看知识库命中和外部系统查询到的企业 Context
  -> 查看引用来源
  -> 查看数据来源和缓存过期时间
  -> 一键加入当前 Case

能力生成
  -> 从知识生成 Skill
  -> 从知识生成 Subagent
  -> 从历史研判生成 Workflow 输入模板
  -> 人工审核后发布
```

### 在研判链路中的使用

知识库应在告警研判和调查过程中自动参与，而不是要求用户手动搜索。

```text
Alert 进入
  -> 提取 IP / 域名 / 账号 / 主机 / 进程 / Hash
  -> 查询知识库中的规则、文档和历史结论
  -> 按需查询 CMDB / IAM / 工单 / 拓扑等外部 Context Provider
  -> 命中白名单、例外规则、历史误报、业务归属和资产重要性
  -> 注入 Agent 研判上下文
  -> 影响风险等级、证据链和建议动作
  -> 分析师确认后回写历史结论
```

Case 详情页中应展示一个“企业 Context”区域：

```text
企业 Context
  -> 资产重要性（来自 CMDB 或资产系统）
  -> 业务负责人（来自 OA / IAM / CMDB）
  -> 白名单 / 黑名单命中
  -> 历史相似事件
  -> 相关企业文档
  -> 已知误报规则
  -> 引用来源和数据来源
  -> 缓存状态和过期时间
```

### 生成 Skill 和 Subagent

知识库可以反向生成能力，但生成结果必须经过人工审核。

```text
稳定知识
  -> 生成 Skill：沉淀产品使用规范、企业 SOP、研判规则
  -> 生成 Subagent：沉淀专门角色，如“内网资产研判员”“钓鱼邮件分析员”
  -> 生成 Workflow 输入模板：沉淀常见调查入口和参数结构
```

示例：

```text
历史 30 次“运维扫描误报”结论
  -> 归纳白名单条件
  -> 生成 Skill 草稿
  -> 关联扫描器资产和维护窗口
  -> 人工审核
  -> 发布为 enterprise-noise-reduction Skill
```

### 用户路径

```text
用户进入知识库
  -> 导入企业文档、黑白名单、历史研判结论
  -> 配置 CMDB / IAM / 工单 / 拓扑等外部 Context Provider
  -> 系统建立可检索知识和可查询上下文源
  -> 告警进入时自动召回知识，并按需查询外部 Context
  -> Agent 结合企业 Context 完成降噪和研判
  -> 分析师确认结论并回写知识库
  -> 系统从稳定知识中生成 Skill 或 Subagent 草稿
  -> 人工审核后发布
```

## 模块六：报告生成与 Workspace

### 融入方式

报告生成优先复用 Workspace，不急于新增独立文档系统。

建议：

- Workflow 输出结构化 `ReportDraft`。
- 报告文件写入 Workspace 或用户 outputs 目录。
- Case 详情页展示报告草稿。
- 用户可从 Case 页面导出 Markdown、PDF 或 JSON。

### UI 融入

在 Case 详情页增加报告 Tab：

```text
报告
  -> 事件摘要
  -> 影响范围
  -> 调查时间线
  -> 证据列表
  -> 根因分析
  -> 已执行动作
  -> 整改建议
  -> 残余风险
```

在 Workspace 页面增加安全报告筛选：

```text
Workspace
  -> 全部文件
  -> 调查报告
  -> 证据附件
  -> Workflow 输出
  -> Benchmark 结果
```

## 模块七：Benchmark 与评估

### 融入方式

Benchmark 不应成为第一阶段 UI 主入口，但应进入 Workflow 和报告链路。

建议先在后端和 CLI 侧提供：

```text
flocks/security_ops/benchmark/
  datasets/
  runner.py
  metrics.py
```

指标：

- 告警研判准确率。
- 证据完整性。
- 工具调用正确性。
- 幻觉率。
- 平均调查耗时。
- 报告质量。
- 人工采纳率。

### UI 融入

第一阶段可放在 Workflow 详情页：

```text
Workflow 详情
  -> 执行历史
  -> Benchmark
  -> 样例告警
  -> 预期输出
  -> 评估结果
```

商业版可再扩展为独立“评估中心”。

## 推荐信息架构

综合当前分支、`main` 分支和 `soc_ui` 分支，建议把产品导航改成“两级导航”：

- 顶部横向大导航：`Agent 工作区`、`SOC 工作区`、`系统中心`。
- 每个大导航下方保留当前产品形态的左侧导航，用来展示该工作区内的具体页面。

这种结构比把所有页面都堆在一个侧栏里更清晰：顶部导航表达用户当前工作域，左侧导航表达该工作域内的任务路径。

建议导航如下：

```text
顶部横向导航
  -> Agent 工作区
  -> SOC 工作区
  -> 系统中心
```

### Agent 工作区

Agent 工作区承载 Flocks 的 Agent、工具、模型、Workflow、知识库和插件生态，是“配置能力、生成能力、运行 Agent”的地方。

```text
Agent 工作区左侧导航
  -> 会话
  -> Workspace
  -> 任务
  -> 工作流
  -> Agent
  -> Skills
  -> 知识库
  -> 工具
  -> 数据源与设备
  -> Hub
  -> 模型
  -> 通道
```

### SOC 工作区

SOC 工作区承载安全运营人员的日常使用路径，是“看告警、做研判、查证据、出报告”的地方。`soc_ui` 的场景页面应优先融入这里。

```text
SOC 工作区左侧导航
  -> SOC 总览
  -> 告警研判
  -> 调查案件
  -> 安全设备
  -> 态势情报
  -> 漏洞排查
  -> 钓鱼演练
  -> 互联网攻击面
  -> 报告中心
```

### 系统中心

系统中心承载系统治理、账号、权限、审计、日志、监控和 Pro 能力。

```text
系统中心左侧导航
  -> 账号管理
  -> 权限管理
  -> 审计日志
  -> 系统日志
  -> 监控
  -> Flocks Pro 升级
```

`soc_ui` 的 `/soc/*` 页面不再作为独立的第四类导航，而是作为 SOC 工作区的页面原型和预置数据来源。

```text
soc_ui 页面融入关系
  -> /soc                  对应 SOC 总览
  -> /soc/alerts           对应 告警研判 / 调查案件
  -> /soc/assets           对应 安全设备 / 数据源与设备的运营视图
  -> /soc/intel            对应 态势情报
  -> /soc/vulnerabilities  对应 漏洞排查
  -> /soc/drills           对应 钓鱼演练
  -> /soc/attack-surface   对应 互联网攻击面
```

SOC 工作区中的场景页面仍可保留两种模式：

```text
运营视图
  -> 展示场景结果、事件链路、分析师操作路径

配置车间
  -> 展示如何用 Rex 生成 Agent / Tool / Workflow / Channel 配置蓝图
```

长期路由关系建议：

```text
/soc/*                  -> SOC 工作区的场景页 / onboarding / 场景预览路由
/security/alerts        -> 真实告警研判工作台
/security/cases         -> 真实调查案件列表
/security/cases/:id     -> 真实 Case 详情
/devices                -> 真实数据源与设备接入
/workflows              -> 真实 Workflow 管理
```

开源版可以默认展示：

```text
Agent 工作区、SOC 工作区、系统中心
```

Pro 或企业版再增强展示：

```text
系统中心中的权限管理、审计日志、监控、Flocks Pro 升级、多用户配额；SOC 工作区中的企业级 SOC 场景包和企业报表
```

## 核心用户路径

### 路径零：从 SOC 工作区理解产品价值

```text
用户进入首页
  -> 进入 SOC 工作区
  -> 查看 SOC 总览
  -> 点击 Rex 今日建议
  -> 进入告警运营
  -> 查看告警降噪漏斗
  -> 打开事件簇 Drawer
  -> 查看深度调查和跨设备证据链
  -> 打开 Agent 调查过程
  -> 切换到配置车间
  -> 让 Rex 生成该场景的配置蓝图
```

对应 `soc_ui` 设计资产：

- `/soc` 提供总览和跨场景故事线。
- `/soc/alerts` 提供告警漏斗、事件簇、调查 Drawer 和 Agent 会话 Drawer。
- `ModeSwitch` 提供“运营视图 / 配置车间”切换。
- `BlueprintConversationDrawer` 展示 Rex 生成配置蓝图的过程。

该路径适合开源项目场景化呈现、售前沟通、场景包预览和用户 onboarding。它不要求用户一开始完成真实接入，能先让用户理解 Flocks 在安全运营中的价值。

### 路径一：首次接入安全数据源

```text
用户登录
  -> 首页 Onboarding
  -> 或从 SOC 工作区点击“配置这个场景”
  -> 配置模型
  -> 进入“数据源与设备”
  -> 选择 SIEM / EDR / NDR / 威胁情报 / 资产系统
  -> 填写地址和凭证
  -> 测试连接
  -> 自动发现可用工具
  -> 推荐安装对应 Skill / Workflow
  -> 生成或启用场景 Workflow
  -> 完成接入
```

对应现有能力：

- `Provider` 负责凭证和连接测试。
- `Tool` 负责 API 工具。
- `Skill` 负责产品使用规范。
- `Hub` 负责场景包安装。
- `main` 的 DeviceIntegration 负责 UI 和设备状态。
- `soc_ui` 的配置车间负责自然语言到配置蓝图的引导。

### 路径二：自动告警研判

```text
外部 SIEM / NDR 通过 Syslog 或 API 推送告警
  -> Flocks 创建 Alert
  -> 告警进入“告警研判”队列
  -> 用户点击告警
  -> 系统推荐 Workflow
  -> Agent 自动查询情报、资产、日志、历史行为
  -> 生成 Evidence / Finding / Confidence
  -> 用户查看证据链
  -> 用户确认结论或继续追问
  -> 生成 Case 和报告
```

对应现有能力：

- `flocks/ingest/syslog/` 承接事件触发。
- `Workflow` 承接自动研判。
- `ToolRegistry` 调用安全产品 API。
- `Session` 承接继续追问。
- `Workspace` 承接报告和附件。
- `soc_ui` 的告警漏斗和事件簇 Drawer 可作为 `/security/alerts` 的页面原型。

### 路径三：主动事件调查

```text
用户进入“调查案件”
  -> 新建 Case
  -> 输入 IP / 域名 / Hash / 主机 / 账号
  -> 选择 Skill / Workflow
  -> 自动注入企业 Context
  -> Agent 调用数据源补全上下文
  -> 形成调查时间线
  -> 输出风险结论和建议动作
  -> 用户确认处置或生成报告
```

对应现有能力：

- `Agent` 提供安全分析角色。
- `Skill` 提供调查规则。
- `知识库` 提供企业上下文、黑白名单、历史研判结论。
- `Workflow` 提供标准步骤。
- `Tool` 提供数据查询。
- `Case` 新增结构化调查容器。
- `soc_ui` 的深度调查 Drawer 和 AgentSessionDrawer 可演进为 Case 详情页中的证据链和 AI 助手。

### 路径四：安装安全场景包

```text
用户进入 Hub
  -> 选择“安全场景包”
  -> 查看包含内容：Agent / Skill / Tools / Workflow / Report Template
  -> 一键安装
  -> 跳转到数据源配置
  -> 运行样例告警
  -> 查看 Benchmark 结果
```

对应现有能力：

- `Hub` 已支持插件 catalog/install/uninstall。
- `PluginLoader` 已支持 Agent、Tool、Task、Channel 扩展。
- 安全场景包应成为 Hub 的一类标准包。
- `soc_ui` 的每个场景页可以作为安全场景包的可视化说明页。

### 路径五：运营治理与审计

```text
管理员进入系统中心
  -> 查看用户和权限
  -> 查看审计日志
  -> 查看系统日志
  -> 查看连接器健康状态
  -> 管理 Pro 升级和企业能力
```

对应现有能力：

- 当前分支已有 AuditLogs、Flocks Pro、Auth、Permission。
- `main` 分支已有 SystemLog。
- 两者应合并到统一系统中心。

## 页面与模块映射

```text
顶部导航
  -> Agent 工作区
  -> SOC 工作区
  -> 系统中心

Agent 工作区
  会话
    -> Agent 对话
    -> 调查辅助
    -> 继续追问
  Workspace
    -> 报告
    -> 证据附件
    -> Workflow 产物
  任务
    -> 定时任务
    -> 批量任务
    -> 后台执行
  工作流
    -> 安全 Workflow 模板
    -> 知识库 Context 注入
    -> Benchmark
  Agent
    -> 安全分析 Agent
    -> Subagent
    -> 角色配置
  Skills
    -> 安全产品使用规范
    -> 调查方法
    -> 企业 SOP
  知识库
    -> 企业文档
    -> 黑白名单
    -> 历史研判结论
    -> 企业 Context 检索
    -> 生成 Skill / Subagent 草稿
  工具
    -> API 工具
    -> MCP 工具
    -> 安全工具权限
    -> Dry-run / Approval
  数据源与设备
    -> 安全连接器
    -> 设备状态
    -> 凭证配置
    -> 能力映射
    -> 机房 / 分组运营视图
  Hub
    -> 安全场景包
    -> Agent 包
    -> 工具包
    -> Workflow 模板
  模型
    -> LLM Provider
    -> 默认模型
    -> 模型能力配置
  通道
    -> 企业微信 / 飞书 / 钉钉
    -> 告警推送
    -> 人工确认入口

SOC 工作区
  SOC 总览
    -> 安全运营引导
    -> 今日建议
    -> 跨场景态势
  告警研判
    -> Alert 队列
    -> 告警降噪漏斗
    -> 事件簇
    -> 自动研判
    -> Workflow 推荐
    -> 结果复核
  调查案件
    -> Case 列表
    -> Case 详情
    -> 证据链
    -> 时间线
    -> AI 助手
    -> Agent 调查过程
    -> 报告草稿
  安全设备
    -> 设备健康
    -> 数据源状态
    -> 机房 / 分组视图
    -> 跳转数据源配置
  态势情报
    -> 今日情报
    -> 影响资产
    -> 转漏洞排查 / 转告警研判
  漏洞排查
    -> CVE 影响判断
    -> 资产验证计划
    -> 复测闭环
  钓鱼演练
    -> 演练流程
    -> 合规边界
    -> 复盘报告
  互联网攻击面
    -> 互联网暴露资产
    -> 风险归属
    -> 转漏洞排查 / 转数据源与设备
  报告中心
    -> 事件报告
    -> 值班报告
    -> 月报 / 周报
    -> 管理层摘要

系统中心
  账号管理
    -> 用户
    -> 角色
    -> 配额
  权限管理
    -> 工具权限
    -> 高风险动作审批
    -> Human approval
  审计日志
    -> Agent 操作审计
    -> 工具调用审计
    -> 用户操作审计
  系统日志
    -> Backend 日志
    -> WebUI 日志
    -> 实时 tail
  监控
    -> 服务状态
    -> 任务状态
    -> 连接器健康
  Flocks Pro 升级
    -> License
    -> Pro 能力
    -> 升级和更新
```

## 后端集成建议

### 第一阶段：不新增复杂数据湖

优先新增轻量安全运营域模块：

```text
flocks/security_ops/
  models.py
  routes.py
  case_store.py
  evidence.py
  normalize.py
  knowledge/
```

`routes.py` 只提供安全对象和 Case 的轻量 API，不替代 Workflow、Tool、Hub、Provider。

### 第二阶段：复用现有存储

第一阶段可以复用 `Storage` 或 SQLite 表存储：

```text
security_alerts
security_cases
security_evidence
security_actions
security_case_sessions
security_case_workflow_executions
```

不建议一开始引入 Elasticsearch、ClickHouse 或完整数据湖，避免开源版复杂度失控。

### 第三阶段：与 Workflow 执行历史关联

建议建立关联关系：

```text
Case
  -> Alert
  -> WorkflowExecution
  -> Session
  -> Evidence
  -> ReportDraft
```

这样可以把现有 Workflow 执行历史自然升级为 SOC 调查时间线。

## 与当前分支和 main 的合并建议

### 应优先吸收 main 的能力

- `DeviceIntegration` 页面和后端设备接入路由。
- `SystemLog` 页面。
- `main` 的设备接入、系统日志和新版页面视觉，但导航结构需要调整为顶部三大工作区。
- 新版列表页和 SessionChat 视觉体验。

### 应吸收 `soc_ui` 的能力

- `webui/src/pages/Soc/*` 的 SOC 场景页面。
- `ModeSwitch` 的“运营视图 / 配置车间”双模式。
- `ConfigWorkshop` 和 `BlueprintConversationDrawer` 的自然语言配置体验。
- 告警运营页的漏斗、事件簇、研判 Drawer、深度调查 Drawer 和 Agent 会话 Drawer。
- SOC 预置数据文件中跨告警、设备、情报、漏洞、攻击面的统一场景 narrative。
- `soc.json` 中的 SOC 场景文案，可以作为开源版场景页和场景包说明的基础。

吸收方式建议分两步：

```text
第一步：将 `/soc/*` 接入 SOC 工作区，作为场景页 / onboarding / 场景预览
第二步：将其中成熟交互迁移到 `/security/*` 和 `/devices` 的真实工作台
```

### 应保留当前分支的能力

- AuditLogs。
- Flocks Pro 升级。
- Auth hook 扩展点。
- 用户、权限和会话共享策略。
- Pro 激活状态下的品牌和更新逻辑。

### 需要统一的地方

导航建议统一为顶部横向三大工作区，每个工作区内部使用左侧导航：

```text
顶部导航：Agent 工作区 / SOC 工作区 / 系统中心

Agent 工作区左侧导航：
  会话 / Workspace / 任务 / 工作流 / Agent / Skills / 知识库 / 工具 / 数据源与设备 / Hub / 模型 / 通道

SOC 工作区左侧导航：
  SOC 总览 / 告警研判 / 调查案件 / 安全设备 / 态势情报 / 漏洞排查 / 钓鱼演练 / 互联网攻击面 / 报告中心

系统中心左侧导航：
  账号管理 / 权限管理 / 审计日志 / 系统日志 / 监控 / Flocks Pro 升级
```

路由建议：

```text
/soc
/soc/alerts
/soc/assets
/soc/intel
/soc/vulnerabilities
/soc/drills
/soc/attack-surface
/security/alerts
/security/cases
/security/cases/:id
/devices
/workflows
/workspace
/audit-logs
/system-logs
```

路由语义建议：

```text
/soc/*              -> SOC 工作区场景页 / onboarding / 场景预览
/security/*         -> 真实安全运营数据和真实 Case
/devices            -> 真实数据源与设备接入
/workflows          -> Workflow 编排与模板管理
```

## 分阶段落地路线

### 阶段零：引入 SOC 工作区场景页面

目标：先把 `soc_ui` 的高保真场景页面作为 SOC 工作区的产品体验样板融入当前产品，支持开源场景页、售前沟通和场景包说明。

建设内容：

- 引入 `webui/src/pages/Soc/*`。
- 引入 `soc.json` 多语言文案。
- 在顶部导航增加 `SOC 工作区`，并在该工作区左侧导航挂载 SOC 总览、告警研判、安全设备、态势情报、漏洞排查、钓鱼演练、互联网攻击面等页面。
- 保留 SOC 预置数据文件，明确标注为预置数据来源。
- 将 `/soc/*` 与真实 `/security/*` 路由区分。
- 在每个场景页增加跳转真实工作台的入口，例如 `/security/alerts`、`/devices`、`/workflows`。

用户可完成：

```text
查看 SOC 场景页 -> 理解跨场景故事线 -> 切换配置车间 -> 查看 Rex 配置蓝图 -> 跳转真实配置页面
```

### 阶段一：接入与研判闭环

目标：让 Flocks 能接入安全数据源，并完成一条告警研判闭环。

建设内容：

- 合并 `main` 的 DeviceIntegration。
- 页面改名为“数据源与设备”。
- 将 `/soc/assets` 的机房和设备健康视图融入 `/devices` 的运营视图。
- 新增轻量 `Alert` 和 `Case` 模型。
- 新增 `/security/alerts` 告警列表。
- 以 `/soc/alerts` 的告警漏斗和事件簇列表作为页面原型。
- 复用已有 `tdp_alert_triage` workflow。
- 将 workflow 执行结果沉淀为 Evidence。

用户可完成：

```text
配置数据源 -> 接收告警 -> 运行研判 -> 查看证据链 -> 生成报告草稿
```

### 阶段二：调查案件与证据链

目标：让安全分析师可以围绕 Case 做结构化调查。

建设内容：

- 新增 `/security/cases` 和 Case 详情页。
- 支持 Case 关联 Session 和 Workflow Execution。
- 展示调查时间线。
- 展示 Evidence、Finding、Recommended Action。
- 在 Case 页内嵌 AI 助手。
- 将 `soc_ui` 的深度调查 Drawer、跨设备证据链、AgentSessionDrawer 演进为真实 Case 详情交互。

用户可完成：

```text
新建 Case -> 输入实体 -> 注入企业 Context -> 运行 Skill / Workflow -> 补证据 -> 复核结论 -> 输出报告
```

### 阶段三：知识库与场景包

目标：让企业上下文可以被检索、复用和反馈，并能沉淀为 Skill、Subagent 或场景包。

建设内容：

- 建设企业安全知识库。
- 支持企业文档、黑白名单、历史研判结论导入。
- 支持配置 CMDB / IAM / OA / 工单 / 拓扑等外部 Context Provider。
- 支持按 Alert / Entity / Asset / Identity / Case 召回知识库内容，并按需查询外部企业 Context。
- 支持分析师确认结论后回写知识库。
- 支持从稳定知识生成 Skill 或 Subagent 草稿。
- Hub 增加安全场景包分类。
- Workflow 支持引用知识库内容和外部 Context Provider 查询结果。
- Skills 页面展示安全产品使用规范、企业 SOP 和知识生成草稿。
- 工具页面展示 Read-only / Action 工具风险分类。
- 将 `soc_ui` 的配置车间映射为“自然语言生成场景包蓝图”的统一入口。
- 将 `soc_ui` 的态势情报、漏洞排查、钓鱼演练、攻击面页面作为场景包模板。

用户可完成：

```text
导入知识 -> 配置依赖数据源 -> 运行样例 -> 回写结论 -> 生成 Skill / Subagent 草稿 -> 发布为团队标准能力
```

### 阶段四：评估与治理

目标：让安全运营效果可评估、可审计、可治理。

建设内容：

- Workflow Benchmark。
- 告警研判指标。
- Agent 工具调用审计。
- Action 工具 human approval / dry-run。
- 系统中心整合审计日志、系统日志、监控和 Pro 能力。

用户可完成：

```text
评估研判效果 -> 查看审计 -> 管理权限 -> 控制高风险处置动作
```

## 开源版与商业版边界

### 开源版建议包含

- SOC 工作区场景页面。
- 数据源与设备接入框架。
- 通用安全对象模型。
- 告警研判和调查 Case 基础 UI。
- 安全 Workflow 模板。
- Evidence / Investigation Trace。
- 企业安全知识库。
- 从知识生成 Skill / Subagent 草稿。
- 报告草稿生成。
- 简单 Benchmark。

### 商业版建议增强

- 企业级连接器。
- 多租户和组织架构。
- 高级权限和审批流。
- 完整审计和合规报表。
- 企业数据湖。
- 行业场景包。
- 高级 SOC 大屏和运营报表。
- 私有化交付与 HA 部署。
- 将 SOC 工作区场景页面升级为客户可配置的行业解决方案中心。

## 最终建议

安全运营模块融入 Flocks 时，不应新建一套独立 SOC 系统，而应围绕现有 Agent、Tool、Skill、Workflow、Hub 和 Workspace 做安全运营语义增强。

最优产品路径是：

```text
main 的设备接入和安全运营 UI
  + soc_ui 的 SOC 工作区场景页面和产品化交互
  + 当前分支的 Pro / 审计 / 权限治理
  + 新增 Security Ops 轻量对象模型和 Case 详情页
  = 面向安全运营的 Flocks Security Agent Framework
```

第一优先级不是完整数据湖，而是把“场景视图 -> 数据源接入 -> 告警研判 -> 证据链 -> Case -> 报告 -> 配置复用”这条路径打通。`soc_ui` 分支提供了非常好的产品化表达，应作为 SOC 工作区场景页面和真实工作台的 UI 原型来源；`main` 分支的设备接入负责真实接入能力；当前分支的 Pro、审计和权限负责企业治理能力。三者融合后，Flocks 开源版会明显区别于普通 Agent 框架，并为后续商业 AISOC Copilot 打下产品基础。
