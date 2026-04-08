"""
Agent CLI commands

Provides agent management commands: list
Ported from original cli/cmd/agent.ts
"""

import asyncio
import json
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.tree import Tree

from flocks.agent.registry import Agent
from flocks.agent.agent import AgentInfo


agent_app = typer.Typer(
    name="agent",
    help="Manage agents",
    no_args_is_help=True,
)

console = Console()


def _format_mode(mode: str) -> str:
    """Format agent mode for display"""
    mode_colors = {
        "primary": "[cyan]primary[/cyan]",
        "subagent": "[yellow]subagent[/yellow]",
        "all": "[green]all[/green]",
    }
    return mode_colors.get(mode, mode)


def _format_permission_rules(agent: AgentInfo) -> str:
    """Format permission rules as compact string"""
    if not agent.permission:
        return "[dim]none[/dim]"
    
    # Group by action
    allow = []
    deny = []
    ask = []
    
    for rule in agent.permission:
        target = rule.permission
        if rule.pattern and rule.pattern != "*":
            target = f"{target}:{rule.pattern}"
        
        if rule.action == "allow":
            allow.append(target)
        elif rule.action == "deny":
            deny.append(target)
        elif rule.action == "ask":
            ask.append(target)
    
    parts = []
    if allow:
        parts.append(f"[green]allow[/green]: {', '.join(allow[:5])}{'...' if len(allow) > 5 else ''}")
    if deny:
        parts.append(f"[red]deny[/red]: {', '.join(deny[:5])}{'...' if len(deny) > 5 else ''}")
    if ask:
        parts.append(f"[yellow]ask[/yellow]: {', '.join(ask[:3])}{'...' if len(ask) > 3 else ''}")
    
    return "; ".join(parts) if parts else "[dim]none[/dim]"


@agent_app.command("list")
def agent_list(
    format: str = typer.Option(
        "table", "--format",
        help="Output format: table, json, or tree"
    ),
    all_agents: bool = typer.Option(
        False, "-a", "--all",
        help="Include hidden agents"
    ),
    mode: Optional[str] = typer.Option(
        None, "-m", "--mode",
        help="Filter by mode: primary, subagent, or all"
    ),
):
    """
    List all available agents
    
    Shows agents with their mode and permission configuration.
    Use --all to include hidden agents (compaction, title, summary).
    """
    # Get agents
    if all_agents:
        agents = Agent.list()
    else:
        agents = Agent.list_visible()
    
    # Filter by mode
    if mode:
        agents = [a for a in agents if a.mode == mode or a.mode == "all"]
    
    # Sort: native first, then by name
    agents.sort(key=lambda a: (not a.native, a.name))
    
    if not agents:
        console.print("[dim]No agents found[/dim]")
        return
    
    # Output based on format
    if format == "json":
        json_data = [
            {
                "name": a.name,
                "mode": a.mode,
                "native": a.native,
                "hidden": a.hidden,
                "description": a.description,
                "permission": [r.model_dump() for r in a.permission],
            }
            for a in agents
        ]
        console.print(json.dumps(json_data, indent=2))
    
    elif format == "tree":
        # Tree format grouped by mode
        tree = Tree("[bold]Agents[/bold]")
        
        # Group by mode
        by_mode = {"primary": [], "subagent": [], "all": []}
        for agent in agents:
            by_mode.setdefault(agent.mode, []).append(agent)
        
        for mode_name, mode_agents in by_mode.items():
            if not mode_agents:
                continue
            
            mode_branch = tree.add(f"[bold cyan]{mode_name}[/bold cyan]")
            
            for agent in mode_agents:
                label = agent.name
                if agent.hidden:
                    label += " [dim](hidden)[/dim]"
                if not agent.native:
                    label += " [yellow](custom)[/yellow]"
                
                agent_branch = mode_branch.add(label)
                
                if agent.description:
                    agent_branch.add(f"[dim]{agent.description[:60]}...[/dim]" if len(agent.description or "") > 60 else f"[dim]{agent.description}[/dim]")
        
        console.print(tree)
    
    else:
        # Table format
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Name", style="bold")
        table.add_column("Mode")
        table.add_column("Type")
        table.add_column("Permissions", max_width=60)
        
        for agent in agents:
            name = agent.name
            if agent.hidden:
                name += " [dim](hidden)[/dim]"
            
            agent_type = "[green]native[/green]" if agent.native else "[yellow]custom[/yellow]"
            
            table.add_row(
                name,
                _format_mode(agent.mode),
                agent_type,
                _format_permission_rules(agent)
            )
        
        console.print(table)
        console.print(f"\n[dim]{len(agents)} agent(s)[/dim]")


@agent_app.command("show")
def agent_show(
    name: str = typer.Argument(..., help="Agent name to show"),
):
    """
    Show details of a specific agent
    """
    agent = Agent.get(name)
    
    if not agent:
        console.print(f"[red]Agent not found: {name}[/red]")
        raise typer.Exit(1)
    
    # Display agent info
    console.print()
    console.print(f"[bold cyan]Agent: {agent.name}[/bold cyan]")
    console.print(f"  Mode:     {_format_mode(agent.mode)}")
    console.print(f"  Type:     {'[green]native[/green]' if agent.native else '[yellow]custom[/yellow]'}")
    console.print(f"  Hidden:   {'yes' if agent.hidden else 'no'}")
    
    if agent.description:
        console.print()
        console.print("[dim]Description:[/dim]")
        console.print(f"  {agent.description}")
    
    if agent.model:
        console.print()
        console.print("[dim]Model:[/dim]")
        console.print(f"  Provider: {agent.model.provider_id}")
        console.print(f"  Model:    {agent.model.model_id}")
    
    if agent.temperature is not None:
        console.print(f"  Temperature: {agent.temperature}")
    
    if agent.top_p is not None:
        console.print(f"  Top P:    {agent.top_p}")
    
    if agent.steps is not None:
        console.print(f"  Max Steps: {agent.steps}")
    
    # Permission rules
    console.print()
    console.print("[dim]Permission Rules:[/dim]")
    
    if agent.permission:
        for rule in agent.permission:
            action_color = {
                "allow": "green",
                "deny": "red",
                "ask": "yellow",
            }.get(rule.action, "white")
            
            pattern_str = f" ({rule.pattern})" if rule.pattern and rule.pattern != "*" else ""
            console.print(f"  [{action_color}]{rule.action:5}[/{action_color}] {rule.permission}{pattern_str}")
    else:
        console.print("  [dim]No rules defined[/dim]")
    
    # System prompt
    if agent.prompt:
        console.print()
        console.print("[dim]System Prompt:[/dim]")
        # Show first 500 chars
        prompt_preview = agent.prompt[:500]
        if len(agent.prompt) > 500:
            prompt_preview += "..."
        console.print(Panel(prompt_preview, border_style="dim"))


@agent_app.command("permissions")
def agent_permissions(
    name: str = typer.Argument(..., help="Agent name"),
    tool: str = typer.Argument(..., help="Tool name to check"),
    pattern: Optional[str] = typer.Option(
        None, "-p", "--pattern",
        help="Optional pattern for path-based rules"
    ),
):
    """
    Check whether an agent declares a specific tool.
    """
    agent = Agent.get(name)
    
    if not agent:
        console.print(f"[red]Agent not found: {name}[/red]")
        raise typer.Exit(1)
    
    _ = pattern
    result = asyncio.run(Agent.has_tool(name, tool))
    color = "green" if result else "red"
    console.print(f"[{color}]{'allow' if result else 'deny'}[/{color}]")
