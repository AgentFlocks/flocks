"""Tests for .flocks/plugins/skills/tool-builder/scripts/validator.py."""
import sys
import textwrap
from pathlib import Path

import pytest

# Make the validator importable without installing it.
SKILL_DIR = Path(__file__).parent.parent.parent / ".flocks" / "plugins" / "skills" / "tool-builder"
sys.path.insert(0, str(SKILL_DIR / "scripts"))

from validator import main, validate_yaml_tool, validate_python_tool  # noqa: E402


# ── helpers ────────────────────────────────────────────────────────────────

def write(tmp_path: Path, filename: str, content: str) -> Path:
    p = tmp_path / filename
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


# ── YAML-HTTP mode ─────────────────────────────────────────────────────────

class TestYamlHttpTool:
    def test_valid_minimal_http_tool_passes(self, tmp_path):
        p = write(tmp_path, "my_tool.yaml", """\
            name: my_tool
            description: A well-described tool that does something useful for the agent.
            category: custom
            enabled: true
            handler:
              type: http
              method: GET
              url: https://example.com/api
            inputSchema:
              type: object
              properties:
                q:
                  type: string
                  description: Search query
        """)
        report = validate_yaml_tool(p)
        assert report.fail_count == 0, report.issues

    def test_missing_name_is_a_failure(self, tmp_path):
        p = write(tmp_path, "no_name.yaml", """\
            description: A fine description.
            category: custom
            enabled: true
            handler:
              type: http
              url: https://example.com
        """)
        report = validate_yaml_tool(p)
        assert report.fail_count > 0
        assert any("name" in i.message.lower() for i in report.issues if i.level == "FAIL")

    def test_undeclared_url_placeholder_is_a_failure(self, tmp_path):
        p = write(tmp_path, "bad_url.yaml", """\
            name: bad_url
            description: Long enough description for the validator to be happy here.
            category: custom
            enabled: true
            handler:
              type: http
              method: GET
              url: https://example.com/{undeclared_param}
            inputSchema:
              type: object
              properties: {}
        """)
        report = validate_yaml_tool(p)
        assert report.fail_count > 0
        assert any("undeclared_param" in i.message for i in report.issues if i.level == "FAIL")

    def test_invalid_category_is_a_failure(self, tmp_path):
        p = write(tmp_path, "bad_cat.yaml", """\
            name: bad_cat
            description: Some long enough description that passes the length check ok.
            category: nonsense
            enabled: true
            handler:
              type: http
              url: https://example.com
        """)
        report = validate_yaml_tool(p)
        assert report.fail_count > 0


# ── YAML-script mode ───────────────────────────────────────────────────────

class TestYamlScriptTool:
    def test_valid_script_tool_passes(self, tmp_path):
        handler_py = write(tmp_path, "my_handler.py", """\
            async def handle(ctx, q: str) -> dict:
                return {"result": q}
        """)
        p = write(tmp_path, "my_script_tool.yaml", f"""\
            name: my_script_tool
            description: Script-based tool with a proper handler that does something.
            category: custom
            enabled: true
            handler:
              type: script
              script_file: {handler_py.name}
              function: handle
            inputSchema:
              type: object
              properties:
                q:
                  type: string
                  description: Input query
        """)
        report = validate_yaml_tool(p)
        assert report.fail_count == 0, report.issues

    def test_missing_script_file_is_a_failure(self, tmp_path):
        p = write(tmp_path, "no_script.yaml", """\
            name: no_script
            description: Script tool whose script_file does not exist on disk.
            category: custom
            enabled: true
            handler:
              type: script
              script_file: nonexistent_handler.py
        """)
        report = validate_yaml_tool(p)
        assert report.fail_count > 0


# ── Python tool mode ───────────────────────────────────────────────────────

class TestPythonTool:
    def test_valid_python_tool_passes(self, tmp_path):
        p = write(tmp_path, "my_python_tool.py", """\
            from flocks.tool.registry import ToolRegistry, ToolResult

            @ToolRegistry.register_function(
                name="my_python_tool",
                description="Does something useful for the agent in a local context.",
                category="custom",
                parameters=[
                    {"name": "text", "type": "string", "description": "Input text"},
                ],
            )
            async def my_python_tool(ctx, text: str) -> ToolResult:
                return ToolResult(output=text)
        """)
        report = validate_python_tool(p)
        assert report.fail_count == 0, report.issues

    def test_missing_register_decorator_is_a_failure(self, tmp_path):
        p = write(tmp_path, "no_decorator.py", """\
            async def handle(text: str) -> dict:
                return {"result": text}
        """)
        report = validate_python_tool(p)
        assert report.fail_count > 0

    def test_non_async_function_is_a_failure(self, tmp_path):
        p = write(tmp_path, "sync_fn.py", """\
            from flocks.tool.registry import ToolRegistry, ToolResult

            @ToolRegistry.register_function(
                name="sync_fn",
                description="A synchronous function that should be async.",
                category="custom",
                parameters=[],
            )
            def sync_fn() -> ToolResult:
                return ToolResult(output="ok")
        """)
        report = validate_python_tool(p)
        assert report.fail_count > 0


# ── CLI --strict mode ──────────────────────────────────────────────────────

class TestCliStrictMode:
    def test_strict_exits_nonzero_on_warnings(self, tmp_path):
        # A tool with no parameters triggers a WARN.
        p = write(tmp_path, "warn_tool.yaml", """\
            name: warn_tool
            description: Decent description but has no parameters at all.
            category: custom
            enabled: true
            handler:
              type: http
              url: https://example.com
        """)
        exit_code = main(["--strict", str(p)])
        assert exit_code != 0

    def test_non_strict_exits_zero_on_warnings_only(self, tmp_path):
        p = write(tmp_path, "warn_tool2.yaml", """\
            name: warn_tool2
            description: Decent description but has no parameters at all here.
            category: custom
            enabled: true
            handler:
              type: http
              url: https://example.com
        """)
        exit_code = main([str(p)])
        assert exit_code == 0
