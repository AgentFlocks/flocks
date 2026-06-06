# 可插拔 Agent Loop 设计文档

> 状态：**草稿 / RFC（v3，二轮代码审阅后定稿）**  
> 作者：AI 辅助生成  
> 日期：2026-06-06  
> 分支：`feat/pluggable-agent-loop`

> **v3 修订要点**（二轮审阅，核对 `SessionLoop.run()` 生命周期）：
> 1. 分派点由"session 加载后（L290）"再**下沉到 `_run_loop()` 调用处（L359）**——否则 raptor 路径会绕过 `_active_loops` 注册、busy/idle 状态、中断、orphan/finally 清理，导致**中断失效与重复执行**。
> 2. 引擎接口由 `run(session_id, provider_id, model_id, agent_name, callbacks)` 改为 **`run(ctx: LoopContext, callbacks)`**——引擎接收已就绪的 `LoopContext`（含解析好的 model、`session_ctx`、`abort_event`），无需重新加载/解析。
> 3. §4.3 递归纠结随之消失：native 直接走 `cls._run_loop(ctx, ...)`，registry 只装非原生引擎。
>
> **v2 修订要点**（一轮审阅，基于全仓库代码核对）：
> 1. 注入点由"修改 14 处调用站"改为**在 `SessionLoop.run()` 内部单点分派**。
> 2. 引擎解析只依赖已加载的 `session`，不依赖 `PromptRequest`（绝大多数调用站没有 request）。
> 3. P2 集成方式由"进程内 import"改为**子进程 / RPC**（hermes-agent 是扁平模块包，无法安全进程内导入）。
> 4. 补充子 agent 引擎继承策略（默认 `native`）。
> 5. `loop_engine` 先存 `SessionInfo.metadata`，免 schema 迁移。

---

## 1. 背景与动机

### 1.1 现状

Flocks 目前只有一套 agent loop 实现：

```
SessionLoop._run_loop()
  └── SessionRunner._process_step()
        └── SessionRunner._call_llm()
              └── StreamProcessor  (流式 + 串行工具执行)
```

整条链路 async 原生、与 SSE/SQLite 深度集成，适合多会话并发的 server 场景。

### 1.2 hermes-agent 的高价值能力（Raptor 引擎来源）

通过对 `open_source/hermes-agent` 的深度分析，发现其 agent loop（`agent/conversation_loop.py`）在**单任务执行质量**上有以下优势：

| 能力 | Flocks 现状 | Raptor（移植自 hermes-agent） |
|---|---|---|
| 工具并行执行 | ❌ 串行 await | ✅ ThreadPool 8 并发，智能判定路径冲突后降级串行 |
| 动态工具加载 | ❌ 全量 schema 传模型 | ✅ `tool_search`/`tool_describe`/`tool_call` 三桥接，按需折叠省 token |
| Checkpoint / 回滚 | 部分（SessionRevert） | ✅ 写文件/破坏性命令前自动快照 |
| 中断 + 软注入 | abort（cancel task） | ✅ `interrupt()` 硬中断 + `steer()` 注入不中断 |
| 多层 API 重试 | 空响应 + API 错误 | ✅ 429 / 压缩 / fallback provider / invalid JSON 四层恢复 |
| Subagent 并行 | delegate_task（串行） | ✅ batch 并发 + 中断传播 |

### 1.3 目标

将 hermes-agent 的 agent loop 以 **Raptor 引擎**的形式**可插拔**地集成到 Flocks，让用户在 WebUI 中选择使用哪个引擎，而无需改动会话管理、SSE 推送、工具注册、压缩等基础设施。

---

## 2. 核心设计原则

1. **最小侵入**：分派只在 `SessionLoop.run()` 内部单点完成，14 处调用站零改动。  
2. **向后兼容**：默认引擎 `native` 走原 inline 路径，不传 `loop_engine` 行为与现在逐字节一致。  
3. **SSE 透明**：引擎内部事件仍经 `LoopCallbacks.event_publish_callback` 推送，WebUI 无感。  
4. **分阶段实施**：P0 建框架（零风险），P1 接 WebUI，P2 再接 Raptor 实现。

---

## 3. 整体架构

### 3.1 关键决策：在 `_run_loop()` 调用处单点分派

全仓库共有 **14 处** `SessionLoop.run(...)` 调用（HTTP、CLI、channel、task、delegate_task、subagent、activity_forwarder 等），其中绝大多数只传 `session_id` + `callbacks`，**没有 `PromptRequest`**。因此引擎分派**不能**放在调用站。

