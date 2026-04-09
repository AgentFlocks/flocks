---
name: agent-builder
category: system
description: "Creates new sub-agents by generating YAML config and prompt files under ~/.flocks/plugins/agents/. The created agent can then be delegated to by Rex via delegate_task. Use it when the user asks to create, add, or generate a new agent."
description_cn: "通过在 ~/.flocks/plugins/agents/ 下生成 YAML 配置和 prompt 文件来创建新的子 Agent。创建完成后，Rex 可以通过 `delegate_task` 调用它。适用于用户要求创建、新增或生成新 Agent 的场景。"
---

# Agent Builder

Create a new sub-agent from user requirements. Produces a YAML config file + prompt file under `~/.flocks/plugins/agents/<name>/`, loadable by the system without restart.

**Required directory layout:**
```
~/.flocks/plugins/agents/
└── {name}/          ← subdirectory named after the agent (kebab-case)
    ├── agent.yaml   ← YAML config
    └── prompt.md    ← system prompt
```

> ⚠️ Do NOT create flat files like `agents/{name}.yaml` — the subdirectory format is mandatory.

---

## Workflow

### 1. Clarify Requirements

Use the `Question` tool to confirm (skip if already clear):

- **Agent name**: `kebab-case` format (e.g. `threat-analyst`, `code-reviewer`)
- **Role description**: one-sentence summary of the agent's specialty
- **Capability boundary**: what the agent can and cannot do
- **Execution mode**: read-only analysis (`read_only`) / general execution (`react`) / plan-then-execute (`plan_and_execute`) / codebase exploration (`explore`)

### 2. Generate Prompt File

Create `~/.flocks/plugins/agents/{name}/prompt.md` with the following structure:

```markdown
You are a specialized {role} agent.

## Mission
{Core responsibilities, 1-2 paragraphs}

## Capabilities
- **Capability 1**: specific description
- **Capability 2**: specific description

## Output Format
{Structured output requirements: tables, checklists, etc.}

## Constraints
- {Constraint 1}
- {Constraint 2}
```

**Prompt writing principles**:
- Open with a clear role identity
- List concrete capabilities, not vague descriptions
- Define a structured output format so callers can use results directly
- Constraints must be consistent with the `permission` config (e.g. a `read_only` agent's prompt should state "do NOT create or modify files")

### 3. Generate YAML Config File

Create `~/.flocks/plugins/agents/{name}/agent.yaml`. Full schema:

```yaml
# Required
name: {name}                    # kebab-case, globally unique
description: >                  # One-sentence description; Rex uses this to decide when to delegate
  {Agent's role and specialty}

# Behavior
mode: subagent                  # Always "subagent" for sub-agents
strategy: read_only             # react | plan_and_execute | read_only | explore
delegatable: true               # Whether delegate_task can invoke this agent
hidden: false                   # Whether to hide from the frontend

# Prompt source (pick one)
prompt_file: prompt.md          # External prompt.md in the same directory (recommended)
# prompt: "inline prompt..."    # Inline (for short prompts)
# prompt_builder: "module:func" # Dynamic Python builder (advanced)

# Optional
color: "#E74C3C"                # Frontend display color, 6-digit hex
temperature: 0.3                # 0.0 - 1.0
# top_p: null
# steps: 50                    # Max execution steps

# Model override (optional; omit to use the global default)
# model:
#   provider_id: custom-openai-compatible
#   model_id: "claude-sonnet-4-5-20250929"

# Permission rules
permission:
  "*": deny                     # Deny all by default
  read: allow                   # Open as needed
  grep: allow
  glob: allow
  # bash: allow                 # When shell execution is needed
  # write: allow                # When file writing is needed
  # edit: allow                 # When file editing is needed
  # websearch: allow            # When web search is needed
  # webfetch: allow             # When web fetching is needed
  # delegate_task: allow        # When re-delegation is needed (use with caution)

# Delegation metadata (tells Rex when to delegate to this agent)
prompt_metadata:
  category: {domain}            # Domain category, e.g. security / code-quality / devops
  cost: medium                  # CHEAP / medium / EXPENSIVE
  triggers:
    - domain: {trigger_domain}
      trigger: "{trigger condition description}"
  use_when:
    - "{applicable scenario 1}"
    - "{applicable scenario 2}"
  avoid_when:
    - "{inapplicable scenario}"
```

### 4. Strategy Selection Guide

| Strategy | Use Case | Tool Scope | Example Agents |
|----------|----------|------------|----------------|
| `read_only` | Analysis/consultation only, no file mutations | Read-only tools | oracle, librarian, momus |
| `react` | General execution, observe-think-act loop | All tools | general |
| `plan_and_execute` | Complex tasks requiring planning before execution | All tools | rex, hephaestus |
| `explore` | Codebase exploration and search | Search/read tools | explore |

### 5. Permission Templates

**Read-only analysis** (e.g. code review, security audit):
```yaml
permission:
  "*": deny
  read: allow
  grep: allow
  glob: allow
  codesearch: allow
```

**Read-only + network** (e.g. documentation lookup, threat intelligence):
```yaml
permission:
  "*": deny
  read: allow
  grep: allow
  glob: allow
  bash: allow
  websearch: allow
  webfetch: allow
  codesearch: allow
```

**Full execution** (e.g. code generation, refactoring):
```yaml
permission:
  "*": allow
  delegate_task: deny
  call_omo_agent: deny
```

### 6. Validation

After generating files, verify:

1. **YAML syntax**: run `python3 -c "import yaml; from pathlib import Path; yaml.safe_load(Path('~/.flocks/plugins/agents/{name}/agent.yaml').expanduser().read_text(encoding='utf-8'))"`
2. **Prompt file exists**: confirm `~/.flocks/plugins/agents/{name}/prompt.md` has been created
3. **Directory structure**: ensure files are inside `~/.flocks/plugins/agents/{name}/`, NOT as flat files like `agents/{name}.yaml`
4. **Name uniqueness**: ensure no collision with built-in agents (reserved names: rex, hephaestus, oracle, librarian, explore, general, metis, momus, multimodal-looker, rex-junior, build, plan, compaction, title, summary)
5. **Trigger reload**: call the refresh API so Rex recognizes the new agent immediately — **no restart needed**:
   ```bash
   curl -s -X POST http://localhost:8000/api/agents/refresh
   ```
   A successful response looks like `{"count": N}` where N is the total number of loaded agents. If the count increased, the new agent has been picked up correctly.

### 7. Output

After creation, inform the user:
- File paths created (e.g. `~/.flocks/plugins/agents/{name}/agent.yaml` and `prompt.md`)
- Agent name and role
- Can be invoked via `delegate_task(subagent_type="{name}", ...)`
- Takes effect immediately after calling `POST /api/agents/refresh` (no restart needed)

---

## Constraints

- Agent names must be `kebab-case` (lowercase letters + digits + hyphens)
- `mode` is always `subagent` (this skill only creates sub-agents)
- **Files MUST be written to `~/.flocks/plugins/agents/<name>/` subdirectory** — flat files like `agents/<name>.yaml` are legacy and should NOT be created
- The subdirectory name must match the agent `name` field exactly
- Do not create agents with names that collide with built-in agents
