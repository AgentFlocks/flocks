# Pluggable Self-Evolution Module (`flocks.evolution`)

Optional 4-layer self-evolution stack for Flocks. **Disabled by default**; existing installs see no behaviour change after upgrade.

When enabled, an agent can:

1. **Acquire** missing capabilities (close capability gaps).
2. **Sediment** successful approaches as reusable `SKILL.md` files (procedural memory).
3. **Track** how often each skill is used.
4. **Curate** the skill library in the background — archive stale skills, merge umbrella skills.

Each layer is replaceable by a third-party plugin (Python or YAML) without modifying core code. NoOp implementations are wired by default so call sites never branch on `is None`.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                       EvolutionEngine (singleton)                    │
│                                                                      │
│   acquirer (L1)   author (L2)    tracker (L3)   curator (L4)        │
│       │              │               │              │                │
│       ▼              ▼               ▼              ▼                │
│  delegate_task    skill_manage   skill_tool     command:new hook     │
│  interception     tool           bump_use       (throttled)          │
└──────────────────────────────────────────────────────────────────────┘
```

| Layer | Abstract base | Default builtin | Trigger point |
|---|---|---|---|
| **L1 CapabilityAcquirer** | Close capability gaps | `BuiltinSelfEnhanceAcquirer` (passthrough — keeps original `self-enhance` subagent flow) | `delegate_task(subagent_type="self-enhance", ...)` interception |
| **L2 SkillAuthor** | Persist experience as SKILL.md | `BuiltinSkillAuthor` (writes to `~/.flocks/plugins/skills/`) | `skill_manage` tool |
| **L3 UsageTracker** | Per-skill telemetry | `BuiltinFsUsageTracker` (per-project sidecar JSON) | `skill_tool_impl` after successful skill load |
| **L4 Curator** | Background skill maintenance | `BuiltinIdleCurator` (pure-function v1 + LLM v2) | `command:new` hook (throttled by `min_idle_hours`) |

---

## Enabling the module

Add the following to `flocks.json`:

```json
{
  "evolution": {
    "enabled": true,
    "acquirer": {"enabled": true, "use": "builtin"},
    "author":   {"enabled": true, "use": "builtin"},
    "tracker":  {"enabled": true, "use": "builtin"},
    "curator": {
      "enabled": true,
      "use": "builtin",
      "settings": {
        "min_idle_hours": 24,
        "stale_after_days": 14,
        "archive_after_days": 60
      }
    }
  }
}
```

Selection rules per layer:
- `enabled: false` → NoOp (layer disabled regardless of `use`)
- `use: null` → NoOp (layer enabled but no strategy chosen)
- `use: "builtin"` → bundled implementation
- `use: "<plugin-name>"` → third-party plugin registered with `EvolutionEngine`
- Unknown `use` value → falls back to NoOp with a warning (server startup never blocked)

Permission for the `skill_manage` tool (defaults to `ask`):

```json
{
  "permission": {"skill_manage": "ask"}
}
```

---

## CLI

```
flocks evolution status               # active strategies + curator state
flocks evolution status --apply-transitions   # also force-run pure transitions
flocks evolution list-strategies      # all registered factory names
flocks evolution report               # tracker rows + manifest authorship gating
flocks evolution curate               # force a v1 pure-function pass (bypass throttle)
flocks evolution curate --llm         # launch v2 LLM curator subagent (writes a report)
flocks evolution pause                # set CuratorState.paused = true
flocks evolution resume               # unpause
```

---

## Writing a third-party strategy

### Python plugin

Drop a file under `~/.flocks/plugins/evolution/<layer>/` (or `<project>/.flocks/plugins/evolution/<layer>/` for project-scope) and export `EVOLUTION_<LAYER>S` (one of `EVOLUTION_ACQUIRERS`, `EVOLUTION_AUTHORS`, `EVOLUTION_TRACKERS`, `EVOLUTION_CURATORS`):

```python
# ~/.flocks/plugins/evolution/acquirer/my_acquirer.py
from flocks.evolution import (
    AcquireContext, AcquireResult, CapabilityAcquirer,
    CapabilityGap, StrategySpec,
)

class MyAcquirer(CapabilityAcquirer):
    name = "my-acquirer"
    is_noop = False
    passthrough = False  # MUST be False to actually intercept self-enhance

    async def can_handle(self, gap: CapabilityGap) -> bool:
        return "email" in gap.keywords

    async def acquire(self, gap: CapabilityGap, ctx: AcquireContext) -> AcquireResult:
        # Talk to your private capability registry, install internal SDKs, etc.
        return AcquireResult(
            acquired=True,
            tool_name="my_email",
            notes="installed internal email client",
            attempted=["check-cache", "install"],
        )

