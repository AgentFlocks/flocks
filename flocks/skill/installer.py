"""
Skill Installer

Handles:
1. Installing skills from external sources (GitHub, raw URL, local path, clawhub,
   skills.sh, SafeSkill)
2. Installing a skill's declared tool dependencies (brew, npm, uv, pip, go)

Source scheme routing:
  safeskill:<source>       → SafeSkill CLI staging import
  skills-sh:<id>           → skills.sh registry via GitHub source
  clawhub:<name>           → clawhub.com registry API
  github:<owner>/<repo>    → GitHub raw download
  https://...              → Direct HTTP download
  /local/path or ./path    → Local filesystem copy
  <owner>/<repo>           → Shorthand for GitHub
"""

from __future__ import annotations

import asyncio
import io
import os
import platform
import re
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

from flocks.skill.skill import Skill, SkillInfo, SkillInstallSpec
from flocks.utils.log import Log


log = Log.create(service="skill.installer")

_NETWORK_TIMEOUT_SEC = 20
_SKILLS_SH_CLI_TIMEOUT_SEC = 45
_INSTALL_TIMEOUT_SEC = 90

# ---------------------------------------------------------------------------
# Result Types
# ---------------------------------------------------------------------------

@dataclass
class SkillInstallResult:
    success: bool
    skill_name: Optional[str] = None
    location: Optional[str] = None
    message: str = ""
    error: Optional[str] = None


@dataclass
class DepInstallResult:
    success: bool
    spec_id: Optional[str] = None
    command: List[str] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    error: Optional[str] = None
    message: str = ""


# ---------------------------------------------------------------------------
# Source Resolution
# ---------------------------------------------------------------------------

def _user_skills_root() -> Path:
    return Path.home() / ".flocks" / "plugins" / "skills"


def _resolve_install_root(scope: str) -> Path:
    """Return the install root directory for the given scope."""
    if scope == "project":
        from flocks.project.instance import Instance  # avoid circular import
        project_dir = Instance.get_directory() or os.getcwd()
        return Path(project_dir) / ".flocks" / "plugins" / "skills"
    return _user_skills_root()


def _normalize_github_repo_path(repo_path: str) -> str:
    """Normalize GitHub web paths to owner/repo[/skill-dir]."""
    parts = repo_path.strip("/").split("/")
    if len(parts) < 4 or parts[2] not in {"blob", "tree"}:
        return repo_path.strip("/")

    owner, repo, view_kind, _branch, *subpath_parts = parts
    if view_kind == "blob" and subpath_parts[-1:] == ["SKILL.md"]:
        subpath_parts = subpath_parts[:-1]

    subpath = "/".join(subpath_parts)
    return f"{owner}/{repo}/{subpath}".rstrip("/")


def _resolve_source(source: str) -> dict:
    """
    Parse source string into a typed dict with keys: kind, value.

    Supported kinds:
      skills_sh  – skills.sh registry
      safeskill  – SafeSkill CLI staging import
      clawhub    – clawhub.com registry
      github     – GitHub raw download
      url        – arbitrary HTTPS URL
      local      – local filesystem path
    """
    source = source.strip()

    if source.startswith(("skills-sh:", "skills.sh:")):
        prefix = "skills-sh:" if source.startswith("skills-sh:") else "skills.sh:"
        return {"kind": "skills_sh", "value": source[len(prefix):]}

    if source.startswith("safeskill:"):
        return {"kind": "safeskill", "value": source[len("safeskill:"):]}

    if source.startswith("safeskill://"):
        return {"kind": "safeskill", "value": source}

    if source.startswith("clawhub:"):
        return {"kind": "clawhub", "value": source[len("clawhub:"):]}

    if source.startswith("github:"):
        return {
            "kind": "github",
            "value": _normalize_github_repo_path(source[len("github:"):]),
        }

    if source.startswith(("http://", "https://")):
        skills_sh_match = re.match(
            r"https?://(?:www\.)?skills\.sh/?(?P<id>.*)$",
            source,
        )
        if skills_sh_match:
            return {"kind": "skills_sh", "value": skills_sh_match.group("id")}

        # Detect GitHub URLs and handle them specially
        gh_match = re.match(
            r"https?://github\.com/([^/]+/[^/]+)(?:/tree/[^/]+)?(/.*)?$",
            source,
        )
        if gh_match:
            repo = gh_match.group(1).rstrip("/")
            subpath = (gh_match.group(2) or "").strip("/")
            repo_path = f"{repo}/{subpath}" if subpath else repo
            return {"kind": "github", "value": _normalize_github_repo_path(repo_path)}
        return {"kind": "url", "value": source}

    if source.startswith(("/", "./", "../", "~/")):
        return {"kind": "local", "value": os.path.expanduser(source)}

    # Bare "owner/repo" or "owner/repo/subpath" shorthand → GitHub
    if re.match(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_./-]+$", source):
        return {"kind": "github", "value": source}

    return {"kind": "url", "value": source}


# ---------------------------------------------------------------------------
# Skill Installer
# ---------------------------------------------------------------------------