更关键的是：分派也**不能**放在 `SessionLoop.run()` 顶部（session 加载后立即 return engine），否则会**绕过 `run()` 内部的全部生命周期管理**——`_active_loops` 注册、busy/idle 状态、orphan 清理、`finally` 清理、错误处理都在 `_run_loop()` 调用的前后。绕过它们会导致 `is_running` 恒 False（**重复执行**）、`abort()` 失效（**中断失效**）。

因此分派点必须**下沉到 `_run_loop()` 调用处**：共享脚手架全部复用，只替换"循环体"这一行。引擎接收**已就绪的 `LoopContext`**（含解析好的 model、`session_ctx`、`abort_event`）。

```
14 处调用站（HTTP / CLI / channel / task / subagent ...）
        │   全部不变，只调 SessionLoop.run(session_id, ...)
        ▼
┌──────────────────────────────────────────────────────────┐
│  SessionLoop.run()        （以下脚手架对所有引擎共享）     │
│   is_running 守卫 → Session.get_by_id → _resolve_model    │
│   → 构建 LoopContext(ctx) → _active_loops[sid]=ctx        │
│   → SessionStatus.busy → orphan 清理                      │
│   ┌──── try ────────────────────────────────────────────┐ │
│   │ engine_id = _resolve_loop_engine(ctx.session)        │ │  ← 单点分派
│   │ if engine_id != "native":                            │ │
│   │     result = await Registry.get(engine_id).run(ctx)  │ │
│   │ else:                                                │ │
│   │     result = await cls._run_loop(ctx, callbacks)     │ │  ← 原逻辑零变化
│   └──── finally: del _active_loops / idle / touch ───────┘ │
└───────────────────────────┬──────────────────────────────┘
                            │ (engine_id != "native")
                            ▼
                ┌───────────────────────┐
                │   AgentLoopEngine     │  run(ctx, callbacks)
                └──────────┬────────────┘
                           ▼
            ┌──────────────────────────────────────┐
            │           RaptorEngine               │
            │   (P2，子进程 / RPC 适配器)          │
            │   监听 ctx.abort_event → 杀子进程     │
            │   MessageBridge  ToolBridge          │
            │   StreamBridge   subprocess(JSON-RPC)│
            │   └─> hermes-agent run_conversation  │
            └──────────────────────────────────────┘
                            │
            ┌───────────────┼───────────────┐
            ▼               ▼               ▼
     Flocks SSE       Flocks SQLite    Flocks ToolRegistry
     (publish_event)  (Message/Parts)  (ToolRegistry.execute)
```

> 因为分派在 `_run_loop()` 调用处、native 直接调 `cls._run_loop(ctx, ...)`，registry 只装非原生引擎，**不存在 `run()` 自调用**，递归问题天然不存在。raptor 通过 `ctx.abort_event` 接入 Flocks 既有的 `abort()` / `abort_children()` 中断链路。

---

## 4. 详细接口设计

### 4.1 `AgentLoopEngine` 协议（`engine/base.py`）

引擎接收**已就绪的 `LoopContext`**（由 `SessionLoop.run()` 在分派前构建好），而非裸 `session_id`——这样引擎无需重新加载 session、重新解析 model，并能直接访问 `ctx.abort_event`（中断）、`ctx.session_ctx`（读写消息）、`ctx.provider_id/model_id/agent_name`。

```python
from typing import Any, Protocol, runtime_checkable
# from flocks.session.session_loop import LoopContext, LoopCallbacks, LoopResult

@runtime_checkable
class AgentLoopEngine(Protocol):
    id: str           # 机器标识，如 "raptor"（native 不进 registry）
    display_name: str # WebUI 下拉显示名
    description: str  # WebUI tooltip

    async def run(
        self,
        ctx: Any,                         # LoopContext（含 session/model/session_ctx/abort_event）
        callbacks: Any = None,            # LoopCallbacks
    ) -> Any:                             # LoopResult
        ...
```

> `LoopContext` / `LoopResult` / `LoopCallbacks` 复用 `session_loop.py` 的现有定义；引擎须返回格式相同的 `LoopResult`，由 `SessionLoop.run()` 的 `finally` 统一收尾。
>
> 接口与 `SessionLoop._run_loop(ctx, callbacks)` 的签名**完全对齐**——native 路径就是直接调 `_run_loop`，非原生引擎是 `_run_loop` 的可替换实现。

