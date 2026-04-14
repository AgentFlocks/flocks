import re
import subprocess
import textwrap
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
    assert 'FLOCKS_INSTALL_LANGUAGE' in script
    assert 'zh-CN' in script
    assert 'FLOCKS_UV_INSTALL_SH_URL' in script
    assert 'https://astral.org.cn/uv/install.sh' in script
    assert 'FLOCKS_UV_INSTALL_PS1_URL' in script
    assert 'https://astral.org.cn/uv/install.ps1' in script
    assert 'PUPPETEER_CHROME_DOWNLOAD_BASE_URL' in script
    assert 'https://cdn.npmmirror.com/binaries/chrome-for-testing' in script


def test_install_zh_powershell_bootstrap_uses_gitee_archive_and_delegates_to_zh_workspace_installer() -> None:
    script = (REPO_ROOT / "install_zh.ps1").read_text(encoding="utf-8-sig")

    assert 'https://gitee.com/$RepoSlug/repository/archive/$Version.zip' in script
    assert 'https://gitee.com/$RepoSlug/archive/refs/tags/$Version.zip' in script
    assert 'scripts\\install_zh.ps1' in script
    assert 'https://gitee.com/flocks/flocks/raw/main/install_zh.sh' in script
    assert 'https://gitee.com/flocks/flocks/raw/main/install_zh.ps1' in script
    assert 'FLOCKS_INSTALL_LANGUAGE' in script
    assert 'zh-CN' in script
    assert 'FLOCKS_UV_INSTALL_SH_URL' in script
    assert 'https://astral.org.cn/uv/install.sh' in script
    assert 'FLOCKS_UV_INSTALL_PS1_URL' in script
    assert 'https://astral.org.cn/uv/install.ps1' in script
    assert 'FLOCKS_UV_INSTALL_PS1_FALLBACK_URL' in script
    assert 'https://uv.agentsmirror.com/install-cn.ps1' in script
    assert 'PUPPETEER_CHROME_DOWNLOAD_BASE_URL' in script
    assert 'https://cdn.npmmirror.com/binaries/chrome-for-testing' in script


def test_install_zh_bash_wrapper_sets_cn_sources_and_reuses_main_installer() -> None:
    script = (SCRIPT_DIR / "install_zh.sh").read_text(encoding="utf-8")

    assert 'FLOCKS_INSTALL_LANGUAGE' in script
    assert 'zh-CN' in script
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
    assert 'FLOCKS_UV_INSTALL_SH_URL' in script
    assert 'https://astral.org.cn/uv/install.sh' in script
    assert 'FLOCKS_UV_INSTALL_PS1_URL' in script
    assert 'https://astral.org.cn/uv/install.ps1' in script
    assert 'FLOCKS_NPM_REGISTRY' in script
    assert 'https://registry.npmmirror.com/' in script
    assert 'FLOCKS_NODEJS_MANUAL_DOWNLOAD_URL' in script
    assert "https://nodejs.org/zh-cn/download" in script
    assert 'exec bash "$SCRIPT_DIR/install.sh" "$@"' in script


def test_install_zh_powershell_wrapper_sets_cn_sources_and_reuses_main_installer() -> None:
    script = (SCRIPT_DIR / "install_zh.ps1").read_text(encoding="utf-8-sig")

    assert 'FLOCKS_INSTALL_LANGUAGE' in script
    assert 'zh-CN' in script
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
    assert 'FLOCKS_UV_INSTALL_SH_URL' in script
    assert 'https://astral.org.cn/uv/install.sh' in script
    assert 'FLOCKS_UV_INSTALL_PS1_URL' in script
    assert 'https://astral.org.cn/uv/install.ps1' in script
    assert 'FLOCKS_UV_INSTALL_PS1_FALLBACK_URL' in script
    assert 'https://uv.agentsmirror.com/install-cn.ps1' in script
    assert 'FLOCKS_NPM_REGISTRY' in script
    assert 'https://registry.npmmirror.com/' in script
    assert 'FLOCKS_NODEJS_MANUAL_DOWNLOAD_URL' in script
    assert "https://nodejs.org/zh-cn/download" in script
    assert 'Join-Path $PSScriptRoot "install.ps1"' in script


