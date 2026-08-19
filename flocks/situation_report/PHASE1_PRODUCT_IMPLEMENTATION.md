# 一期 A1 产品流程实现说明

`product/` 是一期产品运行时；历史 RAG、Agent 效果实验、工作流和测试集脚本不属于本合同。

本文记录实验分支可直接运行的一期接口和部署边界，不依赖开发机上的外部文档路径。

## 当前实现边界

1. 报告复用原始 `POST /api/session` Schema，使用已有 `category=situation-report` 标记报告类型。后端只保存响应中的 `id`，不提交/保存 Project ID；Flocks 不增加 `workspace`、专用创建响应或报告创建幂等。
2. Flocks 不感知业务用户。后端使用原有 API Token 调用 Flocks；没有 `X-Flocks-User-Key` 或 delegated user。
3. Flocks 内部为每个报告 Session 注册一个一对一 Project，全部归原有 `api-token-service` 服务身份。物理工作区不按业务用户分层，直接以 Session ID 定位：

   ```text
   {dataPath}/situation-report-product/projects/{sessionID}/
   ```

   Project registry 继续复用 Flocks 原生的 owner 哈希 registry。若后续 Project 数量达到明显规模并确认单目录操作成为瓶颈，再为物理工作区增加哈希分片；不通过伪造业务用户目录分片。

4. `prompt_async` 仍是原接口。当且仅当服务身份请求 `agent=situation-report-product` 时进入产品执行分支。
5. 产品请求只有一个 text Part：

   ```text
   SITUATION_REPORT_REQUEST_V1
   {"action":{...严格 JSON...}}
   用户原始指令
   ```

6. operation 由业务入口确定：首次生成按钮发送 `generate`，专用的“应用并重新生成”按钮发送 `regenerate`，所有报告对话均发送 `modify`。对话文字即使包含“重新生成”也不改变、不阻断 `modify`。
7. prompt 不携带模板或素材引用。generate、modify、regenerate 每轮统一调用：

   ```text
   GET /internal/flocks/v1/report-sessions/{sessionID}/state/latest
       ?knownReportVersion=...
       &knownTemplateVersion=...
       &knownMaterialVersion=...
   ```

8. changed 资源并行下载；全部格式与哈希校验通过后才切换 `index.json` 当前指针。失败保持旧状态。
9. modify 使用同步后的后端报告并校验基线版本；regenerate 检查报告版本但不下载、不读取报告正文。
10. A1 单 Agent 只通过受限工具读取本轮上下文和分页素材、写候选、执行校验。
11. 输出先写不可变版本，再持久化一条不进入后续模型上下文的终态结果消息，最后发布 `situation.report.status`。终态 Event 返回结果消息的 `messageID/messagePartID` 以及原下载接口可接受的绝对输出路径；失败和取消也写对应终态消息。
12. 后端调用原始 `/api/file/download?path=...`。没有 `sessionID` 下载参数、报告分支、专用 ETag 或版本响应头。
13. 模板/素材在配置页只保存时不触发 Flocks；下一次可执行对话自动同步。

## 目录

```text
{productRoot}/projects/{sessionID}/
  index.json
  input/backend-reports/{backendReportVersion}/report.md
  templates/snapshots/{templateSnapshotID}/template.md
  materials/snapshots/{materialSnapshotID}/materials.jsonl
  runs/{generationID}/
    request.json
    event_state.json
    validation.json
    finalization.json
    preprocessing/generation_context_001.json
  work/{generationID}/report.md
  .locks/
  output/
    report.md
    status.json
    versions/{flocksReportVersion}/
      report.md
      metadata.json
```

目录本身是内部 Project worktree；Flocks Session 记录保存其内部 `project_id/directory`。`index.json` 只保存 `sessionID` 与报告运行状态，不保存业务用户、业务报告 ID 或共享复制关系。

## 主要模块

| 模块 | 职责 |
| --- | --- |
| `contracts.py` | text 信封和 action 严格 Schema |
| `session_state.py` | sessionID 状态创建、读取和原子保存 |
| `project_workspace.py` | 报告 Session 的内部 Project 创建、回滚和绑定校验 |
| `snapshots.py` | 下载安全、origin 限制、大小/哈希和格式校验 |
| `backend_sync.py` | 每轮三资源 latest、并行下载和原子指针切换 |
| `policy.py` | 报告意图白名单和 `open_report_config(sessionID)` |
| `workspace.py` | Agent 受限读取、分页素材、候选写入与校验 |
| `output.py` | 不可变输出版本、current 指针和状态文件 |
| `events.py` | `situation.report.status` 序号与发布 |
| `orchestrator.py` | A1 前置同步、Agent 调用、有界恢复、不可变发布、终态结果消息和状态事件 |
| `dispatch.py` | 原始 prompt 路由与产品运行时之间的窄适配 |

已删除：`bindings.py`、`copy.py`、业务用户/Project/Session 绑定和 Flocks 内共享复制。保留的是 Flocks 原生 Session -> Project 内部映射。

## 核心代码接入

对 Flocks 核心仅保留：

- `flocks/server/routes/session.py`：原始 Session 创建 Schema 不变；`category=situation-report` 时内部创建一对一 Project。原始 `prompt_async` 中按生产 Agent 名称和 API Token 服务身份进行窄分派；普通路径保持不变。
- `flocks/session/runner.py`：把 Agent YAML 的 `temperature` 和 `strict_tools` 传入本轮模型运行器；`strict_tools` 仅在该 Agent 上启用，确保运行时只暴露白名单工具。
- `flocks/provider/options.py`、`flocks/provider/sdk/anthropic.py`：支持显式 `thinking.type=disabled`，并在关闭 thinking 时继续传递温度；发送 Anthropic 工具 Schema 前只在副本上递归移除 Cloudwise 不接受的 `default` 字段，不改变本地参数校验。

