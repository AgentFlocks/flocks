# workflow_template

> `workflow.md` is the single human-editable workflow specification. Flocks uses this file to understand intent, then keeps `workflow.json` aligned with the executable graph.

## 1. Workflow Card

- Workflow ID: `workflow_template`
- Reference directory: `.flocks/plugins/skills/workflow-builder/references/workflow_template/`
- Category: `template`
- Status: skill reference template, not a scannable workflow
- Entry node: `template_entry`
- Terminal node: `template_entry`

## 2. Business Goal

Describe the operational problem this workflow solves, who will use it, and what a successful run produces.

Success criteria:

- [ ] The expected input shape is clear.
- [ ] Each module has an explicit responsibility.
- [ ] The final output contract is clear to humans and downstream systems.
- [ ] Failure and empty-input behavior are documented.

## 3. Runtime Contract

### Inputs

Replace this section with the real input keys and shapes.

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `input` | object | yes | - | Primary workflow input. |

### Outputs

Replace this section with the final output contract.

| Field | Type | Description |
| --- | --- | --- |
| `result` | object | Final workflow result. |

### Tunables

List thresholds, switches, timeouts, file paths, concurrency settings, and rollback notes.

## 4. Flow Map

`template_entry`

| Order | Node | Type | Responsibility | Next |
| --- | --- | --- | --- | --- |
| 1 | `template_entry` | Python | Placeholder entry node. Replace before use. | final output |

## 5. Module Specs

### 1. template_entry

| Item | Content |
| --- | --- |
| Module type | Python |
| Responsibility | Placeholder node that marks this directory as a template. |
| Inputs | Workflow inputs |
| Outputs | `templateOnly`, `message` |
| Edit focus | Replace this node with the real first module. |

Generation notes for Flocks:

- Keep node IDs stable after users start configuring publish modes.
- When adding or renaming outputs, update downstream edges and the runtime contract.
- Do not store plaintext secrets in this directory.

## 6. Data Flow And Field Contract

Document every cross-module field that must remain stable.

- `template_entry -> final output`

## 7. Publish And Triggers

The publish page reads `config.json` as a template and runtime state from storage.

- If `publish.type` is `api_service`, show API publish controls.
- If only `syslog` is configured, show only syslog listener start/stop controls.
- If only `kafka` is configured, show only kafka consumer start/stop controls.
- If only `schedule` is configured, show only schedule start/stop controls.
- Store secret references or configured booleans only; never store plaintext secrets.

Workflow configuration guidance lives in `guide.md`.

- `workflow-config-guide` defines interaction rules only.
- A real workflow's own `guide.md` defines that workflow's actual configuration questions, defaults, samples, and validation steps.
- Workflow chat shortcut buttons must read the real workflow's `guide.md` before asking or applying any configuration step.

## 8. Change Guide

| Change type | Edit first | Also check |
| --- | --- | --- |
| Input shape | Runtime Contract | Entry module, sample inputs |
| Module logic | Module Specs | Upstream outputs, downstream inputs |
| Output shape | Runtime Contract | Terminal module, downstream consumers |
| Publish mode | Publish And Triggers / `config.json` | Auth, secret refs, runtime state |

## 9. Flocks Generation Constraints

- `workflow.md` describes intent, module boundaries, field contracts, and validation.
- `workflow.json` describes executable nodes, edges, code, triggers, and metadata.
- Regeneration should preserve node IDs unless the user explicitly requests a graph change.
- Deleting or renaming a node requires updating edges, mappings, samples, and tests.

## 10. Validation Checklist

- [ ] `workflow.md` and `workflow.json` describe the same flow.
- [ ] A representative sample input runs successfully.
- [ ] At least one edge or error case is documented.
- [ ] Publish page only shows capabilities enabled by `config.json`.
- [ ] No plaintext secrets are stored in the workflow directory.
