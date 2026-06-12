# 嵌套工作流（Subworkflow Composition）

> 仅在用户需要嵌套工作流时参考本文件。

## 概念

- **嵌套只是"编译期"概念**：把子工作流当作父工作流的一个节点来建模。
- **运行时引擎只执行 `workflow.json`**（`python`/`logic`/`branch`/`loop`），不理解子工作流。
- 产物层面需输出两份 JSON：
  - `workflow.composition.json`：描述"父工作流如何调用子工作流"的组合格式
  - `workflow.json`：展开后的可执行 workflow

## 组合格式（composition v1）

字段名严格一致：

```json
{
  "format": "flocks-workflow-composition-v1",
  "name": "your_workflow_name",
  "nameI18n": {
    "zh-CN": "你的工作流名称",
    "en-US": "Your Workflow Name"
  },
  "start": "node_id",
  "nodes": [
    {
      "id": "call_A",
      "type": "subworkflow",
      "workflow_path": "../A/workflow.json",
      "return_node": "some_node_in_A",
      "stop_at_return_node": true
    },
    { "id": "branch_1", "type": "branch", "select_key": "is_false_positive" },
    { "id": "step_x", "type": "python", "code": "..." }
  ],
  "edges": [
    { "from": "call_A", "to": "branch_1", "mapping": { "alert_id": "alert_id" } }
  ]
}
```

## 展开规则

生成 `workflow.json` 时必须遵守：

1. 每个 `subworkflow` 节点 → 内联为子工作流的所有节点与边
2. 子工作流节点/边 ID 加前缀：`<subworkflow_node_id>.`（例如 `call_A.fetch_alert`）
3. 父图边指向 `subworkflow` 节点 → 实际连到子工作流的 `start` 节点
4. 父图边从 `subworkflow` 节点发出 → 实际从子工作流的 `return_node` 发出
5. `stop_at_return_node=true` → 子工作流中 `from == return_node` 的出边在展开时移除

## 最佳实践

- 在父工作流中添加 1-2 个"汇总节点"（python 节点）整理子工作流输出为命名空间字段（如 `triage_*`、`investigation_*`），避免字段冲突
- `return_node` 必须选择一个确定会执行到的节点（如分支汇合后的汇总节点）
- 子工作流在 `return_node` 后还有副作用节点时 → 设置 `stop_at_return_node=true`