### 4.2 `LoopEngineRegistry`（`engine/registry.py`）

> registry 只装**非原生**引擎（raptor 等）。`native` 不在其中——它由 `SessionLoop.run()` 走 inline `_run_loop`。

```python
class LoopEngineRegistry:
    _engines: Dict[str, AgentLoopEngine] = {}   # 仅非原生引擎

    @classmethod
    def register(cls, engine: AgentLoopEngine) -> None: ...

    @classmethod
    def get(cls, engine_id: str) -> AgentLoopEngine:
        # 仅在 engine_id != "native" 时调用；未知 id 抛错（调用方已先判过 native）
        ...

    @classmethod
    def ids(cls) -> set[str]:
        # 已注册的非原生引擎 id 集合，供 _resolve_loop_engine 校验
        ...

    @classmethod
    def list(cls) -> List[Dict[str, str]]:
        # 非原生引擎元数据；/api/loop-engines 会在最前面拼上 NATIVE_ENGINE_META
        ...
```

### 4.3 `native` 引擎 = 原 `_run_loop`（无需实体类，无递归）

由于分派点就在 `_run_loop()` 调用处，`native` 路径直接调用 `cls._run_loop(ctx, callbacks)`——**它本身就是原生引擎**，无需任何包装类，也**不存在 `run()` 自调用的递归问题**。

`native` **不注册进 registry**（registry 只装 raptor 等非原生引擎）。`/api/loop-engines` 列举时，把 `native` 作为**静态首项**拼到 registry 列表前即可：

```python
# engine/native.py —— 仅元数据，供 /api/loop-engines 列举
NATIVE_ENGINE_META = {
    "id": "native",
    "name": "Flocks Native",
    "description": "Flocks 原生 async loop，多会话并发优先",
}
```

```python
# SessionLoop.run() 内，分派处
engine_id = cls._resolve_loop_engine(ctx.session)
if engine_id != "native":
    result = await LoopEngineRegistry.get(engine_id).run(ctx, callbacks or LoopCallbacks())
else:
    result = await cls._run_loop(ctx, callbacks or LoopCallbacks())   # ← 原逻辑，零变化
```

### 4.4 `RaptorEngine`（`engine/raptor/engine.py`，P2 实现）

RaptorEngine 适配 hermes-agent 的 `run_conversation`（`run_agent.py` / `agent/conversation_loop.py`）。

> ⚠️ **进程内 import 不可行**。核对 `open_source/hermes-agent/setup.py` 发现它**只打包 skills，未声明任何 Python package**，整个项目靠扁平模块运行（`import run_agent`、`import model_tools`、`import tools.registry`）。从 flocks 包内直接 import 会：
> 1. 与 flocks 自身模块命名空间冲突（顶层 `tools/`、`agent/` 等重名风险）；
> 2. hermes-agent 大量依赖进程全局状态（`get_hermes_home()`、`_last_resolved_tool_names`），与 Flocks 多会话并发的事件循环不兼容；
> 3. hermes-agent 的 `AGENTS.md` 明令插件不得改 core，且严格依赖 prompt caching（mid-conversation 不能改 toolset/system prompt）。
>
> **因此 P2 采用子进程 + JSON-RPC**（对齐 hermes-agent 自己 TUI 的 `tui_gateway` 与 `codex_app_server` 模式），而非 `asyncio.to_thread` 进程内调用。

#### 进程模型

```
Flocks (asyncio)                       Raptor 子进程 (同步 + 线程, 独立 venv/sys.path)
  RaptorEngine.run(ctx, callbacks)       hermes-agent run_conversation()
      │   spawn + JSON-RPC over stdio          │
      ├──> prompt.submit  ───────────────────> │ 执行 LLM + 工具循环
      │ <── tool.call.request ──────────────── │ (请求 Flocks 执行某工具)
      │     ToolRegistry.execute(...)          │
      │ ──> tool.call.result ────────────────> │
      │ <── message.delta / tool.progress ──── │ (流式增量)
      │ <── prompt.complete ────────────────── │
      │                                         │
      │  ctx.abort_event 触发 → 杀子进程 ──────X │ (中断)
```

子进程在 hermes-agent 自己的目录 + venv 下运行，彻底隔离命名空间与全局状态；Flocks 侧只通过 JSON-RPC 通信。

