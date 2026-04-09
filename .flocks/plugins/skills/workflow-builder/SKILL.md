---
name: workflow-builder
category: system
description: "Generates built-in Flocks workflows (`workflow.md`, `workflow.json`) from natural-language requirements. Use this skill when the user asks to create, design, generate, or assemble a workflow, or any multi-step process such as alert investigation, incident response, or SOP/runbook automation."
description_cn: "根据自然语言描述生成 Flocks 内置工作流（`workflow.md`、`workflow.json`）。当用户提出创建、设计、生成、搭建工作流，或任何多步骤流程需求时使用本 skill，例如告警调查、事件响应或 SOP/Runbook 自动化。"
---

# Workflow Builder

分七个阶段构建工作流：**场景确认与流程设计** → **简化 JSON 预览循环** → **完整 workflow.md** → **完整 workflow.json** → **逐节点测试** → **集成测试** → **性能评估与优化**。

> **产物**：`workflow.json` 中所有可执行节点均为 `type="python"` 并自带 `code`。最终交付物固定为：`workflow.md`、`workflow.json`。

## 参考资料（按需读取）

| 文件 | 内容 | 何时读取 |
|------|------|---------|
| [references/reference.md](references/reference.md) | 节点类型详解、出边选择行为、分支/循环/Join 规则、Edge Mapping 指南、Tool vs LLM 决策、文件输出规则、报告生成模板、`workflow.json` 骨架模板 | **生成 `workflow.json` 前建议读取** |
| [references/composition.md](references/composition.md) | 嵌套工作流（subworkflow）组合格式与展开规则 | 仅在用户需要嵌套工作流时读取 |

---

## 0. 开始前

### Todo List（每次创建必须生成）

**在开始任何工作前，必须先用 Todo 工具列出完整任务清单**，并在整个过程中实时更新状态（pending → in_progress → completed）。标准 Todo 清单如下，根据实际工作流复杂度增减：

```
[ ] 0.   核验可用工具列表（读取 registry.py）
[ ] 1.   场景深度确认：与用户对话，明确业务场景与核心目标
[ ] 1.   输出思考维度分析 + Mermaid 流程简图，与用户沟通对齐
[ ] 1.   获取样例数据（用户上传或自动构造后确认）
[ ] 2.   生成简化版预览 JSON（仅节点名称+描述，无代码）
[ ] 2.   写入简化 workflow.json 文件，供页面展示流程图
[ ] 2.   向用户展示并收集修改建议（循环直至满意）
[ ] 3.   生成完整 workflow.md（人读描述）
[ ] 3.   写入 workflow.md 文件
[ ] 3.   向用户展示流程摘要并请求确认
[ ] 4.   读取 reference.md
[ ] 4.   生成完整 workflow.json（含代码）
[ ] 4.   写入 workflow.json 文件
[ ] 4.   验证 JSON 格式 + Python 语法
[ ] 4.   保存样例数据到 /api/workflow/{id}/sample-inputs
[ ] 5.   逐节点测试：节点 1 - <node_id>
[ ] 5.   逐节点测试：节点 2 - <node_id>
[ ] 5.   逐节点测试：节点 N - <node_id>（按拓扑顺序补充）
[ ] 6.   集成测试：全量运行验证
[ ] 6.   记录各节点及总运行时间
[ ] 7.   性能评估：识别瓶颈节点
[ ] 7.   优化慢节点（并发/缓存/精简 prompt 等）
[ ] 7.   通知用户工作流已就绪
```

> 每完成一项立即标记为 completed；每进入一项立即标记为 in_progress。**严禁在 Todo 全部完成前宣布任务结束。**

---

### 实时工具核验（重要）

生成 workflow 前必须核验可用工具列表与参数签名：

- **强制读取** `flocks/flocks/tool/registry.py`（`ToolInfo.parameters` 是参数 schema 的权威来源）。
- 工具名必须一致，参数名必须严格对齐，禁止调用 `run_workflow`（在 `WORKFLOW_TOOL_BLOCKLIST` 中）。

