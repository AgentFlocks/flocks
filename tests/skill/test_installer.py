"""
Tests for flocks.skill.installer and eligibility checking.
"""

import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flocks.skill.skill import Skill, SkillInfo, SkillInstallSpec, SkillRequires, SkillMetadata
from flocks.skill.installer import (
    SkillInstaller,
    SkillInstallResult,
    DepInstallResult,
    _resolve_source,
    _user_skills_root,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_skill_cache():
    """Clear Skill cache before each test."""
    Skill.clear_cache()
    yield
    Skill.clear_cache()


@pytest.fixture
def tmp_skills_dir(tmp_path: Path):
    """Temp directory to serve as the user skills root."""
    d = tmp_path / ".flocks" / "plugins" / "skills"
    d.mkdir(parents=True)
    return d


def make_skill_info(
    name: str = "test-skill",
    requires_bins: list | None = None,
    requires_env: list | None = None,
    install_specs: list | None = None,
) -> SkillInfo:
    requires = None
    if requires_bins or requires_env:
        requires = SkillRequires(bins=requires_bins, env=requires_env)

    specs = None
    if install_specs:
        specs = [SkillInstallSpec(**s) for s in install_specs]

    return SkillInfo(
        name=name,
        description="A test skill",
        location=f"/nonexistent/skills/{name}/SKILL.md",
        requires=requires,
        install_specs=specs,
    )


# ---------------------------------------------------------------------------
# _resolve_source
# ---------------------------------------------------------------------------

class TestResolveSource:
    def test_safeskill_scheme(self):
        r = _resolve_source("safeskill:ioc-lookup")
        assert r["kind"] == "safeskill"
        assert r["value"] == "ioc-lookup"

    def test_clawhub_scheme(self):
        r = _resolve_source("clawhub:github")
        assert r["kind"] == "clawhub"
        assert r["value"] == "github"

    def test_github_scheme(self):
        r = _resolve_source("github:owner/repo")
        assert r["kind"] == "github"
        assert r["value"] == "owner/repo"

    def test_github_url(self):
        r = _resolve_source("https://github.com/owner/repo")
        assert r["kind"] == "github"
        assert r["value"] == "owner/repo"

    def test_github_url_with_subpath(self):
        r = _resolve_source("https://github.com/owner/repo/tree/main/subdir")
        assert r["kind"] == "github"
        assert "owner/repo" in r["value"]

    def test_github_blob_url_with_skill_directory_subpath(self):
        r = _resolve_source(
            "https://github.com/mattpocock/skills/blob/main/skills/engineering/diagnose"
        )
        assert r["kind"] == "github"
        assert r["value"] == "mattpocock/skills/skills/engineering/diagnose"

    def test_github_blob_url_to_skill_md_uses_parent_directory(self):
        r = _resolve_source(
            "https://github.com/mattpocock/skills/blob/main/skills/engineering/diagnose/SKILL.md"
        )
        assert r["kind"] == "github"
        assert r["value"] == "mattpocock/skills/skills/engineering/diagnose"

    def test_github_scheme_blob_path_with_subpath(self):
        r = _resolve_source(
            "github:mattpocock/skills/blob/main/skills/engineering/diagnose"
        )
        assert r["kind"] == "github"
        assert r["value"] == "mattpocock/skills/skills/engineering/diagnose"

    def test_https_url(self):
        r = _resolve_source("https://example.com/SKILL.md")
        assert r["kind"] == "url"
        assert r["value"] == "https://example.com/SKILL.md"

    def test_skills_sh_url(self):
        r = _resolve_source("https://www.skills.sh/owner/repo/demo")
        assert r["kind"] == "skills_sh"
        assert r["value"] == "owner/repo/demo"

    def test_local_absolute(self):
        r = _resolve_source("/home/user/skills/my-skill")
        assert r["kind"] == "local"
        assert r["value"] == "/home/user/skills/my-skill"

    def test_local_relative(self):
        r = _resolve_source("./my-skill")
        assert r["kind"] == "local"

    def test_shorthand_owner_repo(self):
        r = _resolve_source("owner/repo")
        assert r["kind"] == "github"
        assert r["value"] == "owner/repo"


# ---------------------------------------------------------------------------
# SkillInfo eligibility checking
# ---------------------------------------------------------------------------

class TestCheckEligibility:
    def test_no_requires_is_eligible(self):
        skill = make_skill_info("no-reqs")
        result = Skill.check_eligibility(skill)
        assert result.eligible is True
        assert result.missing == []

    def test_bin_present(self):
        skill = make_skill_info("bin-skill", requires_bins=["python3"])
        result = Skill.check_eligibility(skill)
        # python3 should be present in the test environment
        assert result.eligible is True
        assert result.missing == []

    def test_bin_missing(self):
        skill = make_skill_info("bin-skill", requires_bins=["__nonexistent_binary__"])
        result = Skill.check_eligibility(skill)
        assert result.eligible is False
        assert any("bin:__nonexistent_binary__" in m for m in (result.missing or []))

    def test_env_present(self):
        os.environ["TEST_FLOCKS_VAR"] = "hello"
        try:
            skill = make_skill_info("env-skill", requires_env=["TEST_FLOCKS_VAR"])
            result = Skill.check_eligibility(skill)
            assert result.eligible is True
        finally:
            del os.environ["TEST_FLOCKS_VAR"]

    def test_env_missing(self):
        skill = make_skill_info("env-skill", requires_env=["__MISSING_ENV_VAR__"])
        result = Skill.check_eligibility(skill)
        assert result.eligible is False
        assert any("env:__MISSING_ENV_VAR__" in m for m in (result.missing or []))

    def test_multiple_missing(self):
        skill = make_skill_info(
            "multi-miss",
            requires_bins=["__no_bin_1__", "__no_bin_2__"],
            requires_env=["__NO_ENV_VAR__"],
        )
        result = Skill.check_eligibility(skill)
        assert result.eligible is False
        assert len(result.missing or []) == 3


# ---------------------------------------------------------------------------
# Skill._parse_frontmatter (YAML upgrade)
# ---------------------------------------------------------------------------

class TestParseFrontmatter:
    def test_simple_frontmatter(self):
        content = "---\nname: test-skill\ndescription: Hello\n---\n# Body"
        data = Skill._parse_frontmatter(content)
        assert data["name"] == "test-skill"
        assert data["description"] == "Hello"

    def test_nested_metadata_flocks(self):
        content = (
            "---\n"
            "name: test-skill\n"
            "description: Hello\n"
            "metadata:\n"
            "  flocks:\n"
            "    requires:\n"
            "      bins: [gh]\n"
            "    install:\n"
            "      - kind: brew\n"
            "        formula: gh\n"
            "---\n"
        )
        data = Skill._parse_frontmatter(content)
        assert data["name"] == "test-skill"
        meta = data.get("metadata", {}).get("flocks", {})
        assert meta["requires"]["bins"] == ["gh"]
        assert meta["install"][0]["kind"] == "brew"

    def test_metadata_openclaw_compatible(self):
        content = (
            "---\n"
            "name: openclaw-compat\n"
            "description: Test\n"
            "metadata:\n"
            "  openclaw:\n"
            "    requires:\n"
            "      bins: [node]\n"
            "---\n"
        )
        data = Skill._parse_frontmatter(content)
        meta = data.get("metadata", {}).get("openclaw", {})
        assert meta["requires"]["bins"] == ["node"]

    def test_no_frontmatter_returns_empty(self):
        data = Skill._parse_frontmatter("# Just a markdown file")
        assert data == {}


# ---------------------------------------------------------------------------
# SkillInstaller._save_skill_content
# ---------------------------------------------------------------------------

class TestSaveSkillContent:
    def test_save_valid_content(self, tmp_skills_dir: Path):
        content = "---\nname: my-skill\ndescription: My skill\n---\n# Body"
        with patch("flocks.skill.installer._user_skills_root", return_value=tmp_skills_dir):
            result = SkillInstaller._save_skill_content(content, "global")

        assert result.success is True
        assert result.skill_name == "my-skill"
        saved = tmp_skills_dir / "my-skill" / "SKILL.md"
        assert saved.exists()
        assert saved.read_text() == content

    def test_save_invalid_name_fails(self, tmp_skills_dir: Path):
        content = "---\nname: Invalid Name!\ndescription: Bad\n---\n"
        with patch("flocks.skill.installer._user_skills_root", return_value=tmp_skills_dir):
            result = SkillInstaller._save_skill_content(content, "global")

        assert result.success is False
        assert result.error is not None

    def test_save_no_name_uses_hint(self, tmp_skills_dir: Path):
        content = "---\ndescription: No name\n---\n"
        with patch("flocks.skill.installer._user_skills_root", return_value=tmp_skills_dir):
            result = SkillInstaller._save_skill_content(content, "global", skill_name_hint="hinted-skill")

        assert result.success is True
        assert result.skill_name == "hinted-skill"


# ---------------------------------------------------------------------------
# SkillInstaller.install_from_source
# ---------------------------------------------------------------------------

class TestInstallFromSource:
    @pytest.mark.asyncio
    async def test_skills_sh_cli_staging_imports_agent_skill(self, tmp_skills_dir):
        class Proc:
            returncode = 0

            async def communicate(self):
                return b"installed", b""

        async def fake_create_subprocess_exec(*_cmd, **kwargs):
            staged_skill = Path(kwargs["cwd"]) / ".agents" / "skills" / "demo"
            staged_skill.mkdir(parents=True)
            (staged_skill / "SKILL.md").write_text(
                "---\nname: demo\ndescription: Demo\n---\n",
                encoding="utf-8",
            )
            return Proc()

        with (
            patch("flocks.skill.installer.shutil.which", return_value="/usr/bin/npx"),
            patch("flocks.skill.installer._user_skills_root", return_value=tmp_skills_dir),
            patch(
                "flocks.skill.installer.asyncio.create_subprocess_exec",
                fake_create_subprocess_exec,
            ),
        ):
            result = await SkillInstaller.install_from_source(
                "https://www.skills.sh/owner/repo/demo"
            )

        assert result.success is True
        assert result.skill_name == "demo"
        assert (tmp_skills_dir / "demo" / "SKILL.md").exists()

    @pytest.mark.asyncio
    async def test_safeskill_requires_npx(self, tmp_skills_dir):
        with patch("flocks.skill.installer.shutil.which", return_value=None):
            result = await SkillInstaller.install_from_source("safeskill:test")
        assert result.success is False
        assert "npx is required" in (result.error or "")

    @pytest.mark.asyncio
    async def test_local_file(self, tmp_path: Path, tmp_skills_dir: Path):
        skill_dir = tmp_path / "source-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: local-test\ndescription: Local\n---\n"
        )
        with patch("flocks.skill.installer._user_skills_root", return_value=tmp_skills_dir):
            result = await SkillInstaller.install_from_source(str(skill_dir))

        assert result.success is True
        assert result.skill_name == "local-test"

    @pytest.mark.asyncio
    async def test_local_file_not_found(self):
        result = await SkillInstaller.install_from_source("/nonexistent/path/SKILL.md")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_url_success(self, tmp_skills_dir: Path):
        mock_content = "---\nname: url-skill\ndescription: From URL\n---\n"

        class Resp:
            status_code = 200
            text = mock_content
            headers = {}

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def get(self, _url: str):
                return Resp()

        with (
            patch("flocks.skill.installer._user_skills_root", return_value=tmp_skills_dir),
            patch("httpx.AsyncClient", return_value=Client()),
        ):
            result = await SkillInstaller.install_from_source(
                "https://example.com/SKILL.md"
            )

        assert result.success is True
        assert result.skill_name == "url-skill"

    @pytest.mark.asyncio
    async def test_url_http_error(self, tmp_skills_dir: Path):
        class Resp:
            status_code = 404

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def get(self, _url: str):
                return Resp()

        with (
            patch("flocks.skill.installer._user_skills_root", return_value=tmp_skills_dir),
            patch("httpx.AsyncClient", return_value=Client()),
        ):
            result = await SkillInstaller.install_from_source("https://example.com/SKILL.md")

        assert result.success is False
        assert "404" in (result.error or "")

    @pytest.mark.asyncio
    async def test_github_api_403_falls_back_to_raw_skill_md(self, tmp_skills_dir: Path):
        skill_content = (
            "---\n"
            "name: web-design-guidelines\n"
            "description: Web design review\n"
            "---\n"
            "# Web Interface Guidelines\n"
        )

        class Resp:
            def __init__(self, status_code: int, text: str = ""):
                self.status_code = status_code
                self.text = text

            def json(self):
                return []

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def get(self, url: str):
                if url.endswith("/main/skills/web-design-guidelines/SKILL.md"):
                    return Resp(200, skill_content)
                if "api.github.com" in url:
                    return Resp(403, "rate limited")
                return Resp(404, "not found")

        with (
            patch("flocks.skill.installer._user_skills_root", return_value=tmp_skills_dir),
            patch("httpx.AsyncClient", return_value=Client()),
        ):
            result = await SkillInstaller.install_from_source(
                "github:vercel-labs/agent-skills/web-design-guidelines"
            )

        assert result.success is True
        assert result.skill_name == "web-design-guidelines"
        assert "raw GitHub SKILL.md fallback" in result.message
        assert (tmp_skills_dir / "web-design-guidelines" / "SKILL.md").exists()


