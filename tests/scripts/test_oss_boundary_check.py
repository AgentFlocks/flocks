"""What scripts/oss_boundary_check.py counts as naming a separate commercial build (G01)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "oss_boundary_check.py"


@pytest.fixture()
def checker(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("oss_boundary_check_under_test", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # Its dataclasses look their module up while the module is being run.
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)

    def found(relative: str, line: str) -> bool:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(line + "\n", encoding="utf-8")
        try:
            return bool(module.scan())
        finally:
            path.unlink()

    return found


@pytest.mark.parametrize(
    "line",
    [
        "import { flocksproPolicyApi } from '@/api/flocksproPolicy';",  # lower case, then a capital
        "from flockspro.license import runtime",
        "FLOCKSPRO_HOME = '/opt'",
        "if suite.edition == \"pro\":",
        "const locked = suite.edition === 'pro';",
        "PluginEdition = Literal[\"oss\", \"pro\"]",
        "def is_pro_edition_status(status):",
        "def _maybe_pro_suite(suite_id):",
        "t('workspace.proOnlyTitle')",
        "version.replace(/^pro-v/i, '')",
        "# Durable audit (Pro) records the attempt",
        "badge: 'PRO'",
    ],
)
def test_names_and_protocol_of_a_commercial_build_are_counted(checker, line):
    assert checker("flocks/example.py", line)


@pytest.mark.parametrize(
    "line",
    [
        "flocksProvider = providers['flocks']",
        "flocksProcess.on('exit', done)",
        "def is_provided_by_deployment(plugin_type, plugin_id):",
        "isProcessing: bool = False",
        "function isProviderAllowed(id) {}",
        "edition_needs_licence(entry.edition)",
        "model = 'gemini-2.5-pro'",
        "process the prompt and protect the project",
    ],
)
def test_ordinary_words_are_not_counted(checker, line):
    assert not checker("flocks/example.py", line)


def test_model_names_in_the_provider_catalogues_are_not_the_product_word(checker):
    assert not checker("flocks/provider/sdk/vertex.py", 'name="Gemini 1.5 Pro"')
    assert not checker("tui/flocks/cli/cmd/auth.ts", 'openai: "ChatGPT Plus/Pro or API key"')
    assert checker("flocks/server/app.py", "# where the Pro gate restores the identity")
    # Names and protocol count everywhere, catalogues included.
    assert checker("flocks/provider/sdk/vertex.py", "if edition == 'pro': pass")


def test_the_public_source_is_clean():
    import subprocess

    result = subprocess.run([sys.executable, str(_SCRIPT)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "public source: 0 occurrence(s)" in result.stdout
