# workflow_template 配置引导参考

这个文件是 `workflow-builder` skill 内部的 `guide.md` 参考模板，用于创建真实工作流时改写成该工作流自己的配置引导文件。

真实工作流被配置时，Rex 必须读取真实工作流目录下的 `guide.md`，而不是把本参考模板当成工作流细节来源。

`workflow-config-guide` skill 只提供交互协议；本文件才是工作流配置细节、默认选项和验证方式的来源。

## 1. 工作流定位

- 工作流 ID：`workflow_template`
- 适用场景：模板占位工作流，用于复制后改造成真实工作流。
- 当前状态：隐藏模板，不应在普通 UI 中作为可运行工作流展示。

## 2. AI 引导方式

真实工作流的 `guide.md` 不需要描述 UI 快捷入口。快捷入口只是用户意图来源，Rex 应该：

1. 读取真实工作流的 `guide.md` 全文。
2. 根据用户点击的入口或自然语言需求，自动定位相关章节。
3. 提取该章节里的默认值、约束、样例、验证方法和禁止事项。
4. 用 question 工具一次只问一个最关键问题。
5. 必须提供自定义/补充输入；没有则填 `none`。

## 3. 输入模式

创建真实工作流的 `guide.md` 时，请把这里改成真实入口。

建议写清楚：

- 支持哪些入口：API、Syslog、Kafka、Webhook、Schedule、File、Manual test。
- 每种入口对应的 `config.json`/Storage 模板字段。
- 哪些入口互斥，哪些可以同时存在。
- 默认推荐哪个入口，以及原因。

Rex 提问要求：

- 一次只问一个输入模式问题。
- 必须提供自定义/补充输入；没有则填 `none`。
- 如果输入模式会改变发布模板，先展示 diff，再用 question 工具确认。

## 4. 来源形态

创建真实工作流的 `guide.md` 时，请描述真实来源。

建议写清楚：

- 来源产品或系统名称。
- payload 是对象、列表、文件路径、文本消息还是其它格式。
- 必填字段、可选字段和默认值。
- 字段映射由哪个节点处理。
- 是否已有代表性样例。

## 5. 输出去向

创建真实工作流的 `guide.md` 时，请描述真实输出。

建议写清楚：

- API 返回字段。
- 文件落盘路径。
- Kafka/IM/channel/下游工作流等外发方式。
- 输出失败或空结果时的行为。

## 6. 处理规则

创建真实工作流的 `guide.md` 时，请描述用户最常改的业务规则。

建议写清楚：

- 过滤条件。
- 阈值。
- 去重或聚合策略。
- 开关项。
- 哪些低层参数默认隐藏，不主动询问。

## 7. 样例验证

创建真实工作流的 `guide.md` 时，请放入一条最小可用样例，或说明用户应该粘贴什么格式。

Rex 验证时应优先做轻量检查：

- JSON/文本格式是否正确。
- 字段映射是否能进入入口节点。
- 预期输出是否符合 workflow.md。
- 不要启用外部副作用，除非用户明确确认。

## 8. 应用方式

发布配置模板的生效来源：

- 必须优先使用内置工具 `workflow_config_manage(action="get", workflow_id="<id>")` 读取 Storage/SQL 中的生效模板；读取运行态配置时使用 `workflow_config_manage(action="get", workflow_id="<id>", config_type="poller")`、`workflow_config_manage(action="get", workflow_id="<id>", config_type="syslog")` 或 `workflow_config_manage(action="get", workflow_id="<id>", config_type="kafka")`。
- 如果库里没有，调用 `workflow_config_manage(action="sync", workflow_id="<id>")`，由后端读取工作流目录下的 `config.json` 并迁移到 Storage/SQL。
- 修改配置前先调用 `workflow_config_manage(action="diff", workflow_id="<id>", config_type="<type>", config={...})` 生成差异；用户确认后才调用 `workflow_config_manage(action="put", workflow_id="<id>", config_type="<type>", config={...})` 写入完整配置。
- `config.json` 是导入/兜底模板，不是运行态开关。
- 不要直接写 `config.json` 来表示发布、接入或触发配置已经生效。
- 不要读取 `server_api_token` 或 `service_api_token`，不要手工 curl 本机后端配置接口，例如 `/config`、`/poller-config`、`/syslog-config` 或 `/kafka-config`；如果 `workflow_config_manage` 不可用或失败，只能保存草稿并说明未应用。
- 支持的运行态配置读写必须调用 `workflow_config_manage(config_type=...)`；启停、发布、取消发布等非配置运行态动作才调用运行时接口，不要通过修改 `config.json` 完成。
- 如果后端配置库不可用，只能把目标配置保存为草稿到 outputs，并明确说明未应用、未发布、未启动。

应用变更前：

- 展示计划。
- 展示 diff。
- 用 question 工具确认应用、保存草稿或暂不修改。

## 9. 查配置

只读检查顺序：

1. 读取本文件。
2. 读取 `workflow.md` 和 `workflow.json`。
3. 调用 `workflow_config_manage(action="get", workflow_id="<id>")` 或 `workflow_config_manage(action="status", workflow_id="<id>")`；需要查看定时、Syslog 或 Kafka 配置时，补充对应 `config_type`。
4. 必要时查看 `config.json` 是否只是兜底模板。
5. 汇总已配置项、缺失项和最推荐下一步。

查配置不得修改文件或运行态。
