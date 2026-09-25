"""The design contract must fail independently of the legacy palette ratchet."""

import importlib.util
from pathlib import Path
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ui_spec_check.py"
spec = importlib.util.spec_from_file_location("ui_spec_check", SCRIPT)
ui_spec = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ui_spec
spec.loader.exec_module(ui_spec)


@pytest.fixture
def token_copy(tmp_path):
    path = tmp_path / "tokens.css"
    path.write_text(ui_spec.TOKEN_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def test_current_tokens_follow_the_authoritative_design():
    assert ui_spec.check_design_tokens(ui_spec.DESIGN_SOURCE, ui_spec.TOKEN_FILE) == []


@pytest.mark.parametrize(
    ("old", "new", "name"),
    [
        ("--bg-canvas: #252c35", "--bg-canvas: #000000", "[dark] --bg-canvas"),
        ("--accent-ai: #6d28d9", "--accent-ai: #ff0000", "[light] --accent-ai"),
        ("--font-body: 14px", "--font-body: 13px", "--font-body"),
        ("--line-body: 22px", "--line-body: 20px", "--line-body"),
        ("--control-md: 36px", "--control-md: 32px", "--control-md"),
        ("--radius-card: 12px", "--radius-card: 16px", "--radius-card"),
        ("--motion-normal: 180ms", "--motion-normal: 300ms", "--motion-normal"),
        ('"PingFang SC", ', "", "--font-sans"),
    ],
)
def test_token_drift_is_rejected(token_copy, old, new, name):
    original = token_copy.read_text(encoding="utf-8")
    assert old in original
    token_copy.write_text(original.replace(old, new), encoding="utf-8")
    assert any(name in problem for problem in ui_spec.check_design_tokens(ui_spec.DESIGN_SOURCE, token_copy))


def test_missing_dark_theme_does_not_inherit_light_colours_silently(token_copy):
    original = token_copy.read_text(encoding="utf-8")
    token_copy.write_text(original.replace(".dark {", ".unused-theme {"), encoding="utf-8")
    problems = ui_spec.check_design_tokens(ui_spec.DESIGN_SOURCE, token_copy)
    assert any("[dark] --bg-canvas" in problem for problem in problems)


def test_design_final_override_wins_and_demo_content_does_not(tmp_path, token_copy):
    design = tmp_path / "design.html"
    original = ui_spec.DESIGN_SOURCE.read_text(encoding="utf-8")
    design.write_text(
        original
        + '<pre>:root { --bg-canvas: red; }</pre>'
        + '<style>/* :root { --bg-canvas: red; } */'
        + '[data-theme="dark"] { --bg-canvas: #123456; }</style>',
        encoding="utf-8",
    )
    problems = ui_spec.check_design_tokens(design, token_copy)
    assert any("[dark] --bg-canvas: expected #123456" in problem for problem in problems)
    assert not any("[light] --bg-canvas" in problem for problem in problems)


def test_only_published_theme_blocks_form_the_contract():
    themes = ui_spec.theme_tokens(
        ':root { --control-md: 36px; --spec-space-inline: 8px; }'
        ':root, [data-theme="light"] { --bg-canvas: #fff; }'
        '[data-theme="dark"] { --bg-canvas: #252c35; }'
        '@media (prefers-color-scheme:dark) { :root:not([data-theme]) { --bg-canvas: red; } }'
        '.pane :is(input,textarea) { --control-md: 40px; }',
        design=True,
    )
    assert themes["light"] == {"--control-md": "36px", "--bg-canvas": "#fff"}
    assert themes["dark"] == {"--control-md": "36px", "--bg-canvas": "#252c35"}


def test_conditional_theme_cannot_pass_as_an_unconditional_contract(token_copy):
    content = token_copy.read_text(encoding="utf-8")
    token_copy.write_text(content.replace(".dark {", "@media (min-width: 1200px) { .dark {") + "}", encoding="utf-8")
    assert any(
        "[dark] --bg-canvas" in problem
        for problem in ui_spec.check_design_tokens(ui_spec.DESIGN_SOURCE, token_copy)
    )


def test_incomplete_or_missing_design_never_passes(tmp_path, token_copy):
    design = tmp_path / "design.html"
    assert ui_spec.check_design_tokens(design, token_copy)
    design.write_text("<style>:root { --bg-canvas: #fff; }</style>", encoding="utf-8")
    assert "missing required tokens" in ui_spec.check_design_tokens(design, token_copy)[0]


def test_equivalent_css_formatting_is_accepted(token_copy):
    content = token_copy.read_text(encoding="utf-8")
    content = content.replace("#252c35", "#252C35").replace("rgba(0, 0, 0, 0.4)", "rgba(0,0,0,.40)")
    token_copy.write_text(content, encoding="utf-8")
    assert ui_spec.check_design_tokens(ui_spec.DESIGN_SOURCE, token_copy) == []


def test_an_overlay_cannot_bypass_literal_colour_rule_with_token_filename(tmp_path):
    path = tmp_path / "tokens.css"
    path.write_text(":root { --own-brand: #123456; }", encoding="utf-8")
    scanned = list(ui_spec.iter_files([tmp_path]))
    assert scanned == [(path, tmp_path)]
    assert ui_spec.scan_file(path, tmp_path)[0].rule == "hex-colour"
    assert list(ui_spec.iter_files([ui_spec.TOKEN_FILE])) == []


@pytest.mark.parametrize("utility", ["bg-surface/50", "dark:text-fg-secondary/70", "border-danger/[0.4]", "hover:bg-action-hover/80"])
def test_semantic_opacity_is_rejected(tmp_path, utility):
    path = tmp_path / "component.tsx"
    path.write_text(f'<div className="{utility}" />', encoding="utf-8")
    assert [hit.rule for hit in ui_spec.scan_file(path, tmp_path)] == ["semantic-opacity"]


def test_valid_semantic_colours_and_opacity_property_are_not_rejected(tmp_path):
    path = tmp_path / "component.tsx"
    path.write_text('<div className="bg-surface text-fg-secondary border-danger bg-overlay-backdrop opacity-50" />', encoding="utf-8")
    assert ui_spec.scan_file(path, tmp_path) == []


def test_literal_colour_before_arbitrary_property_separator_is_rejected(tmp_path):
    path = tmp_path / "component.tsx"
    path.write_text('<div className="[scrollbar-color:#e4e4e7_transparent]" />', encoding="utf-8")
    assert [hit.rule for hit in ui_spec.scan_file(path, tmp_path)] == ["hex-colour"]


def test_baseline_update_cannot_waive_token_drift(monkeypatch, tmp_path, token_copy, capsys):
    token_copy.write_text(":root { --bg-canvas: #000; }", encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    original = '{"files": {"anything.tsx": 99999}}\n'
    baseline.write_text(original, encoding="utf-8")
    monkeypatch.setattr(ui_spec, "TOKEN_FILE", token_copy)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--root", str(tmp_path), "--baseline", str(baseline), "--update", "--allow-increase"])
    assert ui_spec.main() == 1
    assert "baseline updates cannot waive design drift" in capsys.readouterr().out
    assert baseline.read_text(encoding="utf-8") == original
