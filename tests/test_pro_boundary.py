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


# Packages that only ship inside the commercial build. OSS may expose generic
# mechanisms they use, but never imports them -- not even behind a guard: the
# licence and audit bridges that used to are gone, and extensions reach Core
# through flocks.extensions only.
_COMMERCIAL_PACKAGES = frozenset({"flockspro", "flocks_code_security"})


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

    # The generic hooks are the only B1--B4 integration surface here.
    assert not imports.intersection(_REMOVED_SECURITY_MODULES)
    assert not {
        item
        for item in imports
        if item.split(".", 1)[0] in _COMMERCIAL_PACKAGES
    }