---

## 1. 第一阶段：场景确认与流程设计

> 目标：通过对话真正理解用户需求，输出完整的思考维度与流程简图，让用户在花时间构建工作流之前就能确认方向。

### 1.1 深度场景对话

使用 `Question` 工具与用户确认以下维度（根据场景选取相关项，不必逐条询问，尽量合并为 1-2 轮对话）：

**业务背景**
- 这个工作流解决什么安全/业务问题？触发条件是什么？
- 谁会使用这个工作流？是定时自动触发还是手动触发？
- 有没有现有的 SOP 或人工处理流程可以参考？

**数据与工具**
- 输入数据是什么？（告警字段、IP/域名/哈希、日志条目等）
- 需要调用哪些外部工具或服务？（威胁情报、SIEM、资产库等）
- 输出结果是什么？（报告、工单、通知、打标签等）

**流程要求**
- 有没有需要特殊处理的条件分支？（如高危 vs 低危、内部 IP vs 外部 IP）
- 对运行时间有要求吗？（实时响应 < 30s？批量处理可接受数分钟？）
- 有哪些已知的"陷阱"或边界情况需要注意？

### 1.2 输出思考维度与流程简图

对话完成后，在消息中输出以下内容供用户确认：

**思考维度总结**（结构化列举，涵盖：数据流、工具调用链、分支逻辑、异常处理、性能关键点、可扩展性）

**流程简图**（使用 Mermaid flowchart 语法，清晰展示节点与边关系）

示例格式：
```
## 思考维度

**数据流**：输入告警 → 资产丰富 → 情报查询 → LLM 分析 → 报告输出
**分支逻辑**：IP 类型判断（内网 / 外网）→ 不同查询策略
**性能关键点**：情报查询可并发；LLM 调用是主要耗时节点
**异常处理**：工具调用失败时降级到日志记录，不中断流程
**可扩展性**：后续可插入 SOAR 工单节点

## 流程简图

\`\`\`mermaid
flowchart TD
    A[接收告警] --> B[提取 IP/域名]
    B --> C{IP 类型?}
    C -->|内网| D[查询资产库]
    C -->|外网| E[查询威胁情报]
    D --> F[汇总上下文]
    E --> F
    F --> G[LLM 分析]
    G --> H[生成报告]
\`\`\`
```

### 1.3 获取样例数据

在流程简图得到用户认可后，请用户上传一条完整的样例输入数据（JSON 格式）：

- 若用户能提供：直接使用
- 若用户无法提供：根据场景自动构造一条最小可用样例 JSON，并请用户确认字段和数值是否合理

> 样例数据将用于后续每个节点的逐步测试，是测试阶段的核心依据。获得样例后，待工作流 ID 确定时调用 `POST /api/workflow/{id}/sample-inputs` 保存（body: `{ "sampleInputs": <样例 JSON 对象> }`）。

---

## 2. 第二阶段：简化版 JSON 预览与确认循环

> 目标：在投入完整代码编写之前，让用户在页面上直观地看到流程图，并就节点/边的设计提出修改建议。

### 2.1 生成简化版 workflow.json

生成一份**只有结构、没有代码**的简化 JSON 文件：

- 每个节点使用 `type="logic"`（只需 `description`，无需 `code`）
- 包含节点的 `id`、`name`（可读名称）、`description`（功能说明）
- 包含完整的 `edges`（`from`、`to`、`label`）
- **不包含任何 Python 代码**

简化 JSON 最小结构示例：

