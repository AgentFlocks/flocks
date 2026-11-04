#!/usr/bin/env python3
"""
OneSec CLI - 精简版

支持 5 个查询接口：
  threat search
  threat top
  log search
  log types
  log trend
"""

import sys
from datetime import datetime

import click
from rich import box
from rich.console import Console
from rich.json import JSON
from rich.table import Table

console = Console()


def print_error(message: str) -> None:
    console.print(f"[bold red]✗ {message}[/bold red]")


def print_info(message: str) -> None:
    console.print(f"[cyan]ℹ {message}[/cyan]")


def format_timestamp(ts: int) -> str:
    if not ts:
        return "-"
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)


@click.group()
@click.option("--token", "-t", help="CSRF Token，或设置 ONESEC_CSRF_TOKEN")
@click.option("--base-url", "-u", help="Base URL，或设置 ONESEC_BASE_URL")
@click.option("--debug", "-d", is_flag=True, help="启用调试输出")
@click.pass_context
def cli(ctx, token, base_url, debug):
    """OneSec CLI 精简版。"""
    ctx.ensure_object(dict)

    if ctx.resilient_parsing or "--help" in sys.argv or "-h" in sys.argv:
        return

    from api_client import OneSecAPIError, OneSecClient, resolve_auth_file
    from config import BASE_URL, TOKEN

    actual_base_url = base_url or BASE_URL
    auth_file = resolve_auth_file()
    actual_token = token or TOKEN

    if auth_file is None and not actual_token:
        print_error("未提供认证信息。请配置 auth-state.json、onesec_cookie.json 或 ONESEC_CSRF_TOKEN。")
        sys.exit(1)

    ctx.obj["client"] = OneSecClient(
        base_url=actual_base_url,
        auth_file=auth_file,
        csrf_token=actual_token,
    )
    ctx.obj["debug"] = debug
    ctx.obj["auth_file"] = auth_file
    ctx.obj["api_error_cls"] = OneSecAPIError

    if debug:
        print_info(f"Base URL: {actual_base_url}")
        print_info(f"Auth File: {auth_file}" if auth_file else "Auth: Token")


@cli.group()
@click.pass_context
def threat(ctx):
    """威胁行动查询。"""
    pass


@threat.command(name="search")
@click.option("--days", "-d", default=7, type=int, help="查询最近 N 天，默认 7")
@click.option("--page", "-p", default=1, type=int, help="页码，默认 1")
@click.option("--page-size", "-s", default=20, type=int, help="每页数量，默认 20")
@click.option("--keyword", "-k", default="", help="关键词搜索")
@click.option(
    "--json-output",
    "-j",
    "json_output",
    flag_value=True,
    default=True,
    help="输出原始 JSON（默认开启）",
)
@click.option("--table-output", "json_output", flag_value=False, default=True, help="切换为表格输出")
@click.pass_context
def search_threats(ctx, days, page, page_size, keyword, json_output):
    """搜索威胁行动。"""
    client = ctx.obj["client"]
    api_error_cls = ctx.obj.get("api_error_cls", Exception)
    try:
        result = client.search_threat_actions(days=days, page=page, page_size=page_size, keyword=keyword)
        if json_output:
            console.print(JSON.from_data(result))
            return

        if result.get("code") == 0:
            data = result.get("data", {})
            items = data.get("items", [])
            table = Table(title=f"[bold red]威胁行动（第 {page} 页）[/bold red]", box=box.ROUNDED)
            table.add_column("主机名", style="cyan")
            table.add_column("威胁名称", style="red")
            table.add_column("严重程度", style="yellow")
            table.add_column("最后检测", style="dim")

            severity_map = {1: "低危", 2: "中危", 3: "高危", 4: "严重"}
            for item in items[:page_size]:
                table.add_row(
                    str(item.get("host_name", "-"))[:24],
                    str(item.get("threat_name", "-"))[:40],
                    severity_map.get(item.get("threat_severity"), str(item.get("threat_severity", "-"))),
                    format_timestamp(item.get("last_signal_time", 0)),
                )
            console.print(table)
            console.print(f"[dim]共 {data.get('total', len(items))} 条记录[/dim]")
        else:
            print_error(f"搜索失败: {result.get('message', 'Unknown error')}")
    except api_error_cls as e:
        print_error(f"API 请求失败: {e}")


@threat.command(name="top")
@click.option("--days", "-d", default=7, type=int, help="查询最近 N 天，默认 7")
@click.option("--limit", "-l", default=10, type=int, help="返回数量，默认 10")
@click.option(
    "--json-output",
    "-j",
    "json_output",
    flag_value=True,
    default=True,
    help="输出原始 JSON（默认开启）",
)
@click.option("--table-output", "json_output", flag_value=False, default=True, help="切换为表格输出")
@click.pass_context
def get_threat_top(ctx, days, limit, json_output):
    """查看 TOP 威胁名称。"""
    client = ctx.obj["client"]
    api_error_cls = ctx.obj.get("api_error_cls", Exception)
    try:
        result = client.get_threat_name_top(days=days, limit=limit)
        if json_output:
            console.print(JSON.from_data(result))
            return

        if result.get("code") == 0:
            items = result.get("data", [])
            table = Table(title="[bold red]TOP 威胁[/bold red]", box=box.ROUNDED)
            table.add_column("排名", style="dim")
            table.add_column("威胁名称", style="cyan")
            table.add_column("数量", style="yellow")
            for i, item in enumerate(items[:limit], 1):
                if isinstance(item, dict):
                    table.add_row(str(i), str(item.get("name", "-"))[:40], str(item.get("count", "-")))
                else:
                    table.add_row(str(i), str(item)[:40], "-")
            console.print(table)
        else:
            print_error(f"获取失败: {result.get('message', 'Unknown error')}")
    except api_error_cls as e:
        print_error(f"API 请求失败: {e}")


