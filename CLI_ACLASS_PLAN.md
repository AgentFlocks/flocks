# Flocks CLI A 类能力实施计划

## 范围与约束

仅修改本 worktree；不启动/停止服务，不提交、不推送，不修改真实用户配置。测试使用临时存储/配置及 mocked provider，不调用生产设备或付费模型。现有 service_manager 的 static/degraded 失败不处理。

## 代码调查与设计决策

CLI 采用 Typer + 同步门面/异步实现，测试用 CliRunner、monkeypatch、AsyncMock。已调查所有 CLI 模块的入口与调用关系，重点阅读 main、session_runner、session、agent、ACP、MCP、stats 以及相关底层接口。服务管理代码保持原样。

* headless 直接复用 SessionLoop.run 和 LoopCallbacks.runner_callbacks；不复用包含 Rich Live、交互 question、吞异常逻辑的展示层。保留现有 TUI 行为。
* agent.py 的 Agent.get/list/list_visible 已变成异步，必须修复同步调用才能注册交付。
* 权限必须在 ToolRegistry.execute 的实际执行边界生效，不能只过滤给模型的工具列表。运行控制使用 ContextVar，随异步任务传播，不写 session 权限或全局配置。
* JSON Schema 使用现有 jsonschema 依赖：将 schema 加入指令，并在最终结果输出前严格验证；失败非零退出。不声称所有 provider 原生支持 constrained decoding。
* MCP 使用已安装的官方 mcp SDK stdio transport，复用 headless 服务；ACP 的传输没有 EOF 唤醒且协议不同，不直接复用，也不扩大本任务去开放 ACP/debug。

## 分批、实现和验收

| 批次/缺口 | 具体实现 | 验收 |
|---|---|---|
| 1/P0 非交互 | 新建 cli/headless.py、commands/exec.py；exec prompt 或 `-` stdin；-C/-m/--agent/-o；直接运行完整 SessionLoop | CliRunner 测试参数、stdin、空输入拒绝；fake provider 多步工具调用；异常非零；不调用 Prompt/Live |
| 1/P0 结构化 | 输出 text/json/stream-json；stream 事件 session/text_delta/tool_start/tool_end/result；schema 本地验证；stdout 只输出协议，其他打印重定向 stderr | JSON 解析、逐行 NDJSON 解析、schema 成功/失败、错误结果、最终文件输出 |
| 2/P1 会话 | 新建 cli 会话解析 helper；session new [prompt]；resume [id] --last --prompt；exec --session/--last 同一执行路径；无 prompt new 仅建会话，resume 继续已有历史 | 临时 session/message 存储验证，最近会话限定当前 worktree，错误 ID/归档/跨工作树不静默新建 |
| 2/P1 平台命令 | 修正并注册 agent；新增 workflow.py、device.py、model.py；workflow list 复用中心扫描、run 复用 run_workflow；device store.list_devices；Provider.list_models | 命令注册、真实本地纯计算 workflow、masked device 列表、模型与 agent 异步接口测试 |
| 3/P1 权限/沙箱 | runtime_controls.py context policy；exec --permission-mode default/acceptEdits/bypassPermissions/plan/dontAsk；工具 glob allow/deny（deny 优先）；--sandbox off/on/read-only/workspace-write 接现有 Docker 沙箱，要求启用时失败关闭 | 实际注册测试工具的调用/拒绝；嵌套调用不越权；sandbox 配置、创建失败不执行；不交互 |
| 3/P2 配置 | -c dotted.path=value（JSON 值或字符串）；Config 的 context-local override；--strict-config 拒绝未知有类型配置字段；退出恢复 | 深层覆写、错误类型/未知字段、并发隔离、不落盘 |
| 4/P2 插件 | plugin list/install；按 PluginLoader 扩展目录安装本地文件/目录，支持显式 --root；默认用户插件根；拒绝覆盖/符号链接，原子拷贝，不写配置 | 临时 root 安装、scan_directory 发现、重复安装/错误输入失败 |
| 4/P2 MCP server | mcp serve；官方 SDK FastMCP 注册 flocks_exec，绑定启动时目录/权限；stdout stdio 协议；串行执行保护共享运行时 | 官方 MCP client initialize/list_tools/call_tool/EOF 集成测试；实际调用复用 headless |
| 5/P3 图片 | -i 可重复，校验 PNG/JPEG/GIF/WebP；FilePart data URI 持久化；复用 runner 多模态转换 | 无效图片拒绝；用户消息含 FilePart；转换为图片 block |
| 5/P3 预算 | --max-budget USD；运行期累计 usage 成本，模型请求前检查、每次 usage 后累计；达到预算阻止后续请求/工具 | 多步超限、零预算、无定价拒绝、续跑不计历史成本；输出 usage |

预算是模型调用边界的停止阈值，已发出的单次请求可能超过额度；不能中途预测 provider 账单。文档与 help 必须明确。无价格或非 USD 不按零成本放行。子任务继承上下文，但脱离当前进程的外部服务调用不属于此预算；受限模式拒绝可逃逸的后台编排工具。