# ---------------------------------------------------------------------------
# SkillInstaller._build_install_command
# ---------------------------------------------------------------------------

class TestBuildInstallCommand:
    def test_brew(self):
        spec = SkillInstallSpec(kind="brew", formula="gh")
        cmd = SkillInstaller._build_install_command(spec)
        assert cmd == ["brew", "install", "gh"]

    def test_npm(self):
        spec = SkillInstallSpec(kind="npm", package="clawhub")
        cmd = SkillInstaller._build_install_command(spec)
        assert cmd == ["npm", "install", "-g", "--ignore-scripts", "clawhub"]

    def test_uv(self):
        spec = SkillInstallSpec(kind="uv", package="some-tool")
        cmd = SkillInstaller._build_install_command(spec)
        assert cmd == ["uv", "tool", "install", "some-tool"]

    def test_go(self):
        spec = SkillInstallSpec(kind="go", module="github.com/user/tool@latest")
        cmd = SkillInstaller._build_install_command(spec)
        assert cmd == ["go", "install", "github.com/user/tool@latest"]

    def test_missing_formula_returns_none(self):
        spec = SkillInstallSpec(kind="brew")
        assert SkillInstaller._build_install_command(spec) is None

    def test_os_skip(self):
        spec = SkillInstallSpec(kind="brew", formula="gh", os=["win32"])
        cmd = SkillInstaller._build_install_command(spec)
        # On non-Windows, should return None
        import platform
        if platform.system().lower() != "windows":
            assert cmd is None


