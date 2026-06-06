# 可插拔 Agent Loop 设计文档

> 状态：**草稿 / RFC**  
> 作者：AI 辅助生成  
> 日期：2026-06-06  
> 分支：`feat/pluggable-agent-loop`

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

### 1.2 Hermes-Agent 的高价值能力

通过对 `open_source/hermes-agent` 的深度分析，发现其 agent loop（`agent/conversation_loop.py`）在**单任务执行质量**上有以下优势：

| 能力 | Flocks 现状 | Hermes |
|---|---|---|
| 工具并行执行 | ❌ 串行 await | ✅ ThreadPool 8 并发，智能判定路径冲突后降级串行 |
| 动态工具加载 | ❌ 全量 schema 传模型 | ✅ `tool_search`/`tool_describe`/`tool_call` 三桥接，按需折叠省 token |
| Checkpoint / 回滚 | 部分（SessionRevert） | ✅ 写文件/破坏性命令前自动快照 |
| 中断 + 软注入 | abort（cancel task） | ✅ `interrupt()` 硬中断 + `steer()` 注入不中断 |
| 多层 API 重试 | 空响应 + API 错误 | ✅ 429 / 压缩 / fallback provider / invalid JSON 四层恢复 |
| Subagent 并行 | delegate_task（串行） | ✅ batch 并发 + 中断传播 |

### 1.3 目标

将 Hermes 的 agent loop 以**可插拔**方式集成到 Flocks，让用户在 WebUI 中选择使用哪个引擎，而无需改动会话管理、SSE 推送、工具注册、压缩等基础设施。

---

## 2. 核心设计原则

1. **最小侵入**：现有 `SessionLoop.run()` 调用链仅改一行，行为不变。  
2. **向后兼容**：默认引擎 `native`，不传 `loop_engine` 等同现在。  
3. **SSE 透明**：引擎内部事件仍经 `LoopCallbacks.event_publish_callback` 推送，WebUI 无感。  
4. **分阶段实施**：P0 建框架（零风险），P1 接 WebUI，P2 再接 Hermes 实现。

---

## 3. 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│  routes/session.py                                          │
│  _process_session_message() / _run_existing_user_message()  │
└────────────────────┬────────────────────────────────────────┘
                     │  engine = LoopEngineRegistry.get(loop_engine)
                     │  result = await engine.run(session_id, ...)
                     ▼
         ┌───────────────────────┐
         │   AgentLoopEngine     │  ← Protocol (base.py)
         │   (统一契约接口)       │
         └──────────┬────────────┘
            ┌───────┴────────┐
            ▼                ▼
  ┌──────────────────┐  ┌──────────────────────────────────┐
  │ FlocksNativeEngine│  │        HermesEngine              │
  │  (零风险包装)     │  │      (P2 实现，适配器模式)        │
  │                  │  │                                  │
  │ SessionLoop.run()│  │  MessageBridge  ToolBridge        │
  └──────────────────┘  │  StreamBridge   asyncio.to_thread │
                        │  └─> run_conversation(agent, ...) │
                        └──────────────────────────────────┘
                                         │
                         ┌───────────────┼───────────────┐
                         ▼               ▼               ▼
                  Flocks SSE       Flocks SQLite    Flocks ToolRegistry
                  (publish_event)  (Message/Parts)  (ToolRegistry.execute)
```

---

## 4. 详细接口设计

### 4.1 `AgentLoopEngine` 协议（`engine/base.py`）

```python
from typing import Optional, Any, Protocol, runtime_checkable

@runtime_checkable
class AgentLoopEngine(Protocol):
    id: str           # 机器标识，如 "native" / "hermes"
    display_name: str # WebUI 下拉显示名
    description: str  # WebUI tooltip

    async def run(
        self,
        session_id: str,
        provider_id: Optional[str] = None,
        model_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        callbacks: Optional[Any] = None,  # LoopCallbacks
    ) -> Any:  # LoopResult
        ...
```

> `LoopResult` 与 `LoopCallbacks` 复用 `session_loop.py` 的现有定义，HermesEngine 需返回格式相同的 `LoopResult`。

### 4.2 `LoopEngineRegistry`（`engine/registry.py`）

```python
class LoopEngineRegistry:
    _engines: Dict[str, AgentLoopEngine] = {}

    @classmethod
    def register(cls, engine: AgentLoopEngine) -> None: ...

    @classmethod
    def get(cls, engine_id: Optional[str]) -> AgentLoopEngine:
        # 未知 id 或 None → 降级返回 "native"
        ...

    @classmethod
    def list(cls) -> List[Dict[str, str]]:
        # 返回 [{"id", "name", "description"}, ...]，供 /api/loop-engines 使用
        ...
