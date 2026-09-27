# Flocks 知识库：从零部署 RAGFlow、接入 Flocks、检索与 Workflow

> 适用范围：本仓库 `feat/rag3` 上 **Core 直连 RAGFlow** 的实现（源码基线 `6f500125`），以 RAGFlow **v0.27.2** 为对接目标。以下采用 **macOS/Linux 上从源码运行 Flocks + Docker Compose 运行 RAGFlow** 的路径；二者可在同一台主机，也可分机部署。文中的“知识集”对应代码和 RAGFlow API 的 `dataset`，**不是另一个资源类型**。当前 `flocksrag` 只是不可用的占位适配器。
>
> 本指南基于本仓库源码及下方 RAGFlow v0.27.2 官方资料的检索摘要编写；官方页面全文在编写时未能抓取，也未在一台全新机器上执行端到端安装。**请核对所下载的 v0.27.2 镜像、Compose 文件和实际界面**，尤其不要把版本、模型或端口的示例值当成已探测到的运行配置。本文所有密钥命令都由部署者在目标机器上执行；不要把真实密钥写进仓库、命令行参数、工单或截图。

## 1. 先弄清架构与三种凭据

```text
浏览器 / Agent / Workflow → Flocks Core (/api/knowledgebase) → RAGFlow (/api/v1/...)
```

- Core 内的 `flocks/knowledgebase/` 负责 API 适配、校验、文件/文档投影、检索范围；RAGFlow 负责源文件、解析、Embedding、索引与检索。**不需要启动旧的 `services/knowledgebase` 独立服务或 8767 端口**；它的源码已从本分支移除。（`flocks/knowledgebase/client.py:68-79`；`flocks/knowledgebase/ragflow.py:245-354`）
- **模型供应商凭据**：交给 RAGFlow，供其调用 Embedding 服务；可选云模型或自建模型。**RAGFlow API key**：由 RAGFlow 签发，供 Flocks Core 通过 Bearer 请求 RAGFlow。**Flocks 对话模型凭据**另由 Flocks 管理，用于 Agent 生成回答。三者不可互换。（`flocks/knowledgebase/ragflow.py:17-48`；`flocks/tool/knowledgebase.py:20-57`）
- Flocks 界面的“就绪”只表示 Core 已创建本地客户端，**不执行 RAGFlow 探活/模型测试**。必须另做带 RAGFlow API key 的数据集列表请求，并实际完成一次上传→关联→解析→检索。（`flocks/knowledgebase/runtime.py:20-69`；`flocks/server/routes/knowledgebase.py:135-138`）

## 2. 新机器准备

