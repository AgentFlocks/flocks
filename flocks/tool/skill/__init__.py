"""
Skill management tools for Rex.

Provides:
  - flocks_skills — wraps the `flocks skills *` CLI (search / install /
    install-deps / list / remove). Used to discover and acquire skills
    from the public registry.
  - skill_manage — L2 evolution surface for *creating* and *editing*
    skills the agent itself authored. Routed through
    EvolutionEngine.author so the storage strategy is pluggable. The
    tool no-ops with a friendly error when evolution is disabled.

The two tools are intentionally separate: flocks_skills is for skills
shipped by others, skill_manage is for skills the agent writes itself
(procedural memory) and is the entry point monitored by the L4 curator.
"""

# Modules in this package are import-loaded via ToolRegistry's _tool_groups
# table (see flocks/tool/registry.py). We do not import here so we don't
# double-register the @ToolRegistry.register_function decorator side-effects.