def test_main_bash_installer_uses_configured_default_sources_without_probing() -> None:
    script = (SCRIPT_DIR / "install.sh").read_text(encoding="utf-8")

    assert 'FLOCKS_INSTALL_LANGUAGE' in script
    assert 'FLOCKS_UV_DEFAULT_INDEX' in script
    assert 'FLOCKS_UV_INSTALL_SH_URL' in script
    assert 'https://astral.sh/uv/install.sh' in script
    assert 'FLOCKS_NPM_REGISTRY' in script
    assert 'Using PyPI index: $UV_DEFAULT_INDEX' in script
    assert 'Using npm registry: $NPM_REGISTRY' in script
    assert 'Using uv install script: $UV_INSTALL_SH_URL' in script
    assert 'pick_fastest_url' not in script
    assert 'Probing PyPI and npm registries to choose the faster source' not in script
    assert 'npm_config_registry="$NPM_REGISTRY" npm install' in script
    assert 'npm_config_registry="$NPM_REGISTRY" npm install --global agent-browser' in script
    assert "FLOCKS_NODEJS_MANUAL_DOWNLOAD_URL" in script
    assert "https://nodejs.org/en/download" in script
    assert "nodejs_manual_download_hint" in script
    assert "FLOCKS_NVM_INSTALL_SCRIPT_URL" in script
    assert "https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh" in script
    assert "load_nvm()" in script
    assert 'curl -o- "$NVM_INSTALL_SCRIPT_URL" | bash' in script
    assert 'curl -LsSf "$UV_INSTALL_SH_URL" | sh' in script
    assert 'nvm install "$MIN_NODE_MAJOR"' in script
    assert 'nvm use "$MIN_NODE_MAJOR" >/dev/null' in script
    assert "Homebrew was not found. Trying to install nvm..." in script
    assert "Homebrew was not found. Using the existing nvm installation..." in script


def test_main_powershell_installer_uses_configured_default_sources_and_admin_precheck() -> None:
    script = (SCRIPT_DIR / "install.ps1").read_text(encoding="utf-8-sig")

    assert 'FLOCKS_INSTALL_LANGUAGE' in script
    assert 'FLOCKS_UV_DEFAULT_INDEX' in script
    assert 'FLOCKS_UV_INSTALL_PS1_URL' in script
    assert 'FLOCKS_UV_INSTALL_PS1_FALLBACK_URL' in script
    assert 'https://astral.sh/uv/install.ps1' in script
    assert 'https://uv.agentsmirror.com/install-cn.ps1' in script
    assert 'Using PyPI index: $script:UvDefaultIndex' in script
    assert 'Using npm registry: $script:NpmRegistry' in script
    assert 'Using uv install script: $script:UvInstallPs1Url' in script
    assert "irm '$script:UvInstallPs1Url' | iex" in script
    assert "irm '$script:UvInstallPs1FallbackUrl' | iex" in script
    assert 'function Assert-Administrator' in script
    assert 'Assert-Administrator' in script


def test_windows_powershell_installers_require_admin_before_install() -> None:
    for path in (
        REPO_ROOT / "install.ps1",
        REPO_ROOT / "install_zh.ps1",
        SCRIPT_DIR / "install.ps1",
        SCRIPT_DIR / "install_zh.ps1",
    ):
        script = path.read_text(encoding="utf-8-sig")
        assert 'function Test-IsAdministrator' in script
        assert 'function Assert-Administrator' in script


def test_windows_bootstrap_installers_only_create_missing_parent_directories() -> None:
    for path in (
        REPO_ROOT / "install.ps1",
        REPO_ROOT / "install_zh.ps1",
    ):
        script = path.read_text(encoding="utf-8-sig")
        assert "Split-Path -Parent $InstallDir" in script
        assert "Test-Path -LiteralPath $installParent" in script