```json
{
  "id": "alert_triage",
  "name": "告警分级调查",
  "description": "自动化 NDR 告警调查工作流",
  "start": "receive_alert",
  "nodes": [
    {
      "id": "receive_alert",
      "type": "logic",
      "name": "接收告警",
      "description": "接收输入告警，提取 IP、端口、协议等关键字段"
    },
    {
      "id": "check_ip_type",
      "type": "branch",
      "name": "判断 IP 类型",
      "description": "判断源 IP 是内网地址还是外网地址，分支处理"
    },
    {
      "id": "query_threat_intel",
      "type": "logic",
      "name": "查询威胁情报",
      "description": "调用威胁情报工具查询外部 IP 的恶意评分、标签"
    },
    {
      "id": "generate_report",
      "type": "logic",
      "name": "生成分析报告",
      "description": "汇总所有上下文，由 LLM 生成结构化调查报告"
    }
  ],
  "edges": [
    { "from": "receive_alert", "to": "check_ip_type", "order": 0 },
    { "from": "check_ip_type", "to": "query_threat_intel", "label": "外网", "order": 0 },
    { "from": "query_threat_intel", "to": "generate_report", "order": 0 }
  ]
}
```

### 2.2 写入文件并展示

1. 将简化 JSON 写入规范目录下的 `workflow.json`（**必须使用绝对路径**，见第 9 节）：用户级为 `~/.flocks/plugins/workflows/<id>/`，项目级为 `<workspace>/.flocks/plugins/workflows/<id>/`
2. 在消息中告知用户：「已更新流程图，请在工作流页面查看。对节点名称、描述或流程结构有什么修改建议？」

### 2.3 用户反馈循环（循环直至满意）

收集用户的修改建议，按照以下循环执行，**直到用户确认满意**：

```
接收用户反馈
  ↓
分析修改需求（增/删节点、改描述、调整边关系）
  ↓
更新简化 JSON
  ↓
重新写入文件
  ↓
向用户展示更新摘要，询问是否满意
  ↓
[满意] → 进入第三阶段
[还有修改] → 重新循环
```

> **提示**：前端检测到 `workflow.json` 更新后会自动刷新流程图，用户无需手动刷新。

---

## 3. 第三阶段：生成完整 workflow.md（人读描述）

> 以第一阶段确认的流程结构为基础，生成**操作手册级别**的详细流程文档。

### 核心要求

每个步骤必须包含：

