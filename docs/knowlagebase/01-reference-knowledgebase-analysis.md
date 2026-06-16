# Flocks 知识库参考实现分析

本文分析本机 `~/iCloud/0_work/projects/github` 下 8 个仓库与“知识库 / 记忆 / RAG / 上下文 / 安全知识工具”相关的实现：

- `DeepSeekSelfTool`
- `ai-agent-deep-dive`
- `claude-code`
- `claude-code-sourcemap`
- `dify`
- `openclaw`
- `opencode`
- `hermes-agent`

说明：

- `claude-code` 是 clean-room Rust reimplementation 与规格分析资料，不是官方源码。
- `claude-code-sourcemap` 是 sourcemap 还原源码，本文只做架构层总结，不依赖或复用其源码。
- `hermes` 在本机实际目录名为 `hermes-agent`。

## 总体结论

这 8 个仓库大致覆盖 6 类知识库范式：

| 项目 | 核心范式 | 存储形态 | 检索/注入方式 | 主要特点 |
|---|---|---|---|---|
| `dify` | 产品级知识库/RAG 平台 | Dataset、Document、Segment、Metadata、外部知识 API | workflow 节点、hit testing、semantic/full-text/hybrid/keyword retrieval | 数据集生命周期完整，权限、元数据、分段、rerank、外部知识都比较成熟 |
| `openclaw` | Markdown 源文件 + 向量/全文索引 | `MEMORY.md`、`memory/YYYY-MM-DD.md`、SQLite/QMD | `memory_search`、`memory_get`、预压缩 flush | 文件为真相源，索引可重建，插件化后端 |
| `claude-code-sourcemap` | 文件型 typed memory + session memory + auto consolidation | `MEMORY.md`、topic files、session memory、agent memory | 启动注入、相关记忆选择、后台 dream、session hook | 记忆分类、索引、时效提示、团队记忆、agent 记忆都较完整 |
| `claude-code` | 后台“记忆整理”规格 | Markdown memory files、session state、transcript | dream consolidation、compaction summary | 强调长期记忆治理、自动整理、跨会话连续性 |
| `hermes-agent` | Provider 化长期记忆系统 | `MEMORY.md`/`USER.md` + 外部 provider | prefetch、sync_turn、memory tool、context engine | 生命周期钩子完整，安全防护强，支持外部记忆后端 |
| `opencode` | 指令/规则/Skill 上下文系统 | `AGENTS.md`、`CLAUDE.md`、`.opencode`、Skill Markdown | 系统 prompt 注入、按需 `skill` 加载、`@file` 附件 | 轻知识库，重配置、发现、权限和上下文按需加载 |
| `DeepSeekSelfTool` | 固定安全任务模板工具箱 | Prompt template + GUI 输入 | 用户选择任务后直接调用模型 | 没有知识库，但安全任务模板非常贴近蓝队使用 |
| `ai-agent-deep-dive` | Agent OS 研究材料 | PDF/Markdown 研究报告 | 人读资料 | 提供 Agent 产品架构视角，不是可运行知识库模块 |

对 Flocks 的直接启发：

1. 知识库不应只等于向量库。至少要同时包含 Dataset、Document、Segment、Entity、Relation、Memory、Skill、Workflow knowledge node。
2. 源文件或原始记录必须可审计，向量索引只是派生物。
3. 网安场景需要精确检索和结构化字段，不能只靠 semantic search。
4. Agent 使用知识时要分层：系统级规则、任务级 Skill、按需召回、原文读取、结构化 enrich。
5. 写入知识需要治理：权限、review、TTL、冲突、敏感信息扫描、prompt injection 防护。
6. 成熟知识库应支持测试与观测：hit testing、query log、召回质量、用户反馈、引用链。

## Dify

参考文件：