```

### 4.3 `FlocksNativeEngine`（`engine/native.py`）

```python
class FlocksNativeEngine:
    id = "native"
    display_name = "Flocks Native"
    description = "Flocks 原生 async loop，多会话并发优先"

    async def run(self, session_id, provider_id=None, model_id=None,
                  agent_name=None, callbacks=None) -> LoopResult:
        from flocks.session.session_loop import SessionLoop
        return await SessionLoop.run(
            session_id, provider_id, model_id, agent_name, callbacks
        )
```

### 4.4 `HermesEngine`（`engine/hermes/engine.py`，P2 实现）

HermesEngine 是适配器，需要三个 Bridge：

#### MessageBridge

- **读**：`SessionContext.get_messages()` → Flocks `MessageInfo/Parts` → Hermes OpenAI `messages` 列表  
  - `TextPart` → `{"role": "assistant", "content": "..."}`  
  - `ToolPart` → `{"role": "tool", "tool_call_id": ..., "content": ...}`  
- **写**：Hermes 输出的 assistant/tool 消息 → `Message.create()` + `ToolPart` 写回 Flocks 存储，保证 WebUI 渲染一致

#### ToolBridge

- 把 Flocks `ToolRegistry` 中当前 session 可用工具**动态注册**进 Hermes `tools/registry`  
- handler 实现：`asyncio.run_coroutine_threadsafe(ToolRegistry.execute(name, ctx, **kwargs), loop)` 回主事件循环执行 Flocks 异步工具  
- 这样 Hermes loop 复用 Flocks 的 device/skill/MCP 工具，**无需重写工具层**

#### StreamBridge（callbacks）

```
Hermes stream_delta_callback
    → publish_event("message.part.updated", {delta: ...})

Hermes tool_progress_callback  
    → publish_event("message.part.updated", {tool_part: ...})

Hermes approval_callback
    → publish_event("session.permission", {...})
```

#### 线程隔离

Hermes 是同步+线程模型，必须与 Flocks asyncio 事件循环隔离：

```python
loop = asyncio.get_event_loop()
await asyncio.to_thread(_run_hermes_sync, agent, user_message, bridge_callbacks)
```

`_run_hermes_sync` 在独立线程运行 `run_conversation(...)`，工具回调通过 `run_coroutine_threadsafe` 回主循环。

---

## 5. 数据模型变更

### 5.1 `PromptRequest`（`server/routes/session.py`）

新增可选字段：

```python
class PromptRequest(BaseModel):
    # ... 现有字段 ...
    loop_engine: Optional[str] = Field(
        None,
        description="Agent loop engine id: 'native' (default) | 'hermes'. "
                    "Overrides session and global defaults for this request.",
    )
```

### 5.2 `SessionInfo`（`session/session.py`）

新增可选字段（支持会话级固定引擎）：

```python
class SessionInfo(BaseModel):
    # ... 现有字段 ...
    loop_engine: Optional[str] = Field(
        None,
        description="Pinned loop engine for this session. None = follow global default.",
    )
```

### 5.3 引擎解析优先级

与现有 `_resolve_model` 链同构（越靠前优先级越高）：

```
1. PromptRequest.loop_engine          (请求级显式指定)
2. SessionInfo.loop_engine            (会话级固定)
3. Storage.read("loop_engine_default") (全局配置)
4. "native"                           (硬编码 fallback)
```

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
    "id": "hermes",
    "name": "Hermes",
    "description": "Hermes agent loop，并行工具 / 动态工具 / checkpoint"
  }
]
```

### 修改现有端点

`POST /api/{sessionID}/prompt_async` 和 `POST /api/{sessionID}/prompt` 的 request body 新增可选字段 `loop_engine`（向后兼容，不传默认 `native`）。

---

## 7. 调用链改造（两处 + 一处）

当前两处 `SessionLoop.run(...)` 调用均在 `server/routes/session.py`：

| 函数 | 行号（参考） | 说明 |
|---|---|---|
| `_run_existing_user_message` | L1668 | replay 路径 |
| `_process_session_message` | L2555 | 主路径 |

两处改法完全一致，以主路径为例：

```python
# 改前
from flocks.session.session_loop import SessionLoop, LoopCallbacks
result = await SessionLoop.run(
    session_id=sessionID,
    provider_id=provider_id,
    model_id=model_id,
    agent_name=agent_name,
    callbacks=loop_callbacks,
)

# 改后
    from flocks.engine import LoopEngineRegistry
from flocks.session.session_loop import LoopCallbacks
engine = LoopEngineRegistry.get(_resolve_loop_engine(session, request))
result = await engine.run(
    session_id=sessionID,
    provider_id=provider_id,
    model_id=model_id,
    agent_name=agent_name,
    callbacks=loop_callbacks,
)
```

新增辅助函数 `_resolve_loop_engine(session, request)` 实现上面第 5.3 节的优先级链。

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

当引擎为 `hermes` 时，assistant 消息气泡右上角显示小 badge：

```
[Hermes ⚡]
```