| 项目 | 建议/依据 |
|---|---|
| RAGFlow 主机 | RAGFlow v0.27.2 官方 Docker 指南要求至少约 **4 CPU、16 GB 内存、50 GB 磁盘**，Docker **24+**、Compose **2.26.1+**；以实际语料、模型和所选后端容量为准。[官方配置/构建文档](https://ragflow.io/docs/v0.27.2/build_docker_image) |
| Flocks 源码运行 | Python **3.12**（项目声明 `>=3.12,<3.13`）、`uv`、Node.js/npm；建议 Node.js **22.13+**，以满足当前前端依赖的要求。安装工具请见 [uv 官方安装页](https://docs.astral.sh/uv/getting-started/installation/)、[Node.js 官方下载页](https://nodejs.org/en/download/)。（`pyproject.toml:9`；`webui/package-lock.json:6440-6447`） |
| 端口和网络 | RAGFlow 的示例宿主 HTTP 端口为 **9380**，Flocks 开发后端/前端默认为 **8000/5173**。先确定端口和防火墙策略；不要向公网裸露数据库、对象存储或管理接口。（`scripts/dev.sh:16-27,230-250`；[RAGFlow 配置](https://ragflow.io/docs/v0.27.2/configurations)） |
| Apple Silicon / ARM | **不要假设官方 x86_64 镜像可直接运行。** RAGFlow 官方 ARM 构建指南要求自行构建适配镜像；其 macOS Compose 示例并非持续维护的部署保证。优先用受支持的 x86_64 Linux 主机或按该版本文档单独验证 ARM 构建。[官方 ARM 构建说明](https://ragflow.io/docs/v0.27.2/build_docker_image) |

下面的安装命令面向有权限执行 Docker 的部署者；**不要在已有生产实例直接覆盖其 `.env`、数据卷或模型配置**。Windows、Flocks Docker 镜像及现有安装包的步骤见仓库根目录 [安装说明](../../README.md)；如果所装发行版**不包含本分支 Core 直连改动**，不能直接照搬本文配置。

## 3. 安装 RAGFlow v0.27.2

在 RAGFlow 主机上，安装并启动 Docker Engine/Desktop 与 Compose v2，然后取得对应版本的源码和 Compose 模板：

```bash
git clone --depth 1 --branch v0.27.2 https://github.com/infiniflow/ragflow.git
cd ragflow/docker
# 先检查本版本的 .env / docker-compose.yml：镜像标签、宿主端口、持久卷和依赖。
# CPU 场景确保 RAGFLOW_IMAGE 与所检出版本匹配，例如 infiniflow/ragflow:v0.27.2。
docker compose -f docker-compose.yml up -d
docker compose -f docker-compose.yml ps
```

官方版本资料给出的默认 HTTP 映射是 `SVR_HTTP_PORT=9380` → 容器 `9380`；**以当前 `docker/.env` 和 Compose 的真实值为准**。同主机可用 `http://127.0.0.1:9380` 打开 RAGFlow；若改变了映射，则用实际宿主端口。不要把另一个机器/容器里的 `127.0.0.1` 误认为 RAGFlow 宿主地址。[RAGFlow v0.27.2 配置](https://ragflow.io/docs/v0.27.2/configurations) · [官方 Compose 来源](https://github.com/infiniflow/ragflow/blob/v0.27.2/docker/docker-compose.yml)

首次访问按 RAGFlow 页面完成注册/登录；**不要预设默认账号密码**。如需 GPU 或 Ollama 等本地模型，先按 v0.27.2 [官方快速入门](https://github.com/infiniflow/ragflow/blob/v0.27.2/docs/quickstart.mdx)与[本地模型说明](https://ragflow.io/docs/v0.27.2/deploy_local_llm)核对 `DEVICE`、镜像、驱动与容器可达性；不要凭另一版本教程替换 Compose 文件。

可做匿名健康检查（**只能说明进程可响应，不代表 API key 或模型可用**）：

```bash
curl --fail --silent --show-error http://127.0.0.1:9380/api/v1/system/healthz
```

### 3.1 在 RAGFlow 中配置 Embedding

1. 先准备**模型服务**（云供应商 API 凭据，或可从 RAGFlow 容器访问的自建服务）。在 RAGFlow 的 **用户设置 / Model providers（模型供应商）**中添加供应商实例，填写其凭据和服务地址，添加**类型为 Embedding** 的准确模型 ID，并按界面提供的方式验证可用性。这里填写的是**模型供应商密钥，不是 RAGFlow 给 Flocks 用的 API key**。[配置模型供应商](https://ragflow.io/docs/v0.27.2/llm_api_key_setup)
2. 在 **System Model Settings（系统模型设置）**中把该模型设为默认 **Embedding model**；需要生成式回答的 RAGFlow 功能还可能需要单独设置聊天模型，但 Flocks 的 `rag_retrieve` 本身只要求检索片段。版本界面如有不同，以 v0.27.2 文档和实际选项为准。[默认模型配置](https://ragflow.io/docs/v0.27.2/llm_api_key_setup) · [本地模型示例](https://ragflow.io/docs/v0.27.2/deploy_local_llm)
3. **在 Flocks 创建第一个知识集之前完成默认模型配置。** 当前 Flocks 建知识集只向 RAGFlow 发送 `name`/`description`，不传 Embedding 模型；RAGFlow 自己决定新建数据集采用的模型。创建后进 RAGFlow 界面检查该知识集实际绑定的模型是否正确，**不要仅凭“默认已设置”推断创建成功或旧数据自动重建**。（`flocks/knowledgebase/service.py:108-115`；[RAGFlow Indexer 文档](https://ragflow.io/docs/v0.27.2/configure_indexer_component)）
4. 同一会话若要同时检索多个知识集，先检查它们的 Embedding 模型是否兼容；RAGFlow 文档要求跨知识集检索使用相同的 Embedding 模型。Flocks 的 Session 选择器不会替你验证模型是否一致。已建索引更换模型或维度应按 RAGFlow 文档评估重建，不能认为改了全局默认值就会自动更新。（`flocks/knowledgebase/session_datasets.py:29-34`；[RAGFlow Indexer 文档](https://ragflow.io/docs/v0.27.2/configure_indexer_component)）

如果选用 Ollama：官方本地模型指南以 `bge-m3` 为 Embedding 示例；RAGFlow 在容器中访问宿主机 Ollama，Docker Desktop 可使用 `host.docker.internal`，Linux/分机应先验证实际容器网络地址。**模型服务的 `localhost` 是它自身所在网络命名空间**，不能直接照抄宿主回环地址。[Ollama/本地模型指南](https://ragflow.io/docs/v0.27.2/deploy_local_llm)

### 3.2 获取供 Flocks 调用的 RAGFlow API key

登录 RAGFlow，点击右上角头像 → 配置页面 → **API**，创建/复制 API key；使用**能访问目标知识集的相同租户/账号**的 key。这个 key 只在 Flocks 服务器端的 SecretManager 中保存，浏览器不得持有。[获取 API key](https://ragflow.io/docs/v0.27.2/acquire_ragflow_api_key) · [Bearer 鉴权](https://ragflow.io/docs/v0.27.2/http_api_reference)

> **兼容性门槛**：Core 不只调用 RAGFlow 标准的“向知识集上传文档”接口；当前实现还依赖文件仓库 `/api/v1/files`、下载、`/api/v1/files/link-to-datasets?mode=add`、文档解析和检索等路径。即使健康接口返回 200，也要验证该版本与该账号的**实际 API key**能访问这些操作；否则会得到 502 `upstream_*` 或数据结构错误。不要用浏览器登录 Cookie 代替 API key，也不要假定别的 RAGFlow 版本完全兼容。（`flocks/knowledgebase/ragflow.py:245-354`；[v0.27.2 HTTP API 文档](https://ragflow.io/docs/v0.27.2/http_api_reference)）

## 4. 获取、构建和运行 Flocks（从源码）

下面以包含本功能的 `feat/rag3` 为示例。若以后功能已合入正式发行版，改用对应的已验证 tag/分支；**仓库 `main` 的一键安装脚本或预构建 `latest` 镜像不保证包含该功能**。

```bash
git clone --depth 1 --branch feat/rag3 https://github.com/AgentFlocks/flocks.git
cd flocks
# 按官方安装说明准备 uv、Python 3.12、Node.js/npm。
uv python install 3.12
uv sync --locked
npm --prefix webui ci --include=dev
npm --prefix webui run build
```

`npm ci --include=dev` 必须在**当前 checkout 的** `webui/` 执行；`webui/package.json` 的 build 使用 `tsc && vite build`，若报 `tsc: command not found`，通常是该 checkout 没有装前端 devDependencies，而不是 RAGFlow 故障。`.venv` 也是按项目目录管理的：来自另一目录的 `VIRTUAL_ENV does not match ... .venv` 只是 uv 不使用旧虚拟环境的提示，可在新终端不激活旧 venv 后重试。（`webui/package.json:6-9,44-63`；`scripts/dev.sh:219-250`）

**开发启动**（脚本还会再执行一次前端 build，并启动 8000 后端 + 5173 Vite 前端）：

```bash
bash scripts/dev.sh
```

若只需本机运行已构建的页面和 Core 后端，可在源码目录运行 `uv run flocks serve --host 127.0.0.1 --port 8000`，打开 `http://127.0.0.1:8000/`；Core 会优先寻找 `webui/dist` 的 `index.html`。不要同时用 `dev.sh` 占用 8000。Flocks 的 `flocks start` 也可管理后台和 WebUI，它默认会构建前端，具体启动/状态/停止见 [项目根 README](../../README.md)。（`flocks/server/static_webui.py:38-69`；`flocks/cli/main.py:220-253,356-384`）

**第一次打开 Flocks 页面时**，若尚无管理员，按初始化页面设置本地管理员用户名和密码并登录；不要将 RAGFlow 登录账号当成 Flocks 管理员账号。要让 Agent 在对话或 Workflow 的 LLM 节点**生成文字回答**，还须按 Flocks 自己的模型配置流程接入可用的对话模型及其凭据，Agent 设置里可选择模型；这与 RAGFlow 的 Embedding 模型和 RAGFlow API key 分开。（`webui/src/pages/SetupAdmin/index.tsx:7-25,38-85`；`flocks/server/routes/auth.py:269-287`；`webui/src/pages/Agent/AgentSheet.tsx:142-152,187-205`）

> RAGFlow 和 Flocks **分别启动**，但不再启动旧中间服务。部署 Flocks 容器时，RAGFlow 的 `127.0.0.1` 指向**该容器自己**；使用它能访问的 RAGFlow 容器服务名、宿主地址或受控网关，并确认防火墙/TLS。源码模式同主机的示例地址为 `http://127.0.0.1:9380`，如果 RAGFlow 宿主端口映射到别的值，应同步调整下文 `base_url`。

## 5. 用 Flocks 自身配置与凭据机制连接 RAGFlow

配置字段在 `api_services.knowledgebase`，与 Flocks 的其他普通配置一起存；当前代码只认 `provider: "ragflow"`，`flocksrag` 尚未实现。`credential_id` 必须是**纯凭据条目 ID**，不是 API key 文本或 `{secret:...}` 引用；`base_url` 是 Core 实际可访问的 RAGFlow HTTP(S) 根地址（可带部署前缀），不是浏览器的 Flocks 地址，也不是旧服务 8767。（`flocks/knowledgebase/runtime.py:20-69`；`flocks/knowledgebase/client.py:27-65`）

在新装 Flocks 的根目录、**Core 启动前**运行下面的一次性示例。它在终端无回显地询问 API key，先拒绝覆盖已有 `knowledgebase` 配置或不同密钥，再通过现有 `SecretManager` 与 `ConfigWriter` 写入；**不会执行 RAGFlow 联机操作或重启服务**。分机部署时先把 `base_url` 换成 Core 可达的地址，别将容器内地址错写为 `127.0.0.1`。

```bash
uv run python - <<'PY'
import getpass

from flocks.config.config_writer import ConfigWriter
from flocks.security.secrets import get_secret_manager

secret_id = "knowledgebase_ragflow_api_key"
base_url = "http://127.0.0.1:9380"  # 按 RAGFlow 实际宿主映射/网络地址修改

if ConfigWriter.get_api_service_raw("knowledgebase") is not None:
    raise SystemExit("已存在知识库配置，请先检查，不要直接覆盖")
key = getpass.getpass("请输入 RAGFlow API key（不回显）：")
if len(key) < 32 or any(ord(char) < 33 or ord(char) > 126 for char in key):
    raise SystemExit("Key 不符合 Core 要求：至少 32 位非空白 ASCII 字符")
secrets = get_secret_manager()
if secrets.get(secret_id) is not None:
    raise SystemExit("目标凭据 ID 已存在，请先检查，不要覆盖")
secrets.set(secret_id, key)
ConfigWriter.set_api_service("knowledgebase", {
    "enabled": True,
    "provider": "ragflow",
    "base_url": base_url,
    "credential_id": secret_id,
})
print("已写入知识库连接；凭据值不在输出中。")
PY
```

这段命令会修改**运行机**的 Flocks 普通配置和凭据文件，请先确认使用正确账号与配置目录，避免在已有部署上盲目执行。SecretManager 默认凭据文件为 `~/.flocks/config/.secret.json`（或 `FLOCKS_CONFIG_DIR` 指定目录），以 **0600** 文件权限保存**明文 JSON**，不是加密保险库；文件、备份与日志都需访问控制，不能提交 Git。（`flocks/security/secrets.py:34-86,143-154`；`flocks/config/config_writer.py:756-777`）

若你改用手工编辑 Flocks 配置，等价的**普通字段**形状为：

```json
{
  "api_services": {
    "knowledgebase": {
      "enabled": true,
      "provider": "ragflow",
      "base_url": "http://127.0.0.1:9380",
      "credential_id": "knowledgebase_ragflow_api_key"
    }
  }
}
```

这只是该配置节点的示例，**不要用它覆盖整份现有 `flocks.json`**；key 必须先经 SecretManager 保存。`timeout_seconds` 默认 30 秒，`max_upload_bytes` 默认 32 MiB，后者也限制引擎响应体。Flocks 会合并用户文件、自定义配置路径及内联配置；若你通过 `FLOCKS_CONFIG`/`FLOCKS_CONFIG_CONTENT` 注入更高优先级配置，先确认没有覆盖新条目。（`flocks/knowledgebase/runtime.py:46-65`；`flocks/config/config.py:1506-1547`）

**配置只在 Core 启动时读取一次。** 如果先启动 Flocks 再写配置，须由部署者重启 **Flocks Core**（开发脚本可停止后重新运行）；界面的“重新检查连接”不会热加载。不要为此重启 RAGFlow、重新上传文件或改 Session 的 `dataset_ids`。（`flocks/knowledgebase/runtime.py:20-24,68-77`；`flocks/server/app.py:496-519`）

### 5.1 区分本地“已配置”和远端可用

1. 打开 Flocks UI，登录后进入 **Workspace → 知识库**。如果出现“知识库尚未配置”，先核对 `enabled/provider/base_url/credential_id`、凭据规则以及 **Core 是否在写配置后重启**；`flocksrag` 不能作为 provider。状态只取决于 Core 是否创建了 client，不做远端探活。（`flocks/server/routes/knowledgebase.py:135-138`）
2. **单独验证 RAGFlow API**：在完成配置的目标机器上，可用下方代码经同一 Core 适配层读取一页数据集；只输出数量或脱敏错误，不显示 API key 或数据集内容。它不写入或删除数据，但会访问 RAGFlow。运行时应使用与 Core 相同的 Flocks 配置目录/账号：

```bash
uv run python - <<'PY'
import asyncio

from flocks.config.config import Config
from flocks.knowledgebase.client import Connection, KnowledgebaseClient
from flocks.knowledgebase.errors import KnowledgebaseError
from flocks.security.secrets import get_secret_manager

async def main():
    cfg = await Config.get()
    entry = getattr(cfg, "api_services", {}).get("knowledgebase")
    if not isinstance(entry, dict) or not entry.get("enabled") or entry.get("provider") != "ragflow":
        raise SystemExit("知识库连接尚未正确配置")
    token = get_secret_manager().get(entry["credential_id"])
    if token is None:
        raise SystemExit("指定凭据不存在")
    client = KnowledgebaseClient(Connection(entry["base_url"], token))
    try:
        page = await client.datasets(page=1, page_size=1)
        print("RAGFlow 数据集 API 可用；total =", page["total"])
    except KnowledgebaseError as exc:
        print("检索失败：HTTP", exc.status, "code =", exc.code)
        raise SystemExit(1) from None
    finally:
        await client.close()

asyncio.run(main())
PY
```

这只验证“列知识集”，不代表文件上传/关联/解析/检索已通过；首次安装仍需完成第 6–7 节端到端试用。如果“就绪”为 true 但请求失败，重点检查 Core 所在网络可否访问 `base_url`、RAGFlow API key 的租户权限、版本对应路径与 RAGFlow 响应结构。**不要打印/粘贴未经脱敏的请求头、错误 body 或配置文件。**

## 6. Flocks UI：上传文件并创建知识集

登录同一 Flocks 实例，按以下顺序操作；**源文件 ID、知识集 ID、知识集内文档 ID 是三种不同 ID**。UI 文案“知识集”与 API 的 `dataset` 指同一对象。（`flocks/knowledgebase/service.py:18-52,125-135`）

1. **上传原件**：Workspace → **知识库 → 知识库文件** → **上传文件**，选一个本地文件，等待提示。当前页面是平铺文件列表，可预览/下载/删除；**当前页面没有文件夹创建、移动或批量上传控件**，不要照未启用的翻译文案寻找这些按钮。上传只创建文件仓库条目，**不会建知识集、不会自动解析**。（`webui/src/pages/Workspace/knowledge/FilesPage.tsx:10-72`；`flocks/knowledgebase/service.py:93-98`）
2. **创建知识集**：切换到 **知识集**，点“创建知识集”，输入名称和可选说明。创建成功后打开其详情。该表单**不选择 Embedding 模型**：最终使用 RAGFlow 创建数据集时采用的模型，所以务必先完成 §3.1，并在 RAGFlow 中核实新建知识集实际模型。（`webui/src/pages/Workspace/knowledge/DatasetsPage.tsx:10-65`；`flocks/knowledgebase/service.py:111-115`）
3. **关联文件**：在知识集详情点 **关联已有文件**，从知识库文件中选择刚上传的原件，确认关联。该操作只确认“已提交关联”，**不等于已获得文档 ID 或完成解析**；稍后重新进入该知识集或刷新加载，等文档列表中出现新文档。原件可以供多个知识集引用，但关联条目并非原文件自身。（`webui/src/pages/Workspace/knowledge/DatasetsPage.tsx:113-169`；`flocks/knowledgebase/service.py:125-127`；`flocks/knowledgebase/ragflow.py:319-328`）
4. **显式解析**：在该文档所在行点 **开始解析**。操作返回确认后，RAGFlow 才异步推进解析/Embedding/索引。当前 UI 显示的是文档 `status` 字符串，**没有自动轮询解析进度**；需要重进详情或手动重新加载，直到 RAGFlow 中看到可检索的完成状态。不要把“已提交解析”当作内容立刻可检索。（`webui/src/pages/Workspace/knowledge/DatasetsPage.tsx:76-94,129-145`；`flocks/knowledgebase/service.py:41-52,133-135`）
5. **移出与删除**：移出文档只发当前知识集的文档删除请求，不应等同删除文件仓库原件；删除整个知识集后 Core 会尽力从 Session 绑定中移除对应 ID，但**不保证所有历史/归档 Session 同步清理**。对删除原文件可能影响的关联，先按实际 RAGFlow 行为核实，不凭前端提示推断完整级联。（`flocks/knowledgebase/service.py:100-101,117-135`；`flocks/server/routes/knowledgebase.py:223-229`；`flocks/knowledgebase/session_datasets.py:52-73`）

**输入与容量边界**：默认文件上限为 32 MiB，但 BFF 还以 multipart **整个 Content-Length** 和该文件上限比较，紧贴 32 MiB 的文件可能因边界开销被拒；请留余量，不要据此认定 RAGFlow 坏了。文件名不能包含路径分隔符。知识集名 1–255 字符且不能只有空白；说明最长 4000 字符。当前后端拒绝名称和说明中的控制字符（包括说明里的换行），虽然界面是多行输入框，也请先使用单行说明。（`flocks/server/routes/knowledgebase.py:99-175`；`flocks/knowledgebase/schemas.py:35-54`）

## 7. 在对话里检索已绑定的知识集

1. 打开**要提问的同一会话**，在 Session 的 **Context（上下文）→ 知识集** 点“选择知识集”；勾选一个或多个已解析的知识集，点 **应用知识集** 保存。只改变该 Session 的 `knowledgebase.dataset_ids`，**不会把数据集复制到会话，也不证明文档已经解析完成**。可保存空列表以清空；最多 50 个唯一的合法 ID。不同 Session 的绑定相互独立。（`webui/src/pages/Session/SessionDatasetSection.tsx:55-120`；`flocks/knowledgebase/session_datasets.py:13-49,76-87`）
2. 使用有权调用 `rag_retrieve` 的 Agent 提问。内置 **Rex** 的空 `tools` 清单会解析为全部启用的内置工具；其他 Agent 不应假定自动获得此工具，需在其**实际可编辑的工具列表及权限规则**中已有授权。内置 Agent 的设置界面只保存模型/温度，不提供“改 Rex 工具列表”的保存路径；不应为用例去直接编辑运行中的受保护配置。（`flocks/agent/agents/rex/agent.yaml:1-12`；`flocks/agent/toolset.py:31-46,100-119`；`webui/src/pages/Agent/AgentSheet.tsx:149-152,194-206`）
3. 可在对话中明确提出：

   > 请在当前会话的知识集中使用 `rag_retrieve` 搜索“这里填写你的问题”，只根据返回片段回答；没有相关片段就说明找不到，并写明能确认的 `document_name` 或 `document_id`。不要编造来源。

   Agent 是否真的调用工具，取决于工具是否启用、Agent 工具集合、权限回调、模型的工具选择，以及当前会话是否已有绑定；**不是发送一条聊天消息就自动执行 RAG**。工具可能要求运行时授权；即使声明 `requires_confirmation=False`，handler 仍调用 `ctx.ask`。（`flocks/tool/knowledgebase.py:20-93`）
4. `dataset` 参数省略或传空数组 `[]`，表示检索该 Session **全部**已绑定知识集；传非空数组只能是绑定 ID 的子集，越界报 `dataset_not_bound`；**Session 根本没绑定任何知识集时返回空 chunks，不会搜索全库**。`top_k` 默认 5，阈值/权重默认 0.2/0.3；这些是传给 RAGFlow 的检索参数，不是生成答案的设置。（`flocks/knowledgebase/retrieval.py:12-44`；`flocks/knowledgebase/schemas.py:75-92`）
5. `rag_retrieve` 只返回检索片段（如 `content`、`document_name`、`dataset_id`、`document_id`、`similarity`），**不自带强制引用格式，也不保证自动回答或引用每个来源**。需要可核查的引用时，在提示词中要求列出来源，并核对实际片段。（`flocks/knowledgebase/service.py:55-66,137-155`）

## 8. Workflow 引用：支持通用工具节点，但需要会话上下文

Workflow 的通用 `type: "tool"` 节点可以指定 `tool_name: "rag_retrieve"`，**并没有单独的“知识集节点”或 Workflow 级 `dataset_ids` 绑定界面**。Workflow 需要原会话、工具和权限上下文；独立从无绑定的新临时 Session 启动不能自动继承聊天会话的选择。部分 sandbox 执行路径不会把该上下文传给工具节点，不能把“Workflow 有工具节点”理解为所有执行路径已支持知识库检索。（`flocks/workflow/models.py:12-19,45-54,82-84`；`flocks/workflow/tool_context.py:58-85`；`flocks/workflow/engine.py:911-939`）

**可供受控环境验证的最小用法**：在§7已绑定知识集的同一会话，用有 `run_workflow` **和** `rag_retrieve` 权限的 Agent 调用 `run_workflow`；其 `workflow` 参数传内联对象（非文件路径），例如：

```json
{
  "workflow": {
    "name": "answer_from_selected_knowledge",
    "start": "retrieve",
    "nodes": [
      {
        "id": "retrieve",
        "type": "tool",
        "tool_name": "rag_retrieve",
        "tool_args": { "top_k": 5 },
        "output_key": "evidence"
      },
      {
        "id": "answer",
        "type": "llm",
        "prompt": "问题：{{ question }}\n检索片段：{{ evidence }}\n只根据检索片段回答；找不到依据就明确说找不到，尽量注明可确认的文档名。",
        "output_key": "answer"
      }
    ],
    "edges": [
      { "from": "retrieve", "to": "answer", "mapping": { "question": "keywords", "evidence": "evidence" } }
    ]
  },
  "inputs": { "keywords": "这里填写要检索的问题" },
  "ensure_requirements": false
}
```

这是**源码推导的条件示例，不是已通过的 Workflow 端到端用例**：`run_workflow` 接受内联对象和 `inputs`，工具节点合并 `tool_args` 与 `inputs`（同名时 inputs 覆盖），边映射把原始检索结果给后续 LLM 节点；该 LLM 节点还需 Flocks 自己的对话模型配置。（`flocks/tool/task/run_workflow.py:377-411,445-463,482-496,728-742`；`flocks/workflow/engine.py:744-753,911-974`；`flocks/workflow/edge_resolver.py:27-43,68-81`）

**使用前逐项确认**：

- 请使用**已绑定知识集的会话中发起的** `run_workflow`，而不是只从文件/定时任务/UI另启的 Workflow；聊天工具会构造保留 `session_id`、Agent 和权限回调的嵌套上下文，但是否保留当前认证身份仍需实测。`run_workflow` 本身要求工具授权和确认。（`flocks/tool/task/run_workflow.py:174-183,377-383,728-742`）
- runner 默认在未开启 sandbox 时选择 host；当使用 sandbox runtime，通用工具节点可能回退到 `session_id="workflow"`，丢失原会话绑定。**不要为了让示例成功而擅自关闭生产安全沙箱**；先在隔离环境核对运行模式、权限和跨用户访问。（`flocks/workflow/runner.py:113-127,447-492`；`flocks/workflow/engine.py:915-928`；`flocks/workflow/tools_adapter.py:50-80`）
- Workflow 默认 `history_mode="summary"`，最终输出/历史可能只含片段结构和数量，**不保证将全部原文 chunks 再返回给对话 Agent**；示例用显式 edge `mapping` 把检索节点的原始输出送到后续 LLM 节点。实际回答仍应核对来源与权限。（`flocks/workflow/runner.py:320-335`；`flocks/workflow/engine.py:526-545,744-753`）
- 文件 Workflow 路径可能注入额外 `_workflow_path` 等输入，直接把所有输入交给 `rag_retrieve` 时可能触发未知参数错误；此处使用**内联对象**规避该特定问题。对于独立 Workflow、sandbox、多 Agent 场景，未完成隔离环境的权限与检索测试前，不应承诺“运行后自动引用知识集”。（`flocks/workflow/runner.py:274-282`；`flocks/workflow/engine.py:928-939`）

如果只是需要稳定地在对话中引用文档，**优先使用§7的同会话 `rag_retrieve`**；Workflow 是需验证上下文与输出链路的进阶用法。

## 9. 常见问题与验收清单

| 现象 | 优先核对 |
|---|---|
| Flocks 显示“知识库尚未配置” | `api_services.knowledgebase` 是否确实生效、`enabled=true`、`provider=ragflow`、纯 `credential_id` 是否存在且满足长度要求、是否在写入后重新启动 Core。`flocksrag` 目前不能用。浏览器“重新检查连接”只重新请求状态，不热加载配置。（`flocks/knowledgebase/runtime.py:20-69`） |
| 显示就绪，但文件/知识集请求失败 | 容器与宿主的 URL 是否指对网络、端口、防火墙/TLS；带同一 API key 的数据集列表能否返回 `code=0`；当前 RAGFlow 是否兼容所需 Files API。就绪不是健康证明。（`flocks/server/routes/knowledgebase.py:135-138`；`flocks/knowledgebase/ragflow.py:147-182`） |
| `npm run build` 找不到 `tsc` | 在当前 checkout 的 `webui/` 执行 `npm ci --include=dev`；`tsc` 属于 devDependencies。不要只复制 Python `.venv`。（`webui/package.json:6-9,44-63`） |
| 上传被 413 拒绝 | 默认 32 MiB 文件预算；multipart 整体声明长度也会与文件限额比较，接近阈值需留余量。确认文件名合法。（`flocks/server/routes/knowledgebase.py:95-175`） |
| 文件上传了却检索不到 | 上传、关联知识集、等待文档出现、显式开始解析、待索引完成、绑定到**本会话**，六步缺一不可；排查 Embedding key/model 可用性及检索阈值，不要反复上传同一文件。（`flocks/knowledgebase/service.py:93-155`；`flocks/knowledgebase/retrieval.py:23-44`） |
| Workflow 查不到绑定或只显示摘要 | 检查来源会话、Agent 的工具权限和执行 runtime；独立临时 Session 没有原会话绑定，sandbox 工具节点可能丢上下文；summary 模式不保证原 chunks 都回传。先用同会话直接工具调用验证。（§8） |
| 要升级 RAGFlow/换模型 | 本指南仅面向 v0.27.2 接口；升级前核对 Files/link/parse/retrieval API 和数据备份。更改 Embedding 模型可能需要重建索引，不能简单修改默认值后期待旧知识集自动更新。（§3.1） |

**最小验收**（在新部署的测试知识集上）：RAGFlow 健康响应 → 模型供应商 Embedding 可用并设为默认 → 获取 RAGFlow API key → Flocks 构建/启动并完成配置 → Flocks 适配层带 key 列表成功 → 上传小文件 → 创建知识集并在 RAGFlow 验证其模型 → 关联并解析至完成 → 在同一会话绑定知识集 → `rag_retrieve` 返回来自该知识集的片段。完成这条链路后，再考虑§8的 Workflow 验证。

## 资料与代码依据

- [RAGFlow v0.27.2 配置/镜像与端口](https://ragflow.io/docs/v0.27.2/configurations)、[官方 Docker Compose 源文件](https://github.com/infiniflow/ragflow/blob/v0.27.2/docker/docker-compose.yml)、[官方快速入门](https://github.com/infiniflow/ragflow/blob/v0.27.2/docs/quickstart.mdx)。
- [RAGFlow v0.27.2 模型供应商配置](https://ragflow.io/docs/v0.27.2/llm_api_key_setup)、[本地模型部署](https://ragflow.io/docs/v0.27.2/deploy_local_llm)、[Indexer/Embedding 选择](https://ragflow.io/docs/v0.27.2/configure_indexer_component)。
- [RAGFlow v0.27.2 获取 API key](https://ragflow.io/docs/v0.27.2/acquire_ragflow_api_key)、[HTTP API 与文件关联](https://ragflow.io/docs/v0.27.2/http_api_reference)、[ARM 构建限制](https://ragflow.io/docs/v0.27.2/build_docker_image)。
- Flocks 真实实现：`flocks/knowledgebase/{runtime,client,ragflow,service,schemas,session_datasets,retrieval}.py`、`flocks/server/routes/knowledgebase.py`、`flocks/tool/knowledgebase.py`、`webui/src/pages/Workspace/knowledge/{FilesPage,DatasetsPage}.tsx`、`webui/src/pages/Session/SessionDatasetSection.tsx`、`flocks/workflow/{models,runner,engine,edge_resolver,tools_adapter}.py`。本文的 `file:line` 位置以仓库 `feat/rag3` 的 `6f500125` 为依据；若代码升级，请以新版本为准。