- `api/models/dataset.py`
- `api/core/rag/retrieval/dataset_retrieval.py`
- `api/core/rag/datasource/retrieval_service.py`
- `api/core/rag/retrieval/retrieval_methods.py`
- `api/core/rag/index_processor/index_processor_base.py`
- `api/core/rag/index_processor/processor/paragraph_index_processor.py`
- `api/core/rag/index_processor/processor/parent_child_index_processor.py`
- `api/core/rag/rerank/weight_rerank.py`
- `api/core/rag/data_post_processor/data_post_processor.py`
- `api/core/workflow/nodes/knowledge_retrieval/knowledge_retrieval_node.py`
- `api/core/workflow/nodes/knowledge_index/knowledge_index_node.py`
- `api/services/hit_testing_service.py`
- `api/services/external_knowledge_service.py`
- `api/services/metadata_service.py`

### 形态

Dify 是这批项目里最接近“完整知识库产品”的实现。它不是简单 memory，而是围绕 Dataset 建立了完整知识生命周期。

核心对象包括：

- `Dataset`：租户级知识库，包含权限、provider、indexing technique、embedding provider/model、retrieval model、外部知识绑定、多模态开关等。
- `Document`：文档处理生命周期，包含 parsing、cleaning、splitting、indexing、completed/error 等状态。
- `DocumentSegment` / child chunk：可检索切片。
- `DatasetProcessRule`：处理规则，支持 automatic、custom、hierarchical。
- `DatasetMetadata`：用户自定义元数据和内置字段。
- `ExternalKnowledgeApis` / `ExternalKnowledgeBindings`：外部知识 API 绑定。

### 入库与处理

Dify 的入库是显式 pipeline：

1. datasource 加载。
2. 文档解析。
3. 清洗。
4. 分段。
5. embedding 或 keyword index。
6. 存储 segment。
7. 后台任务处理 indexing、retry、disable/enable、delete、clean。

它支持 paragraph index 和 parent-child index。后者对 Flocks 很有价值：网安文档里经常有“章节级上下文 + 子段落证据”，例如 IR runbook、规则说明、厂商公告，检索子段落时需要保留父章节语义。

### 检索

Dify 支持多种 retrieval method：

- semantic search
- full text search
- hybrid search
- keyword search

`RetrievalService` 会并行执行 keyword、vector、full-text 或外部检索，再去重和合并。后处理支持 rerank model、weighted rerank、score threshold、结果 reorder。它还支持 metadata filtering 和 attachment/image query。

### Hit Testing

`HitTestingService` 提供知识库调试能力：用户输入 query，系统返回 records，并记录 `DatasetQuery`。这是产品化知识库必须具备的能力。Flocks 后续应提供“告警样本/问题样本 -> 召回结果 -> 调权/修正”的测试闭环。

### 外部知识

`ExternalDatasetService` 支持外部知识 API：

- 注册 endpoint 与 api_key。
- 检查 `/retrieval` 可达性。
- 绑定外部 dataset。
- 通过外部服务返回 content、title、score、metadata。
- 请求使用 SSRF proxy，说明外部知识接入必须有网络安全边界。

### 对 Flocks 的启发

- Flocks 应设计 Dataset/Document/Segment，而不是只暴露 memory 文件。
- 必须有 document status 和 ingest job，能看到索引进度、错误、重试。
- 必须支持元数据过滤：租户、客户、资产组、告警类型、规则 ID、时间范围。
- 必须提供 hit testing 页面/API。
- 外部知识 API 要作为一等能力，方便接入 SIEM、EDR、CMDB、TI、工单系统。

## OpenClaw

参考文件：

- `docs/concepts/memory.md`
- `docs/cli/memory.md`
- `src/agents/memory-search.ts`
- `src/memory/memory-schema.ts`
- `src/memory/backend-config.ts`
- `src/gateway/server-startup-memory.ts`
- `src/memory/manager-embedding-ops.ts`

### 形态

OpenClaw 的 memory 设计非常清晰：Markdown 是源，索引是派生。

默认文件布局：

- `memory/YYYY-MM-DD.md`：每日追加日志。
- `MEMORY.md`：长期 curated memory。

Agent 面向两个核心工具：

- `memory_search`：语义召回 indexed snippets。
- `memory_get`：按路径和行号读取具体 Markdown 内容。

### 索引与检索

SQLite schema 包含：