## 风险规避

1. 默认配置可触发插件引导写入：验证时使用临时 FLOCKS_ROOT/config/data；不对生产配置运行有副作用的命令。
2. 现有日志或插件 stdout 会污染 JSON/MCP：保留输出句柄，业务执行期间重定向 stdout 到 stderr。
3. 异步调用和共享缓存：不在各字段解析时反复 asyncio.run；一次命令一个 loop；MCP 串行运行；配置覆写不污染缓存。
4. 权限与 Docker 不是同一概念：工具 allowlist 不声称是 OS sandbox；Docker 明确请求必须可用。
5. 外部模型/设备/Docker 无法在隔离环境进行生产验收：以真实存储/执行引擎和可控 provider/工具做集成测试，最终明确列出验证范围。

## 最终验收命令

```
env -u VIRTUAL_ENV uv run pytest tests/cli/ -q --tb=short
env -u VIRTUAL_ENV uv run ruff check flocks/cli/
env -u VIRTUAL_ENV uv run flocks --help
```

## 实施记录

规划后已完成全部十项实现和本地验证。无未实现的条目；外部服务验证范围见下文。

* 发现既有 help 测试明确断言 agent 不存在，与本次验收目标冲突。更新为正向断言 agent 及所有新增组存在，保留所有其他隐藏命令的否定断言；不是删除测试或放宽断言。新增命令放独立 Agent CLI help panel，保留运维命令的原有显示宽度。


## 最终实现、验收与决策记录

以下测试命令统一前缀为 `env -u VIRTUAL_ENV uv run pytest`。

| 缺口 | 实现位置（file:line） | 验证命令参数 | 实际结果 |
|---|---|---|---|
| 1 非交互执行 | flocks/cli/commands/exec.py:75；flocks/cli/headless.py:177 | tests/cli/test_exec_command.py -q | stdin、参数和真实 SessionLoop 多轮工具执行通过 |
| 2 结构化/Schema | flocks/cli/commands/exec.py:42；flocks/cli/headless.py:33 | tests/cli/test_exec_command.py -k 'output or schema' -q | text/json/NDJSON、stdout 隔离、Schema 有效/无效通过 |
| 3 会话能运行 | flocks/cli/commands/session.py:338；flocks/cli/headless.py:151 | tests/cli/test_exec_command.py tests/cli/test_platform_capabilities.py -k 'session or resume' -q | 创建、续跑、历史持久化、worktree 隔离、归档/不存在拒绝通过 |
| 4 平台命令 | flocks/cli/commands/agent.py:75；workflow.py:15、30；device.py:10；model.py:10 | tests/cli/test_platform_capabilities.py -q | agent 三命令、模型/设备接口、真实 workflow 成功与失败通过 |
| 5 权限/沙箱 | flocks/session/runtime_controls.py:19；flocks/tool/registry.py:1203；flocks/cli/headless.py:209 | tests/cli/test_runtime_controls.py tests/cli/test_exec_command.py -k 'permission or registry or sandbox or real_session' -q | 实际工具边界 allow/deny、恶意模型越权调用拒绝、沙箱不可用拒绝通过 |
| 6 运行期配置 | flocks/cli/headless.py:121；flocks/config/runtime.py；config.py:1520；config_writer.py:101 | tests/cli/test_exec_command.py -k config -q | dotted path、Python 字段名/JSON 别名、严格校验、恢复、原始配置读取和禁止落盘通过 |
| 7 插件 | flocks/cli/commands/plugin.py:91 | tests/cli/test_platform_capabilities.py -k plugin -q | 临时 root 安装、PluginLoader 实际加载、重复/符号链接/空目录拒绝通过 |
| 8 MCP server | flocks/cli/commands/mcp.py:686；flocks/cli/mcp_server.py:11 | tests/cli/test_mcp_serve.py -q | 官方客户端 stdio initialize/list_tools/tools/call/退出通过 |
| 9 图片 | flocks/cli/headless.py:52 | tests/cli/test_exec_command.py -k 'image or real_session' -q | 图片校验、FilePart 持久化、真实 runner 转成 image block 通过 |
| 10 预算 | flocks/session/runtime_controls.py:19；flocks/session/runner.py:2246、3494 | tests/cli/test_runtime_controls.py tests/cli/test_exec_command.py -k 'budget or real_session' -q | 零预算、未知定价/非 USD、真实首轮收费后阻止第二次模型请求通过 |

### 完整回归结果