用于对比两个引擎的输出质量。

### 8.5 i18n

在 `webui/src/i18n.ts` 的 `en`/`zh` namespace 下追加：

```ts
// session namespace 追加
"loopEngine.label": "引擎",
"loopEngine.native": "Flocks Native",
"loopEngine.hermes": "Hermes",
"loopEngine.tooltip.native": "Flocks 原生异步循环，多会话并发优先",
"loopEngine.tooltip.hermes": "Hermes loop：并行工具 / 动态工具 / 自动 checkpoint",
```

---

## 9. 目录结构（完成后）

`engine/` 与 `session/`、`tool/`、`provider/` **同层**，作为独立的横切编排模块。
这样 `engine/hermes.py` 可以自由 import `session/`、`tool/`、`provider/` 而不产生循环依赖。

```
flocks/flocks/
├── engine/                         ← 新增目录（与 session/ tool/ provider/ 同层）
│   ├── __init__.py                 # 导出 LoopEngineRegistry，触发 native 自注册
│   ├── base.py                     # AgentLoopEngine Protocol
│   ├── registry.py                 # LoopEngineRegistry
│   ├── native.py                   # FlocksNativeEngine（P0，包装 session/session_loop）
│   └── hermes/                     # HermesEngine（P2）
│       ├── __init__.py
│       ├── engine.py               # HermesEngine 主适配器
│       ├── message_bridge.py       # Flocks Parts ↔ OpenAI messages
│       ├── tool_bridge.py          # Flocks ToolRegistry ↔ Hermes registry
│       └── stream_bridge.py        # Hermes callbacks → publish_event SSE
├── session/
│   ├── session_loop.py             # 不变（被 native.py 包装）
│   └── ...
├── tool/                           # 不变
├── provider/                       # 不变
└── ...

flocks/flocks/server/routes/
└── session.py                      # 两处调用站改为 LoopEngineRegistry.get().run()
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

- [ ] 创建 `session/engine/base.py`（AgentLoopEngine Protocol）
- [ ] 创建 `session/engine/registry.py`（LoopEngineRegistry）
- [ ] 创建 `session/engine/native.py`（FlocksNativeEngine，包装 SessionLoop）
- [ ] 创建 `session/engine/__init__.py`（注册 native，导出）
- [ ] 改 `routes/session.py` 两处调用站
- [ ] 新增 `GET /api/loop-engines` 端点
- [ ] `PromptRequest` + `SessionInfo` 增加 `loop_engine` 字段

**验收**：所有现有测试通过，行为与改前完全一致。

### P1 — WebUI 接入

- [ ] `useLoopEngines.ts` hook
- [ ] Session 页工具栏引擎下拉（引擎 < 2 时隐藏）
- [ ] `sendText` payload 携带 `loop_engine`
- [ ] i18n 文案

**验收**：引擎下拉暂时隐藏（只有 native），发送请求携带正确字段。

### P2 — HermesEngine 适配器

- [ ] `MessageBridge`：Flocks Parts ↔ OpenAI messages
- [ ] `ToolBridge`：Flocks ToolRegistry ↔ Hermes registry（线程安全）
- [ ] `StreamBridge`：Hermes callbacks → publish_event SSE
- [ ] 线程隔离：`asyncio.to_thread` + `run_coroutine_threadsafe`
- [ ] `hermes.py` 主适配器，注册进 LoopEngineRegistry
- [ ] 启用 WebUI 引擎下拉（此时出现两个选项）

**验收**：选择 Hermes 引擎时，工具可并行执行，SSE 推送正常，WebUI 渲染一致。

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
| Hermes 同步线程模型阻塞 asyncio 事件循环 | 高 | `asyncio.to_thread` 强制隔离，工具回调用 `run_coroutine_threadsafe` |
| Flocks `MessageInfo/Parts` ↔ OpenAI messages 格式映射不完整 | 高 | P2 需完整映射全部 Part 类型；先只支持 TextPart + ToolPart |
| Hermes 工具与 Flocks ToolRegistry 命名冲突 | 中 | ToolBridge 加命名空间前缀，如 `flocks__bash` |
| 多会话并发下 Hermes `_last_resolved_tool_names` 全局状态 | 中 | ToolBridge 在线程局部变量中 save/restore |
| `run_conversation` 内部直接读写磁盘（hermes home）| 低 | 配置 hermes_home 指向 flocks workspace 目录 |

---

## 12. 不做的事（Out of Scope）

- 不替换 Flocks 的 Provider 层（Hermes 使用 Flocks ToolBridge 调工具，但 LLM 调用仍走 Hermes 的 `chat_completion_helpers`）
- 不合并 Flocks 和 Hermes 的会话持久化（各自持久化，MessageBridge 负责同步）
- 不把 Hermes 设为默认引擎（默认永远是 `native`）
- trajectory_compressor（离线训练数据工具）不纳入集成范围