**中断打通**：`RaptorEngine.run(ctx, ...)` 启动一个 watcher 协程 `await ctx.abort_event.wait()`，一旦 Flocks 既有的 `SessionLoop.abort(session_id)` / `abort_children()` 置位 `abort_event`，立即终止子进程并返回 `LoopResult(action="aborted")`。无需为 raptor 另造中断链路。

#### MessageBridge

- **读**：`SessionContext.get_messages()` → Flocks `MessageInfo/Parts` → OpenAI `messages`，随 `prompt.submit` 一次性传给子进程  
  - `TextPart` → `{"role": "assistant", "content": "..."}`  
  - `ToolPart` → `{"role": "tool", "tool_call_id": ..., "content": ...}`  
- **写**：子进程回传的 assistant/tool 消息 → `Message.create()` + `ToolPart` 写回 Flocks 存储，保证 WebUI 渲染一致

#### ToolBridge

- 子进程不直接持有 Flocks 工具，而是通过 `tool.call.request` RPC **回调 Flocks** 执行：Flocks 侧 `await ToolRegistry.execute(name, ctx, **kwargs)` 后把结果 `tool.call.result` 回传  
- 工具 schema 在 `prompt.submit` 时一次性下发，**会话内保持稳定**（避免破坏 hermes 的 prompt cache）  
- 这样 Raptor 复用 Flocks 的 device/skill/MCP 工具，**无需重写工具层**

#### StreamBridge（RPC event → SSE）

```
子进程 message.delta   → publish_event("message.part.updated", {delta: ...})
子进程 tool.progress   → publish_event("message.part.updated", {tool_part: ...})
子进程 approval.request → publish_event("session.permission", {...})
```

#### ⚠️ P2 前置项：导入/集成可行性 Spike

正式开发 P2 前必须先做一个 spike，确认：
1. hermes-agent 能否以子进程独立启动并通过 stdio JSON-RPC 接受一次 `run_conversation`；
2. 工具回调往返延迟是否可接受；
3. hermes home / 凭证 / 模型配置如何映射到 Flocks 的 provider 配置。

spike 结论决定 P2 是否可行、以及子进程 vs（受限的）进程内方案的最终取舍。

---

## 5. 数据模型变更

### 5.1 `PromptRequest`（`server/routes/session.py`）

新增可选字段：

```python
class PromptRequest(BaseModel):
    # ... 现有字段 ...
    loop_engine: Optional[str] = Field(
        None,
        description="Agent loop engine id: 'native' (default) | 'raptor'. "
                    "Overrides session and global defaults for this request.",
    )
```

### 5.2 引擎选择如何到达 `SessionLoop.run()`

由于分派发生在 `SessionLoop.run()` 内部、且 14 处调用站中只有 2 个 HTTP 入口持有 `PromptRequest`，引擎选择**必须在 HTTP handler 持久化到 session**，再由 `run()` 从 session 读取。

**持久化载体：复用 `SessionInfo.metadata`（免 schema 迁移）。**
`SessionInfo` 已有 `metadata: Dict[str, Any]` 字段，P0 直接用 `metadata["loop_engine"]` 存储，无需新增正式字段、无需存储迁移：

```python
# server/routes/session.py，发消息时（仅 2 个 HTTP 入口）
if request.loop_engine:
    session.metadata["loop_engine"] = request.loop_engine
    await Session.update(session.project_id, session.id, metadata=session.metadata)
```

> 若后续确认要长期保留，再在 P3 升格为 `SessionInfo` 的正式字段（仿 `model_pinned`）。

### 5.3 引擎解析优先级（`SessionLoop._resolve_loop_engine(session)`）

分派函数**只接收已加载的 `session`**（不接收 `request`，因为大多数调用站没有），解析链（越靠前优先级越高）：

```
1. session.metadata["loop_engine"]      (会话级，WebUI 发消息时写入)
2. Config / Storage 全局默认            ("loop_engine_default"，可选)
3. "native"                             (硬编码 fallback)
```

```python
@staticmethod
def _resolve_loop_engine(session) -> str:
    engine = (session.metadata or {}).get("loop_engine")
    if engine and engine in LoopEngineRegistry.ids() | {"native"}:
        return engine
    # 可选：读全局默认
    return "native"
```

### 5.4 子 agent 引擎继承策略

