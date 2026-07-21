# Workflow Generator 详细参考

> 本文件包含 workflow 生成的详细约束、示例和模板。SKILL.md 中有引用指向此文件的各节。

## 目录

1. [节点类型详解](#1-节点类型详解)
2. [出边选择行为](#2-出边选择行为)
3. [分支/循环/Join 详细规则](#3-分支循环join-详细规则)
4. [Edge Mapping 详细指南](#4-edge-mapping-详细指南)
5. [Tool vs LLM 决策指南](#5-tool-vs-llm-决策指南)
6. [文件输出规则](#6-文件输出规则)
7. [报告生成最佳实践](#7-报告生成最佳实践)
8. [LLM 调用规范](#8-llm-调用规范)
9. [workflow.json 骨架模板](#9-workflowjson-骨架模板)
10. [工具调用示例](#10-工具调用示例)

---

## 1. 节点类型详解

| 类型 | 必填字段 | 行为说明 |
| :--- | :--- | :--- |
| **python** | `code` | 主路径。执行 Python 代码，操作 `outputs` 字典。建议同时提供 `description`。 |
| **logic** | `description` | 快速原型/兜底。支持内嵌代码块；无代码时由运行时 codegen 生成。 |
| **branch** | `select_key` | 根据 `select_key` 值匹配 `edge.label` 跳转。 |
| **loop** | `select_key` | 语义同 branch，用于循环（继续/退出）。 |

---

## 2. 出边选择行为

这是引擎 `_select_edges` 的真实行为，**生成时必须遵守**：

| 节点类型 | 出边选择方式 |
| :--- | :--- |
| `python` | 执行后**所有出边**都触发（不做 label 匹配） |
| `logic` | 走 **label 匹配**（与 branch/loop 相同）。通过 `select_key`（默认 `"result"`）从 payload 取值选边。多出边时必须设置 `label` 和 `select_key` |
| `branch`/`loop` | 不执行 `code`，通过 `select_key`（默认 `"result"`）取值选边 |

**label 匹配规则**：
- `bool` 值：label 必须用 `"true"` / `"false"`
- `str` 值：label 必须与该字符串完全一致
- `None` 或无命中：回退到空 label 默认边（最多 1 条）

---

## 3. 分支/循环/Join 详细规则

### 分支生成

**在 workflow.md 中描述**：
```markdown
### X. [步骤名称]
- **决策分支**:
  - 条件：`if risk_level == "High"`
  - 分支1（高风险）：执行操作 A
  - 分支2（其他）：执行操作 B
```

**在 workflow.json 中实现**：
```json
{
  "id": "check_risk",
  "type": "branch",
  "select_key": "risk_level",
  "description": "根据风险等级进行分支判断"
}
```
出边示例：
```json
{
  "from": "check_risk",
  "to": "handle_high_risk",
  "label": "High",
  "mapping": {
    "case_id": "case_id",
    "risk_level": "risk_level"
  }
},
{
  "from": "check_risk",
  "to": "handle_normal",
  "label": "",
  "mapping": {
    "case_id": "case_id",
    "risk_level": "risk_level"
  }
}
```

### Join 节点

通过 `join: true` 标记。引擎等待所有入边到达后合并 payload 并执行一次。
- `join_mode`: `flat`（默认）或 `namespace`
- `join_conflict`: `overwrite`（默认）或 `error`

### 分支汇合强制规则

1. **多入边汇聚 → 必须 `join=true`**：节点有 ≥2 条来自不同源的入边，且非互斥分支时必须设置 `join=true`。
2. **互斥判断标准**：所有入边来自同一 branch/loop 节点的不同 label 出边 → 互斥，不需要 join。
3. **昂贵节点保护**：含 `llm.ask()` 或 `tool.run('write', ...)` 的节点，禁止被两条非互斥路径直达。必须先经 `join=true` 汇合节点。
4. **推荐模式**：在汇合点放一个 python 节点（`join=true`），归一化多分支输出为统一格式，再传给后续步骤。

---

## 4. Edge Mapping 详细指南

- `edge.mapping`：字段传递与重命名（下游 key → 上游 payload 路径）
- `edge.const`：注入常量参数
- **点路径支持**：`mapping: { "user_id": "data.user.id" }`
- **根路径引用**：`mapping: { "full_data": "$" }`

### 新建 workflow 的强制规则

- **每条 edge 都必须写非空 `mapping` 对象**。新建 workflow 默认启用 strict edge mapping；无 `mapping` 的 edge 会被创建/运行校验拒绝。
- `const` 只能补充常量，不能替代 `mapping`。即使只需要常量参数，也要映射一个确定存在的小字段作为显式数据契约。
- 默认只映射下游节点实际读取的字段，不要用 `$` 传递完整 payload，除非下游确实需要完整对象且该对象已经是上游刻意裁剪过的小对象。
- 字段需要重命名时，用 `mapping` 把下游 key 映射到上游 payload 路径。
- 下游要 `tool.run(..., **inputs)` 时，用 `edge.mapping`/`edge.const` 把 inputs 规整到工具参数形状。
- 控制流边如果下游不需要业务字段，也必须映射一个确定存在的小字段（如 `case_id`、`has_results`、`status`）；如果没有合适字段，让上游节点写出 `outputs["_edge_context"] = True`，并映射 `{ "_edge_context": "_edge_context" }`。
- `branch`/`loop` 出边同样必须写 `mapping`。映射源可以来自分支节点收到的输入 payload，例如 `{ "search_text": "search_text" }`。

### 禁止生成的 edge

```json
{ "from": "step_1", "to": "step_2" }
```

上面这种无 `mapping` 的 edge 禁止出现在新建 workflow 中。

### 避免脆弱映射

- 不要映射"上游不一定产出的 key"（尤其是 `logic` 节点推断输出），否则下游拿不到该字段会 KeyError
- 传递对象时确保上游一定写出 `outputs["xxx"]`；否则按扁平 key 逐个映射

---

## 5. Tool vs LLM 决策指南

每步明确标注 Tool-driven 还是 LLM-driven。

### 决策优先级（高→低）

1. **用户显式指定时必须遵守**：工具名/参数与可用 schema 不一致时提出替代方案。

2. **优先 Tool 的场景**（确定性优先）：
   - 检索/枚举/定位（搜索代码、查文件、列目录、抓网页）
   - 读写/转换/格式化（写文件、生成 JSON、字段变换）
   - 参数严格对齐的调用

3. **使用 LLM 的场景**（需语言/推理能力时）：
   - 总结/改写/抽取（对工具结果做摘要、报告）
   - 需策略判断且无法规则化

4. **推荐组合**：`tool.run_safe(...)` 获取数据 → `llm.ask(...)` 分析 → `tool.run('write', ...)` 落盘

5. **禁止**：不要用 LLM 臆测可通过工具核验的事实

---

## 6. 文件输出规则

节点有任何文件输出时，统一写入 `~/.flocks/workspace/outputs/<YYYY-MM-DD>/` 目录下。

- 日期在**执行时**动态取（`datetime.date.today().isoformat()`），不依赖 session 启动时的注入值
- 目录通过 `WorkspaceManager.get_instance().get_workspace_dir() / 'outputs' / date_str` 构造
- **禁止**使用项目相对路径（如裸 `artifacts/`），会落到项目根目录污染代码仓库

---

## 7. 报告生成最佳实践

默认生成详细结构化报告，除非用户明确要求简化。

### 标准报告结构

```markdown
# [报告标题]

## 执行摘要
[1-2 段概述关键发现和结论]

## 详细分析
[按维度展开，如：告警详情、威胁情报、内部上下文]

## 关键发现
- 发现点 1：具体描述
- 发现点 2：具体描述

## 风险评估
- 风险等级：[Low/Medium/High/Critical]
- 风险说明：[详细说明]

## 建议与行动项
1. [具体建议 1]
2. [具体建议 2]

## 数据来源
- [使用的数据源和工具]
```

报告生成节点的 `description` 应明确包含：报告章节、信息类型、格式要求（Markdown）、详细程度。

---

## 8. LLM 调用规范

- 在代码中构造 prompt，使用 `llm.ask(prompt)` 调用
- 若需结构化 JSON 输出，在 prompt 中要求"纯 JSON 字符串"，并用 `json.loads` 解析
- `llm.ask()` 支持 `temperature` 参数（可选）

---

## 9. workflow.json 骨架模板

最小可执行 workflow 结构：

```json
{
  "name": "my_workflow",
  "nameI18n": {
    "zh-CN": "我的工作流",
    "en-US": "My Workflow"
  },
  "description": "工作流用途说明（可选）",
  "start": "step_1",
  "nodes": [
    {
      "id": "step_1",
      "type": "python",
      "description": "获取输入数据并调用工具",
      "code": "query = inputs.get('query', '')\nresult = tool.run_safe('websearch', query=query)\noutputs['search_text'] = result['text']\noutputs['has_results'] = result['success'] and len(result['text']) > 0"
    },
    {
      "id": "check_results",
      "type": "branch",
      "select_key": "has_results",
      "description": "根据搜索是否有结果分支"
    },
    {
      "id": "summarize",
      "type": "python",
      "description": "使用 LLM 生成摘要并落盘",
      "code": "import os, datetime\nfrom flocks.workspace.manager import WorkspaceManager\nsearch_text = inputs.get('search_text', '')\nprompt = f'请总结以下内容：\\n{search_text}'\nsummary = llm.ask(prompt)\nws = WorkspaceManager.get_instance()\noutput_dir = str(ws.get_workspace_dir() / 'outputs' / datetime.date.today().isoformat())\nos.makedirs(output_dir, exist_ok=True)\ntool.run('write', filePath=os.path.join(output_dir, 'summarize_output.md'), content=summary)\noutputs['summary'] = summary"
    },
    {
      "id": "fallback",
      "type": "python",
      "description": "无结果时的兜底处理",
      "code": "outputs['summary'] = '未找到相关结果'"
    }
  ],
  "edges": [
    {
      "from": "step_1",
      "to": "check_results",
      "mapping": {
        "search_text": "search_text",
        "has_results": "has_results"
      }
    },
    {
      "from": "check_results",
      "to": "summarize",
      "label": "true",
      "mapping": {
        "search_text": "search_text"
      }
    },
    {
      "from": "check_results",
      "to": "fallback",
      "label": "false",
      "mapping": {
        "has_results": "has_results"
      }
    }
  ]
}
```

---

## 10. 工具调用示例

> 以下为示例模式，实际工具名和参数以 `registry.py` 为准。

### ⚠️ 返回值类型陷阱（必读）

`tool.run()` 返回 `ToolResult.output`，类型是 `Any`。**大多数工具返回字符串**（格式化文本），少数返回 dict/list。**严禁假设返回值是 dict 并直接调用 `.get()`**。

```python
# ❌ 常见错误：导致 AttributeError: 'str' object has no attribute 'get'
result = tool.run('threatbook_ip', ip=ip)
threats = result.get("threats")  # result 是 str，不是 dict！

# ❌ 常见错误：run_safe 的 obj 也可能是 str
result = tool.run_safe('threatbook_ip', ip=ip)
threats = result["obj"].get("threats")  # obj 可能是 str！

# ✅ 推荐写法：使用 text（永远是 str）
result = tool.run_safe('threatbook_ip', ip=ip)
outputs['intel_text'] = result['text']  # 安全

# ✅ 需要结构化数据时：先检查类型
result = tool.run_safe('threatbook_ip', ip=ip)
obj = result['obj']
if isinstance(obj, dict):
    outputs['threats'] = obj.get('threats', [])
elif isinstance(obj, str):
    import json
    try:
        parsed = json.loads(obj)
        outputs['threats'] = parsed.get('threats', []) if isinstance(parsed, dict) else []
    except json.JSONDecodeError:
        outputs['threats'] = []
else:
    outputs['threats'] = []
```

### 标准调用模式

**workflow.md 中的写法**：
```markdown
- **工具/模型**: Tool: websearch
- **处理逻辑**: 
  - 调用搜索：`result = tool.run_safe('websearch', query=inputs.get('query'))`
  - 取结果文本：`result["text"]`（成功时为搜索结果，失败时为空串）
  - ⚠️ 不要对 `result["obj"]` 直接调用 `.get()`，需先用 `isinstance` 检查类型
```

**workflow.json python 节点**：
```json
{
  "id": "search",
  "type": "python",
  "description": "Tool: websearch\n调用方式：tool.run_safe('websearch', query=...)\n成功时从 result['text'] 取搜索文本，失败时走兜底。",
  "code": "query = inputs.get('query', '')\nresult = tool.run_safe('websearch', query=query)\nif result['success']:\n    outputs['search_text'] = result['text']\nelse:\n    outputs['search_text'] = ''\n    outputs['error'] = result['error']"
}
```

### 工具参数对齐最佳实践

1. 在 `workflow.md` 的输入中直接使用工具参数名
2. 每条 edge 都写非空 `edge.mapping`，完成上游字段到下游节点或工具参数名的转换
3. python 节点中 `result = tool.run_safe("xxx", **inputs)` 或按需取参数
4. 仅快速原型时使用 `logic` 节点
5. **默认用 `result["text"]` 取结果**，仅在明确需要结构化数据且已做类型检查时才用 `result["obj"]`
