---
name: skill-builder
category: system
description: Create or improve skill. Use when the user asks to create, add, generate, update, refactor, package, or test a skill, convert a repeated workflow into a reusable skill, write a `SKILL.md`, or add `references/`, `scripts/` for a skill.
---

# Skill Builder

Create a reusable skill directory, not just a loose markdown file. Start with the smallest structure that can work: `SKILL.md` first, then add `references/`, `scripts/`, `assets/`, or `evals/evals.json` only when they reduce repeated work or keep the prompt lean.

## When to Use

Use this skill when the user wants to:

- create a brand new skill
- turn a repeated prompt or workflow into a reusable skill
- improve an existing skill's structure, trigger description, or bundled resources
- add realistic eval prompts for a skill
- package a repo-local skill so it can be reused elsewhere

## Decide First: Skill or Tool?

Choose a **skill** when the capability is mostly instructions, shell commands, and existing tools.

Choose a **tool** instead when the task needs:

- strict runtime behavior every time
- built-in auth or API-key handling
- binary or streaming data handling
- a new system integration that should not depend on prompt interpretation

If the task is really a tool problem, say so early and switch to tool creation instead of forcing a skill.

## Clarify the Contract

If important details are missing, ask only for the smallest set of answers needed:

1. **Skill name**: must be `kebab-case`
2. **Capability**: what the skill should help the agent do
3. **Triggering context**: what kinds of user requests should activate it
4. **Outputs**: what files or results the skill should produce
5. **Testing**: whether to add eval prompts now

If the request is mostly clear, propose sensible defaults and keep moving.

## Directory Layout

Use this layout unless the task clearly needs less:

```text
<skill-root>/
├── SKILL.md
├── evals/
│   └── evals.json          # optional
├── references/            # optional
├── scripts/               # optional
└── assets/                # optional
```

### Scope Rules

- Each skill must live in its own directory, and the directory name must exactly match the skill name in `kebab-case`
- Prefer the user-global path: `~/.flocks/plugins/skills/<name>/SKILL.md`
- Do not write user-created skills to `.flocks/skills/`; that location is for built-in skills, not user/project-authored plugin skills
- Do not scatter skill files into `docs/`, `tests/`, or ad hoc output folders

## Build Workflow

### 1. Capture the Real Intent

Before writing the file, pin down:

- the user problem the skill solves
- the user language that should trigger the skill
- the expected output format
- whether the skill needs helper scripts, references, env vars, or config

When converting an existing workflow into a skill, extract the process from the conversation or nearby files instead of asking the user to repeat everything.

### 2. Inspect Nearby Examples

Read one or two similar skills in the current repo and reuse the local conventions for:

- frontmatter tone
- section naming
- path conventions
- validation style

Do not copy text blindly. Reuse structure, not wording.

### 3. Draft the Frontmatter

At minimum, every skill needs:

```yaml
---
name: my-skill
description: What it does and when to use it.
---
```

### 4. Write the Description for Triggering

The description is the main trigger. Make it slightly "pushy" instead of passive.

Good descriptions include both:

- **what the skill does**
- **when it should be used**

Prefer outcomes over internals.

```yaml
# Better
description: Create or improve reusable documentation skills. Use whenever the user asks to turn a repeated docs workflow into a skill, generate a SKILL.md, or add references and evals for documentation automation.
```

Avoid descriptions that only say "this skill helps manage skills" without any trigger hints.

### 5. Write the Body with Progressive Disclosure

Keep `SKILL.md` focused and easy to execute. A good default shape is:

1. what the skill does
2. when to use it
3. the minimum workflow to follow
4. pitfalls and validation
5. references to deeper files only when needed

Suggested sections:

- `## When to Use`
- `## Quick Start`
- `## Workflow` or `## Procedure`
- `## Pitfalls`
- `## Verification`

If the body starts getting long, move stable detail into `references/` and point to it explicitly. If the skill needs deterministic parsing or repeated transformations, add a helper under `scripts/` instead of asking the model to rewrite the same logic each time.

### 6. Prefer Simple Dependencies

Follow the guidance:

- prefer shell, Python stdlib, and existing tools first
- avoid adding dependencies unless they clearly reduce repeated work
- if setup is required, document it plainly in the skill

Do not create helper scripts "just in case".

### 7. Add Eval Prompts When Useful

If the skill has verifiable behavior, file outputs, or a repeatable workflow, create `evals/evals.json` with 2-3 realistic prompts.

Use prompts that sound like a real user, not abstract benchmark text.

```json
{
  "skill_name": "example-skill",
  "evals": [
    {
      "id": 1,
      "prompt": "Create a new skill for X with Y constraints.",
      "expected_output": "A valid skill directory with a strong description and the right files.",
      "files": []
    }
  ]
}
```

If the skill is highly subjective and the user does not want evals, skipping them is acceptable, but say that choice explicitly.

### 8. Verify Before Finishing

Before you report success:

- confirm the directory name matches the skill name
- confirm `name` is valid `kebab-case`
- confirm the description is strong enough to trigger
- confirm every referenced file actually exists
- confirm the file layout matches the chosen scope
- keep the skill lean; move bulky material to `references/` when needed

When working inside this repository, prefer at least one concrete verification step:

```bash
uv run python - <<'PY'
import asyncio
from flocks.skill.skill import Skill

async def main():
    skill = await Skill.get("my-skill")
    assert skill is not None
    print(skill.name, skill.source, skill.location)

asyncio.run(main())
PY
```

If the repo already has skill-related tests, add or update a focused test rather than relying only on a manual check.

## Output Checklist

When you finish, report:

- created or updated files
- chosen scope: `project` or `user`
- the trigger-rich description you encoded
- any skipped extras such as evals or scripts, and why

## Constraints

- Prefer the simplest viable skill.
- Do not add extra abstractions without clear reuse.
- Preserve the original skill name when editing an existing skill unless the user explicitly asks to rename it.
- Explain why steps matter instead of filling the skill with rigid commands.
- Keep the main skill body reasonably short; use `references/` and `scripts/` for overflow.
