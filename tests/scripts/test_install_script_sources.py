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
    assert 'FLOCKS_UV_INSTALL_SH_FALLBACK_URL' in script
    assert 'https://uv.agentsmirror.com/install-cn.sh' in script
    assert 'FLOCKS_UV_INSTALL_PS1_URL' in script
    assert 'https://astral.org.cn/uv/install.ps1' in script
    assert 'FLOCKS_NPM_REGISTRY' in script
    assert 'https://registry.npmmirror.com/' in script
    assert 'FLOCKS_NVM_INSTALL_SCRIPT_URL' in script
    assert 'https://gitee.com/mirrors/nvm/raw/v0.40.3/install.sh' in script
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
    assert 'FLOCKS_NVM_INSTALL_SCRIPT_URL' in script
    assert 'https://gitee.com/mirrors/nvm/raw/v0.40.3/install.sh' in script
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
    assert 'FLOCKS_UV_INSTALL_SH_FALLBACK_URL' in script
    assert 'FLOCKS_NPM_REGISTRY' in script
    assert 'Using PyPI index: $UV_DEFAULT_INDEX' in script
    assert 'Using npm registry: $NPM_REGISTRY' in script
    assert 'Using uv install script: $UV_INSTALL_SH_URL' in script
    assert 'Using nvm install script: $NVM_INSTALL_SCRIPT_URL' in script
    assert 'Using uv fallback script' not in script
    assert '使用 uv 备用安装脚本: $UV_INSTALL_SH_FALLBACK_URL' in script
    assert 'pick_fastest_url' not in script
    assert 'Probing PyPI and npm registries to choose the faster source' not in script
    assert 'npm_config_registry="$NPM_REGISTRY" npm install' in script
    assert 'npm_config_registry="$NPM_REGISTRY" npx --yes @puppeteer/browsers install chrome@stable --path "$browser_dir"' in script
    assert 'npm_config_registry="$NPM_REGISTRY" npm install --global agent-browser' in script
    assert 'local connector_dir="$ROOT_DIR/.flocks/plugins/channels/dingtalk/dingtalk-openclaw-connector"' in script
    assert "FLOCKS_NODEJS_MANUAL_DOWNLOAD_URL" in script
    assert "https://nodejs.org/en/download" in script
    assert "nodejs_manual_download_hint" in script
    assert "FLOCKS_NVM_INSTALL_SCRIPT_URL" in script
    assert "https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh" in script
    assert "FLOCKS_NVM_GITEE_REPO_URL" in script
    assert "https://gitee.com/mirrors/nvm.git" in script
    assert "FLOCKS_NVM_GITEE_RAW_URL_PREFIX" in script
    assert "https://gitee.com/mirrors/nvm/raw" in script
    assert "load_nvm()" in script
    assert "should_patch_nvm_install_script_for_gitee()" in script
    assert "patch_nvm_install_script_for_gitee()" in script
    assert "run_nvm_install_script()" in script
    assert "install_nodejs_with_nvm()" in script
    assert "install_nodejs_linux_with_package_manager()" in script
    assert 'curl -fsSL "$NVM_INSTALL_SCRIPT_URL" -o "$install_script"' in script
    assert 'curl -LsSf "$UV_INSTALL_SH_URL" | sh' in script
    assert 'curl -LsSf "$UV_INSTALL_SH_FALLBACK_URL" | sh' in script
    assert 'nvm install "$MIN_NODE_MAJOR"' in script
    assert 'nvm use "$MIN_NODE_MAJOR" >/dev/null' in script
    assert "Homebrew failed to install Node.js. Falling back to nvm..." in script
    assert 'info "Trying to install Node.js ${MIN_NODE_MAJOR} with nvm first on Linux..."' in script
    assert 'warn "nvm installation failed on Linux. Falling back to the system package manager..."' in script
    assert "https://github.com/" in script
    assert "https://raw.githubusercontent.com/" in script


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
          local output_file=""
          while [[ $# -gt 0 ]]; do
            case "$1" in
              -o)
                output_file="$2"
                shift 2
                ;;
              *)
                shift
                ;;
            esac
          done

          cat > "$output_file" <<'EOF'
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