- `files`：文件 hash、mtime、size。
- `chunks`：chunk 文本、路径、行号、embedding。
- `embedding_cache`：按 provider/model/provider_key/hash 缓存 embedding。
- 可选 FTS5 表：全文索引。

配置上支持 OpenAI、本地、Gemini、Voyage、Mistral、Ollama 等 embedding provider。默认 hybrid search 思路是 vector + text，并支持 MMR、temporal decay、watch、onSearch sync。

### QMD 后端

OpenClaw 支持 `memory.backend = "qmd"`，把检索交给 QMD sidecar。它仍以 Markdown 为源，但检索、索引、更新周期、引用模式由后端管理。

### 对 Flocks 的启发

Flocks 现有 `flocks/memory` 已经接近 OpenClaw 路线：`MemoryManager`、`MemoryIndexer`、`HybridSearch`、SQLite/FTS/embedding、`memory_search/get/write`。后续知识库模块应复用这套底座，在其上增加 Dataset、权限、实体、关系、审计和 WebUI。

## Claude Code Sourcemap

参考文件：

- `restored-src/src/memdir/memoryTypes.ts`
- `restored-src/src/memdir/memdir.ts`
- `restored-src/src/memdir/memoryScan.ts`
- `restored-src/src/memdir/memoryAge.ts`
- `restored-src/src/memdir/findRelevantMemories.ts`
- `restored-src/src/services/SessionMemory/sessionMemory.ts`
- `restored-src/src/services/autoDream/autoDream.ts`
- `restored-src/src/tools/AgentTool/agentMemory.ts`
- `restored-src/src/utils/teamMemoryOps.ts`

### Typed Memory

其 memory taxonomy 很值得参考：

- `user`：用户偏好、角色、长期目标。
- `feedback`：用户对 agent 行为的反馈。
- `project`：当前项目中不可从代码直接推导的背景、决策、约束。
- `reference`：外部系统、流程、约定、使用注意事项。

它还明确禁止保存低价值或容易过期的信息，例如代码架构、git 历史、临时任务细节、已经存在于规则文件里的内容。这一点对 Flocks 很关键：不能把所有会话和工具输出都升格为长期知识。

### 文件型记忆结构

核心结构是 `MEMORY.md` + topic files：

- `MEMORY.md` 是索引，不是正文。
- 每个 topic file 带 frontmatter，包含 name、description、type 等。
- `MEMORY.md` 有行数和字节上限，防止启动 prompt 膨胀。
- 相关记忆通过扫描 headers 形成 manifest，再由 side query 选择最多 5 个相关文件。
- 记忆召回会带时效提醒，旧记忆需要和当前事实核对。

### Session Memory

`SessionMemory` 是后台 hook：

- 达到 token 阈值和工具调用阈值后触发。
- 使用 forked agent 更新 session memory 文件。
- 只允许编辑指定 memory file，权限边界很窄。
- 支持手动 extraction。

这说明“会话压缩摘要”和“长期知识库”应该分离。前者为了当前会话延续，后者为了未来可复用。

### Auto Dream

`autoDream` 是后台 consolidation：

- 时间门限。
- session 数门限。
- lock 防并发。
- forked agent 读取 transcript 和 memory，整理 topic files。
- 完成后向主会话追加“改进了哪些 memory”的系统消息。

### Agent Memory 与 Team Memory

Agent memory 支持：

- `user` scope：跨项目。
- `project` scope：项目共享。
- `local` scope：本机本项目，不进版本控制。

Team memory 则对读写搜索进行识别和统计，说明团队共享记忆需要单独的权限和可观测性。

### 对 Flocks 的启发

- 设计 `memory_kind` / `knowledge_kind` 时应避免大而全，先有清晰分类。
- `MEMORY.md` 可以作为人工可读索引，但不能承载正文。
- 自动整理要有门限、锁、审计和可取消任务。
- Agent 写共享知识时必须默认进入 review 或低信任层。
- 旧知识召回时要显式提醒时效，并鼓励工具复核。

## Claude Code Clean-Room Spec

参考文件：

- `README.md`
- `spec/05_components_agents_permissions_design.md`
- `spec/06_services_context_state.md`

