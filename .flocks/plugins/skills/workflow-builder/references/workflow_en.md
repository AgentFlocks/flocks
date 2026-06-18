# [workflow_id]

> This is an English `workflow.md` structure template. When creating a workflow, replace placeholders with real business content and write only the final result to `workflow.md` in the workflow directory.

## 1. Functional Overview

`[workflow_id]` is a [one-sentence description of the workflow type].

It mainly solves three things:

- [Goal 1: what information the workflow receives or organizes.]
- [Goal 2: how it processes, decides, filters, aggregates, or analyzes.]
- [Goal 3: what it outputs and who uses the result.]

Suitable scenarios:

- [Scenario 1]
- [Scenario 2]
- [Scenario 3]

Out of scope:

- [Boundary 1]
- [Boundary 2]
- Do not store plaintext secrets. Credentials, enable/disable state, and runtime configuration should be managed by configuration and storage.

## 2. Flow Map

The workflow runs in this order:

```text
[node_1] -> [node_2] -> [node_3] -> [final_node]
```

| Order | Node | Responsibility |
| --- | --- | --- |
| 1 | `[node_1]` | [Node responsibility] |
| 2 | `[node_2]` | [Node responsibility] |
| 3 | `[node_3]` | [Node responsibility] |

In plain terms:

```text
Raw input
  -> [first processing step]
  -> [second processing step]
  -> [third processing step]
  -> final output
```

## 3. Inputs

### 3.1 Input Modes

| Priority | Field | Type | Purpose |
| --- | --- | --- | --- |
| 1 | `[primary_input]` | `[type]` | [Primary input source] |
| 2 | `[secondary_input]` | `[type]` | [Optional input source] |

If multiple input fields are provided, the workflow should process `[primary_input]` first.

### 3.2 Common Parameters

| Parameter | Default | Description |
| --- | --- | --- |
| `[param_name]` | `[default]` | [Meaning] |

### 3.3 Input Example

```json
{
  "input": "replace with a representative sample"
}
```

## 4. Module Logic

### 4.1 [node_1]: [Node Name]

This node answers:

- [Question 1]
- [Question 2]

Processing logic:

1. [Step 1]
2. [Step 2]
3. [Step 3]

Tool/model:

- Type: Tool-driven / LLM-driven / Python rule
- Call: [tool name or model purpose; write none if not used]

Inputs:

| Field | Source | Description |
| --- | --- | --- |
| `[field]` | `[source]` | [Description] |

Outputs:

| Field | Description |
| --- | --- |
| `[output_field]` | [Description] |

Typical edit points:

- [Common edit point 1]
- [Common edit point 2]

### 4.2 [node_2]: [Node Name]

Describe every node with the same structure. The node descriptions must be clear enough for Flocks to generate a stable `workflow.json` from this document.

## 5. Outputs

The workflow mainly outputs these fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `[result]` | object | [Final result] |
| `[summary]` | string | [Summary] |

If the workflow writes files, write them under:

```text
~/.flocks/workspace/outputs/<YYYY-MM-DD>/
```

Do not write reports, debug files, or intermediate artifacts into the project code directory.

## 6. Publishing And Configuration

The publish page does not decide capabilities directly from `workflow.md`; it reads the `config.json` template and runtime state from storage.

Supported publishing or integration modes:

- API: [supported or not; path or purpose]
- Syslog: [supported or not; port, protocol, start/stop behavior]
- Kafka: [supported or not; topic, consumer group, start/stop behavior]
- Webhook: [supported or not; callback or ingestion behavior]
- Schedule: [supported or not; trigger cadence]

When editing publishing modes:

- Change the publish template in `config.json`.
- Change runtime start/stop state through the publish page and backend runtime state.
- Do not write plaintext API keys, passwords, or tokens into `workflow.md` or `config.json`.

## 7. How To Edit This Workflow

Use the target change to locate the right area:

| Change target | Edit first |
| --- | --- |
| Input fields or entry modes | `[entry_node]` |
| Field mapping or normalization | `[normalize_node]` |
| Decision, filtering, aggregation, or analysis rules | `[logic_node]` |
| Output fields or file format | `[output_node]` |
| Publishing and integration configuration | `config.json` |
| Flow structure, added nodes, or removed nodes | `workflow.md`, then regenerate `workflow.json` after confirmation |

Basic editing principles:

- If you change input fields, update the sample input.
- If you rename standard fields, update every downstream node.
- If you change decision rules, update output descriptions and validation samples.
- If you change output format, confirm downstream systems can still read it.

## 8. Validation

Minimum validation:

1. Run one normal input and confirm the main output fields are non-empty.
2. Run one edge-case input and confirm error handling behaves as expected.
3. If there are branches, validate each important branch at least once.
4. If files are written, check the output path and file content.
5. If there is publish configuration, confirm the publish page only shows enabled capabilities.

Acceptance checklist:

- [ ] Inputs are correctly recognized and parsed.
- [ ] Each node has a clear responsibility and outputs fields downstream nodes can read.
- [ ] Branch, filtering, aggregation, or analysis logic matches expectations.
- [ ] Output fields and file formats are clear.
- [ ] `workflow.md` and `workflow.json` describe the same flow.
- [ ] No plaintext secrets are written into the workflow directory.