Flocks 不打包或维护默认报告模板。模板由后端 latest 返回版本、快照 ID 和下载引用，产品运行时只保存对应 Session 实际拉取并校验通过的不可变快照。

Auth、Project API、Event 订阅和 File Download 均使用原始对外协议；业务后端不调用 Project API。
后端创建报告 Session 时应传入业务标题；原自动标题逻辑检测到已有有效标题后会直接返回，无需修改 Session 生命周期。

## 环境变量

```text
SITUATION_REPORT_PRODUCT_ROOT          可选；默认位于 Config.get_data_path()；外置时同步配置 allowReadPaths
SITUATION_REPORT_BACKEND_BASE_URL      后端内部 API 基址
SITUATION_REPORT_BACKEND_TOKEN         Flocks 调后端的服务凭据
SITUATION_REPORT_DOWNLOAD_ORIGINS      额外允许的下载 origin，逗号分隔
SITUATION_REPORT_MODEL_CONCURRENCY     产品 Agent 全局模型并发，默认 2
```

禁止重新引入：`SITUATION_REPORT_CALLBACK_TOKEN`、状态 callback/outbox、`X-Flocks-User-Key`。
模型及其凭据由 Flocks Provider 配置管理，业务后端通常不在 `prompt_async` 中指定模型。

## 开发环境部署

- 生产 Agent、Skill 和受限工具位于项目级 `.flocks/plugins/`，Flocks 进程必须从仓库根目录启动。仓库 Dockerfile 的 `WORKDIR /opt/flocks` 满足该要求。
- 服务器通过 Git 拉取本实验分支后再构建；不能只安装 Python wheel，因为 wheel 不包含项目级 `.flocks/plugins/`。
- 持久化 Flocks 数据目录；Docker 部署应挂载 `/home/flocks/.flocks`，避免 Session、Project registry 和报告版本随容器删除。
- 启动后先确认 `situation-report-product` Agent、同名 Skill 和五个 `situation_product_*` 工具均可发现，再执行真实后端闭环。
- 测试密钥不得写入仓库、镜像层、启动日志或普通配置文件。

## 开发后端 Mock

`scripts/situation_report_product_mock_backend.py` 是独立的开发联调进程，不进入
Flocks 生产调用链。它从命令行指定的模板和 JSONL 素材初始化每个新 Session，持久化
不可变快照与资源版本，并实现后端正式提供的两个 HTTP 能力：

- `GET /internal/flocks/v1/report-sessions/{sessionID}/state/latest`
- latest 返回的相对快照下载 URL

Mock 的 `/__mock__/...` 路由仅用于联调控制，不是正式后端契约。其中
`PUT /__mock__/report-sessions/{sessionID}/resources/{report|template|materials}?version=N`
用于模拟后端消费成功 Event 后保存报告，或用户只保存配置后升级模板/素材版本。

启动时显式提供本地联调模板和 JSONL 素材；这些文件是 Mock 输入，不属于 Flocks 产品内置资源：

```bash
export SITUATION_REPORT_MOCK_TOKEN='<仅从开发环境密钥注入>'
python scripts/situation_report_product_mock_backend.py \
  --state-dir /tmp/situation-report-product-mock \
  --template /absolute/path/to/test-template.md \
  --materials /absolute/path/to/test-materials.jsonl \
  --host 127.0.0.1 \
  --port 18090
```

Flocks 进程使用相同的开发凭据连接 Mock：

```text
SITUATION_REPORT_BACKEND_BASE_URL=http://127.0.0.1:18090
SITUATION_REPORT_BACKEND_TOKEN=<与 Mock 相同的开发凭据>
```

真实闭环必须在提交 `prompt_async` 前订阅 Flocks `/api/event`。收到本 generation 的
`situation.report.status: succeeded` 后，后端按 Event 的 `output.path` 调原始
`/api/file/download` 保存报告版本。开发联调时再通过上述 `__mock__` PUT 导入该报告，
即可继续验证 modify、regenerate 以及只保存模板/素材后的下一轮同步。Mock 不伪造模型
输出，也不代替后端 Event 消费逻辑。终态 Event 的 `messageID/messagePartID` 可关联
`GET /api/session/{sessionID}/message` 中 `metadata.situationReport.kind=terminal_status`
的持久化结果 Part，用于页面刷新后的终态恢复。

## 测试边界

- 流程测试使用调用方显式提供的 Markdown 模板和真实 JSONL 素材；产品包不提供默认模板或内置测试素材。
- MockTransport 只模拟后端 HTTP 边界，不使用随意构造的素材冒充真实输入。
- 确定性候选报告只用于校验发布流程，不代表模型效果。
- 线上模型只用于验证真实流程、工具调用、结构校验与发布闭环；当前不做主观效果评价或模型横评。
- 需要覆盖：普通 Session/API 鉴权；报告 Session 内部 Project 1:1；普通 Session 不能误入产品 Agent；单 text 信封；每轮 latest；changed/unchanged；部分失败不切换；regenerate 不下载报告；成功/失败/取消终态消息与 Event 关联；Message API 可恢复终态；Event 无用户/Project；原 download 可读取输出；普通 prompt 回归。
