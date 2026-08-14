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

6. prompt 不携带模板或素材引用。generate、modify、regenerate 每轮统一调用：

   ```text
   GET /internal/flocks/v1/report-sessions/{sessionID}/state/latest
       ?knownReportVersion=...
       &knownTemplateVersion=...
       &knownMaterialVersion=...
   ```

7. changed 资源并行下载；全部格式与哈希校验通过后才切换 `index.json` 当前指针。失败保持旧状态。
8. modify 使用同步后的后端报告并校验基线版本；regenerate 检查报告版本但不下载、不读取报告正文。
9. A1 单 Agent 只通过受限工具读取本轮上下文和分页素材、写候选、执行校验。
10. 输出先写不可变版本，再发布 `situation.report.status`。Event 返回原下载接口可接受的绝对输出路径。
11. 后端调用原始 `/api/file/download?path=...`。没有 `sessionID` 下载参数、报告分支、专用 ETag 或版本响应头。
12. 模板/素材在配置页只保存时不触发 Flocks；下一次可执行对话自动同步。

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
    preprocessing/generation_context_001.json
  work/{generationID}/report.md
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
| `orchestrator.py` | A1 前置同步、Agent 调用、发布和状态事件 |
| `dispatch.py` | 原始 prompt 路由与产品运行时之间的窄适配 |

已删除：`bindings.py`、`copy.py`、业务用户/Project/Session 绑定和 Flocks 内共享复制。保留的是 Flocks 原生 Session -> Project 内部映射。

## 核心代码接入

对 Flocks 核心仅保留：

- `flocks/server/routes/session.py`：原始 Session 创建 Schema 不变；`category=situation-report` 时内部创建一对一 Project。原始 `prompt_async` 中按生产 Agent 名称和 API Token 服务身份进行窄分派；普通路径保持不变。
- `pyproject.toml`：打包报告模板。

Auth、Project API、Event 订阅和 File Download 均使用原始对外协议；业务后端不调用 Project API。
后端创建报告 Session 时应传入业务标题；原自动标题逻辑检测到已有有效标题后会直接返回，无需修改 Session 生命周期。

## 环境变量

```text
THREATBOOK_CN_LLM_BASE_URL              开发环境使用 https://llm-test.threatbook-inc.cn/api
THREATBOOK_CN_LLM_API_KEY               模型服务凭据，只能由环境或密钥管理注入
SITUATION_REPORT_PRODUCT_ROOT          可选；默认位于 Config.get_data_path()；外置时同步配置 allowReadPaths
SITUATION_REPORT_BACKEND_BASE_URL      后端内部 API 基址
SITUATION_REPORT_BACKEND_TOKEN         Flocks 调后端的服务凭据
SITUATION_REPORT_DOWNLOAD_ORIGINS      额外允许的下载 origin，逗号分隔
SITUATION_REPORT_MODEL_CONCURRENCY     产品 Agent 全局模型并发，默认 2
```

禁止重新引入：`SITUATION_REPORT_CALLBACK_TOKEN`、状态 callback/outbox、`X-Flocks-User-Key`。

## 开发环境部署

- 生产 Agent、Skill 和受限工具位于项目级 `.flocks/plugins/`，Flocks 进程必须从仓库根目录启动。仓库 Dockerfile 的 `WORKDIR /opt/flocks` 满足该要求。
- 服务器通过 Git 拉取本实验分支后再构建；不能只安装 Python wheel，因为 wheel 不包含项目级 `.flocks/plugins/`。
- 持久化 Flocks 数据目录；Docker 部署应挂载 `/home/flocks/.flocks`，避免 Session、Project registry 和报告版本随容器删除。
- 启动后先确认 `situation-report-product` Agent、同名 Skill 和五个 `situation_product_*` 工具均可发现，再执行真实后端闭环。
- 测试密钥不得写入仓库、镜像层、启动日志或普通配置文件。

## 测试边界

- 流程测试读取仓库内真实 Markdown 模板和真实 JSONL 测试素材。
- MockTransport 只模拟后端 HTTP 边界，不使用随意构造的素材冒充真实输入。
- 确定性候选报告只用于校验发布流程，不代表模型效果。
- 当前不执行线上模型效果测试。
- 需要覆盖：普通 Session/API 鉴权；报告 Session 内部 Project 1:1；普通 Session 不能误入产品 Agent；单 text 信封；每轮 latest；changed/unchanged；部分失败不切换；regenerate 不下载报告；Event 无用户/Project；原 download 可读取输出；普通 prompt 回归。
