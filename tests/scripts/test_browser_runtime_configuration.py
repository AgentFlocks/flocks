from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = REPO_ROOT / "scripts"


def test_bash_installer_prefers_explicit_browser_configuration() -> None:
    script = (SCRIPT_DIR / "install.sh").read_text(encoding="utf-8")

    assert "detect_system_browser_path()" in script
    assert "AGENT_BROWSER_EXECUTABLE_PATH" in script
    assert "get_chrome_for_testing_dir()" in script
    assert "resolve_chrome_for_testing_path_from_dir()" in script
    assert 'npx --yes @puppeteer/browsers install chrome@stable --path "$browser_dir"' in script
    assert 'Downloading Chrome for Testing.' in script
    assert 'If browser installation fails, Flocks can still start and you can reinstall it later.' in script
    assert 'npm_config_registry="$NPM_REGISTRY" npx --yes @puppeteer/browsers install chrome@stable --path "$browser_dir" 1>&2' in script
    assert '"$browser_dir"/**/"Google Chrome for Testing"' in script
    assert '"$browser_dir"/**/chrome.exe' in script
    assert '"$browser_dir"/**/chrome' in script
    assert '"$HOME/.flocks/browser"' in script
    assert 'Found existing Chrome for Testing. agent-browser will use: $browser_path' in script
    assert 'npx --yes @puppeteer/browsers install chrome@stable --path "$browser_dir" 2>&1 | tee' not in script
    assert "agent-browser install" not in script
    assert 'require("@puppeteer/browsers")' not in script
    assert "npx --yes --package @puppeteer/browsers node -e" not in script


def test_powershell_installer_prefers_explicit_browser_configuration() -> None:
    script = (SCRIPT_DIR / "install.ps1").read_text(encoding="utf-8-sig")

    assert "Resolve-BundledChromePath" in script
    assert "flocks-bundled-chrome.exe.relative.txt" in script
    assert "Find-SystemBrowserPath" in script
    assert "AGENT_BROWSER_EXECUTABLE_PATH" in script
    assert "Get-ChromeForTestingDir" in script
    assert "Resolve-ChromeForTestingPath" in script
    assert 'Get-CommandPath "npx.cmd"' in script
    assert '"@puppeteer/browsers"' in script
    assert '"chrome@stable"' in script
    assert '$candidateNames = @("chrome.exe", "Google Chrome for Testing", "chrome")' in script
    assert 'Get-ChildItem -Path $BrowserDir -Recurse -File' in script
    assert 'Join-Path $HOME ".flocks\\browser"' in script
    assert 'Downloading Chrome for Testing.' in script
    assert 'If browser installation fails, Flocks can still start and you can reinstall it later.' in script
    assert '$process = Start-Process' in script
    assert '-NoNewWindow' in script
    assert '-Wait' in script
    assert '-PassThru' in script
    assert '$env:npm_config_registry = $script:NpmRegistry' in script
    assert 'Found existing Chrome for Testing. agent-browser will use: $browserPath' in script
    assert "agent-browser install" not in script
    assert 'require("@puppeteer/browsers")' not in script
