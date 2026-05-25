# Context Compaction — Current Design

> 状态：本文档反映当前生产代码的实际策略。  
> 历史版本（B1/B2/B3 + E1–E6 + 三阶段降级）的中间设计文档已合并到这里。

---

## 1. 总览

Flocks 的上下文压缩遵循 **"hermes 单次 LLM + flocks 多层防护"** 策略：

1. **每步检测**：`SessionLoop` 在每个 step 末尾用 provider 实测 token（若有）或 chars/4 估算判断是否超出 `overflow_threshold`
2. **预剪枝**：触发后先对 `chat_messages` 做无 LLM 的内存压缩（MD5 去重 / 大消息一行化 / 多模态剥图）
3. **单次摘要**：剩余内容经过 per-message 截断（每条 6000 chars head+tail）后一次性送给 LLM
4. **反拖拽**：节省率连续 < 5 % 触发冷却 5 轮；摘要失败按错误类型分档冷却 30s / 60s / 600s
5. **后置剪枝**：摘要写入后对剩余历史再 prune 一遍，避免下轮立刻再触发

---

## 2. 触发机制

### 阈值

| 阈值 | 公式 | 用途 |
|------|------|------|
| `overflow_threshold` | **固定 `context_window × 0.85`** | 触发完整压缩 |
| `preemptive_threshold` | `overflow_threshold - overflow_buffer` | 软阈值，优先轻量清理 |
| `overflow_buffer` | `usable × 0.03–0.08`，clamp 2K–32K | 二者之差 |

`overflow_threshold` 不再随 tier 浮动（之前是 0.80 / 0.85 / 0.87 / 0.90 分档），与 hermes-agent gateway 保持一致。

### Token 估算

```python
SessionPrompt.count_tokens(text) -> len(text) // 4
SessionPrompt.estimate_full_context_tokens(...) -> Σ chars/4 over messages
```

没有 tiktoken，没有 overhead，没有 safety margin。`session_loop.py` 优先使用 provider 实测 `input + cache_read`（B3 — "observed value wins"），仅在 provider 不报数时降级到估算。

### B1 异常 cap

`max_output_tokens ≥ 0.7 × context_window` → cap 到 `0.25 × context_window`。保护 GLM-5.1 这类报告 `max_output=168000` against `context_window=198000` 的异常 metadata。

---

## 3. 预剪枝（hermes-style）

入口：`_prune_chat_messages_for_summary(chat_messages, policy)`

### 区域划分

```
[ head (固定前 3 条) | middle (压缩区) | tail (token 预算保护) ]
                                         ↑
                                last user message anchor
```

- **head**：始终保留前 3 条消息
- **tail**：从尾部累积 `overflow_threshold × 20% × 4` chars 的最近消息
- **最近 user 消息保护**：若最近的 user 消息落在 middle，`tail_start` 强制回退到该索引（防止极大工具输出占满 tail budget 时把用户最新任务压成一行）

### Pass 1：MD5 dedup

middle 区域内容相同（`len ≥ 200`）的旧消息 → `[Duplicate message — same content as a more recent message]`。倒序扫描，保留最新副本。

### Pass 2：大消息一行化

middle 区域 `len > 200 chars` 的消息 → 语义摘要行：

```
[user|tools: bash, grep] (47 lines, 12,345 chars)
[user] 用户原话前 80 字符… (1 lines, 235 chars)
```

### 多模态处理（`_extract_chat_messages`）

`image / image_url / input_image / file / document` 类型 part → 文本占位：

```
[screenshot: removed to save context]
[file: removed to save context]
```

---

## 4. 摘要生成（hermes-style 单次 LLM）

入口：`summary.summarize_single_pass(..., chat_messages=...)`

### Per-message 截断

```python
_MSG_CONTENT_MAX  = 6_000  # 单条上限
_MSG_CONTENT_HEAD = 4_000  # 保留开头
_MSG_CONTENT_TAIL = 1_500  # 保留结尾
```

> 与 hermes-agent `_serialize_for_summary` 完全一致。

每条消息独立截断，不做整体 tail-cut，所以早期决策和用户请求始终有片段存活。

### Iterative summary（E1）

