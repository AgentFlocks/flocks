from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = REPO_ROOT / "scripts"
PACKAGING_WINDOWS_DIR = REPO_ROOT / "packaging" / "windows"


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

    assert "Resolve-ExplicitBrowserPath" in script
    assert "FLOCKS_BROWSER_EXECUTABLE_OVERRIDE" in script
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


def test_powershell_installer_is_bundled_unaware() -> None:
    """install.ps1 must not branch on FLOCKS_INSTALL_ROOT — bundled glue lives in packaging/windows/bootstrap-windows.ps1."""
    script = (SCRIPT_DIR / "install.ps1").read_text(encoding="utf-8-sig")

    # Previous iteration embedded bundled-aware helpers in install.ps1; they must not return.
    assert "Resolve-BundledChromePath" not in script
    assert "flocks-bundled-chrome.exe.relative.txt" not in script
    assert "FLOCKS_INSTALL_ROOT" not in script


def test_powershell_bootstrap_wires_bundled_toolchain() -> None:
    """packaging/windows/bootstrap-windows.ps1 is the single place that bridges the bundled layout to install.ps1."""
    script = (PACKAGING_WINDOWS_DIR / "bootstrap-windows.ps1").read_text(encoding="utf-8-sig")

    assert "Resolve-ChromeExecutablePath" in script
    assert "FLOCKS_SKIP_ADMIN_CHECK" in script
    assert "FLOCKS_BROWSER_EXECUTABLE_OVERRIDE" in script
    assert "tools\\uv" in script
    assert "tools\\node" in script
    assert "tools\\chrome" in script
    assert ".flocks\\browser" in script
    assert "mklink /J" in script
    assert 'scripts\\install_zh.ps1' in script


def test_inno_setup_points_to_packaging_bootstrap() -> None:
    """flocks-setup.iss must invoke the bootstrap from its new packaging location."""
    iss = (PACKAGING_WINDOWS_DIR / "flocks-setup.iss").read_text(encoding="utf-8")

    assert "packaging\\windows\\bootstrap-windows.ps1" in iss
    assert "scripts\\bootstrap-windows.ps1" not in iss


def test_inno_shortcuts_point_to_user_local_bin_wrapper() -> None:
    """Start-menu and desktop shortcuts must match the CLI wrapper location that
    `scripts/install.ps1` writes, so `flocks start` triggered from the shortcut
    and from a freshly opened terminal are strictly equivalent across all
    install flows (source, one-liner, bundled installer)."""
    iss = (PACKAGING_WINDOWS_DIR / "flocks-setup.iss").read_text(encoding="utf-8")

    icons_section_idx = iss.find("[Icons]")
    run_section_idx = iss.find("[Run]", icons_section_idx)
    assert icons_section_idx != -1 and run_section_idx != -1
    icons_block = iss[icons_section_idx:run_section_idx]

    expected_target = "{%USERPROFILE}\\.local\\bin\\flocks.cmd"
    start_menu_lines = [
        line
        for line in icons_block.splitlines()
        if "Start Flocks" in line or "{userdesktop}" in line
    ]
    assert start_menu_lines, "expected Start Flocks + desktop shortcut entries"
    for line in start_menu_lines:
        assert expected_target in line, (
            f"shortcut must target the shared wrapper path; got: {line}"
        )
        assert 'Parameters: "start"' in line

    # Guard against accidentally re-introducing a shortcut to {app}\bin, which
    # would point to a non-existent file because install.ps1 writes the wrapper
    # under %USERPROFILE%\.local\bin.
    assert "{app}\\bin\\flocks.cmd" not in icons_block


def test_inno_finish_page_reminds_user_to_reopen_terminal() -> None:
    """The finish page must tell the user to open a NEW terminal, because cmd.exe
    does not respond to WM_SETTINGCHANGE and pre-existing shells would otherwise
    run `flocks start` with stale env vars (no FLOCKS_NODE_HOME / updated PATH)."""
    iss = (PACKAGING_WINDOWS_DIR / "flocks-setup.iss").read_text(encoding="utf-8")

    messages_idx = iss.find("[Messages]")
    assert messages_idx != -1, "expected [Messages] section with reopen-terminal hint"
    messages_block = iss[messages_idx:]

    assert "FinishedLabel=" in messages_block
    # Bilingual hint (English + 中文) so both locales see it.
    assert "NEW terminal" in messages_block
    assert "请重新打开终端" in messages_block
    assert "flocks start" in messages_block