- **输入/输出**：数据来源、格式、用途。
- **处理逻辑**：具体操作步骤、判定条件、循环方式、异常处理。
- **工具/LLM 标注**：明确该步是 Tool-driven 还是 LLM-driven（详细决策指南见 [reference.md § Tool vs LLM](references/reference.md#5-tool-vs-llm-决策指南)）。
  - **推荐组合**：`tool.run_safe(...)` 获取数据 → `llm.ask(...)` 分析 → `tool.run('write', ...)` 落盘。
  - **默认使用 `tool.run_safe()`**，返回 `{"success", "text", "obj", "error"}` 统一包络。
- **文件落盘**：节点有任何文件输出时，统一写入 `~/.flocks/workspace/outputs/<YYYY-MM-DD>/` 目录下，详见 [reference.md § 文件输出规则](references/reference.md#6-文件输出规则)。
- **决策分支**：写清条件、各分支处理、跳转规则。
- **报告结构**（若涉及）：除非用户要求简化，需包含摘要、分析、发现、建议、来源（模板见 [reference.md § 报告生成](references/reference.md#7-报告生成最佳实践)）。

### ⚠️ 两步交付

1. 先用 `write` 工具将 `workflow.md` **写入文件**（路径与第 9 节一致，例如 `.../plugins/workflows/<id>/workflow.md`）。
   - **⚠️ 路径必须使用绝对路径**：全局目录可用 `python3 -c "import os; print(os.path.expanduser('~/.flocks/plugins/workflows/<id>'))"`；项目目录可先解析 workspace（从 cwd 向上第一个含 `.flocks` 的目录）再拼接 `/.flocks/plugins/workflows/<id>`。
   - **严禁**使用未展开的相对路径（如 `.flocks/plugins/workflows/<id>/` 相对仓库根随手写入错误位置），否则 WebUI 可能无法从实际扫描目录读到文件。
2. 写入成功后，用 `Question` 工具向用户展示流程摘要并请求确认（"确认工作流" / "修改工作流"）。确认后进入第四阶段生成 `workflow.json`。

---

## 4. 第四阶段：生成完整 workflow.json（机器执行）

根据 `workflow.md` 生成严格可执行的 `workflow.json`。**生成前建议读取 [references/reference.md](references/reference.md)**。

### 4.0 节点生成策略

- **主路径**：每个可执行步骤 → `type="python"` 节点，必须同时包含 `code`（执行逻辑）+ `description`（文档说明）。
- **兜底**：`logic` 节点仅在用户明确要求"不写代码"或快速原型时使用，运行时由 codegen 兜底。

### 4.1 运行时硬约束

**顶层字段：**

- `start` 必须等于某个 `nodes[i].id`
- `nodes[].id` 必须唯一
- `name`/`description`（可选）用于工作流级别说明
- `version` 会被运行时忽略，不需要生成

**Node 约束**（对应 `flocks/workflow/models.py`）：

- `python`：`code` 必须非空
- `logic`：`description` 必须非空
- **出边选择行为**（关键）：`python` → 所有出边触发；`logic`/`branch`/`loop` → 通过 `select_key` 取值做 label 匹配选边
- `join=true`：等待所有入边到齐再执行一次

**代码约束：**

- 同步 `exec()` 模型，**严禁** `await`/`async def`/`async for`/`async with`。确保节点的代码要可以独立运行。

**Edge 约束：**

- JSON 中用 `"from"` 而非 `"from_"`；`from`/`to` 引用存在的 node id；`order` ≥ 0。

### 4.2 映射规则

- `workflow.md` 每步对应一个节点，`id` 用 snake_case。
- md 中写的输出字段，必须在 `outputs[...]` 中体现。
- md 中 `Tool: xxx` 标记 → 对应节点 `description` 保留。
- 下游节点如需 `tool.run(..., **inputs)`，用 `edge.mapping`/`edge.const` 规整输入到匹配工具参数形状。
- 详细 Mapping 指南见 [reference.md § Edge Mapping](references/reference.md#4-edge-mapping-详细指南)。

### 4.3 分支/循环与 Join

- **branch/loop 选边**：`bool` 值 label 用 `"true"`/`"false"`；`str` 值精确匹配；无命中回退到空 label 默认边。上游必须把 `select_key` 所需字段写入 payload。
- **分支汇合（强制）**：
  - 多入边且非互斥 → **必须** `join=true`
  - 判断互斥：所有入边来自同一 branch/loop 的不同 label 出边
  - **昂贵节点保护**：含 `llm.ask()` 或 `tool.run('write', ...)` 的节点，禁止被两条非互斥路径直达，必须先经 join 节点
  - 推荐模式：join 节点（python, `join=true`）归一化多分支输出 → 再传给后续步骤
- **嵌套工作流**：见 [references/composition.md](references/composition.md)。

### 4.4 代码实现

**辅助函数：**

| 函数 | 说明 |
|------|------|
| `tool.run(name, **inputs)` | 返回 `ToolResult.output`（类型 `Any`，**通常是字符串**），失败抛异常 |
| `tool.run_safe(name, **inputs)` | **推荐**，返回 `{"success": bool, "text": str, "obj": Any, "error": str\|None}`，永不抛异常 |
| `llm.ask(prompt)` | 调用 LLM，返回字符串 |
| `get_path(path)` | payload 深层取值 |

**⚠️ 返回值类型警告（常见 Bug 源）：**

- **`tool.run()` 返回的是 `Any` 类型**，大多数工具返回的是**字符串**（格式化文本），**不是字典**。**严禁**直接对返回值调用 `.get()` 等字典方法。
- **`tool.run_safe()["obj"]` 也是 `Any` 类型**，可能是 `str`、`dict`、`list` 或 `None`。使用前**必须检查类型**。
- 如果工具返回的是 JSON 格式的字符串，需要用 `json.loads()` 解析后再作为字典操作。

```python
# ❌ 错误：直接在 tool.run() 返回值上调用 .get()
result = tool.run('some_tool', ip=ip)
value = result.get("key")  # AttributeError: 'str' object has no attribute 'get'

# ❌ 错误：假设 obj 一定是 dict
result = tool.run_safe('some_tool', ip=ip)
value = result["obj"].get("key")  # obj 可能是 str，同样报错

# ✅ 正确：使用 text 做字符串操作
result = tool.run_safe('some_tool', ip=ip)
outputs["text"] = result["text"]  # text 永远是 str，安全

# ✅ 正确：需要结构化数据时，先检查 obj 类型
result = tool.run_safe('some_tool', ip=ip)
obj = result["obj"]
if isinstance(obj, dict):
    value = obj.get("key")
elif isinstance(obj, str):
    import json
    try:
        parsed = json.loads(obj)
        value = parsed.get("key") if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        value = None
```

**`tool.run_safe()` 使用指南：**

- 字符串拼接 / LLM prompt 插值 → `result["text"]`（永远是 `str`，最安全）
- 结构化对象取值 → `result["obj"]` + **`isinstance` 类型检查** + `result["success"]` 判断
- **生成 code 时默认使用 `tool.run_safe()`**，且优先使用 `result["text"]`
- 仅在明确知道工具返回类型且无需兜底时，才用 `tool.run()`（仍需注意返回值可能是字符串）

**数据落盘与传递：**

- **文件输出**：有文件输出时写入 `~/.flocks/workspace/outputs/<YYYY-MM-DD>/`（详见全局文件输出约定）
- **数据传递**：`inputs` 和 `outputs` 字典，运行时浅合并 `payload = {**inputs, **outputs}`

> **⚠️** 生成后必须使用 `write` 写入到文件，并验证：1) `json.load` 确认 JSON 格式正确；2) 对每个 `type="python"` 节点的 `code` 执行 `compile(code, "<node_id>", "exec")` 确认 Python 语法正确。若语法报错，修复后重新写入。

---

## 5. 第五阶段：逐节点测试与修复

> **⚠️ 核心原则：绝不轻易停止。** 无论遇到什么错误，都必须持续分析、修复、重试，直到每个节点都运行正常为止。

`workflow.json` 生成并通过语法验证后，**必须执行本阶段**，确保每个节点可以真实运行。

### 5.0 准备样例数据

使用阶段 1 收集并保存的样例输入（可通过 `GET /api/workflow/{id}/sample-inputs` 获取）。若样例数据不存在，用 `Question` 工具向用户索取后保存。

### 5.1 逐节点顺序测试（严格按拓扑顺序，一个节点一个节点地来）

**必须严格按照拓扑顺序（从 `start` 开始，沿出边逐步推进），一次只测试一个节点，当前节点完全通过后才能测试下一个。**

使用 **`run_workflow_node` 工具**（内置工具，直接可用）执行每个节点：

```
run_workflow_node(
  workflow = "/absolute/path/to/workflow.json",   # 或直接传 workflow dict
  node_id  = "<node_id>",
  inputs   = <inputs_dict>
)
```

返回结构：`{ node_id, outputs, stdout, error, traceback, duration_ms, success }`

> **⚠️ 注意**：使用 `run_workflow_node` 工具，**不要**用 `run_workflow`（那是全量运行）。

**执行规则（逐节点）：**

- **第一个节点**的 `inputs` = 用户样例数据（阶段 1 收集的样例 JSON）
- **后续节点**的 `inputs` = **前一个成功节点返回的 `outputs`**（若节点有多个前驱，合并所有前驱的 outputs）
  - 严禁跳过任何节点、严禁用人工构造的数据替代前驱节点的真实输出
- **当前节点 `success=false` 或 `error` 不为 null 时，必须原地 Debug，循环直到通过，才能进入下一节点：**
  1. 仔细分析 `error`、`traceback`、`stdout` 字段，定位根本原因
  2. 修复该节点的 `code`（或 edge mapping、输入字段名等）
  3. 重写 `workflow.json`（保存到磁盘）
  4. 再次用 `run_workflow_node` 测试**同一节点**（`inputs` 不变）
  5. 重复步骤 1–4，**直到该节点 `success=true`**，才能推进到下一节点
- **`success=true` 不等于节点真正通过**，还必须检查输出数据是否有实际内容：
  - **检查 `outputs` 中的关键字段**：若关键字段为空字符串、`null`、空列表 `[]`、空字典 `{}` 或全为默认占位值，**必须调查原因**
  - **判断是否有合理的业务原因**导致为空，若有合理理由则记录说明并继续
  - **若无合理理由，视为节点未真正通过**，需要修复并重跑
- **每个节点真正通过后（success=true 且关键输出非空），记录 `duration_ms`**：

  ```
  ✅ 节点 <node_id>：success=true，关键输出非空，耗时 <duration_ms> ms
  ```

---

## 6. 第六阶段：集成测试（全量运行）

所有节点逐一通过后，执行一次完整的工作流运行：

```
POST /api/workflow/{id}/run
Body: { "inputs": <样例数据> }
```

若全量运行失败，继续按第五阶段的方式定位失败节点并修复，直到全量运行成功。**全量运行失败不是停止的理由，必须继续 Debug 直到通过。**

### 6.1 运行时间汇总

全量运行成功后，汇总并输出完整的节点耗时报告：

```
📊 运行时间汇总
─────────────────────────────────
节点 <node_id_1>：<duration_ms> ms
节点 <node_id_2>：<duration_ms> ms
节点 <node_id_N>：<duration_ms> ms
─────────────────────────────────
总计（逐节点累计）：<sum> ms
全量运行总耗时：<total_ms> ms（来自 POST /run 响应）
```

---

## 7. 第七阶段：性能评估与优化

> 目标：识别耗时瓶颈节点，在不影响正确性的前提下尽可能优化。

### 7.1 瓶颈识别

基于第六阶段的耗时报告，识别以下类型的慢节点（按优先级排序）：

| 类型 | 判断标准 | 常见原因 |
|------|---------|---------|
| **超慢节点** | `duration_ms > 10000`（10s）| LLM prompt 过长、串行工具调用、无效重试 |
| **慢节点** | `duration_ms > 3000`（3s）| 单次工具调用本身耗时、数据处理低效 |
| **占比失衡** | 单节点 > 总时间 50% | 该节点是整体瓶颈，优先优化 |

### 7.2 优化策略（按场景选取）

**LLM 节点优化**
- 裁剪 prompt：移除冗余上下文，只传关键字段
- 压缩中间数据：用摘要替代全量文本传递给 LLM
- 拆分职责：将一个大 LLM 节点拆成多个更小的专项分析节点

**工具调用优化**
- 并行化：将互不依赖的多个工具调用改为并发执行（用 `concurrent.futures.ThreadPoolExecutor`）
- 结果缓存：对同一参数的重复查询，在 `outputs` 中缓存结果避免重复调用
- 精简参数：只查询当前节点实际需要的字段

**数据传递优化**
- 裁剪 payload：在 edge mapping 中只传下游需要的字段，减少不必要的数据搬运
- 提前过滤：在数据进入 LLM 节点前，用工具节点做结构化过滤

### 7.3 实施优化

对每个识别出的瓶颈节点：

1. 在消息中说明**优化方案和预期收益**（如「将情报查询由串行改为并发，预计节省 60% 时间」）
2. 修改 `code` 实现优化
3. 重新运行该节点验证：
   - `success=true` 且关键输出不变
   - `duration_ms` 有明显下降（> 20% 提升才视为有效优化）
4. 若优化未达预期或引入新问题，回滚并记录原因
5. 所有优化完成后，重新执行全量运行，输出优化后的汇总耗时报告

### 7.4 优化报告

优化完成后输出对比报告：

```
⚡ 性能优化报告
─────────────────────────────────────────────
节点              优化前(ms)  优化后(ms)  提升
<node_id_1>       3200        800         75%  ← 并发查询
<node_id_2>       8500        4200        51%  ← 精简 prompt
─────────────────────────────────────────────
全量运行总耗时：<before> ms → <after> ms
```

---

## 8. 修改模式：修改已有工作流

当用户表达的是**修改/调整/优化已有工作流**（而非从零创建）时，进入本模式。

### 8.0 判断是否是修改请求

满足以下任一条件即为修改请求：

- 用户说"修改……"、"调整……"、"把……改成……"、"优化……"、"重构……"
- 已在 ChatTab 上下文中提供了完整 `workflow.json`
- 用户指向某个具体工作流 ID 或名称，要求改动其中某些节点或逻辑

### 8.1 准备

1. **读取现有文件**：优先使用 ChatTab 上下文中已提供的 JSON；若需要也可用 `read` 工具读取 `workflow.json` 和 `workflow.md`。
2. **理解修改意图**：若意图不清晰，先用 `Question` 工具确认：
   - 需要增/删/改哪些节点？
   - 数据流、出边逻辑是否需要同步调整？

### 8.2 先更新 workflow.md（必须）

**修改模式下，必须先更新 MD 文档，经用户确认后再修改 JSON。严禁直接跳到 JSON 修改。**

1. 根据修改意图，更新 `workflow.md`（保留原有结构，仅修改受影响的步骤描述）。
2. 用 `write` 工具将更新后的 `workflow.md` **写入文件**（路径：与同 ID 的 `workflow.json` 所在目录一致，见第 9 节）。
3. 用 `Question` 工具向用户展示变更摘要，询问确认。
4. 用户确认后，进入 8.3 生成变更。

### 8.3 生成变更

**优先最小化变更原则：**

- **单节点改动** → 使用 `edit` 工具精准替换目标字段
- **多节点改动 / 结构重组** → 整体覆写

**遵守所有 workflow.json 约束**（见第 4 节规范）。

### 8.4 验证与写回

修改完成后：
1. `json.load` 确认 JSON 格式正确
2. 对每个 `type="python"` 节点的 `code` 执行 `compile` 验证语法
3. 若验证通过，写入原文件路径

### 8.5 说明变更内容

写回成功后，向用户简述做了哪些改动（diff 式自然语言说明）。

---

## 9. 工作流文件保存目录（创建模式）

### 创建路径（写入）

新建/修改工作流时，写入路径须与 **`flocks/workflow/center.py`**、**`flocks/server/routes/workflow.py`** 采用的规范目录一致（**`plugins/workflows/`**），不要用已退居兼容用途的旧目录作为主落点：

- **用户级（全局）**：`~/.flocks/plugins/workflows/<slug-or-folder>/`（`workflow.json`、`workflow.md`、`meta.json` 由 API 写入时可能同目录）
- **项目级**：`<workspace>/.flocks/plugins/workflows/<slug-or-folder>/`，其中 `<workspace>` 为从当前工作目录向上查找时**第一个**含有 `.flocks` 的目录（与服务端 `_find_workspace_root()` 一致）；若需用户级、与仓库无关的工作流，则用上面的「用户级」路径。
  - ⚠️ 任务输出（报告、artifacts）**不**写入此目录，统一写入 `~/.flocks/workspace/outputs/<YYYY-MM-DD>/`（见全局文件输出约定）

> **说明**：历史上 workflow-builder 曾统一写 `~/.flocks/workflow/<id>/`，因此你会看到旧数据在该路径下；**当前代码在扫描与 API 落盘上以 `plugins/workflows/` 为规范路径**（`~/.flocks/workflow/`、`plugins/workflow/` 等仍为兼容扫描路径，且优先级更低）。

### 读取路径（扫描）

系统会按**从低到高优先级**扫描下列目录（同一逻辑 ID 冲突时**后扫描的覆盖先扫描的**）。写文件时应优先落在列表中**最后的「规范」目录**，避免被后续扫描覆盖或混淆。

**全局（用户目录下）**

| 优先级（低→高） | 路径 | 说明 |
|---|---|---|
| 1 | `~/.flocks/plugins/workflow/` | 全局 legacy |
| 2 | `~/.flocks/workflow/` | 全局旧主路径（兼容） |
| 3 | `~/.flocks/plugins/workflows/` | **全局规范路径（推荐新工作流露地）** |

**项目（workspace 下）**

| 优先级（低→高） | 路径 | 说明 |
|---|---|---|
| 1 | `<workspace>/.flocks/plugins/workflow/` | 项目 legacy |
| 2 | `<workspace>/.flocks/workflow/` | 项目旧路径（兼容） |
| 3 | `<workspace>/.flocks/plugins/workflows/` | **项目规范路径（推荐新工作流露地）** |

### ⚠️ 绝对路径规范（重要）

**必须使用绝对路径写入文件**。

全局目录示例：

```bash
python3 -c "import os; print(os.path.expanduser('~/.flocks/plugins/workflows/<folder>'))"
```

解析项目 workspace 并拼接规范目录示例：

```bash
python3 -c "from pathlib import Path; p=Path.cwd(); ws=next((x for x in [p,*p.parents] if (x/'.flocks').is_dir()), p); print(ws/'.flocks/plugins/workflows/<folder>')"
```

**正确示例**：
- `<HOME_DIR>/.flocks/plugins/workflows/alert_triage/workflow.json` ✅（用户级）
- `<WORKSPACE>/.flocks/plugins/workflows/phishing-email-detection/workflow.json` ✅（项目级）

**错误示例**：
- `.flocks/plugins/workflows/alert_triage/workflow.json` ❌（未展开相对路径，易写错磁盘位置）
- 仅因习惯写入 `~/.flocks/workflow/...` 作为**新**工作流首选 ❌（仍可被扫描，但与当前规范及 API 默认落盘不一致）

---

## 10. 持续执行原则（全局强制）

**在整个 Workflow 创建流程中，以下原则不可违反：**

1. **绝不中途放弃**：任何阶段遇到错误，都必须持续分析原因、尝试不同修复方式，循环重试直到解决。
2. **失败不是终点，是 Debug 的起点**：连续失败时，换思路（检查参数名、输入数据结构、工具调用方式、边的 mapping 等），直到找到根本原因。
3. **Todo 驱动完成**：所有任务项在 Todo 列表中清晰可见，必须逐一完成、逐一标记，**严禁在 Todo 全部 completed 之前宣布任务完成**。
4. **只有以下情况才能停下来询问用户**：
   - 样例数据缺失且无法自动构造
   - 需要用户提供必要的外部凭证或配置（API Key、服务地址等）
   - 需要用户确认流程描述是否正确（场景确认阶段、md 确认阶段）
   - 其他**必须由用户决策**的内容
5. **除上述情况外，所有问题必须自行解决，直到工作流完美运行为止。**

---

## workflow.md 标准模板

```markdown
# [Workflow Name]

## 业务场景
[目标和背景]

## 流程步骤

### 1. [步骤名称]
- **描述**: [操作手册级别描述]
- **工具/模型**: [Tool: xxx / LLM: xxx]
- **输入**: [字段名: 来源和格式]
- **输出**: [字段名: 格式和用途]
- **处理逻辑**:
  - [操作步骤]
  - [工具调用：`result = tool.run_safe('name', ...)`，用 `result["text"]` 取结果]
- **决策分支**（如适用）:
  - 条件 → 分支处理

### 2. [步骤名称]
...
```
