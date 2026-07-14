from pathlib import Path


_ROOT = Path(__file__).resolve().parents[2]
_SKILL_ROOT = _ROOT / ".flocks/plugins/skills/browser-use"


def test_browser_skill_documents_isolated_runtime_artifacts_and_safe_cleanup() -> None:
    skill = (_SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    headless = (_SKILL_ROOT / "references/cdp-headless.md").read_text(encoding="utf-8")

    assert "命名 session 使用 `bu-<BU_NAME>.*`" in skill
    assert "`flocks browser --reload`" in skill
    assert 'rm -f "$HOME/.flocks/browser/bu.sock"' not in headless