def test_main_bash_installer_falls_back_to_nvm_when_brew_is_missing_on_macos() -> None:
    script = (SCRIPT_DIR / "install.sh").read_text(encoding="utf-8")
    script_without_main = re.sub(r'\nmain "\$@"\s*$', "\n", script)
    test_script = script_without_main + textwrap.dedent(
        r"""

        export HOME="$(mktemp -d)"
        unset NVM_DIR
        export TEST_LOG="$HOME/install-node.log"

        info() {
          printf '%s\n' "$1" >> "$TEST_LOG"
        }

        fail() {
          printf 'FAIL:%s\n' "$1" >&2
          exit 1
        }

        has_cmd() {
          case "$1" in
            brew)
              return 1
              ;;
            curl)
              return 0
              ;;
            *)
              command -v "$1" >/dev/null 2>&1
              ;;
          esac
        }

        curl() {
          cat <<'EOF'
        mkdir -p "$HOME/.nvm"
        cat > "$HOME/.nvm/nvm.sh" <<'EOS'
        nvm() {
          printf '%s\n' "$*" >> "$HOME/nvm-commands.log"
          if [[ "$1" == "install" ]]; then
            mkdir -p "$HOME/.nvm/versions/node/v22.22.2/bin"
            cat > "$HOME/.nvm/versions/node/v22.22.2/bin/node" <<'EON'
        #!/usr/bin/env bash
        printf 'v22.22.2\n'
        EON
            cat > "$HOME/.nvm/versions/node/v22.22.2/bin/npm" <<'EON'
        #!/usr/bin/env bash
        printf '10.9.7\n'
        EON
            chmod +x "$HOME/.nvm/versions/node/v22.22.2/bin/node" "$HOME/.nvm/versions/node/v22.22.2/bin/npm"
            export PATH="$HOME/.nvm/versions/node/v22.22.2/bin:$PATH"
            return 0
          fi
          if [[ "$1" == "use" ]]; then
            export PATH="$HOME/.nvm/versions/node/v22.22.2/bin:$PATH"
            return 0
          fi
          return 0
        }
        EOS
        EOF
        }

        install_nodejs_macos

        node_version="$(node -v)"
        npm_version="$(npm -v)"
        nvm_commands="$(<"$HOME/nvm-commands.log")"
        install_log="$(<"$TEST_LOG")"

        [[ "$node_version" == "v22.22.2" ]] || {
          printf 'unexpected node version: %s\n' "$node_version" >&2
          exit 1
        }
        [[ "$npm_version" == "10.9.7" ]] || {
          printf 'unexpected npm version: %s\n' "$npm_version" >&2
          exit 1
        }
        [[ "$nvm_commands" == *"install 22"* ]] || {
          printf 'nvm install was not called: %s\n' "$nvm_commands" >&2
          exit 1
        }
        [[ "$nvm_commands" == *"use 22"* ]] || {
          printf 'nvm use was not called: %s\n' "$nvm_commands" >&2
          exit 1
        }
        [[ "$install_log" == *"Trying to install nvm"* ]] || {
          printf 'nvm install message missing: %s\n' "$install_log" >&2
          exit 1
        }
        """
    )

    result = subprocess.run(
        ["bash", "-c", test_script],
        check=False,
        capture_output=True,
        text=True,
    )

    output = f"{result.stdout}\n{result.stderr}"
    assert result.returncode == 0, output


def test_main_bash_installer_checks_node_modules_dir_before_accepting_global_prefix() -> None:
    script = (SCRIPT_DIR / "install.sh").read_text(encoding="utf-8")
    script_without_main = re.sub(r'\nmain "\$@"\s*$', "\n", script)
    test_script = script_without_main + textwrap.dedent(
        r"""

        export HOME="$(mktemp -d)"
        export TEST_PREFIX="$HOME/system-prefix"
        export TEST_LOG="$HOME/npm-prefix.log"
        mkdir -p "$TEST_PREFIX/lib/node_modules"

        chmod 755 "$TEST_PREFIX"
        chmod 755 "$TEST_PREFIX/lib"
        chmod 555 "$TEST_PREFIX/lib/node_modules"

        has_cmd() {
          [[ "$1" == "npm" ]]
        }

        nodejs_manual_download_hint() {
          printf ''
        }

        info() {
          printf '%s\n' "$1" >> "$TEST_LOG"
        }

        fail() {
          printf 'FAIL:%s\n' "$1" >&2
          exit 1
        }

        refresh_path() {
          :
        }

        npm() {
          if [[ "$1" == "config" && "$2" == "get" && "$3" == "prefix" ]]; then
            printf '%s\n' "$TEST_PREFIX"
            return 0
          fi

          if [[ "$1" == "config" && "$2" == "set" && "$3" == "prefix" ]]; then
            printf '%s\n' "$4" > "$HOME/npm-prefix-set.txt"
            return 0
          fi

          printf 'unexpected npm invocation: %s\n' "$*" >&2
          exit 1
        }

        ensure_npm_global_prefix_writable

        configured_prefix="$(<"$HOME/npm-prefix-set.txt")"
        install_log="$(<"$TEST_LOG")"

        [[ "$configured_prefix" == "$HOME/.npm-global" ]] || {
          printf 'unexpected configured prefix: %s\n' "$configured_prefix" >&2
          exit 1
        }
        [[ "$install_log" == *"Switching to user prefix"* ]] || {
          printf 'missing fallback log: %s\n' "$install_log" >&2
          exit 1
        }
        """
    )

    result = subprocess.run(
        ["bash", "-c", test_script],
        check=False,
        capture_output=True,
        text=True,
    )

    output = f"{result.stdout}\n{result.stderr}"
    assert result.returncode == 0, output


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