### 形态

该仓库描述的 Claude Code 知识相关能力主要分三层：

1. `SessionMemory`：会话内/跨会话摘要与连续性。
2. `autoDream`：后台 memory consolidation engine。
3. `CLAUDE.md`/agent/skills：项目规则与 agent 专用上下文。

其价值不在具体实现，而在把 memory 放进“agent runtime”的整体架构中：会话状态、压缩、后台任务、agent、skill、权限共同组成知识系统。

## Hermes Agent

参考文件：

- `agent/memory_provider.py`
- `agent/memory_manager.py`
- `tools/memory_tool.py`
- `agent/context_engine.py`
- `agent/context_references.py`
- `hermes_cli/memory_setup.py`
- `gateway/memory_monitor.py`

### Provider 抽象

`MemoryProvider` 定义了完整生命周期：

- `initialize()`
- `system_prompt_block()`
- `prefetch()`
- `queue_prefetch()`
- `sync_turn()`
- `get_tool_schemas()`
- `handle_tool_call()`
- `shutdown()`
- 可选：`on_turn_start()`、`on_session_end()`、`on_session_switch()`、`on_pre_compress()`、`on_delegation()`

这是一套比普通 RAG 更完整的“记忆操作系统”接口。

### 安全设计

Hermes 的 `MemoryManager` 做了几件很适合 Flocks 的事：

- 只允许一个 external provider，避免 schema 膨胀和后端冲突。
- provider 失败隔离，后台 prefetch/sync 不阻断主流程。
- memory context fencing。
- streaming scrubber，防止 `<memory-context>` 泄露给用户。
- 内置 `MEMORY.md`/`USER.md` 使用严格权限、文件锁和 threat scan。

### Context References

Hermes 支持 `@file`、`@folder`、`@diff`、`@staged`、`@git`、`@url` 等上下文引用，并做 allowed-root、敏感 home dir、token soft/hard limit 控制。

对 Flocks 来说，这提示知识库入口不应只有“上传文档”，还应允许用户或 workflow 明确引用当前上下文、日志片段、diff、工单、URL，并自动变成可审计来源。

## opencode

参考文件：