class SkillInstaller:
    """Install skills from external sources and manage skill dependencies."""

    @staticmethod
    async def _run_subprocess(
        cmd: list[str],
        *,
        timeout_sec: float,
        cwd: Optional[str] = None,
        env: Optional[dict[str, str]] = None,
    ) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout_sec,
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.communicate(), timeout=5)
            except Exception:
                pass
            raise TimeoutError(f"Command timed out after {timeout_sec:g}s: {' '.join(cmd)}")
        return (
            proc.returncode if proc.returncode is not None else 0,
            stdout_b.decode(errors="replace"),
            stderr_b.decode(errors="replace"),
        )

    # ------------------------------------------------------------------
    # Install skill itself
    # ------------------------------------------------------------------

    @classmethod
    async def install_from_source(
        cls,
        source: str,
        scope: str = "global",
        yes: bool = False,
    ) -> SkillInstallResult:
        """
        Install a skill from an external source.

        Args:
            source: Source string (URL, GitHub, clawhub:<name>, local path …)
            scope:  "global" → ~/.flocks/plugins/skills/
                    "project" → .flocks/plugins/skills/ (cwd)
            yes:    When True, pass -y to downstream CLIs (e.g. `skills add`)
                    so installs can run non-interactively.

        Returns:
            SkillInstallResult
        """
        resolved = _resolve_source(source)
        kind = resolved["kind"]
        value = resolved["value"]

        log.info("skill.install.start", {"source": source, "kind": kind, "scope": scope, "yes": yes})

        async def _install() -> SkillInstallResult:
            if kind == "skills_sh":
                return await cls._install_from_skills_sh(value, scope, yes=yes)
            elif kind == "safeskill":
                return await cls._install_from_safeskill(value, scope)
            elif kind == "clawhub":
                return await cls._install_from_clawhub(value, scope)
            elif kind == "github":
                return await cls._install_from_github(value, scope)
            elif kind == "url":
                return await cls._install_from_url(value, scope)
            elif kind == "local":
                return await cls._install_from_local(value, scope)
            return SkillInstallResult(
                success=False,
                error=f"Unsupported source kind: {kind}",
            )

        try:
            return await asyncio.wait_for(_install(), timeout=_INSTALL_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            return SkillInstallResult(
                success=False,
                error=f"Skill install timed out after {_INSTALL_TIMEOUT_SEC}s: {source}",
            )

    @classmethod
    async def _install_from_skills_sh(
        cls,
        identifier: str,
        scope: str,
        yes: bool = False,
    ) -> SkillInstallResult:
        """Install a skills.sh skill via npx staging, then GitHub fallback."""
        normalized = cls._normalize_skills_sh_identifier(identifier)
        if not normalized:
            return SkillInstallResult(
                success=False,
                error="skills.sh skill identifier is required, e.g. skills-sh:owner/repo/skill",
            )

        staged = await cls._install_from_skills_sh_cli(normalized, scope, yes=yes)
        if staged.success:
            return staged

        if normalized.count("/") >= 2:
            result = await cls._install_from_github(normalized, scope)
            if result.success:
                return result

        resolved = await cls._resolve_skills_sh_github_identifier(normalized)
        if not resolved:
            return SkillInstallResult(
                success=False,
                error=(
                    f"Could not resolve skills.sh skill {identifier!r}. "
                    "Use an identifier like skills-sh:owner/repo/skill-name."
                ),
            )
        return await cls._install_from_github(resolved, scope)

    @classmethod
    async def _install_from_skills_sh_cli(
        cls,
        identifier: str,
        scope: str,
        yes: bool = False,
    ) -> SkillInstallResult:
        """Run `npx skills add` in staging so default agent-dir installs are imported."""
        npx = shutil.which("npx")
        if not npx:
            return SkillInstallResult(
                success=False,
                error="npx is not available for skills.sh CLI install",
            )

        with tempfile.TemporaryDirectory(prefix="flocks-skills-sh-") as tmp:
            staging = Path(tmp)
            env = os.environ.copy()
            env["HOME"] = str(staging)
            env["XDG_CONFIG_HOME"] = str(staging / ".config")
            cmd = [npx, "-y", "skills", "add", identifier]
            if yes:
                cmd.append("-y")
            try:
                returncode, stdout, stderr = await cls._run_subprocess(
                    cmd,
                    cwd=str(staging),
                    env=env,
                    timeout_sec=_SKILLS_SH_CLI_TIMEOUT_SEC,
                )
            except TimeoutError as exc:
                return SkillInstallResult(success=False, error=str(exc))
            except Exception as exc:
                return SkillInstallResult(
                    success=False,
                    error=f"Failed to run skills.sh CLI: {exc}",
                )
            output = (stdout + stderr).strip()
            if returncode != 0:
                return SkillInstallResult(
                    success=False,
                    error=output or f"skills.sh CLI failed with exit {returncode}",
                )

            imported = cls._import_staged_skill_dirs(staging, scope)
            if not imported:
                return SkillInstallResult(
                    success=False,
                    error=(
                        "skills.sh CLI completed but no SKILL.md files were found "
                        "in staged agent skill directories."
                    ),
                )

        Skill.clear_cache()
        names = ", ".join(name for name, _ in imported)
        return SkillInstallResult(
            success=True,
            skill_name=imported[0][0],
            location=str(imported[0][1]),
            message=f"Imported skills.sh skill(s) into Flocks: {names}",
        )

    @classmethod
    async def _install_from_safeskill(cls, source: str, scope: str) -> SkillInstallResult:
        """Run SafeSkill CLI in a staging directory and import its agent output."""
        npx = shutil.which("npx")
        if not npx:
            return SkillInstallResult(
                success=False,
                error="npx is required for safeskill installs. Install Node.js/npm first.",
            )

        source = source.strip()
        if not source:
            return SkillInstallResult(
                success=False,
                error=(
                    "safeskill source is required, e.g. "
                    "safeskill:safeskill://official/acme/code-review"
                ),
            )

        with tempfile.TemporaryDirectory(prefix="flocks-safeskill-") as tmp:
            staging = Path(tmp)
            cmd = [
                npx,
                "-y",
                "@safeskill/cli",
                "add",
                source,
                "--copy",
                "-y",
                "-a",
                "universal",
            ]
            try:
                returncode, stdout, stderr = await cls._run_subprocess(
                    cmd,
                    cwd=str(staging),
                    timeout_sec=_SKILLS_SH_CLI_TIMEOUT_SEC,
                )
            except TimeoutError as exc:
                return SkillInstallResult(success=False, error=str(exc))
            except Exception as exc:
                return SkillInstallResult(
                    success=False,
                    error=f"Failed to run SafeSkill CLI: {exc}",
                )
            output = (stdout + stderr).strip()
            if returncode != 0:
                return SkillInstallResult(
                    success=False,
                    error=output or f"SafeSkill CLI failed with exit {returncode}",
                )

            imported = cls._import_staged_skill_dirs(staging, scope)
            if not imported:
                return SkillInstallResult(
                    success=False,
                    error=(
                        "SafeSkill CLI completed but no SKILL.md files were found "
                        "in the staging agent directories."
                    ),
                )

        Skill.clear_cache()
        names = ", ".join(name for name, _ in imported)
        return SkillInstallResult(
            success=True,
            skill_name=imported[0][0],
            location=str(imported[0][1]),
            message=f"Imported SafeSkill skill(s) into Flocks: {names}",
        )

    @staticmethod
    def _normalize_skills_sh_identifier(identifier: str) -> str:
        """Normalize skills.sh prefixes and URLs to owner/repo/skill-path."""
        value = identifier.strip().strip("/")
        for prefix in ("skills-sh/", "skills.sh/", "skils-sh/", "skils.sh/"):
            if value.startswith(prefix):
                value = value[len(prefix):]
                break
        if value.startswith("https://www.skills.sh/"):
            value = value[len("https://www.skills.sh/"):]
        if value.startswith("http://www.skills.sh/"):
            value = value[len("http://www.skills.sh/"):]
        if value.startswith("https://skills.sh/"):
            value = value[len("https://skills.sh/"):]
        if value.startswith("http://skills.sh/"):
            value = value[len("http://skills.sh/"):]
        return value.strip("/")

    @classmethod
    async def _resolve_skills_sh_github_identifier(cls, identifier: str) -> Optional[str]:
        """Resolve a skills.sh detail page to a GitHub owner/repo[/skill] path."""
        try:
            import httpx
        except ImportError:
            return None

        normalized = cls._normalize_skills_sh_identifier(identifier)
        if normalized.count("/") < 2:
            return None

        try:
            async with httpx.AsyncClient(timeout=_NETWORK_TIMEOUT_SEC, follow_redirects=True) as client:
                resp = await client.get(f"https://skills.sh/{normalized}")
                if resp.status_code != 200:
                    return None
        except Exception:
            return None

        install_match = re.search(
            r"npx\s+skills\s+add\s+(?P<repo>https?://github\.com/[^\s<]+|[^\s<]+)"
            r"(?:\s+--skill\s+(?P<skill>[^\s<]+))?",
            resp.text,
            flags=re.IGNORECASE,
        )
        if install_match:
            repo = install_match.group("repo").strip().strip("\"'")
            skill = (install_match.group("skill") or "").strip().strip("\"'")
            repo = cls._github_repo_slug(repo)
            if repo:
                return f"{repo}/{skill}" if skill else repo

        parts = normalized.split("/", 2)
        repo = f"{parts[0]}/{parts[1]}"
        skill_path = parts[2]
        return f"{repo}/{skill_path}"

    @staticmethod
    def _github_repo_slug(value: str) -> Optional[str]:
        value = value.strip().strip("/")
        if value.startswith("https://github.com/"):
            value = value[len("https://github.com/"):]
        elif value.startswith("http://github.com/"):
            value = value[len("http://github.com/"):]
        parts = value.split("/")
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
        return None

    @classmethod
    def _import_staged_skill_dirs(cls, staging: Path, scope: str) -> List[tuple[str, Path]]:
        """Copy staged SafeSkill agent directories into Flocks skill storage."""
        install_root = _resolve_install_root(scope)
        imported: List[tuple[str, Path]] = []
        seen: set[Path] = set()
        candidate_roots = [
            staging / ".agents" / "skills",
            staging / ".claude" / "skills",
            staging / ".cursor" / "skills",
            staging / "skills",
        ]
        for root in candidate_roots:
            if not root.exists():
                continue
            for skill_md in root.rglob("SKILL.md"):
                skill_dir = skill_md.parent.resolve()
                if skill_dir in seen:
                    continue
                seen.add(skill_dir)
                try:
                    content = skill_md.read_text(encoding="utf-8")
                except Exception:
                    continue
                data = Skill._parse_frontmatter(content)
                name = (data.get("name") or skill_dir.name).strip()
                if not name or not Skill._is_valid_name(name):
                    continue
                dest = install_root / name
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(skill_dir, dest)
                imported.append((name, dest / "SKILL.md"))
        return imported

    @classmethod
    async def _install_from_clawhub(cls, name: str, scope: str) -> SkillInstallResult:
        """Download a skill from clawhub.ai registry (ZIP bundle)."""
        import io
        import zipfile

        try:
            import httpx
        except ImportError:
            return SkillInstallResult(
                success=False,
                error="httpx is required to download skills. Run: uv add httpx",
            )

        zip_url = f"https://wry-manatee-359.convex.site/api/v1/download?slug={name}"
        try:
            async with httpx.AsyncClient(timeout=_NETWORK_TIMEOUT_SEC, follow_redirects=True) as client:
                resp = await client.get(zip_url)
                if resp.status_code == 404:
                    return SkillInstallResult(
                        success=False,
                        error=(
                            f"Could not find skill '{name}' on clawhub. "
                            "Try using a direct GitHub URL instead."
                        ),
                    )
                if resp.status_code != 200:
                    return SkillInstallResult(
                        success=False,
                        error=f"clawhub returned HTTP {resp.status_code} for skill '{name}'",
                    )
                content_type = ""
                headers = getattr(resp, "headers", None)
                if headers is not None:
                    header_value = headers.get("content-type", "")
                    if isinstance(header_value, str):
                        content_type = header_value
                    elif asyncio.iscoroutine(header_value):
                        header_value.close()
                if "text/html" in content_type:
                    return SkillInstallResult(
                        success=False,
                        error=(
                            f"Could not find skill '{name}' on clawhub. "
                            "Try using a direct GitHub URL instead."
                        ),
                    )
                zip_bytes = resp.content
        except Exception as exc:
            return SkillInstallResult(success=False, error=f"Download failed: {exc}")

        # Extract ZIP to skill directory
        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                names_in_zip = zf.namelist()
                if "SKILL.md" not in names_in_zip:
                    return SkillInstallResult(
                        success=False,
                        error=f"Invalid clawhub package: no SKILL.md found in zip for '{name}'",
                    )

                skill_md_content = zf.read("SKILL.md").decode("utf-8")
                stripped = skill_md_content.lstrip()
                if stripped.lower().startswith("<!doctype") or stripped.lower().startswith("<html"):
                    return SkillInstallResult(
                        success=False,
                        error="Downloaded SKILL.md is an HTML page, not valid content.",
                    )

                data = Skill._parse_frontmatter(skill_md_content)
                skill_name = (data.get("name") or name).strip()
                if not skill_name or not Skill._is_valid_name(skill_name):
                    return SkillInstallResult(
                        success=False,
                        error=f"Invalid or missing skill name in SKILL.md frontmatter: {skill_name!r}",
                    )

                install_root = _resolve_install_root(scope)
                skill_dir = install_root / skill_name
                skill_dir.mkdir(parents=True, exist_ok=True)

                for zip_entry in names_in_zip:
                    if zip_entry == "_meta.json":
                        continue
                    # Skip directory entries
                    if zip_entry.endswith("/"):
                        continue
                    dest = (skill_dir / zip_entry).resolve()
                    # Zip Slip prevention: ensure dest stays inside skill_dir
                    if not str(dest).startswith(str(skill_dir.resolve())):
                        log.warn("skill.install.clawhub.zip_slip", {
                            "entry": zip_entry,
                            "skill": name,
                        })
                        continue
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(zf.read(zip_entry))

        except zipfile.BadZipFile:
            return SkillInstallResult(
                success=False,
                error=f"Invalid ZIP file downloaded for skill '{name}'",
            )
        except Exception as exc:
            return SkillInstallResult(success=False, error=f"Failed to extract skill: {exc}")

        skill_path = skill_dir / "SKILL.md"
        Skill.clear_cache()
        log.info("skill.install.clawhub.ok", {"name": skill_name, "url": zip_url})
        log.info("skill.install.saved", {"name": skill_name, "path": str(skill_path)})
        return SkillInstallResult(
            success=True,
            skill_name=skill_name,
            location=str(skill_path),
            message=f"Skill '{skill_name}' installed from clawhub to {skill_path}",
        )

    @classmethod
    async def _install_from_github(cls, repo_path: str, scope: str) -> SkillInstallResult:
        """
        Download an entire skill directory from a GitHub repository using the
        GitHub Contents API, preserving the full folder structure.

        repo_path formats:
          owner/repo               → downloads repo root
          owner/repo/subpath       → downloads subpath/ directory
        """
        try:
            import httpx
        except ImportError:
            return SkillInstallResult(
                success=False,
                error="httpx is required to download skills. Run: uv add httpx",
            )

        parts = repo_path.strip("/").split("/")
        if len(parts) < 2:
            return SkillInstallResult(
                success=False,
                error=f"Invalid GitHub repo path: {repo_path!r}. Expected owner/repo[/subpath]",
            )

        owner, repo = parts[0], parts[1]
        subpath = "/".join(parts[2:]) if len(parts) > 2 else ""

        # Candidate directory paths to try (in order)
        if subpath:
            candidate_paths = [
                subpath,
                f"skills/{subpath}",
                f".agents/skills/{subpath}",
                f".claude/skills/{subpath}",
            ]
        else:
            candidate_paths = [""]

        errors: List[str] = []
        try:
            async with httpx.AsyncClient(
                timeout=_NETWORK_TIMEOUT_SEC,
                follow_redirects=True,
                headers={"Accept": "application/vnd.github+json"},
            ) as client:
                for branch in ("main", "master"):
                    for dir_path in candidate_paths:
                        result = await cls._download_github_dir(
                            client, owner, repo, branch, dir_path, scope
                        )
                        if result.success:
                            return result
                        if result.error:
                            errors.append(result.error)

                # Unauthenticated GitHub Contents API can return 403 rate-limit
                # errors while raw.githubusercontent.com still works. In that case
                # install the SKILL.md directly instead of reporting a misleading
                # "directory not found" error.
                for branch in ("main", "master"):
                    for dir_path in candidate_paths:
                        result = await cls._download_github_skill_md_raw(
                            client, owner, repo, branch, dir_path, scope
                        )
                        if result.success:
                            return result
                        if result.error:
                            errors.append(result.error)

                skill_hint = Path(subpath).name if subpath else None
                for branch in ("main", "master"):
                    result = await cls._download_github_archive_skill(
                        client,
                        owner,
                        repo,
                        branch,
                        candidate_paths,
                        skill_hint,
                        scope,
                    )
                    if result.success:
                        return result
                    if result.error:
                        errors.append(result.error)
        except Exception as exc:
            return SkillInstallResult(
                success=False,
                error=f"GitHub download failed for {owner}/{repo}: {exc}",
            )

        if errors and any("GitHub API 403" in error for error in errors):
            return SkillInstallResult(
                success=False,
                error=(
                    f"GitHub API returned 403 for {owner}/{repo}. "
                    "This is usually an unauthenticated API rate-limit or access issue, "
                    "and raw SKILL.md fallback also failed. "
                    f"Last error: {errors[-1]}"
                ),
            )
        return SkillInstallResult(
            success=False,
            error=f"Could not find a skill directory in GitHub repo: {owner}/{repo}",
        )

    @classmethod
    async def _download_github_archive_skill(
        cls,
        client: Any,
        owner: str,
        repo: str,
        branch: str,
        candidate_paths: list[str],
        skill_hint: Optional[str],
        scope: str,
    ) -> SkillInstallResult:
        """Download a GitHub zip archive and import the matching skill directory."""
        archive_url = f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{branch}"
        resp = await client.get(archive_url)
        if resp.status_code != 200:
            return SkillInstallResult(
                success=False,
                error=f"GitHub archive HTTP {resp.status_code} for {archive_url}",
            )

        try:
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                return cls._import_skill_from_github_zip(
                    zf,
                    owner,
                    repo,
                    branch,
                    candidate_paths,
                    skill_hint,
                    scope,
                )
        except zipfile.BadZipFile:
            return SkillInstallResult(
                success=False,
                error=f"GitHub archive for {owner}/{repo}@{branch} is not a valid ZIP file",
            )

    @classmethod
    def _import_skill_from_github_zip(
        cls,
        zf: zipfile.ZipFile,
        owner: str,
        repo: str,
        branch: str,
        candidate_paths: list[str],
        skill_hint: Optional[str],
        scope: str,
    ) -> SkillInstallResult:
        normalized_candidates = {path.strip("/") for path in candidate_paths}
        skill_hint = (skill_hint or "").strip()
        skill_mds = [name for name in zf.namelist() if name.endswith("/SKILL.md")]

        for skill_md in skill_mds:
            parts = Path(skill_md).parts
            if len(parts) < 2:
                continue
            skill_dir = "/".join(parts[:-1])
            relative_dir = "/".join(parts[1:-1])
            dir_name = parts[-2]
            try:
                content = zf.read(skill_md).decode("utf-8")
            except Exception:
                continue
            data = Skill._parse_frontmatter(content)
            name = (data.get("name") or dir_name).strip()
            if not name or not Skill._is_valid_name(name):
                continue

            if relative_dir not in normalized_candidates:
                if not skill_hint or (name != skill_hint and dir_name != skill_hint):
                    continue

            skill_root = _resolve_install_root(scope) / name
            skill_root.mkdir(parents=True, exist_ok=True)
            skill_root_resolved = skill_root.resolve()
            prefix = f"{skill_dir}/"
            file_count = 0
            for member in zf.namelist():
                if not member.startswith(prefix) or member.endswith("/"):
                    continue
                rel_path = member[len(prefix):]
                if not rel_path:
                    continue
                dest = (skill_root / rel_path).resolve()
                try:
                    dest.relative_to(skill_root_resolved)
                except ValueError:
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(zf.read(member))
                file_count += 1

            Skill.clear_cache()
            return SkillInstallResult(
                success=True,
                skill_name=name,
                location=str(skill_root / "SKILL.md"),
                message=(
                    f"Skill '{name}' installed to {skill_root} "
                    f"from GitHub archive {owner}/{repo}@{branch} ({file_count} files)"
                ),
            )

        return SkillInstallResult(
            success=False,
            error=f"No matching SKILL.md found in GitHub archive for {owner}/{repo}@{branch}",
        )

    @classmethod
    async def _download_github_skill_md_raw(
        cls,
        client: Any,
        owner: str,
        repo: str,
        branch: str,
        dir_path: str,
        scope: str,
    ) -> SkillInstallResult:
        """Download SKILL.md through raw.githubusercontent.com as API fallback."""
        raw_path = f"{dir_path.strip('/')}/SKILL.md" if dir_path else "SKILL.md"
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{raw_path}"
        resp = await client.get(url)
        if resp.status_code != 200:
            return SkillInstallResult(
                success=False,
                error=f"Raw GitHub HTTP {resp.status_code} for {url}",
            )
        hint = Path(dir_path).name if dir_path else repo
        result = cls._save_skill_content(resp.text, scope, skill_name_hint=hint)
        if result.success:
            result.message = (
                f"{result.message} (installed from raw GitHub SKILL.md fallback)"
            )
        return result

    @classmethod
    async def _download_github_dir(
        cls,
        client: Any,
        owner: str,
        repo: str,
        branch: str,
        dir_path: str,
        scope: str,
    ) -> SkillInstallResult:
        """
        Recursively download all files in a GitHub directory via the Contents API
        and save them to the skill install root, preserving directory structure.
        """
        api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{dir_path}?ref={branch}"
        resp = await client.get(api_url)
        if resp.status_code != 200:
            return SkillInstallResult(
                success=False,
                error=f"GitHub API {resp.status_code} for {api_url}",
            )

        entries = resp.json()
        if not isinstance(entries, list):
            return SkillInstallResult(
                success=False,
                error=f"Expected directory listing from GitHub API, got: {type(entries)}",
            )

        # Verify SKILL.md exists in this directory
        names = {e["name"] for e in entries if e.get("type") == "file"}
        if "SKILL.md" not in names:
            return SkillInstallResult(
                success=False,
                error=f"No SKILL.md found at {dir_path or 'repo root'} on branch {branch}",
            )

        # Determine skill name from SKILL.md content first
        skill_md_entry = next(e for e in entries if e["name"] == "SKILL.md")
        skill_md_resp = await client.get(skill_md_entry["download_url"])
        if skill_md_resp.status_code != 200:
            return SkillInstallResult(
                success=False,
                error=f"Failed to download SKILL.md: HTTP {skill_md_resp.status_code}",
            )
        skill_md_content = skill_md_resp.text

        # Parse skill name from frontmatter
        data = Skill._parse_frontmatter(skill_md_content)
        name = (data.get("name") or "").strip()
        if not name or not Skill._is_valid_name(name):
            return SkillInstallResult(
                success=False,
                error=f"Invalid or missing skill name in SKILL.md: {name!r}",
            )

        skill_dir = _resolve_install_root(scope) / name
        skill_dir.mkdir(parents=True, exist_ok=True)

        # Recursively download all files preserving directory structure
        file_count = await cls._download_github_entries(
            client, entries, skill_dir, relative_base=""
        )

        Skill.clear_cache()
        log.info("skill.install.github.ok", {
            "name": name,
            "path": str(skill_dir),
            "files": file_count,
        })
        return SkillInstallResult(
            success=True,
            skill_name=name,
            location=str(skill_dir / "SKILL.md"),
            message=f"Skill '{name}' installed to {skill_dir} ({file_count} files)",
        )

    @classmethod
    async def _download_github_entries(
        cls,
        client: Any,
        entries: list,
        base_dir: Path,
        relative_base: str,
    ) -> int:
        """
        Recursively download files from a GitHub Contents API listing.
        Returns total number of files written.
        """
        count = 0
        for entry in entries:
            entry_type = entry.get("type")
            entry_name = entry.get("name", "")
            rel_path = f"{relative_base}/{entry_name}".lstrip("/")

            if entry_type == "file":
                download_url = entry.get("download_url")
                if not download_url:
                    continue
                file_resp = await client.get(download_url)
                if file_resp.status_code != 200:
                    log.warn("skill.install.github.file.skip", {
                        "path": rel_path,
                        "status": file_resp.status_code,
                    })
                    continue
                dest = base_dir / rel_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(file_resp.content)
                count += 1
                log.debug("skill.install.github.file", {"path": rel_path})

            elif entry_type == "dir":
                # Fetch subdirectory listing
                sub_url = entry.get("url")
                if not sub_url:
                    continue
                sub_resp = await client.get(sub_url)
                if sub_resp.status_code != 200:
                    log.warn("skill.install.github.dir.skip", {
                        "path": rel_path,
                        "status": sub_resp.status_code,
                    })
                    continue
                sub_entries = sub_resp.json()
                if isinstance(sub_entries, list):
                    count += await cls._download_github_entries(
                        client, sub_entries, base_dir, rel_path
                    )
        return count

    @classmethod
    async def _install_from_url(
        cls,
        url: str,
        scope: str,
        skill_name_hint: Optional[str] = None,
    ) -> SkillInstallResult:
        """Download a SKILL.md from an arbitrary HTTPS URL."""
        try:
            import httpx
        except ImportError:
            return SkillInstallResult(
                success=False,
                error="httpx is required to download skills. Run: uv add httpx",
            )

        try:
            async with httpx.AsyncClient(timeout=_NETWORK_TIMEOUT_SEC, follow_redirects=True) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return SkillInstallResult(
                        success=False,
                        error=f"HTTP {resp.status_code} fetching {url}",
                    )
                content_type = ""
                headers = getattr(resp, "headers", None)
                if headers is not None:
                    header_value = headers.get("content-type", "")
                    if isinstance(header_value, str):
                        content_type = header_value
                    elif asyncio.iscoroutine(header_value):
                        header_value.close()
                if "text/html" in content_type:
                    return SkillInstallResult(
                        success=False,
                        error=f"URL returned an HTML page instead of a SKILL.md file: {url}",
                    )
                content = resp.text
        except Exception as exc:
            return SkillInstallResult(success=False, error=f"Download failed: {exc}")

        return cls._save_skill_content(content, scope, skill_name_hint=skill_name_hint)

    @classmethod
    async def _install_from_local(cls, path: str, scope: str) -> SkillInstallResult:
        """Install a skill from a local SKILL.md file or directory."""
        local_path = Path(path)

        if local_path.is_dir():
            skill_md = local_path / "SKILL.md"
            if not skill_md.exists():
                return SkillInstallResult(
                    success=False,
                    error=f"No SKILL.md found in directory: {path}",
                )
            local_path = skill_md

        if not local_path.exists():
            return SkillInstallResult(success=False, error=f"File not found: {path}")

        try:
            content = local_path.read_text(encoding="utf-8")
        except Exception as exc:
            return SkillInstallResult(success=False, error=f"Cannot read file: {exc}")

        return cls._save_skill_content(content, scope)

    @classmethod
    def _save_skill_content(
        cls,
        content: str,
        scope: str,
        skill_name_hint: Optional[str] = None,
    ) -> SkillInstallResult:
        """Parse content, validate, and persist to the skills directory."""
        # Reject HTML content (e.g. a web page was downloaded instead of raw SKILL.md)
        stripped = content.lstrip()
        if stripped.lower().startswith("<!doctype") or stripped.lower().startswith("<html"):
            return SkillInstallResult(
                success=False,
                error="Downloaded content is an HTML page, not a valid SKILL.md file. "
                      "Use a direct raw file URL (e.g. raw.githubusercontent.com).",
            )

        # Parse frontmatter to extract name
        data = Skill._parse_frontmatter(content)
        name = (data.get("name") or skill_name_hint or "").strip()

        if not name:
            return SkillInstallResult(
                success=False,
                error="Cannot determine skill name from SKILL.md frontmatter.",
            )

        if not Skill._is_valid_name(name):
            return SkillInstallResult(
                success=False,
                error=f"Invalid skill name: {name!r}. Must match [a-z0-9]+(-[a-z0-9]+)*",
            )

        skill_dir = _resolve_install_root(scope) / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text(content, encoding="utf-8")

        Skill.clear_cache()
        log.info("skill.install.saved", {"name": name, "path": str(skill_path)})

        return SkillInstallResult(
            success=True,
            skill_name=name,
            location=str(skill_path),
            message=f"Skill '{name}' installed to {skill_path}",
        )

    # ------------------------------------------------------------------
    # Install skill dependencies
    # ------------------------------------------------------------------

    @classmethod
    async def install_deps(
        cls,
        skill_name: str,
        install_id: Optional[str] = None,
        timeout_ms: int = 300_000,
    ) -> List[DepInstallResult]:
        """
        Install a skill's declared tool dependencies.

        Args:
            skill_name: Name of the skill
            install_id: If set, only install the spec with this id
            timeout_ms: Subprocess timeout in milliseconds (default 5 min)

        Returns:
            List of DepInstallResult, one per executed spec
        """
        skill = await Skill.get(skill_name)
        if not skill:
            return [DepInstallResult(
                success=False,
                error=f"Skill not found: {skill_name}",
            )]

        specs = skill.install_specs or []
        if not specs:
            return [DepInstallResult(
                success=True,
                message=f"Skill '{skill_name}' has no install specs.",
            )]

        if install_id is not None:
            specs = [s for s in specs if s.id == install_id]
            if not specs:
                return [DepInstallResult(
                    success=False,
                    error=f"No install spec with id='{install_id}' in skill '{skill_name}'",
                )]

        results: List[DepInstallResult] = []
        timeout_sec = timeout_ms / 1000

        for spec in specs:
            result = await cls._execute_install_spec(spec, timeout_sec)
            results.append(result)

        return results

    @classmethod
    async def _execute_install_spec(
        cls,
        spec: SkillInstallSpec,
        timeout_sec: float,
    ) -> DepInstallResult:
        """Build and execute an install command for one SkillInstallSpec."""
        cmd = cls._build_install_command(spec)
        if not cmd:
            return DepInstallResult(
                success=False,
                spec_id=spec.id,
                error=f"Cannot build install command for kind={spec.kind!r}",
            )

        log.info("skill.dep.install.start", {"kind": spec.kind, "cmd": cmd})
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout_sec
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return DepInstallResult(
                    success=False,
                    spec_id=spec.id,
                    command=cmd,
                    error=f"Install timed out after {timeout_sec}s",
                )

            stdout = stdout_b.decode(errors="replace")
            stderr = stderr_b.decode(errors="replace")
            returncode = proc.returncode if proc.returncode is not None else 0
            success = returncode == 0

            log.info("skill.dep.install.done", {
                "kind": spec.kind,
                "returncode": returncode,
                "success": success,
            })
            return DepInstallResult(
                success=success,
                spec_id=spec.id,
                command=cmd,
                stdout=stdout,
                stderr=stderr,
                returncode=returncode,
                error=None if success else f"Command exited with code {returncode}",
            )
        except Exception as exc:
            return DepInstallResult(
                success=False,
                spec_id=spec.id,
                command=cmd,
                error=str(exc),
            )

    @staticmethod
    def _build_install_command(spec: SkillInstallSpec) -> Optional[List[str]]:
        """Return the argv list for the install spec, or None if unsupported."""
        current_os = platform.system().lower()  # darwin / linux / windows

        # OS guard
        if spec.os:
            os_map = {"darwin": "darwin", "linux": "linux", "windows": "win32"}
            allowed = {os_map.get(o, o) for o in spec.os}
            if current_os not in allowed and f"{current_os}" not in spec.os:
                log.warn("skill.dep.install.os_skip", {
                    "kind": spec.kind,
                    "spec_os": spec.os,
                    "current_os": current_os,
                })
                return None

        if spec.kind == "brew":
            if not spec.formula:
                return None
            return ["brew", "install", spec.formula]

        if spec.kind == "npm":
            if not spec.package:
                return None
            return ["npm", "install", "-g", "--ignore-scripts", spec.package]

        if spec.kind == "uv":
            if not spec.package:
                return None
            return ["uv", "tool", "install", spec.package]

        if spec.kind == "pip":
            if not spec.package:
                return None
            return [sys.executable, "-m", "pip", "install", spec.package]

        if spec.kind == "go":
            if not (spec.module or spec.package):
                return None
            return ["go", "install", spec.module or spec.package]

        # download kind is handled separately (binary download, not a package manager)
        return None

    # ------------------------------------------------------------------
    # Uninstall
    # ------------------------------------------------------------------

    @classmethod
    async def uninstall(cls, skill_name: str) -> SkillInstallResult:
        """
        Remove a user-managed skill from ~/.flocks/plugins/skills/.

        Only skills installed under the user skills root can be removed via API.
        """
        skill = await Skill.get(skill_name)
        if not skill:
            return SkillInstallResult(
                success=False,
                error=f"Skill not found: {skill_name}",
            )

        skill_path = Path(skill.location)
        skill_dir = skill_path.parent

        if not skill_path.is_relative_to(_user_skills_root()):
            return SkillInstallResult(
                success=False,
                error=(
                    f"Skill '{skill_name}' is not user-managed "
                    f"(location: {skill.location}). Only skills installed to "
                    f"~/.flocks/plugins/skills/ can be removed."
                ),
            )

        try:
            shutil.rmtree(skill_dir)
            Skill.clear_cache()
            log.info("skill.uninstall.ok", {"name": skill_name, "dir": str(skill_dir)})
            return SkillInstallResult(
                success=True,
                skill_name=skill_name,
                message=f"Skill '{skill_name}' removed.",
            )
        except Exception as exc:
            return SkillInstallResult(success=False, error=str(exc))
