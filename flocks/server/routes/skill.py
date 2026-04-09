"""
Skill and Command management routes

Provides API endpoints for skills and commands discovery and management.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from flocks.skill.skill import Skill, SkillInfo
from flocks.skill.installer import SkillInstaller, SkillInstallResult, DepInstallResult
from flocks.command.command import Command, CommandInfo
from flocks.storage.storage import Storage
from flocks.utils.log import Log


def _user_skills_root() -> Path:
    """Return the canonical user-level skills directory (~/.flocks/plugins/skills/)."""
    return Path.home() / ".flocks" / "plugins" / "skills"


def _is_user_managed_skill(skill: SkillInfo) -> bool:
    """Return True if this skill lives under the user-managed skills root.

    Only user-managed skills (created via POST /api/skills) can be renamed or
    deleted through the API.  Skills discovered from project .flocks/ directories
    or other system locations are read-only from the API's perspective.
    """
    try:
        return Path(skill.location).is_relative_to(_user_skills_root())
    except ValueError:
        return False


router = APIRouter()
log = Log.create(service="skill-routes")


# =============================================================================
# Request/Response Models
# =============================================================================

class SkillRequiresResponse(BaseModel):
    bins: Optional[List[str]] = None
    any_bins: Optional[List[str]] = None
    env: Optional[List[str]] = None


class SkillInstallSpecResponse(BaseModel):
    id: Optional[str] = None
    kind: str
    label: Optional[str] = None
    bins: Optional[List[str]] = None
    formula: Optional[str] = None
    package: Optional[str] = None
    url: Optional[str] = None


class SkillResponse(BaseModel):
    """Skill response"""
    name: str = Field(..., description="Skill name")
    description: str = Field(..., description="Skill description")
    description_cn: Optional[str] = Field(None, description="Localized Chinese skill description")
    location: str = Field(..., description="Path to SKILL.md")
    source: Optional[str] = Field(None, description="Discovery source")
    content: Optional[str] = Field(None, description="Full SKILL.md content")
    category: Optional[str] = Field(None, description="Skill category (e.g. 'system')")
    # Extended fields
    eligible: Optional[bool] = Field(None, description="Whether all requirements are met")
    missing: Optional[List[str]] = Field(None, description="Missing bins/env vars")
    requires: Optional[SkillRequiresResponse] = Field(None, description="Runtime requirements")
    install_specs: Optional[List[SkillInstallSpecResponse]] = Field(
        None, description="Dependency install specs"
    )


class SkillCreateRequest(BaseModel):
    """Request to create a new skill"""
    name: str = Field(..., description="Skill name")
    description: str = Field(..., description="Skill description")
    description_cn: Optional[str] = Field(None, description="Localized Chinese skill description")
    content: str = Field(..., description="Skill content (markdown)")


class SkillInstallRequest(BaseModel):
    """Request to install a skill from an external source"""
    source: str = Field(
        ...,
        description=(
            "Install source. Supported formats:\n"
            "  clawhub:<name>         – clawhub.com registry\n"
            "  github:<owner>/<repo>  – GitHub repo\n"
            "  https://...            – direct URL to SKILL.md\n"
            "  /local/path            – local file or directory\n"
            "  <owner>/<repo>         – shorthand for GitHub"
        ),
    )
    scope: str = Field(
        default="global",
        description="'global' (default, ~/.flocks/plugins/skills/) or 'project' (.flocks/plugins/skills/)",
    )


class SkillInstallResponse(BaseModel):
    success: bool
    skill_name: Optional[str] = None
    location: Optional[str] = None
    message: str = ""
    error: Optional[str] = None


class DepInstallSpecResult(BaseModel):
    success: bool
    spec_id: Optional[str] = None
    command: List[str] = []
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    error: Optional[str] = None


class DepInstallResponse(BaseModel):
    results: List[DepInstallSpecResult]


class DepInstallRequest(BaseModel):
    install_id: Optional[str] = Field(
        default=None,
        description="If set, only install the spec with this id",
    )
    timeout_ms: int = Field(default=300_000, description="Timeout in milliseconds (default 5 min)")


class CommandResponse(BaseModel):
    """Command response"""
    name: str = Field(..., description="Command name")
    description: str = Field(..., description="Command description")
    template: str = Field(..., description="Command template")
    agent: Optional[str] = Field(None, description="Preferred agent")
    model: Optional[str] = Field(None, description="Preferred model")
    subtask: Optional[bool] = Field(None, description="Run as subtask")
    hidden: bool = Field(False, description="Hidden from UI")


# =============================================================================
# Helpers
# =============================================================================

def _skill_to_response(skill: SkillInfo, include_content: bool = False) -> SkillResponse:
    content = None
    if include_content:
        try:
            with open(skill.location, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            pass

    requires_resp = None
    if skill.requires:
        requires_resp = SkillRequiresResponse(
            bins=skill.requires.bins,
            any_bins=skill.requires.any_bins,
            env=skill.requires.env,
        )

    install_specs_resp = None
    if skill.install_specs:
        install_specs_resp = [
            SkillInstallSpecResponse(
                id=s.id,
                kind=s.kind,
                label=s.label,
                bins=s.bins,
                formula=s.formula,
                package=s.package,
                url=s.url,
            )
            for s in skill.install_specs
        ]

    return SkillResponse(
        name=skill.name,
        description=skill.description,
        description_cn=skill.description_cn,
        location=skill.location,
        source=skill.source,
        content=content,
        category=skill.category,
        eligible=skill.eligible,
        missing=skill.missing,
        requires=requires_resp,
        install_specs=install_specs_resp,
    )


def _build_skill_frontmatter(req: SkillCreateRequest) -> str:
    lines = [
        "---",
        f"name: {req.name}",
        f"description: {req.description}",
    ]
    if req.description_cn:
        lines.append(f"description_cn: {req.description_cn}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


# =============================================================================
# Skill API Endpoints
# =============================================================================

@router.get("/skills", response_model=List[SkillResponse])
async def list_skills():
    """
    Get skill list

    Returns list of all discovered skills from SKILL.md files.
    """
    try:
        skills = await Skill.all()
        result = [_skill_to_response(skill) for skill in skills]
        log.info("skills.list", {"count": len(result)})
        return result
    except Exception as e:
        log.error("skills.list.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to list skills: {str(e)}")


@router.get("/skills/status", response_model=List[SkillResponse])
async def skill_status():
    """
    Get skill status with eligibility information

    Returns all skills enriched with `eligible` and `missing` fields based
    on runtime dependency checks (bins in PATH, env vars set).
    """
    try:
        skills = await Skill.all()
        result = []
        for skill in skills:
            checked = Skill.check_eligibility(skill)
            result.append(_skill_to_response(checked))
        log.info("skills.status", {"count": len(result)})
        return result
    except Exception as e:
        log.error("skills.status.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to get skill status: {str(e)}")


@router.post("/skills/refresh")
async def refresh_skills():
    """
    Refresh skill list

    Forces re-scanning of skill directories and updates cache.
    """
    try:
        skills = await Skill.refresh()
        log.info("skills.refreshed", {"count": len(skills)})
        return {
            "status": "success",
            "message": f"Refreshed {len(skills)} skills",
            "count": len(skills),
        }
    except Exception as e:
        log.error("skills.refresh.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to refresh skills: {str(e)}")


@router.post("/skills/install", response_model=SkillInstallResponse, status_code=status.HTTP_200_OK)
async def install_skill(req: SkillInstallRequest):
    """
    Install a skill from an external source

    Supported sources:
    - `clawhub:<name>` — clawhub.com registry (OpenClaw ecosystem)
    - `github:<owner>/<repo>` or `<owner>/<repo>` — GitHub repository
    - `https://...` — direct URL to a SKILL.md file
    - `/local/path` — local filesystem path
    - `safeskill:<name>` — SafeSkill registry (reserved, future)
    """
    try:
        result = await SkillInstaller.install_from_source(req.source, scope=req.scope)
        if not result.success:
            raise HTTPException(
                status_code=422,
                detail=result.error or "Install failed",
            )
        log.info("skill.install.api.ok", {"source": req.source, "name": result.skill_name})
        return SkillInstallResponse(
            success=result.success,
            skill_name=result.skill_name,
            location=result.location,
            message=result.message,
        )
    except HTTPException:
        raise
    except Exception as e:
        log.error("skill.install.api.error", {"source": req.source, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Install failed: {str(e)}")


@router.get("/skills/{name}", response_model=SkillResponse)
async def get_skill(name: str):
    """
    Get skill details

    Returns skill information including full SKILL.md content.
    """
    try:
        skill = await Skill.get(name)
        if not skill:
            raise HTTPException(status_code=404, detail=f"Skill not found: {name}")

        checked = Skill.check_eligibility(skill)
        return _skill_to_response(checked, include_content=True)
    except HTTPException:
        raise
    except Exception as e:
        log.error("skill.get.error", {"name": name, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to get skill: {str(e)}")


@router.post("/skills/{name}/install-deps", response_model=DepInstallResponse)
async def install_skill_deps(name: str, req: DepInstallRequest):
    """
    Install a skill's tool dependencies

    Executes the install specs declared in the skill's SKILL.md metadata
    (brew, npm, uv, pip, go).  Returns per-spec results.
    """
    try:
        results = await SkillInstaller.install_deps(
            name,
            install_id=req.install_id,
            timeout_ms=req.timeout_ms,
        )
        dep_results = [
            DepInstallSpecResult(
                success=r.success,
                spec_id=r.spec_id,
                command=r.command,
                stdout=r.stdout,
                stderr=r.stderr,
                returncode=r.returncode,
                error=r.error,
            )
            for r in results
        ]
        log.info("skill.install_deps.api.ok", {"name": name, "count": len(dep_results)})
        return DepInstallResponse(results=dep_results)
    except Exception as e:
        log.error("skill.install_deps.api.error", {"name": name, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to install deps: {str(e)}")


@router.post("/skills", response_model=SkillResponse, status_code=status.HTTP_201_CREATED)
async def create_skill(req: SkillCreateRequest):
    """
    Create a new skill

    Creates a new SKILL.md file in the user's skill directory
    (~/.flocks/plugins/skills/<name>/SKILL.md).
    """
    try:
        skill_dir = _user_skills_root() / req.name
        skill_dir.mkdir(parents=True, exist_ok=True)

        skill_path = skill_dir / "SKILL.md"

        frontmatter = _build_skill_frontmatter(req)
        full_content = frontmatter + req.content

        skill_path.write_text(full_content, encoding="utf-8")

        Skill.clear_cache()

        log.info("skill.created", {"name": req.name, "path": str(skill_path)})

        return SkillResponse(
            name=req.name,
            description=req.description,
            description_cn=req.description_cn,
            location=str(skill_path),
            source="user",
            content=full_content,
        )
    except HTTPException:
        raise
    except Exception as e:
        log.error("skill.create.error", {"name": req.name, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to create skill: {str(e)}")


@router.put("/skills/{name}", response_model=SkillResponse)
async def update_skill(name: str, req: SkillCreateRequest):
    """
    Update a skill.

    If req.name differs from name (rename), only user-managed skills
    (~/.flocks/plugins/skills/) can be renamed; creates new directory,
    writes file, removes old directory.
    """
    try:
        skill = await Skill.get(name)
        if not skill:
            raise HTTPException(status_code=404, detail=f"Skill not found: {name}")

        frontmatter = _build_skill_frontmatter(req)
        full_content = frontmatter + req.content
        is_rename = req.name != name

        if is_rename:
            if skill.source == 'project':
                raise HTTPException(
                    status_code=400,
                    detail="Built-in project skills (.flocks/plugins/skills/) cannot be renamed",
                )
            new_dir = _user_skills_root() / req.name
            new_path = new_dir / "SKILL.md"
            new_dir.mkdir(parents=True, exist_ok=True)
            new_path.write_text(full_content, encoding="utf-8")

            old_dir = Path(skill.location).parent
            if old_dir.exists() and old_dir != new_dir:
                remaining = [f for f in old_dir.iterdir() if f.name != "SKILL.md"]
                if not remaining:
                    shutil.rmtree(old_dir)
                else:
                    old_file = old_dir / "SKILL.md"
                    if old_file.exists():
                        old_file.unlink()
                    log.warning("skill.rename.kept_old_dir", {
                        "old_dir": str(old_dir),
                        "remaining_files": [f.name for f in remaining],
                    })
            location = str(new_path)
            log.info("skill.renamed", {"old": name, "new": req.name, "path": location})
        else:
            Path(skill.location).write_text(full_content, encoding="utf-8")
            location = skill.location
            log.info("skill.updated", {"name": name, "path": location})

        Skill.clear_cache()
        return SkillResponse(
            name=req.name,
            description=req.description,
            description_cn=req.description_cn,
            location=location,
            source=skill.source,
            content=full_content,
        )
    except HTTPException:
        raise
    except Exception as e:
        log.error("skill.update.error", {"name": name, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to update skill: {str(e)}")


@router.delete("/skills/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill(name: str):
    """
    Delete a skill

    Removes the skill directory.  Only user-managed skills
    (~/.flocks/plugins/skills/) can be deleted via the API.
    """
    try:
        skill = await Skill.get(name)
        if not skill:
            raise HTTPException(status_code=404, detail=f"Skill not found: {name}")

        if skill.source == 'project':
            raise HTTPException(
                status_code=403,
                detail="Built-in project skills (.flocks/plugins/skills/) cannot be deleted",
            )

        skill_dir = Path(skill.location).parent
        if skill_dir.exists():
            shutil.rmtree(skill_dir)

        Skill.clear_cache()

        log.info("skill.deleted", {"name": name})
        return None
    except HTTPException:
        raise
    except Exception as e:
        log.error("skill.delete.error", {"name": name, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to delete skill: {str(e)}")


# =============================================================================
# Command API Endpoints
# =============================================================================

@router.get("/commands", response_model=List[CommandResponse])
async def list_commands():
    """
    Get command list

    Returns list of all registered slash commands.
    """
    try:
        commands = Command.list()

        result = []
        for cmd in commands:
            result.append(CommandResponse(
                name=cmd.name,
                description=cmd.description,
                template=cmd.template,
                agent=cmd.agent,
                model=cmd.model,
                subtask=cmd.subtask,
                hidden=cmd.hidden,
            ))

        log.info("commands.list", {"count": len(result)})
        return result
    except Exception as e:
        log.error("commands.list.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to list commands: {str(e)}")


@router.get("/commands/{name}", response_model=CommandResponse)
async def get_command(name: str):
    """
    Get command details

    Returns detailed information about a specific command.
    """
    try:
        cmd = Command.get(name)
        if not cmd:
            raise HTTPException(status_code=404, detail=f"Command not found: {name}")

        return CommandResponse(
            name=cmd.name,
            description=cmd.description,
            template=cmd.template,
            agent=cmd.agent,
            model=cmd.model,
            subtask=cmd.subtask,
            hidden=cmd.hidden,
        )
    except HTTPException:
        raise
    except Exception as e:
        log.error("command.get.error", {"name": name, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to get command: {str(e)}")
