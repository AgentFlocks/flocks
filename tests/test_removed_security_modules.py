"""The security modules that left the open product stay gone (B1--B4)."""

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


def test_runtime_source_imports_no_removed_security_module() -> None:
    """The runtime keeps neutral hook mechanics and never imports the B1--B4 policy owners."""
    source_root = Path(__file__).parents[1] / "flocks"
    imports = {
        imported
        for module_path in source_root.rglob("*.py")
        for imported in _imports_in(module_path)
    }

    # The generic hooks are the only B1--B4 integration surface here.
    assert not imports.intersection(_REMOVED_SECURITY_MODULES)