@cli.group()
@click.pass_context
def log(ctx):
    """日志查询。"""
    pass


@log.command(name="search")
@click.argument("sql")
@click.option("--limit", "-l", default=10, type=int, help="显示前 N 条，默认 10")
@click.option("--hours", "-H", default=None, type=int, help="查询最近 N 小时，优先于 --days")
@click.option("--days", "-d", default=1, type=int, help="查询最近 N 天，默认 1")
@click.option(
    "--json-output",
    "-j",
    "json_output",
    flag_value=True,
    default=True,
    help="输出原始 JSON（默认开启）",
)
@click.option("--table-output", "json_output", flag_value=False, default=True, help="切换为表格输出")
@click.pass_context
def search_logs(ctx, sql, limit, hours, days, json_output):
    """按 SQL 搜索日志。"""
    client = ctx.obj["client"]
    api_error_cls = ctx.obj.get("api_error_cls", Exception)
    try:
        result = client.search_logs_by_sql(sql, days=days, hours=hours, page_size=max(limit, 50))
        if json_output:
            console.print(JSON.from_data(result))
            return

        if result.get("code") == 0:
            data = result.get("data", {})
            items = data.get("tbBaseLogList", []) if isinstance(data, dict) else []
            if not items:
                console.print("[dim]未找到匹配的日志[/dim]")
                return

            table = Table(title="[bold green]日志搜索结果[/bold green]", box=box.ROUNDED)
            table.add_column("时间", style="dim")
            table.add_column("主机", style="cyan")
            table.add_column("威胁", style="red")
            for item in items[:limit]:
                threat = item.get("threat", {}) if isinstance(item.get("threat"), dict) else {}
                table.add_row(
                    format_timestamp(item.get("event_time") or item.get("time")),
                    str(item.get("host_name", "-"))[:24],
                    str(threat.get("name") or item.get("event_name", "-"))[:40],
                )
            console.print(table)
            console.print(f"[dim]找到 {data.get('total', len(items))} 条记录[/dim]")
        else:
            print_error(f"搜索失败: {result.get('message', 'Unknown error')}")
    except api_error_cls as e:
        print_error(f"API 请求失败: {e}")


@log.command(name="types")
@click.option("--days", "-d", default=1, type=int, help="查询最近 N 天，默认 1")
@click.option(
    "--json-output",
    "-j",
    "json_output",
    flag_value=True,
    default=True,
    help="输出原始 JSON（默认开启）",
)
@click.option("--table-output", "json_output", flag_value=False, default=True, help="切换为表格输出")
@click.pass_context
def get_log_types(ctx, days, json_output):
    """查看日志类型统计。"""
    client = ctx.obj["client"]
    api_error_cls = ctx.obj.get("api_error_cls", Exception)
    try:
        result = client.get_log_type_count(days=days)
        if json_output:
            console.print(JSON.from_data(result))
            return

        if result.get("code") == 0:
            data = result.get("data", {})
            table = Table(title="[bold cyan]日志类型统计[/bold cyan]", box=box.ROUNDED)
            table.add_column("类型", style="cyan")
            table.add_column("数量", style="yellow", justify="right")
            for key, value in data.items():
                table.add_row(str(key), str(value))
            console.print(table)
        else:
            print_error(f"获取失败: {result.get('message', 'Unknown error')}")
    except api_error_cls as e:
        print_error(f"API 请求失败: {e}")


@log.command(name="trend")
@click.option("--days", "-d", default=1, type=int, help="查询最近 N 天，默认 1")
@click.option(
    "--json-output",
    "-j",
    "json_output",
    flag_value=True,
    default=True,
    help="输出原始 JSON（默认开启）",
)
@click.option("--table-output", "json_output", flag_value=False, default=True, help="切换为表格输出")
@click.pass_context
def get_log_trend(ctx, days, json_output):
    """查看日志趋势。"""
    client = ctx.obj["client"]
    api_error_cls = ctx.obj.get("api_error_cls", Exception)
    try:
        result = client.get_log_trend(days=days)
        if json_output:
            console.print(JSON.from_data(result))
            return

        if result.get("code") == 0:
            data = result.get("data", {})
            trends = data.get("trend", []) if isinstance(data, dict) else []
            table = Table(title="[bold magenta]日志趋势[/bold magenta]", box=box.ROUNDED)
            table.add_column("时间", style="cyan")
            table.add_column("数量", style="green", justify="right")
            for item in trends[:20]:
                table.add_row(str(item.get("time", "-")), str(item.get("count", 0)))
            console.print(table)
        else:
            print_error(f"获取失败: {result.get('message', 'Unknown error')}")
    except api_error_cls as e:
        print_error(f"API 请求失败: {e}")


if __name__ == "__main__":
    cli()