`delegate_task` / `task` 会创建**子 session** 并递归调用 `SessionLoop.run()`。为避免 raptor 嵌套 raptor（子进程套子进程、复杂度与资源不可控），**子 session 默认 `native`**：

- 子 session 创建时**不写** `metadata["loop_engine"]` → `_resolve_loop_engine` 解析为 `native`
- 仅当显式需求（未来）才允许子 session 指定非原生引擎
- 即：引擎选择**不随父 session 自动继承**

---

## 6. API 变更

### 新增端点

```
GET /api/loop-engines
```

响应：

```json
[
  {
    "id": "native",
    "name": "Flocks Native",
    "description": "Flocks 原生 async loop，多会话并发优先"
  },
  {
    "id": "raptor",
    "name": "Raptor",
    "description": "Raptor loop：并行工具 / 动态工具 / 自动 checkpoint"
  }
]
```

### 修改现有端点

`POST /api/{sessionID}/prompt_async` 和 `POST /api/{sessionID}/prompt` 的 request body 新增可选字段 `loop_engine`（向后兼容，不传默认 `native`）。

---

## 7. 调用链改造（单点分派，14 处调用站零改动）

> 全仓库 `SessionLoop.run(` 共 **14 处**调用（核对结果）：
> `server/routes/session.py`(×2)、`server/routes/agent.py`、`cli/session_runner.py`、
> `channel/inbound/dispatcher.py`(×2)、`task/background.py`、`tool/agent/delegate_task.py`(×2)、
> `tool/agent/task.py`(×2)、`session/runner.py`(×2)、`session/features/activity_forwarder.py`。
>
> **这些调用站全部不改**，只在 `SessionLoop.run()` 内部加分派。

### 7.1 唯一的引擎改造点：`SessionLoop.run()` 内的 `_run_loop()` 调用处

分派点必须在所有生命周期脚手架（`_active_loops` 注册、busy 状态、orphan 清理）**之后**、`finally` 清理**之前**，即原来调 `_run_loop()` 的那一行（约 L357-360）：

```python
# session/session_loop.py — 原代码
        try:
            result = await cls._run_loop(ctx, callbacks or LoopCallbacks())
            return result

# session/session_loop.py — 改后
        try:
            from flocks.engine import LoopEngineRegistry
            engine_id = cls._resolve_loop_engine(ctx.session)
            if engine_id != "native":
                # raptor 等：复用同一个 ctx（含 _active_loops 注册 / abort_event / session_ctx）
                result = await LoopEngineRegistry.get(engine_id).run(ctx, callbacks or LoopCallbacks())
            else:
                result = await cls._run_loop(ctx, callbacks or LoopCallbacks())  # 原逻辑零变化
            return result
        # except / finally 保持不变 —— 对所有引擎统一收尾
```

**为什么必须在这一行而非 `run()` 顶部**：`is_running` 守卫、`_active_loops[sid]=ctx`、busy 状态、orphan 清理都在此行之前已执行；idle / touch / `del _active_loops` 在 `finally` 中统一收尾。在此处分派，raptor **自动继承**全部脚手架与中断链路；放到顶部则全部绕过 → 重复执行 + 中断失效。

外加 `SessionLoop._resolve_loop_engine(session)` 辅助函数（见 §5.3）。

### 7.2 两个 HTTP 入口：把用户选择持久化到 session

只有这两处持有 `PromptRequest`，负责把 `request.loop_engine` 写入 `session.metadata`（见 §5.2），写入后照常调用 `SessionLoop.run()`（签名不变）：

| 函数 | 文件 | 说明 |
|---|---|---|
| `_process_session_message` | `server/routes/session.py` | 主路径 |
| `send_session_message` / `_run_existing_user_message` | `server/routes/session.py` | 同步 / replay 路径 |

```python
# 发消息时，调用 SessionLoop.run() 之前
if request.loop_engine:
    session.metadata["loop_engine"] = request.loop_engine
    await Session.update(session.project_id, session.id, metadata=session.metadata)
# 之后照常：result = await SessionLoop.run(session_id=..., callbacks=...)
```

### 7.3 改动汇总

| 改动 | 文件 | P0 |
|---|---|---|
| 在 `_run_loop()` 调用处加分派分支（脚手架内、finally 前） | `session/session_loop.py` | ✅ |
| 新增 `_resolve_loop_engine(session)` | `session/session_loop.py` | ✅ |
| 2 个 HTTP 入口持久化 `loop_engine` | `server/routes/session.py` | ✅ |
| 其余 12 处调用站 | — | **零改动** |

