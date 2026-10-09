# Flocks 知识库使用指南

[English](README.md) · 简体中文

Flocks中知识库功能直接集成了RAGFlow项目。本文说明如何在新机器上安装 Flocks 与 RAGFlow，完成知识库连接，并在对话或 Workflow 中使用知识集。**知识集**是Flocks上的名称，在 RAGFlow 中对应 Dataset。

## 1. 启动 RAGFlow，配置 Embedding 模型

先按 [RAGFlow 官方部署指南](https://ragflow.io/docs)安装 Docker 和 RAGFlow。使用官方提供的 Compose 文件启动：

```bash
git clone https://github.com/infiniflow/ragflow.git
cd ragflow/docker
docker compose -f docker-compose.yml up -d
```

打开 RAGFlow 页面并登录。默认地址通常为 `http://127.0.0.1:9380`，如果你修改了 Docker 端口映射，请使用实际地址。

在 RAGFlow 中依次完成：

1. 进入 **用户设置 → Model providers（模型供应商）**，添加提供 Embedding 服务的供应商，填写该供应商的 API Key 和服务地址，并添加 **Embedding 类型**的模型。
2. 按页面操作验证模型可用，在 **System Model Settings**（系统模型设置）中将其设为默认 Embedding 模型。
3. 创建后在 RAGFlow 检查该知识集实际使用的模型；要同时检索多个知识集，建议使用相同的 Embedding 模型。
4. 在 RAGFlow 页面右上角头像的 **API** 页面创建 **RAGFlow API Key**。它供 Flocks 连接 RAGFlow，**不是**上面填写的模型供应商 API Key。

如果 Embedding 服务部署在另一台机器或宿主机，请确认 **RAGFlow 容器本身**能访问该模型地址；容器中的 `127.0.0.1` 不等于宿主机。不同 RAGFlow 发行包的界面和文件接口可能有所不同，安装时以[官方文档](https://ragflow.io/docs)和实际部署为准。

## 2. 安装并启动 Flocks

按 [Flocks 安装说明](../../README.md)运行安装脚本；macOS/Linux 可使用：

```bash
curl -fsSL https://raw.githubusercontent.com/AgentFlocks/flocks/main/install.sh | bash
```

按安装提示进入 Flocks 的安装目录，在**新终端**运行：

```bash
flocks start
```

安装脚本和启动命令会处理 Flocks 所需的构建与启动。打开启动时显示的 WebUI 地址；首次使用按页面提示创建管理员账号并登录。

## 3. 在 Flocks 连接 RAGFlow

进入 **Workspace → 知识库 → 连接设置**（未配置提示中也有入口）。右侧抽屉只有三项：

- **知识库引擎**：目前只提供 **RAGFlow**。
- **RAGFlow 地址**：填写 **Flocks 后端**能访问的 RAGFlow 地址，例如同机安装时的 `http://127.0.0.1:9380`。如果 Flocks 在容器中或两者分机部署，不能照抄此回环地址，应填写后端实际能访问的地址。
- **API Key**：填写在 RAGFlow 的 **API** 页面创建的 Key。已有凭据时，框内显示 **“••••••••（已配置）”**，不回显真实 Key；不修改即可沿用，输入新值即可更换。修改地址（包括路径）时需要重新输入 Key。

点击 **保存**：Flocks 会先用这组地址和 Key **只读测试知识集列表与文件列表**。保存成功后**立即生效**，页面自动刷新连接状态和列表。验证或保存失败时，**保留原连接继续使用**。Key 存在 Flocks 的凭据存储中，真实值不会回传浏览器。

已经使用旧连接的请求会继续完成，再释放旧连接。成功保存只验证了列表接口，仍需完成一次文件上传与检索，才能确认整条链路可用。

## 4. 上传文件，创建并解析知识集

1. **上传**：在 **Workspace → 知识库 → 知识库文件**中点击“上传文件”，选择本地文件。上传只是存储原件，**不会自动解析**。文件接近默认 32 MB 上限时，multipart 开销也可能导致上传失败，请适当留出余量。
2. **创建知识集**：切到“知识集”，点击“创建知识集”，填写名称和说明。此处不选择 Embedding 模型，创建前请先在 RAGFlow 配置默认模型。
3. **关联文件**：打开刚创建的知识集，点击“关联已有文件”并选择原件。等待文档出现在知识集的关联文件列表中；文件 ID 与关联后的文档 ID 不是一回事。
4. **开始解析**：在文档行点击“开始解析”。RAGFlow 会处理文档、生成向量索引。当前界面**不会自动轮询状态**，可重新进入知识集详情查看文档状态，确认解析完成后再检索。

## 5. 在对话中引用知识集

打开要提问的会话，在 **Session → Context（上下文）→ 知识集**中选择一个或多个知识集，点击“应用知识集”。此绑定只属于当前会话；清空选择后，检索不会自动搜索所有知识集。

使用有权调用 `rag_retrieve` 的 Agent（例如启用了该内置工具的 Rex），可以这样提问：

> 请先使用知识集，再根据找到的片段回答，并注明能够确认的文档名称；没有相关内容就说找不到。

工具是否被调用取决于 Agent 的工具权限和模型选择；它返回的是检索片段，不保证自动引用或自动生成答案。使用多个知识集时，若 RAGFlow 拒绝检索，请先检查它们的 Embedding 模型是否兼容。

## 6. 在 Workflow 中引用

Workflow 可以用**工具节点**调用 `rag_retrieve`，传入 `dataset`（知识集），`keywords`（问题）和可选 `top_k`，再把检索结果传给后续回答节点。推荐从**已经绑定知识集的同一会话**，让具有 `run_workflow` 和 `rag_retrieve` 权限的 Agent 发起 Workflow。

> Workflow 没有独立的“选择知识集”节点，需要在调用 rag_retrieve 工具时加是 dataset 入参。

## 遇到问题时

| 现象 | 检查方法 |
|---|---|
| “知识库尚未配置” | 打开连接设置并保存有效连接，成功后立即生效；页面状态未更新时可点击“重新检查连接”。 |
| 保存提示连接测试失败 | 从 Flocks 后端的网络位置检查 RAGFlow 地址；确认 RAGFlow API Key 与所登录租户一致，且 RAGFlow 的知识集、文件列表接口可用。匿名健康页可访问不代表 Key 有权限。 |
| 文件已上传却查不到 | 依次确认：已关联到知识集 → 文档出现 → 已开始解析并完成 → 已绑定到当前会话 → Agent 有 `rag_retrieve` 权限。 |
| 上传被拒绝 | 检查文件名和大小；超过上限的上传不会成功，接近上限也需考虑请求封装开销。 |

更多安装、服务管理与模型部署细节分别参考 [Flocks 安装说明](../../README.md)及 [RAGFlow 官方文档](https://ragflow.io/docs)。