EVOLUTION_ACQUIRERS = [StrategySpec(name="my-acquirer", factory=MyAcquirer)]
```

Then activate it in `flocks.json`:

```json
{
  "evolution": {
    "enabled": true,
    "acquirer": {"use": "my-acquirer"}
  }
}
```

### YAML plugin

If your strategy class lives in an installed Python package, you can register it via YAML instead of authoring a Python plugin file:

```yaml
# ~/.flocks/plugins/evolution/tracker/external.yaml
name: my-tracker
module: my_company.flocks_plugins
class: ExternalUsageTracker
```

The YAML factory imports `module.class`, validates that it subclasses the right abstract base, and registers it.

### Subclassing rules per layer

| Layer | Subclass | Required methods |
|---|---|---|
| Acquirer | `flocks.evolution.CapabilityAcquirer` | `async can_handle`, `async acquire` |
| Author | `flocks.evolution.SkillAuthor` | `async create / edit / patch / delete / write_file / remove_file` |
| Tracker | `flocks.evolution.UsageTracker` | `bump_use / bump_view / bump_patch / set_state / set_pinned / report / forget` |
| Curator | `flocks.evolution.Curator` | `should_run / apply_automatic_transitions / async run / load_state / save_state` |

---

## On-disk layout

```
~/.flocks/data/evolution/
├── authored.jsonl                  # L2 author manifest (append-only, tombstone-deletes)
├── usage/
│   ├── _user.json                  # L3 user-scope skill telemetry
│   └── <project_hash>.json         # L3 project-scope (one file per Instance.project.id)
└── curator/
    ├── state.json                  # L4 scheduler state (last_run_at, paused, run_count)
    └── 20260430T093000/            # L4 v2 per-run report dir
        ├── run.json                # launching metadata
        └── REPORT.md               # the curator subagent's written report
```

`FLOCKS_ROOT` env var overrides `~/.flocks` for all of the above (used by tests and sandboxed installs).

---

## Author manifest (the gating list)

`authored.jsonl` is the single source of truth for *which skills the agent itself created* vs. which are bundled or hub-installed. The L4 curator only operates on names listed here, so:

- Bundled skills (shipped in `flocks/.flocks/plugins/skills/`) are never archived or rewritten.
- Hub-installed skills (`flocks skills install ...`) are never archived or rewritten.
- Skills authored via `skill_manage` ARE eligible for curator review.

Records are append-only; deletion writes a tombstone (`{"name": "...", "deleted": true, ...}`) instead of rewriting the file. Last record per `(name, scope)` wins.

---

## Curator safety rails

1. **Manifest gate**: only agent-authored skills are touched.
2. **Pinned exempt**: `tracker.set_pinned(name, True)` removes a skill from automatic transitions forever (until unpinned).
3. **Strict throttle**: `min_idle_hours` between runs. `command:new` hook will not fire the curator more often than this.
4. **Pause switch**: `flocks evolution pause` sets `state.paused = true`; the curator will skip every trigger until you `resume`.
5. **Curator subagent toolset is read-only + skill_manage**: the v2 LLM curator runs with `read / list / glob / grep / skill / skill_manage` — no `bash`, no network, no `write`. It cannot escalate.

---

## Lifecycle of a skill (with telemetry)

```
                ┌──────────┐       bump_use after stale         ┌──────────┐
                │  ACTIVE  │◀──────────────────────────────────│  STALE   │
                └────┬─────┘                                    └────┬─────┘
        idle > stale_after_days                            idle > archive_after_days
                     │                                              │
                     ▼                                              ▼
                ┌──────────┐                               ┌──────────────┐
                │  STALE   │── idle > archive_after_days──▶│   ARCHIVED   │ (terminal)
                └──────────┘                               └──────────────┘
```

`pinned=True` makes a skill skip every transition above.

---

## Backward compatibility

With `evolution.enabled` unset or `false` (the default), every layer wires a NoOp:
- `delegate_task(subagent_type="self-enhance", ...)` runs the bundled subagent flow exactly as before.
- `skill_manage` tool returns a friendly error explaining how to enable the module.
- `skill_tool_impl` calls `tracker.bump_use()` which is a silent no-op.
- `command:new` hook runs the curator handler which immediately returns because `curator.is_noop`.

Upgrading an existing install requires zero action.
