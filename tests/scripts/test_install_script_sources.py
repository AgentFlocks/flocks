from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = REPO_ROOT / "scripts"


def test_install_zh_bash_bootstrap_uses_gitee_archive_and_delegates_to_zh_workspace_installer() -> None:
    script = (REPO_ROOT / "install_zh.sh").read_text(encoding="utf-8")

    assert 'https://gitee.com/%s/repository/archive/%s.zip' in script
    assert 'https://gitee.com/%s/archive/refs/tags/%s.zip' in script
    assert "printf '[flocks-bootstrap-zh] %s\\n' \"$1\" >&2" in script
    assert 'has_cmd unzip || fail "缺少 unzip，无法解压 Gitee zip 源码包。"' in script
    assert 'archive_path="$TMP_DIR/flocks.zip"' in script
    assert 'unzip -q "$archive_path" -d "$TMP_DIR"' in script
    assert 'scripts/install_zh.sh' in script
    assert 'https://gitee.com/flocks/flocks/raw/main/install_zh.sh' in script
    assert 'https://gitee.com/flocks/flocks/raw/main/install_zh.ps1' in script
    assert 'PUPPETEER_CHROME_DOWNLOAD_BASE_URL' in script
    assert 'https://cdn.npmmirror.com/binaries/chrome-for-testing' in script


def test_install_zh_powershell_bootstrap_uses_gitee_archive_and_delegates_to_zh_workspace_installer() -> None:
    script = (REPO_ROOT / "install_zh.ps1").read_text(encoding="utf-8-sig")

    assert 'https://gitee.com/$RepoSlug/repository/archive/$Version.zip' in script
    assert 'https://gitee.com/$RepoSlug/archive/refs/tags/$Version.zip' in script
    assert 'scripts\\install_zh.ps1' in script
    assert 'https://gitee.com/flocks/flocks/raw/main/install_zh.sh' in script
    assert 'https://gitee.com/flocks/flocks/raw/main/install_zh.ps1' in script
    assert 'PUPPETEER_CHROME_DOWNLOAD_BASE_URL' in script
    assert 'https://cdn.npmmirror.com/binaries/chrome-for-testing' in script


def test_install_zh_bash_wrapper_sets_cn_sources_and_reuses_main_installer() -> None:
    script = (SCRIPT_DIR / "install_zh.sh").read_text(encoding="utf-8")

    assert 'FLOCKS_INSTALL_REPO_URL' in script
    assert 'https://gitee.com/flocks/flocks.git' in script
    assert 'FLOCKS_RAW_INSTALL_SH_URL' in script
    assert 'https://gitee.com/flocks/flocks/raw/main/install_zh.sh' in script
    assert 'FLOCKS_RAW_INSTALL_PS1_URL' in script
    assert 'https://gitee.com/flocks/flocks/raw/main/install_zh.ps1' in script
    assert 'PUPPETEER_CHROME_DOWNLOAD_BASE_URL' in script
    assert 'https://cdn.npmmirror.com/binaries/chrome-for-testing' in script
    assert 'FLOCKS_UV_DEFAULT_INDEX' in script
    assert 'https://mirrors.aliyun.com/pypi/simple' in script
    assert 'FLOCKS_NPM_REGISTRY' in script
    assert 'https://registry.npmmirror.com/' in script
    assert 'FLOCKS_NODEJS_MANUAL_DOWNLOAD_URL' in script
    assert "https://nodejs.org/zh-cn/download" in script
    assert 'exec bash "$SCRIPT_DIR/install.sh" "$@"' in script


def test_install_zh_powershell_wrapper_sets_cn_sources_and_reuses_main_installer() -> None:
    script = (SCRIPT_DIR / "install_zh.ps1").read_text(encoding="utf-8-sig")

    assert 'FLOCKS_INSTALL_REPO_URL' in script
    assert 'https://gitee.com/flocks/flocks.git' in script
    assert 'FLOCKS_RAW_INSTALL_SH_URL' in script
    assert 'https://gitee.com/flocks/flocks/raw/main/install_zh.sh' in script
    assert 'FLOCKS_RAW_INSTALL_PS1_URL' in script
    assert 'https://gitee.com/flocks/flocks/raw/main/install_zh.ps1' in script
    assert 'PUPPETEER_CHROME_DOWNLOAD_BASE_URL' in script
    assert 'https://cdn.npmmirror.com/binaries/chrome-for-testing' in script
    assert 'FLOCKS_UV_DEFAULT_INDEX' in script
    assert 'https://mirrors.aliyun.com/pypi/simple' in script
    assert 'FLOCKS_NPM_REGISTRY' in script
    assert 'https://registry.npmmirror.com/' in script
    assert 'FLOCKS_NODEJS_MANUAL_DOWNLOAD_URL' in script
    assert "https://nodejs.org/zh-cn/download" in script
    assert 'Join-Path $PSScriptRoot "install.ps1"' in script


def test_main_bash_installer_uses_configured_default_sources_without_probing() -> None:
    script = (SCRIPT_DIR / "install.sh").read_text(encoding="utf-8")

    assert 'FLOCKS_UV_DEFAULT_INDEX' in script
    assert 'FLOCKS_NPM_REGISTRY' in script
    assert 'Using PyPI index: $UV_DEFAULT_INDEX' in script
    assert 'Using npm registry: $NPM_REGISTRY' in script
    assert 'pick_fastest_url' not in script
    assert 'Probing PyPI and npm registries to choose the faster source' not in script
    assert 'npm_config_registry="$NPM_REGISTRY" npm install' in script
    assert 'npm_config_registry="$NPM_REGISTRY" npm install --global agent-browser' in script
    assert "FLOCKS_NODEJS_MANUAL_DOWNLOAD_URL" in script
    assert "https://nodejs.org/en/download" in script
    assert "nodejs_manual_download_hint" in script


def test_main_powershell_installer_uses_configured_default_sources_without_probing() -> None:
    script = (SCRIPT_DIR / "install.ps1").read_text(encoding="utf-8-sig")

    assert 'FLOCKS_UV_DEFAULT_INDEX' in script
    assert 'FLOCKS_NPM_REGISTRY' in script
    assert 'Using PyPI index: $script:UvDefaultIndex' in script
    assert 'Using npm registry: $script:NpmRegistry' in script
    assert 'Select-FastestUrl' not in script
    assert 'Probing PyPI and npm registries to choose the faster source' not in script
    assert '-Environment @{ npm_config_registry = $script:NpmRegistry }' in script
    assert "FLOCKS_NODEJS_MANUAL_DOWNLOAD_URL" in script
    assert "https://nodejs.org/en/download" in script
    assert "Get-NodejsManualDownloadHint" in script
