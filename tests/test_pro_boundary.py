"""Executable guardrails for the OSS side of the B1--B4 extension boundary."""

from __future__ import annotations

import ast
from pathlib import Path


_REMOVED_SECURITY_MODULES = frozenset(
    {
        "flocks.security.action_gateway",
        "flocks.security.canonical",
        "flocks.security.capability_pool",
        "flocks.security.delegation_context",
        "flocks.security.execution_context",
    }
)


def _imports_in(module_path: Path) -> set[str]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
            if node.module == "flocks.security":
                imports.update(f"{node.module}.{alias.name}" for alias in node.names)
    return imports


def test_oss_runtime_source_has_no_pro_or_removed_security_dependencies() -> None:
    """OSS keeps neutral hook mechanics and never imports B1--B4 policy owners."""
    source_root = Path(__file__).parents[1] / "flocks"
    imports = {
        imported
        for module_path in source_root.rglob("*.py")
        for imported in _imports_in(module_path)
    }

    # Legacy optional audit/licence bridges may import FlocksPro, but policy
    # ownership itself must remain outside OSS.  The generic hooks are the
    # only B1--B4 integration surface here.
    assert not {
        item
        for item in imports
        if item == "flockspro.policy" or item.startswith("flockspro.policy.")
    }
    assert not imports.intersection(_REMOVED_SECURITY_MODULES)