---

## 8. WebUI 变更（P1）

### 8.1 新增 hook：`useLoopEngines`（`webui/src/hooks/useLoopEngines.ts`）

```ts
export interface LoopEngine {
  id: string
  name: string
  description: string
}

export function useLoopEngines(): {
  engines: LoopEngine[]
  loading: boolean
} { ... }
```

调用 `GET /api/loop-engines`，缓存结果，只在引擎列表 > 1 时渲染 UI。

### 8.2 引擎选择器（`webui/src/pages/Session/index.tsx`）

在 Session 页工具栏（agent 下拉旁）并列增加引擎下拉：

```
[Agent: Rex ▼]  [引擎: Flocks Native ▼]  [发送]
```

- 只在注册引擎 ≥ 2 时显示（P0 只有 native，自动隐藏）
- 状态：`zustand` store 增加 `selectedLoopEngine: string | null`
- 持久化：`localStorage` 记住上次选择

### 8.3 `sendText` payload 扩展

```ts
// SessionChat.tsx
const payload: PromptRequest = {
  parts: [...],
  agent: selectedAgent,
  loop_engine: selectedLoopEngine ?? undefined,   // 新增
}
```

### 8.4 Message badge（可选，P2 后启用）

当引擎为 `raptor` 时，assistant 消息气泡右上角显示小 badge：

```
[Raptor ⚡]
```

用于对比两个引擎的输出质量。

### 8.5 i18n

在 `webui/src/i18n.ts` 的 `en`/`zh` namespace 下追加：

```ts
// session namespace 追加
"loopEngine.label": "引擎",
"loopEngine.native": "Flocks Native",
"loopEngine.raptor": "Raptor",
"loopEngine.tooltip.native": "Flocks 原生异步循环，多会话并发优先",
"loopEngine.tooltip.raptor": "Raptor loop：并行工具 / 动态工具 / 自动 checkpoint",
```

---

## 9. 目录结构（完成后）

`engine/` 与 `session/`、`tool/`、`provider/` **同层**，作为独立的横切编排模块。
这样 `engine/raptor/` 可以自由 import `session/`、`tool/`、`provider/` 而不产生循环依赖。

```
flocks/flocks/
├── engine/                         ← 新增目录（与 session/ tool/ provider/ 同层）
│   ├── __init__.py                 # 导出 LoopEngineRegistry（仅注册非原生引擎）
│   ├── base.py                     # AgentLoopEngine Protocol（run(ctx, callbacks)）
│   ├── registry.py                 # LoopEngineRegistry（不含 native）
│   ├── native.py                   # NATIVE_ENGINE_META（仅元数据，原生执行=_run_loop）
│   └── raptor/                     # RaptorEngine（P2，子进程/RPC 适配 hermes-agent）
│       ├── __init__.py
│       ├── engine.py               # RaptorEngine.run(ctx)（spawn 子进程 + JSON-RPC）
│       ├── message_bridge.py       # Flocks Parts ↔ OpenAI messages
│       ├── tool_bridge.py          # tool.call RPC ↔ Flocks ToolRegistry.execute
│       └── stream_bridge.py        # 子进程 RPC event → publish_event SSE
├── session/
│   ├── session_loop.py             # _run_loop() 调用处加分派 + _resolve_loop_engine()
│   └── ...
├── tool/                           # 不变
├── provider/                       # 不变
└── ...

flocks/flocks/server/routes/
└── session.py                      # 2 个 HTTP 入口持久化 loop_engine 到 session.metadata
                                    # 其余 12 处 SessionLoop.run() 调用站零改动
                                    # 新增 GET /api/loop-engines 端点

flocks/webui/src/
├── hooks/
│   └── useLoopEngines.ts           # 新增（P1）
├── pages/Session/
│   └── index.tsx                   # 工具栏新增引擎下拉（P1）
└── i18n.ts                         # 追加文案（P1）
```

---

## 10. 实施分期

### P0 — 框架骨架（零风险重构）