进程内 `OrderedDict` 缓存 `(session_id) → (previous_summary, count)`（最多 1024 session）。每 **5** 次压缩强制全量重建（`ITERATIVE_SUMMARY_REBUILD_INTERVAL`），避免漂移。

`custom_prompt`（`/compact <focus>`）的请求不走缓存，强制重建。

### 失败容错（hermes-style 分档 cooldown）

| 错误类别 | Cooldown |
|---------|---------|
| `RuntimeError`（无 provider 配置） | **600 s** |
| `asyncio.TimeoutError` | **60 s** |
| `JSONDecodeError` / 上游返回 HTML 等非 JSON | **30 s** |
| 其他（rate limit, 5xx, 网络） | **60 s** |

冷却内或 LLM 调用失败时 `summary_text = None`：**直接 `return "continue"` 跳过 archive**，不写 fallback summary。原始消息保持不动，等下次触发时再尝试。`session_loop` 的 `MAX_OVERFLOW_COMPACTION_ATTEMPTS = 3` 兜底，达到上限给用户友好提示（避免在 provider 抖动期间静默归档真实对话造成 data loss）。成功一次后立即清零冷却。

---

## 5. 反拖拽（E4）

```python
INEFFECTIVE_SAVINGS_THRESHOLD = 0.05  # 节省率 < 5 % 视为无效
COOLDOWN_AFTER_INEFFECTIVE    = 3     # 连续 3 次 → 冷却
COOLDOWN_SUPPRESS_COMPACTIONS = 5     # 静默跳过 5 次
```

`CompactionHistory` 字段：

```python
@dataclass
class CompactionHistory:
    last_savings_ratio: float = 1.0
    ineffective_count: int = 0
    cooldown_remaining: int = 0
    total_attempts: int = 0
    total_skipped: int = 0
    summary_cooldown_until: float = 0.0   # hermes-style 摘要失败冷却
    summary_last_error: str = ""
```

`POST_COMPACTION_COOLDOWN_STEPS = 2`：刚压完 2 步内优先轻量清理（`pre_compact_cleanup`）。

---

## 6. 后置剪枝（E5）

`orchestrator.run_compaction()` 在 `SessionCompaction.process()` 成功后追加 `SessionCompaction.prune()` 一次。让本轮已被摘要覆盖的旧 tool 输出立刻标记为 `time.compacted`，避免下轮 overflow 检测立即再触发。

---

## 7. Pruning（持久化 prune）

入口：`pruning.prune(session_id, policy)`

### 按工具名 × user-turn 双维度（`TOOL_PRUNE_POLICY`）

| 保留轮次 | 工具 |
|---------|------|
| 永不剪（-1） | `skill_load`, `memory_*`, `tool_search`, `flocks_skills` |
| 1 轮 | `bash`, `read`, `grep`, `edit`, `write`, … |
| 2 轮 | `websearch`, `tdp_*`, `threatbook_*`, `sangfor_*`, … |
| 3 轮（默认） | 其余 |

倒序扫描；遇到 `state.time.compacted` 或 metadata.summary 停止；超过 `tool_keep_turns` 的旧 tool 标记为 compacted（10 token 占位，**不删消息**）。

### 巨型单条截断（`truncate_oversized_tool_outputs`）

- Pass 1：单条超过 `calculate_max_tool_result_chars(context_window)` → `truncate_tool_result_text_safe` JSON 安全截断
- Pass 2：总 tool chars > `max(4096, context_window × 4 × 0.75)` → 最老输出替换为 `[compacted: tool output removed to free context]`

---

## 8. Per-message token cache（E6）

```python
_MESSAGE_CACHE_MAX = 2_000
_message_token_cache: OrderedDict[str, int] = OrderedDict()
```

`finish is not None and finish != "streaming"` 的消息缓存 token 总数，FIFO 淘汰。流式消息（`finish is None`）不缓存。

修改消息的调用方（`pruning.prune` 翻转 `time.compacted` 后）必须调用 `SessionPrompt.invalidate_message_cache(msg_id)`，否则下轮估算会返回过期值。

---

## 9. 与 hermes、openclaw 对比