# ---------------------------------------------------------------------------
# SkillInstaller.uninstall
# ---------------------------------------------------------------------------

class TestUninstall:
    @pytest.mark.asyncio
    async def test_uninstall_user_managed(self, tmp_skills_dir: Path):
        skill_dir = tmp_skills_dir / "to-remove"
        skill_dir.mkdir(parents=True)
        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text("---\nname: to-remove\ndescription: Test\n---\n")

        mock_skill = SkillInfo(
            name="to-remove",
            description="Test",
            location=str(skill_path),
        )

        with (
            patch("flocks.skill.installer._user_skills_root", return_value=tmp_skills_dir),
            patch.object(Skill, "get", return_value=mock_skill),
        ):
            result = await SkillInstaller.uninstall("to-remove")

        assert result.success is True
        assert not skill_dir.exists()

    @pytest.mark.asyncio
    async def test_uninstall_not_found(self):
        with patch.object(Skill, "get", return_value=None):
            result = await SkillInstaller.uninstall("ghost-skill")

        assert result.success is False
        assert "not found" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_uninstall_non_user_managed(self, tmp_path: Path):
        other_dir = tmp_path / "system" / "my-skill"
        other_dir.mkdir(parents=True)
        skill_path = other_dir / "SKILL.md"
        skill_path.write_text("---\nname: my-skill\ndescription: Sys\n---\n")

        mock_skill = SkillInfo(
            name="my-skill",
            description="Sys",
            location=str(skill_path),
        )

        with patch.object(Skill, "get", return_value=mock_skill):
            result = await SkillInstaller.uninstall("my-skill")

        assert result.success is False
        assert "user-managed" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# SkillInstaller.install_deps  (the function that had the message-field bug)