- [ ] 创建 `engine/base.py`（AgentLoopEngine Protocol，`run(ctx, callbacks)`）
- [ ] 创建 `engine/registry.py`（LoopEngineRegistry，native 不入 registry）
- [ ] 创建 `engine/native.py`（`NATIVE_ENGINE_META`，仅元数据供列举）
- [ ] 创建 `engine/__init__.py`（导出 LoopEngineRegistry）
- [ ] 在 `SessionLoop` 的 `_run_loop()` 调用处加分派分支 + `_resolve_loop_engine(session)`（§7.1）
- [ ] 2 个 HTTP 入口：把 `request.loop_engine` 持久化到 `session.metadata`（§7.2）
- [ ] `PromptRequest` 增加 `loop_engine` 字段（`SessionInfo` 用 metadata，不加正式字段）
- [ ] 新增 `GET /api/loop-engines` 端点

**验收**：所有现有测试通过；不传 `loop_engine` 时行为与改前**逐字节一致**（native 走 inline，14 处调用站全部未改）。

### P1 — WebUI 接入

- [ ] `useLoopEngines.ts` hook
- [ ] Session 页工具栏引擎下拉（引擎 < 2 时隐藏）
- [ ] `sendText` payload 携带 `loop_engine`
- [ ] i18n 文案

**验收**：引擎下拉暂时隐藏（只有 native），发送请求携带正确字段。

### P2 — RaptorEngine 适配器（子进程 / RPC）

- [ ] **前置 Spike**：验证 hermes-agent 子进程 JSON-RPC 可行性（§4.4 末）
- [ ] 子进程 RPC 协议定义（`prompt.submit` / `tool.call` / `message.delta` / `prompt.complete`）
- [ ] `MessageBridge`：Flocks Parts ↔ OpenAI messages
- [ ] `ToolBridge`：`tool.call` RPC → `ToolRegistry.execute`（结果回传）
- [ ] `StreamBridge`：子进程 RPC event → publish_event SSE
- [ ] `engine/raptor/engine.py` 主适配器，注册进 LoopEngineRegistry
- [ ] 启用 WebUI 引擎下拉（此时出现两个选项）

**验收**：选择 Raptor 引擎时，工具可并行执行，SSE 推送正常，WebUI 渲染一致；子进程崩溃能优雅降级。

### P3 — 高级能力对接

- [ ] Checkpoint 事件映射到 Flocks SSE（`checkpoint.created`）
- [ ] interrupt / steer 接入 LoopContext.signal_abort
- [ ] 动态工具折叠（tool_search）
- [ ] Message badge（区分引擎来源）
- [ ] A/B 指标（墙钟时间、token 用量、工具调用次数）写入 message metadata

---

## 11. 风险与注意事项

| 风险 | 等级 | 缓解 |
|---|---|---|
| hermes-agent 无法安全进程内 import（扁平模块 + 进程全局状态 + 命名空间冲突） | 高 | P2 改用**子进程 + JSON-RPC** 彻底隔离；正式开发前先做可行性 spike |
| 子进程崩溃 / RPC 超时 / 僵尸进程 | 高 | 超时与心跳、进程生命周期绑定 session、崩溃时优雅降级并 `on_error` 上报 |
| Flocks `MessageInfo/Parts` ↔ OpenAI messages 格式映射不完整 | 高 | P2 需完整映射全部 Part 类型；先只支持 TextPart + ToolPart |
| 工具回调 RPC 往返延迟累积 | 中 | spike 实测延迟；必要时批量/并行回调 |
| 子 agent 引擎嵌套（raptor 套 raptor） | 中 | 子 session 默认 native，引擎不随父继承（§5.4） |
| 破坏 hermes prompt cache（mid-conversation 改 toolset/system） | 中 | 工具 schema 在 `prompt.submit` 时一次性下发，会话内保持稳定 |
| `_last_resolved_tool_names` 等进程全局状态 | 低 | 子进程隔离后天然规避（各 session 独立子进程） |
| hermes home / 凭证 / 模型配置映射 | 低 | spike 阶段确定映射；hermes_home 指向 flocks workspace 隔离目录 |

---

## 12. 不做的事（Out of Scope）

- 不替换 Flocks 的 Provider 层（Raptor 子进程内 LLM 调用仍走 hermes-agent 自己的 provider 层）
- 不合并 Flocks 和 Raptor 的会话持久化（Flocks 存储为准，MessageBridge 负责同步）
- 不把 Raptor 设为默认引擎（默认永远是 `native`）
- 引擎选择不随子 agent / 子 session 继承（子 session 默认 native，见 §5.4）
- 不修改 14 处 `SessionLoop.run()` 调用站（分派统一在 run() 内部）
- trajectory_compressor（离线训练数据工具）不纳入集成范围