- `packages/opencode/src/session/instruction.ts`
- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/config/config.ts`
- `packages/opencode/src/skill/skill.ts`
- `packages/opencode/src/tool/skill.ts`
- `packages/opencode/src/session/compaction.ts`

### 形态

opencode 没有看到显式“长期记忆向量库”模块，其知识机制主要是上下文与规则层：

1. 指令文件：`AGENTS.md`、`CLAUDE.md`、`CONTEXT.md`。
2. 配置 instructions：文件路径、glob、URL。
3. Skill：`SKILL.md` + frontmatter。
4. `@file` 或 `@agent` 引用。
5. session compaction 与 tool output pruning。

### Skill 系统

Skill 扫描多个目录，只先暴露 skill 名称、描述和位置；模型识别匹配后再调用 `skill` 工具加载全文。`SessionCompaction` 还保护 skill 工具输出，说明 skill 被视为高价值上下文。

### 对 Flocks 的启发

Flocks 知识库不必把所有“怎么做”都放进 RAG。推荐分工：

- Knowledge：事实、证据、案例、规则、资产、情报。
- Skill：操作流程、工具使用协议、高风险系统约束。
- Workflow：自动化编排和状态迁移。

## DeepSeekSelfTool

参考文件：

- `README.md`
- `DeepSeekSelfTool.py`
- `config.py`
- `ollamaMain.py`

### 形态

这是一个面向网络安全场景的 GUI 工具箱，没有看到持久知识库、索引、RAG 或 memory。它的价值在于把安全任务固定成模板：

- 流量分析。
- 解码。
- JS 代码审计。
- 进程分析。
- HTTP 请求转 Python。
- 文本处理。
- 正则生成。
- WebShell 检测。
- 翻译。

### 对 Flocks 的启发

Flocks 知识库不只是“存东西”，还应内置安全任务入口和模板：

- 选择任务类型后自动加载对应知识包、prompt 模板、查询模板、输出格式。
- 对 WebShell、进程、流量、漏洞、规则调优等场景提供结构化输入。
- 任务模板本身也应作为知识对象管理，可版本化、可评估。

## AI Agent Deep Dive

参考文件：

- `README.md`
- `ai-agent-deep-dive-report.pdf`

### 形态

该仓库是研究资料，不是源码实现。它把现代 Coding Agent 总结为 Agent Operating System：

- prompt runtime assembly。
- tool / permission / hook / analytics / MCP pipeline。
- built-in agents。
- skill / plugin / hook 生态。
- `src/memdir` 等记忆模块。

### 对 Flocks 的启发

知识库不应是一个孤立模块，而应融入 Flocks Agent OS：

- Prompt assembly：根据任务注入少量高价值知识。
- Tool layer：提供搜索、读取、写入、关联、评估工具。
- Permission layer：控制不同 agent 的知识访问。
- Hook layer：session end、workflow end、pre-compress、delegation 时触发知识沉淀。
- Plugin/Skill layer：让外部系统和行业知识包可扩展。

## 横向架构对比

| 维度 | Dify | OpenClaw | Claude Sourcemap | Hermes | opencode | DeepSeekSelfTool |
|---|---|---|---|---|---|---|
| 产品化知识库 | 强 | 中 | 中 | 中 | 弱 | 无 |
| 文件可审计 | 中 | 强 | 强 | 强 | 强 | 弱 |
| 向量/全文检索 | 强 | 强 | 弱/间接 | 取决于 provider | 弱 | 无 |
| 结构化元数据 | 强 | 弱 | 中 | 中 | 弱 | 弱 |
| 外部知识接入 | 强 | 中 | 弱 | 强 | URL 指令 | 无 |
| 自动沉淀 | 中 | 中 | 强 | 强 | 弱 | 无 |
| Skill/规则系统 | 中 | 中 | 强 | 中 | 强 | 模板化 |
| 安全边界 | 中 | 中 | 中 | 强 | 中 | 弱 |

## 对 Flocks 的推荐取舍

### 直接采用

- Dify 的 Dataset / Document / Segment / Metadata / Hit Testing / External Knowledge API。
- OpenClaw 的 Markdown 源文件 + 可重建索引 + `search/get` 双工具。
- Claude Sourcemap 的 typed memory、topic file、自动整理、时效提醒、agent/team scope。
- Hermes 的 provider lifecycle、context fencing、streaming scrubber、pre-compress hook。
- opencode 的 Skill 按需加载和 instruction discovery。
- DeepSeekSelfTool 的安全任务模板。

### 不建议照搬

- 不要只做通用聊天知识库；Flocks 必须有 IOC/CVE/资产/告警/规则/playbook 等结构化模型。
- 不要把所有记忆默认共享；共享知识必须有 scope、trust、review。
- 不要只靠向量召回；网安场景必须有 exact search 和 metadata filtering。
- 不要把 Skill 和 Knowledge 混成一个概念；一个教 agent 怎么做，一个提供证据和事实。

## Flocks 当前基础

Flocks 已有 memory 能力：

- `flocks/memory/manager.py`
- `flocks/memory/config.py`
- `flocks/memory/types.py`
- `flocks/memory/sync/indexer.py`
- `flocks/memory/sync/chunking.py`
- `flocks/memory/search/hybrid.py`
- `flocks/session/features/memory.py`
- `flocks/hooks/builtin/session_memory.py`
- `flocks/tool/system/memory.py`

已有能力包括 Markdown 存储、混合检索、embedding provider fallback、chunking、session memory hook、`memory_search/get/write` 工具。

因此 Flocks 知识库推荐以“扩展现有 memory 底座”为主：

1. 保留 memory 作为个人/项目轻量长期记忆。
2. 新增 knowledge 作为可共享、可审计、可权限控制的 SecOps 知识平台。
3. 共用 chunking、embedding、FTS、vector、tool registration 等基础能力。
4. 在 knowledge 层补齐 Dataset、Document、Segment、Entity、Relation、ACL、Audit、Review、Workflow node。