| 维度 | flocks（当前） | hermes-agent | openclaw |
|------|--------------|--------------|----------|
| 触发阈值 | 固定 85 % × ctx | Gateway 85 % + Agent 50 % | Pi 内部 reserve + 被动 overflow |
| Token 估算 | provider 实测 + chars/4 | provider 实测 + chars/4 | estimateTokens × 1.2 safety |
| 工具 prune 粒度 | 按工具名 × user-turn | 统一 tail 预算 + MD5 dedup | 丢整 chunk（safeguard） |
| 摘要 LLM 次数 | **1 次** | **1 次** | ≈ 3 次（2 chunk + 1 merge） |
| 超长处理 | per-msg 6000 chars head+tail | 同上 | 剔除超大单条再重试 |
| 跨次复用 | LRU + 每 5 次重建 | `_previous_summary` 字段 | `previousSummary` 链式 |
| 失败 cooldown | 600 / 60 / 30 s 分档 | 600 / 60 / 30 s 分档 | 仅次数上限 |
| 多模态 | image/file part 文本占位 | 旧截图剥图 | cache-ttl pruning |
| 最近 user 保护 | 强制 anchor 到 tail | 同（`_ensure_last_user_message_in_tail`） | — |

---

## 10. 关键常量速查

| 文件 | 常量 | 值 |
|------|------|---|
| `policy.py` | `overflow_threshold` | `context_window × 0.85` |
| `policy.py` | `MAX_OUTPUT_RATIO_THRESHOLD` / `SAFE_OUTPUT_RATIO` | 0.7 / 0.25 |
| `policy.py` | `ITERATIVE_SUMMARY_REBUILD_INTERVAL` | 5 |
| `compaction.py` | `INEFFECTIVE_SAVINGS_THRESHOLD` | 0.05 |
| `compaction.py` | `COOLDOWN_AFTER_INEFFECTIVE` | 3 |
| `compaction.py` | `COOLDOWN_SUPPRESS_COMPACTIONS` | 5 |
| `compaction.py` | `_PRE_PRUNE_THRESHOLD_CHARS` | 200 |
| `compaction.py` | `_PRE_PRUNE_TAIL_RATIO` | 0.20 |
| `compaction.py` | `_PRE_PRUNE_HEAD_N` | 3 |
| `compaction.py` | summary cooldown | 600 / 60 / 30 s |
| `compaction.py` | `POST_COMPACTION_COOLDOWN_STEPS` | 2 |
| `summary.py` | `_MSG_CONTENT_MAX / HEAD / TAIL` | 6000 / 4000 / 1500 |
| `summary.py` | `COMPACTION_TIMEOUT_SECONDS` | 300 |
| `prompt.py` | `_MESSAGE_CACHE_MAX` | 2000 |
| `session_loop.py` | `MAX_OVERFLOW_COMPACTION_ATTEMPTS` | 3 |

---

## 11. 关键日志事件

| 事件名 | 触发场景 |
|--------|---------|
| `loop.tokens_decision` | 每步触发判定（observed / estimated） |
| `loop.context_overflow_detected` | 越过 overflow_threshold |
| `compaction.pre_prune.anchor_user_msg` | 最近 user 消息回退保护 tail |
| `compaction.process.strategy` | 进入 single_pass 路径 |
| `compaction.summary_cooldown_active` | 处于摘要失败冷却 |
| `compaction.summary_failed.{no_provider,timeout,error}` | LLM 调用失败 |
| `compaction.ineffective_attempt` / `cooldown_engaged` | E4 触发 |
| `compaction.post_prune_failed` | E5 后置 prune 异常（不阻塞主流程） |

---

## 12. 当前已知 trade-off

- 不再有分块路径，对单条 ≥ 160 K chars 的极端工具输出（已经被 `truncate_oversized_tool_outputs` 截断到合理大小后才进 summary）依赖摘要 LLM 的健壮性
- chars/4 估算偏低于实际 token，但 85 % 阈值留出足够 buffer
- 摘要失败 / 冷却期内 `process()` 直接放弃归档让会话保持原状；连续 3 次 overflow 后由 `session_loop` 给用户报错。这是为了避免 provider 抖动时把真实对话替换成简陋 fallback summary 导致 data loss，trade-off 是冷却窗口内会话上下文仍然占满，但保留了下次成功生成正经摘要的可能
