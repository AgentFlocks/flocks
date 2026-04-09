from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = REPO_ROOT / "scripts"


def test_bash_installer_prefers_explicit_browser_configuration() -> None:
    script = (SCRIPT_DIR / "install.sh").read_text(encoding="utf-8")

    assert "detect_system_browser_path()" in script
    assert "AGENT_BROWSER_EXECUTABLE_PATH" in script
    assert "get_chrome_for_testing_dir()" in script
    assert 'npx --yes @puppeteer/browsers install chrome@stable --path "$browser_dir"' in script
    assert 'chrome@*\' \'*|chromium@*\' \'*' in script
    assert 'candidate="${line#* }"' in script
    assert '"$HOME/.flocks/browser"' in script
    assert 'npm_config_registry="$NPM_REGISTRY" npx --yes @puppeteer/browsers install chrome@stable --path "$browser_dir"' in script
    assert "agent-browser install" not in script
    assert 'require("@puppeteer/browsers")' not in script
    assert "npx --yes --package @puppeteer/browsers node -e" not in script


def test_powershell_installer_prefers_explicit_browser_configuration() -> None:
    script = (SCRIPT_DIR / "install.ps1").read_text(encoding="utf-8-sig")

    assert "Find-SystemBrowserPath" in script
    assert "AGENT_BROWSER_EXECUTABLE_PATH" in script
    assert "Get-ChromeForTestingDir" in script
    assert "Invoke-NativeCommandOrFail" in script
    assert '-FilePath "npx.cmd"' in script
    assert '"@puppeteer/browsers"' in script
    assert '"chrome@stable"' in script
    assert '$line -like "chrome@* *"' in script
    assert '$candidate = $line.Substring($firstSpaceIndex + 1).Trim()' in script
    assert 'Join-Path $HOME ".flocks\\browser"' in script
    assert '-Description "Chrome for Testing installation"' in script
    assert '-Environment @{ npm_config_registry = $script:NpmRegistry }' in script
    assert "agent-browser install" not in script
    assert 'require("@puppeteer/browsers")' not in script