# ---------------------------------------------------------------------------

class TestInstallDeps:
    @pytest.mark.asyncio
    async def test_skill_not_found_returns_error(self):
        """install_deps: skill lookup fails → returns DepInstallResult with error."""
        with patch.object(Skill, "get", return_value=None):
            results = await SkillInstaller.install_deps("ghost-skill")

        assert len(results) == 1
        assert results[0].success is False
        assert "not found" in (results[0].error or "").lower()

    @pytest.mark.asyncio
    async def test_no_install_specs_returns_success(self):
        """install_deps: skill has no install specs → returns success with message.

        This exercises the bug-fixed path: DepInstallResult now has message field.
        """
        skill = make_skill_info("no-deps-skill")  # install_specs=None
        with patch.object(Skill, "get", return_value=skill):
            results = await SkillInstaller.install_deps("no-deps-skill")

        assert len(results) == 1
        assert results[0].success is True
        assert "no install specs" in results[0].message.lower()

    @pytest.mark.asyncio
    async def test_install_id_not_found_returns_error(self):
        """install_deps: specified install_id doesn't exist in specs → error."""
        skill = make_skill_info(
            "has-deps",
            install_specs=[{"kind": "brew", "id": "brew-gh", "formula": "gh"}],
        )
        with patch.object(Skill, "get", return_value=skill):
            results = await SkillInstaller.install_deps("has-deps", install_id="nonexistent-id")

        assert len(results) == 1
        assert results[0].success is False
        assert "nonexistent-id" in (results[0].error or "")

    @pytest.mark.asyncio
    async def test_install_id_filters_to_matching_spec(self):
        """install_deps with install_id only runs the matching spec."""
        skill = make_skill_info(
            "multi-deps",
            install_specs=[
                {"kind": "brew", "id": "brew-gh", "formula": "gh"},
                {"kind": "uv", "id": "uv-tool", "package": "some-tool"},
            ],
        )

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))

        with (
            patch.object(Skill, "get", return_value=skill),
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        ):
            results = await SkillInstaller.install_deps("multi-deps", install_id="uv-tool")

        assert len(results) == 1
        assert results[0].spec_id == "uv-tool"

    @pytest.mark.asyncio
    async def test_successful_spec_execution(self):
        """install_deps: spec runs successfully → result.success is True."""
        skill = make_skill_info(
            "brew-skill",
            install_specs=[{"kind": "brew", "id": "brew-gh", "formula": "gh"}],
        )

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"installed gh\n", b""))

        with (
            patch.object(Skill, "get", return_value=skill),
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        ):
            results = await SkillInstaller.install_deps("brew-skill")

        assert len(results) == 1
        assert results[0].success is True
        assert results[0].returncode == 0
        assert "gh" in results[0].stdout

    @pytest.mark.asyncio
    async def test_failed_spec_execution(self):
        """install_deps: spec exits non-zero → result.success is False."""
        skill = make_skill_info(
            "fail-skill",
            install_specs=[{"kind": "brew", "formula": "nonexistent-pkg"}],
        )

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"Error: formula not found\n"))

        with (
            patch.object(Skill, "get", return_value=skill),
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        ):
            results = await SkillInstaller.install_deps("fail-skill")

        assert len(results) == 1
        assert results[0].success is False
        assert results[0].returncode == 1
        assert results[0].error is not None

    @pytest.mark.asyncio
    async def test_spec_timeout(self):
        """install_deps: subprocess times out → result.success is False with timeout error."""
        import asyncio as _asyncio

        skill = make_skill_info(
            "slow-skill",
            install_specs=[{"kind": "brew", "formula": "slow-pkg"}],
        )

        mock_proc = MagicMock()
        mock_proc.kill = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        async def raise_timeout(*args, **kwargs):
            raise _asyncio.TimeoutError()

        with (
            patch.object(Skill, "get", return_value=skill),
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            patch("asyncio.wait_for", side_effect=raise_timeout),
        ):
            results = await SkillInstaller.install_deps("slow-skill", timeout_ms=100)

        assert len(results) == 1
        assert results[0].success is False
        assert "timed out" in (results[0].error or "").lower()
        mock_proc.kill.assert_called_once()


# ---------------------------------------------------------------------------
# SkillInstaller._build_install_command — pip and go-module fallback
# ---------------------------------------------------------------------------

class TestBuildInstallCommandExtended:
    def test_pip(self):
        import sys
        spec = SkillInstallSpec(kind="pip", package="requests")
        cmd = SkillInstaller._build_install_command(spec)
        assert cmd is not None
        assert cmd[0] == sys.executable
        assert "pip" in cmd
        assert "requests" in cmd

    def test_go_module(self):
        spec = SkillInstallSpec(kind="go", module="github.com/user/tool@latest")
        cmd = SkillInstaller._build_install_command(spec)
        assert cmd == ["go", "install", "github.com/user/tool@latest"]

    def test_go_package_fallback(self):
        """go spec with package (no module) should fall back to package."""
        spec = SkillInstallSpec(kind="go", package="github.com/user/tool@latest")
        cmd = SkillInstaller._build_install_command(spec)
        assert cmd is not None
        assert "github.com/user/tool@latest" in cmd

    def test_download_kind_returns_none(self):
        spec = SkillInstallSpec(kind="download", url="https://example.com/tool")
        cmd = SkillInstaller._build_install_command(spec)
        assert cmd is None