* `env -u VIRTUAL_ENV uv run pytest tests/cli/ -q --tb=short`：**221 passed, 1 failed**。唯一失败为用户给定的 `test_supervisor_reports_webui_as_static_endpoint`；保留原断言与失败，不修复。新增 46 个测试项；原有 175 个通过项仍通过。
* `env -u VIRTUAL_ENV uv run pytest tests/config/test_config.py tests/config/test_config_writer.py tests/session/test_runner_step.py tests/session/test_session_loop_working_directory.py -q --tb=short`：**231 passed**。
* `env -u VIRTUAL_ENV uv run ruff check flocks/cli/`：**All checks passed**；本次修改的底层 Python 文件亦通过 ruff。
* `env -u VIRTUAL_ENV uv run flocks --help`：**退出 0**；新增 exec/agent/workflow/device/model/plugin 可见；mcp serve、session new/resume 位于现有命令组中；原 17 个顶层命令保留。
* `git diff --check`：通过。

### 明确语义与限制

* `session new` 无 prompt 只建会话并输出 ID；有 prompt 执行首轮。`session resume ID` / `--last` 无 prompt 时追加 “Continue the previous task.” 并执行新轮次，避免仅返回上一轮答案。需要完整权限/图片/预算参数时使用 `exec --session ID`。
* `--model` 使用 `provider/model`。`-C` 选择工作目录及会话作用域。`-o` 仅在成功时写最终文本；路径按调用者当前目录解释。
* `default` / `dontAsk` 默认允许本地读取和 todo 工具；`acceptEdits` 另允许编辑工具；`plan` 限读取；`bypassPermissions` 免 CLI 审批。allowlist 同时限制工具集合并批准匹配工具，denylist 优先。仍保留底层权限约束。question 永远拒绝，不挂起等待终端输入。
* headless 拒绝 delegate_task/run_workflow/run_workflow_node/task/task_manage 等可脱离调用生命周期的编排入口；workflow 通过单独 `flocks workflow run` 执行。没有把 allowlist 宣称为 OS 隔离。
* Docker sandbox 支持 off/on/read-only/workspace-write；on 与 read-only 使用只读项目挂载。沙箱模式仅允许有沙箱适配器的 bash/read/write/edit；初始化失败、工具缺少 container_name、elevated host execution 均拒绝。未连接真实 Docker daemon，本地以接口与实际执行边界测试验证，未宣称完成 Docker 实机验收。
* `--max-budget` 是本次调用的 USD 模型成本停止阈值，复用现有 CostCalculator/usage 持久化；不包含历史会话费用。一次已发出的请求可超过额度，达到阈值后不再发出请求；无定价、非 USD、缺失 usage 明确失败。headless 不启动标题生成或后台 Goal 评估；预算模式需要模型压缩上下文时明确退出。CLI 不对外部脚本/工具自行调用第三方 API 的账单作保证。
* Schema 是提示约束 + 返回前 jsonschema 校验，不是所有 provider 的原生 constrained decoding；不合规返回 success=false、exit 1。Schema 不自动联网解析外部引用。
* `--strict-config` 对有类型配置对象拒绝未知字段（包括顶层额外字段）；自由字典内部仍按其 schema 处理。覆写可供 Config 和 ConfigWriter 读取；执行期间禁止配置写入和用户 API 服务自动 bootstrap，避免覆写落盘。结束恢复 ContextVar 并使 Agent 缓存失效。
* plugin install 支持本地 .py/.yaml/.yml 文件或单一扩展目录，必选 --kind agents/tools/hooks/tasks；不是远程包管理器。安装不执行代码，按现有扫描深度安装，拒绝覆盖；同文件系统 staging + hard link 原子发布，失败回滚。测试只使用临时 --root。
* MCP 通过官方 SDK 对外提供 flocks_exec；工作目录、权限与预算由服务启动参数固定，调用者不能扩大权限；串行化避免项目 cwd/全局注册表互相干扰。stdio 协议句柄与诊断输出分离，EOF 后清理资源。
* CLI/协议测试不调用生产模型、设备或正在运行的服务。真实 SessionLoop 使用可控 provider，真实 workflow 使用本地计算节点，MCP 使用真实 SDK 子进程。没有执行 start/stop/restart、git commit/push，也没有修改真实 ~/.flocks 配置。

### 使用示例

```sh
flocks exec "解释这个项目" -C . --output-format json
flocks exec - --output-format stream-json < prompt.md
flocks exec "输出结论" --json-schema result.schema.json -o result.json
flocks exec "继续修复" --session SESSION_ID --allowed-tools 'read,glob,grep,edit,bash' --sandbox workspace-write
flocks exec "分析图片" -i screenshot.png -m provider/model --max-budget 0.5
flocks exec "分析" -c model=provider/model -c compaction.auto=false
flocks session new
flocks session resume --last --prompt "继续"
flocks workflow list
flocks workflow run ./workflow.json --inputs '{"n":41}'
flocks device list --format json
flocks model list --format json
flocks plugin install ./my_tool.py --kind tools --root ./isolated-plugins
flocks mcp serve -C . --allowed-tools 'read,glob,grep' --max-budget 1
```

* 最终资源检查：新增 cli/runtime.py 集中关闭 workflow store、channel binding DB 和 Storage；exec、平台命令、agent 扫描与 MCP EOF 路径均清理连接。agent 扫描诊断移至 stderr，JSON 空列表输出 `[]`。