def test_main_bash_installer_falls_back_to_nvm_when_brew_install_fails_on_macos() -> None:
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

        warn() {
          printf 'WARN:%s\n' "$1" >> "$TEST_LOG"
        }

        fail() {
          printf 'FAIL:%s\n' "$1" >&2
          exit 1
        }

        has_cmd() {
          case "$1" in
            brew|curl)
              return 0
              ;;
            *)
              command -v "$1" >/dev/null 2>&1
              ;;
          esac
        }

        brew() {
          printf '%s\n' "$*" >> "$HOME/brew-commands.log"
          return 1
        }

        curl() {
          local output_file=""
          while [[ $# -gt 0 ]]; do
            case "$1" in
              -o)
                output_file="$2"
                shift 2
                ;;
              *)
                shift
                ;;
            esac
          done

          cat > "$output_file" <<'EOF'
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
        brew_commands="$(<"$HOME/brew-commands.log")"
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
        [[ "$brew_commands" == *"install node"* ]] || {
          printf 'brew install node was not attempted: %s\n' "$brew_commands" >&2
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
        [[ "$install_log" == *"WARN:Homebrew failed to install Node.js. Falling back to nvm..."* ]] || {
          printf 'brew fallback warning missing: %s\n' "$install_log" >&2
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


def test_main_bash_installer_uses_cn_uv_fallback_when_primary_script_fails() -> None:
    script = (SCRIPT_DIR / "install.sh").read_text(encoding="utf-8")
    script_without_main = re.sub(r'\nmain "\$@"\s*$', "\n", script)
    test_script = script_without_main + textwrap.dedent(
        r"""

        export HOME="$(mktemp -d)"
        export FLOCKS_INSTALL_LANGUAGE="zh-CN"
        export FLOCKS_UV_INSTALL_SH_URL="https://primary.example/install.sh"
        export FLOCKS_UV_INSTALL_SH_FALLBACK_URL="https://uv.agentsmirror.com/install-cn.sh"
        export TEST_LOG="$HOME/install-uv.log"
        INSTALL_LANGUAGE="$FLOCKS_INSTALL_LANGUAGE"
        UV_INSTALL_SH_URL="$FLOCKS_UV_INSTALL_SH_URL"
        UV_INSTALL_SH_FALLBACK_URL="$FLOCKS_UV_INSTALL_SH_FALLBACK_URL"

        has_cmd() {
          case "$1" in
            curl)
              return 0
              ;;
            uv)
              [[ -f "$HOME/uv-installed" ]]
              return $?
              ;;
            *)
              command -v "$1" >/dev/null 2>&1
              ;;
          esac
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

        ensure_path_persisted() {
          :
        }

        curl() {
          printf '%s\n' "$*" >> "$HOME/curl-commands.log"
          if [[ "$*" == *"primary.example"* ]]; then
            return 22
          fi

          cat <<'EOF'
        touch "$HOME/uv-installed"
        EOF
        }

        install_uv

        curl_commands="$(<"$HOME/curl-commands.log")"
        install_log="$(<"$TEST_LOG")"

        [[ -f "$HOME/uv-installed" ]] || {
          printf 'uv was not installed by fallback script\n' >&2
          exit 1
        }
        [[ "$curl_commands" == *"https://primary.example/install.sh"* ]] || {
          printf 'primary uv script was not attempted: %s\n' "$curl_commands" >&2
          exit 1
        }
        [[ "$curl_commands" == *"https://uv.agentsmirror.com/install-cn.sh"* ]] || {
          printf 'fallback uv script was not attempted: %s\n' "$curl_commands" >&2
          exit 1
        }
        [[ "$install_log" == *"默认 uv 安装脚本失败，正在尝试中国大陆备用源"* ]] || {
          printf 'fallback log missing: %s\n' "$install_log" >&2
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


def test_main_bash_installer_patches_gitee_nvm_script_before_execution() -> None:
    script = (SCRIPT_DIR / "install.sh").read_text(encoding="utf-8")
    script_without_main = re.sub(r'\nmain "\$@"\s*$', "\n", script)
    test_script = script_without_main + textwrap.dedent(
        r"""

        export HOME="$(mktemp -d)"
        export NVM_INSTALL_SCRIPT_URL="https://gitee.com/mirrors/nvm/raw/v0.40.3/install.sh"
        export NVM_GITEE_REPO_URL="https://gitee.com/mirrors/nvm.git"
        export NVM_GITEE_RAW_URL_PREFIX="https://gitee.com/mirrors/nvm/raw"

        curl() {
          local output_file=""
          while [[ $# -gt 0 ]]; do
            case "$1" in
              -o)
                output_file="$2"
                shift 2
                ;;
              *)
                shift
                ;;
            esac
          done

          cat > "$output_file" <<'EOF'
        #!/usr/bin/env bash
        printf '%s\n' 'https://github.com/${NVM_GITHUB_REPO}.git' > "$HOME/patched-nvm-urls.txt"
        printf '%s\n' 'https://raw.githubusercontent.com/${NVM_GITHUB_REPO}/${NVM_VERSION}/nvm.sh' >> "$HOME/patched-nvm-urls.txt"
        printf '%s\n' 'https://raw.githubusercontent.com/${NVM_GITHUB_REPO}/${NVM_VERSION}/nvm-exec' >> "$HOME/patched-nvm-urls.txt"
        printf '%s\n' 'https://raw.githubusercontent.com/${NVM_GITHUB_REPO}/${NVM_VERSION}/bash_completion' >> "$HOME/patched-nvm-urls.txt"
        EOF
        }

        run_nvm_install_script

        patched_urls="$(<"$HOME/patched-nvm-urls.txt")"
        [[ "$patched_urls" == *"https://gitee.com/mirrors/nvm.git"* ]] || {
          printf 'git url was not patched: %s\n' "$patched_urls" >&2
          exit 1
        }
        [[ "$patched_urls" == *"https://gitee.com/mirrors/nvm/raw/\${NVM_VERSION}/nvm.sh"* ]] || {
          printf 'nvm.sh url was not patched: %s\n' "$patched_urls" >&2
          exit 1
        }
        [[ "$patched_urls" == *"https://gitee.com/mirrors/nvm/raw/\${NVM_VERSION}/nvm-exec"* ]] || {
          printf 'nvm-exec url was not patched: %s\n' "$patched_urls" >&2
          exit 1
        }
        [[ "$patched_urls" == *"https://gitee.com/mirrors/nvm/raw/\${NVM_VERSION}/bash_completion"* ]] || {
          printf 'bash_completion url was not patched: %s\n' "$patched_urls" >&2
          exit 1
        }
        [[ "$patched_urls" != *"github.com"* ]] || {
          printf 'github url remained after patch: %s\n' "$patched_urls" >&2
          exit 1
        }
        [[ "$patched_urls" != *"raw.githubusercontent.com"* ]] || {
          printf 'raw github url remained after patch: %s\n' "$patched_urls" >&2
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


def test_main_bash_installer_prefers_nvm_on_linux_before_package_manager() -> None:
    script = (SCRIPT_DIR / "install.sh").read_text(encoding="utf-8")
    script_without_main = re.sub(r'\nmain "\$@"\s*$', "\n", script)
    test_script = script_without_main + textwrap.dedent(
        r"""

        export HOME="$(mktemp -d)"
        export TEST_LOG="$HOME/install-node.log"

        info() {
          printf 'INFO:%s\n' "$1" >> "$TEST_LOG"
        }

        fail() {
          printf 'FAIL:%s\n' "$1" >&2
          exit 1
        }

        has_cmd() {
          case "$1" in
            curl|pacman)
              return 0
              ;;
            *)
              command -v "$1" >/dev/null 2>&1
              ;;
          esac
        }

        curl() {
          local output_file=""
          while [[ $# -gt 0 ]]; do
            case "$1" in
              -o)
                output_file="$2"
                shift 2
                ;;
              *)
                shift
                ;;
            esac
          done

          cat > "$output_file" <<'EOF'
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

        run_with_privilege() {
          printf '%s\n' "$*" >> "$HOME/pkg-commands.log"
          return 0
        }

        install_nodejs_linux

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
        [[ "$install_log" == *"INFO:Trying to install Node.js 22 with nvm first on Linux..."* ]] || {
          printf 'linux nvm-first log missing: %s\n' "$install_log" >&2
          exit 1
        }
        [[ ! -f "$HOME/pkg-commands.log" ]] || {
          printf 'package manager fallback should not run when nvm succeeds: %s\n' "$(<"$HOME/pkg-commands.log")" >&2
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


def test_main_bash_installer_falls_back_to_package_manager_when_nvm_fails_on_linux() -> None:
    script = (SCRIPT_DIR / "install.sh").read_text(encoding="utf-8")
    script_without_main = re.sub(r'\nmain "\$@"\s*$', "\n", script)
    test_script = script_without_main + textwrap.dedent(
        r"""

        export HOME="$(mktemp -d)"
        export TEST_LOG="$HOME/install-node.log"
        export TEST_URL="https://example.invalid/nvm-install.sh"
        NVM_INSTALL_SCRIPT_URL="$TEST_URL"

        info() {
          printf 'INFO:%s\n' "$1" >> "$TEST_LOG"
        }

        warn() {
          printf 'WARN:%s\n' "$1" >> "$TEST_LOG"
        }

        fail() {
          printf 'FAIL:%s\n' "$1" >&2
          exit 1
        }

        nodejs_manual_download_hint() {
          printf ''
        }

        has_cmd() {
          case "$1" in
            curl|pacman)
              return 0
              ;;
            *)
              return 1
              ;;
          esac
        }

        curl() {
          printf '%s\n' "$*" >> "$HOME/curl-commands.log"
          return 22
        }

        run_with_privilege() {
          printf '%s\n' "$*" >> "$HOME/pkg-commands.log"
          return 0
        }

        install_nodejs_linux

        curl_commands="$(<"$HOME/curl-commands.log")"
        pkg_commands="$(<"$HOME/pkg-commands.log")"
        install_log="$(<"$TEST_LOG")"

        [[ "$curl_commands" == *"$TEST_URL"* ]] || {
          printf 'nvm install url was not attempted: %s\n' "$curl_commands" >&2
          exit 1
        }
        [[ "$pkg_commands" == *"pacman -Sy --noconfirm nodejs npm"* ]] || {
          printf 'package manager fallback was not used: %s\n' "$pkg_commands" >&2
          exit 1
        }
        [[ "$install_log" == *"WARN:Failed to install nvm from: $TEST_URL"* ]] || {
          printf 'nvm failure warning missing: %s\n' "$install_log" >&2
          exit 1
        }
        [[ "$install_log" == *"WARN:nvm installation failed on Linux. Falling back to the system package manager..."* ]] || {
          printf 'linux fallback warning missing: %s\n' "$install_log" >&2
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
