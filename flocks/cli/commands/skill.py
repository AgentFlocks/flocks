"""
Skill CLI commands

Provides skill management commands:
  flocks skills list             – list all discovered skills
  flocks skills status           – show eligibility status (deps check)
  flocks skills find <query>     – search installable skills
  flocks skills install <source> – install a skill from URL/GitHub/clawhub/skills.sh/SafeSkill/local
  flocks skills remove <name>    – uninstall a user-managed skill
  flocks skills install-deps     – install a skill's declared tool dependencies
"""

import asyncio
import json
import re
import shutil
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from flocks.skill.skill import Skill, SkillInfo
from flocks.skill.installer import SkillInstaller


skill_app = typer.Typer(
    name="skills",
    help="Manage skills",
    no_args_is_help=True,
)

console = Console()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _eligibility_badge(skill: SkillInfo) -> Text:
    """Return a colored Rich Text badge for eligibility."""
    if skill.eligible is None:
        return Text("—", style="dim")
    if skill.eligible:
        return Text("✓ ready", style="green")
    missing = ", ".join(skill.missing or [])
    return Text(f"⚠ missing: {missing}", style="yellow")


def _source_label(source: Optional[str]) -> str:
    return source or "—"


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

@skill_app.command("list")
def list_skills(
    status: bool = typer.Option(False, "--status", "-s", help="Include eligibility status check"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """List all discovered skills."""
    skills = asyncio.run(Skill.all())

    if status:
        skills = [Skill.check_eligibility(s) for s in skills]

    if json_output:
        import json
        data = [
            {
                "name": s.name,
                "description": s.description,
                "source": s.source,
                "category": s.category,
                "location": s.location,
                "eligible": s.eligible,
                "missing": s.missing,
            }
            for s in skills
        ]
        console.print_json(json.dumps(data))
        return

    if not skills:
        console.print("[dim]No skills found.[/dim]")
        return

    table = Table(show_header=True, header_style="bold cyan", box=None, pad_edge=False)
    table.add_column("Name", style="bold", min_width=20)
    table.add_column("Description", min_width=40)
    table.add_column("Source", style="dim", min_width=8)
    if status:
        table.add_column("Status", min_width=20)

    for s in sorted(skills, key=lambda x: x.name):
        row = [s.name, s.description or "—", _source_label(s.source)]
        if status:
            row.append(_eligibility_badge(s))
        table.add_row(*row)

    console.print(table)
    console.print(f"\n[dim]{len(skills)} skill(s) found.[/dim]")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

@skill_app.command("status")
def check_status(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Check eligibility status for all skills (bin/env requirements)."""
    skills = asyncio.run(Skill.all())
    skills = [Skill.check_eligibility(s) for s in skills]

    if json_output:
        import json
        data = [
            {
                "name": s.name,
                "eligible": s.eligible,
                "missing": s.missing,
                "requires": {
                    "bins": s.requires.bins if s.requires else [],
                    "env": s.requires.env if s.requires else [],
                } if s.requires else None,
            }
            for s in skills
        ]
        console.print_json(json.dumps(data))
        return

    # Three mutually exclusive categories
    no_reqs   = [s for s in skills if s.eligible is True and not s.requires]
    ready     = [s for s in skills if s.eligible is True and s.requires]
    not_ready = [s for s in skills if s.eligible is False]

    console.print(Panel(
        f"[green]Ready:[/green] {len(ready)}  "
        f"[yellow]Missing deps:[/yellow] {len(not_ready)}  "
        f"[dim]No requirements:[/dim] {len(no_reqs)}",
        title="[bold]Skill Status[/bold]",
        expand=False,
    ))

    if skills:
        table = Table(show_header=True, header_style="bold cyan", box=None, pad_edge=False)
        table.add_column("Name", min_width=20)
        table.add_column("Status", min_width=16)
        table.add_column("Details", min_width=30)
        for s in sorted(skills, key=lambda item: item.name):
            install_hint = ""
            if s.install_specs:
                install_hint = f"flocks skills install-deps {s.name}"
            if s.eligible is False:
                status = "[yellow]missing deps[/yellow]"
                details = ", ".join(s.missing or []) or install_hint or "—"
            elif s.requires:
                status = "[green]ready[/green]"
                details = install_hint or "requirements satisfied"
            else:
                status = "[dim]no requirements[/dim]"
                details = "—"
            table.add_row(s.name, status, details)
        console.print(table)


# ---------------------------------------------------------------------------
# find
# ---------------------------------------------------------------------------

@skill_app.command("find")
def find_skills(
    query: str = typer.Argument(..., help="Search keyword, e.g. 'threat intel' or 'github'"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Search installable skills from local, clawhub, skills.sh, SafeSkill, and GitHub."""
    results = asyncio.run(_search_skills(query))

    if json_output:
        import json
        console.print_json(json.dumps(results))
        return

    if not results:
        console.print(f"[dim]No skills found matching '{query}'.[/dim]")
        console.print("\n[dim]Tip: try browsing https://skills.sh or https://safeskill.cn for more skills.[/dim]")
        return

    table = Table(show_header=True, header_style="bold cyan", box=None, pad_edge=False)
    table.add_column("Name", style="bold", min_width=25)
    table.add_column("Description", min_width=45)
    table.add_column("Source", style="dim", min_width=10)
    table.add_column("Install", style="dim")

    for r in results:
        table.add_row(
            r["name"],
            r.get("description", "—"),
            r.get("source", "—"),
            r.get("install_hint", "—"),
        )

    console.print(table)
    console.print(f"\n[dim]{len(results)} result(s) for '{query}'.[/dim]")
    console.print("[dim]Install with: flocks skills install <source>[/dim]")


async def _search_skills(query: str) -> list:
    """
    Search for skills matching query from multiple sources:
    1. Already-installed local skills
    2. clawhub.com search API
    3. skills.sh search API
    4. SafeSkill CLI search
    5. Curated GitHub collections (Anthropic-Cybersecurity-Skills etc.)
    """
    results = []
    query_lower = query.lower()

    # 1. Search installed skills
    installed = await Skill.all()
    for s in installed:
        if query_lower in s.name.lower() or query_lower in (s.description or "").lower():
            results.append({
                "name": s.name,
                "description": s.description,
                "source": f"installed ({s.source})",
                "install_hint": "(already installed)",
            })

    # 2. Search clawhub.com
    clawhub_results = await _search_clawhub(query)
    results.extend(clawhub_results)

    # 3. Search skills.sh
    results.extend(await _search_skills_sh(query))

    # 4. Search SafeSkill when the CLI is available
    results.extend(await _search_safeskill(query))

    # 5. Search curated GitHub skill collections
    github_results = await _search_github_collections(query)
    # Deduplicate by name
    existing_names = {r["name"] for r in results}
    for r in github_results:
        if r["name"] not in existing_names:
            results.append(r)
            existing_names.add(r["name"])

    return results


async def _search_clawhub(query: str) -> list:
    """Query clawhub.com search API."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            # Try clawhub search API endpoint
            resp = await client.get(
                "https://clawhub.com/api/search",
                params={"q": query},
                headers={"Accept": "application/json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                skills = data if isinstance(data, list) else data.get("skills", data.get("results", []))
                return [
                    {
                        "name": s.get("name", s.get("slug", "?")),
                        "description": s.get("description", ""),
                        "source": "clawhub.com",
                        "install_hint": f"clawhub:{s.get('name', s.get('slug', ''))}",
                    }
                    for s in skills
                    if isinstance(s, dict)
                ]
    except Exception:
        pass
    return []


async def _search_skills_sh(query: str) -> list:
    """Query skills.sh search API."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(
                "https://skills.sh/api/search",
                params={"q": query, "limit": 10},
                headers={"Accept": "application/json"},
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            skills = data.get("skills", []) if isinstance(data, dict) else []
            results = []
            for item in skills:
                if not isinstance(item, dict):
                    continue
                identifier = item.get("id")
                repo = item.get("source")
                skill_id = item.get("skillId")
                if not identifier and isinstance(repo, str) and isinstance(skill_id, str):
                    identifier = f"{repo}/{skill_id}"
                if not isinstance(identifier, str) or identifier.count("/") < 2:
                    continue
                parts = identifier.split("/", 2)
                name = str(item.get("name") or parts[-1].split("/")[-1])
                installs = item.get("installs")
                installs_suffix = (
                    f" · {installs:,} installs"
                    if isinstance(installs, int)
                    else ""
                )
                results.append({
                    "name": name,
                    "description": f"Indexed by skills.sh from {parts[0]}/{parts[1]}{installs_suffix}",
                    "source": "skills.sh",
                    "install_hint": f"skills-sh:{identifier}",
                })
            return results
    except Exception:
        return []


async def _search_safeskill(query: str) -> list:
    """Search SafeSkill through its npx CLI when available."""
    npx = shutil.which("npx")
    if not npx:
        return []
    try:
        proc = await asyncio.create_subprocess_exec(
            npx,
            "-y",
            "@safeskill/cli",
            "find",
            query,
            "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, _stderr_b = await asyncio.wait_for(proc.communicate(), timeout=20)
        if proc.returncode != 0:
            return []
        text = stdout_b.decode(errors="replace").strip()
        if not text:
            return []
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return _parse_safeskill_text_results(text)
        items = data if isinstance(data, list) else data.get("skills", data.get("results", []))
        if not isinstance(items, list):
            return []
        results = []
        for item in items:
            if not isinstance(item, dict):
                continue
            source = item.get("source") or item.get("install") or item.get("url") or item.get("id")
            name = item.get("name") or item.get("slug") or source
            if not source or not name:
                continue
            results.append({
                "name": str(name),
                "description": str(item.get("description") or ""),
                "source": "safeskill.cn",
                "install_hint": f"safeskill:{source}",
            })
        return results
    except Exception:
        return []


def _parse_safeskill_text_results(text: str) -> list:
    """Best-effort parsing for SafeSkill CLI text output."""
    results = []
    for line in text.splitlines():
        clean = line.strip(" -\t")
        if not clean or clean.lower().startswith(("name", "found", "search")):
            continue
        match = re.search(r"(safeskill://\S+|https?://\S+|[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+)", clean)
        if not match:
            continue
        source = match.group(1).rstrip(",.;")
        name = source.rstrip("/").split("/")[-1]
        results.append({
            "name": name,
            "description": clean,
            "source": "safeskill.cn",
            "install_hint": f"safeskill:{source}",
        })
    return results


async def _search_github_collections(query: str) -> list:
    """Search curated GitHub skill collection repositories."""
    collections = [
        # Anthropic's cybersecurity skills collection
        ("mukul975", "Anthropic-Cybersecurity-Skills", "skills"),
        # OpenClaw bundled skills
        ("mariozechner", "openclaw", "skills"),
    ]
    results = []
    query_lower = query.lower()

    try:
        import httpx
        async with httpx.AsyncClient(
            timeout=10,
            follow_redirects=True,
            headers={"Accept": "application/vnd.github+json"},
        ) as client:
            for owner, repo, skills_dir in collections:
                try:
                    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{skills_dir}"
                    resp = await client.get(api_url)
                    if resp.status_code != 200:
                        continue
                    entries = resp.json()
                    if not isinstance(entries, list):
                        continue
                    for entry in entries:
                        if entry.get("type") != "dir":
                            continue
                        skill_name = entry["name"]
                        if query_lower not in skill_name.lower():
                            # Try to check description by fetching SKILL.md
                            skill_md_url = (
                                f"https://raw.githubusercontent.com/{owner}/{repo}"
                                f"/main/{skills_dir}/{skill_name}/SKILL.md"
                            )
                            md_resp = await client.get(skill_md_url)
                            if md_resp.status_code != 200:
                                continue
                            if query_lower not in md_resp.text.lower():
                                continue
                            # Parse description
                            desc_match = re.search(r"^description:\s*(.+)$", md_resp.text, re.MULTILINE)
                            description = desc_match.group(1).strip().strip('"\'') if desc_match else ""
                        else:
                            description = ""

                        results.append({
                            "name": skill_name,
                            "description": description,
                            "source": f"github:{owner}/{repo}",
                            "install_hint": f"github:{owner}/{repo}/{skills_dir}/{skill_name}",
                        })
                except Exception:
                    continue
    except Exception:
        pass

    return results


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------

@skill_app.command("install")
def install_skill(
    source: str = typer.Argument(
        ...,
        help=(
            "Install source:\n"
            "  clawhub:<name>        – clawhub.com registry\n"
            "  skills-sh:<id>        – skills.sh identifier (owner/repo/skill)\n"
            "  safeskill:<source>    – SafeSkill Hub/GitHub/local source via SafeSkill CLI\n"
            "  github:<owner>/<repo> – GitHub repository\n"
            "  <owner>/<repo>        – GitHub shorthand\n"
            "  https://...           – direct SKILL.md URL\n"
            "  /local/path           – local path"
        ),
    ),
    skill: Optional[str] = typer.Option(
        None,
        "--skill",
        "-s",
        help="Skill subdirectory name within the source repo (e.g. --skill code-review)",
    ),
    scope: str = typer.Option(
        "global",
        "--scope",
        help="'global' (default) or 'project'",
    ),
):
    """Install a skill from an external source."""
    # If --skill is provided, append it as a subpath to the source
    # e.g. source=https://github.com/owner/repo --skill code-review
    #   → resolved as github:owner/repo/code-review
    effective_source = source
    if skill:
        # Strip trailing slash from source and append skill subpath
        effective_source = source.rstrip("/") + "/" + skill

    with console.status(f"[bold cyan]Installing skill from {effective_source!r}...[/bold cyan]"):
        result = asyncio.run(
            SkillInstaller.install_from_source(effective_source, scope=scope)
        )

    if result.success:
        console.print(f"[green]✓[/green] {result.message}")
        if result.location:
            console.print(f"  [dim]Location: {result.location}[/dim]")

        # Immediately check eligibility
        installed_skill = asyncio.run(Skill.get(result.skill_name or ""))
        if installed_skill and installed_skill.requires:
            checked = Skill.check_eligibility(installed_skill)
            if not checked.eligible:
                console.print(
                    f"\n[yellow]⚠ Skill '{installed_skill.name}' has unmet dependencies:[/yellow]"
                )
                for m in checked.missing or []:
                    console.print(f"  • {m}")
                console.print(
                    f"\n  Run [bold]flocks skills install-deps {installed_skill.name}[/bold] to install them."
                )
    else:
        console.print(f"[red]✗ Install failed:[/red] {result.error}")
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------

@skill_app.command("remove")
def remove_skill(
    name: str = typer.Argument(..., help="Skill name to remove"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
):
    """Uninstall a user-managed skill."""
    if not yes:
        confirmed = typer.confirm(f"Remove skill '{name}'?")
        if not confirmed:
            console.print("[dim]Aborted.[/dim]")
            raise typer.Exit()

    with console.status(f"[bold]Removing skill '{name}'...[/bold]"):
        result = asyncio.run(SkillInstaller.uninstall(name))

    if result.success:
        console.print(f"[green]✓[/green] {result.message}")
    else:
        console.print(f"[red]✗ Remove failed:[/red] {result.error}")
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# install-deps
# ---------------------------------------------------------------------------

@skill_app.command("install-deps")
def install_deps(
    name: str = typer.Argument(..., help="Skill name whose dependencies to install"),
    install_id: Optional[str] = typer.Option(
        None, "--id", help="Install only the spec with this id"
    ),
    timeout: int = typer.Option(300, "--timeout", help="Timeout in seconds (default 300)"),
):
    """Install the tool dependencies declared by a skill."""
    with console.status(f"[bold cyan]Installing dependencies for '{name}'...[/bold cyan]"):
        results = asyncio.run(
            SkillInstaller.install_deps(
                name,
                install_id=install_id,
                timeout_ms=timeout * 1000,
            )
        )

    all_ok = True
    for r in results:
        cmd_str = " ".join(r.command) if r.command else (r.message or "—")
        if r.success:
            console.print(f"[green]✓[/green] {cmd_str}")
            if r.stdout.strip():
                console.print(f"  [dim]{r.stdout.strip()[:200]}[/dim]")
        else:
            all_ok = False
            console.print(f"[red]✗[/red] {cmd_str}")
            if r.error:
                console.print(f"  [red]{r.error}[/red]")
            if r.stderr.strip():
                console.print(f"  [dim]{r.stderr.strip()[:200]}[/dim]")

    if not all_ok:
        raise typer.Exit(code=1)
