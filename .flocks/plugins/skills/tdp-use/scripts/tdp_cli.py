#!/usr/bin/env python3
"""
TDP CLI - 微步TDP命令行工具

支持2个接口:
  monitor threats  - 威胁列表 (api/web/hw/monitor/threat/list)
  logs search      - 日志SQL搜索 (api/web/log/searchBySql)

Usage:
    tdp_cli.py monitor threats
    tdp_cli.py logs search "threat.severity >= 2"
"""

import os
import sys
from datetime import datetime

import click
from api_client import ThreatBookAPIError, ThreatBookClient
from config import BASE_URL, COOKIE_FILE, TOKEN
from rich import box
from rich.console import Console
from rich.json import JSON
from rich.table import Table

console = Console()


def print_error(message: str):
    console.print(f"[bold red]✗ {message}[/bold red]")


def print_success(message: str):
    console.print(f"[bold green]✓ {message}[/bold green]")


def print_info(message: str):
    console.print(f"[cyan]ℹ {message}[/cyan]")


def format_timestamp(ts) -> str:
    if not ts:
        return "-"
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)


@click.group()
@click.option("--token", "-t", help="API Token (or set THREATBOOK_TOKEN env)")
@click.option("--base-url", "-u", help="Base URL (or set THREATBOOK_BASE_URL env)")
@click.option("--debug", "-d", is_flag=True, help="Enable debug mode")
@click.pass_context
def cli(ctx, token, base_url, debug):
    """TDP CLI

    支持接口:
      monitor  - 威胁监控 (threat list)
      logs     - 日志查询 (search by SQL)

    认证方式（任选其一）:
    1. Cookie 认证（推荐）: 自动从 tdp_cookie.json 加载
    2. Token 认证: 使用 --token 或 THREATBOOK_TOKEN 环境变量

    示例:
        tdp_cli.py monitor threats
        tdp_cli.py monitor threats --days 3 --sql "threat.severity >= 2"
        tdp_cli.py logs search
        tdp_cli.py logs search "machine = '192.168.1.100'"
    """
    ctx.ensure_object(dict)

    actual_base_url = base_url or BASE_URL or os.getenv("THREATBOOK_BASE_URL")

    use_cookie = COOKIE_FILE.exists()
    actual_token = token or TOKEN or os.getenv("THREATBOOK_TOKEN")

    if not use_cookie and not actual_token:
        print_error("未提供认证信息。请设置 THREATBOOK_TOKEN 或确保 tdp_cookie.json 存在。")
        sys.exit(1)

    ctx.obj["client"] = ThreatBookClient(
        token=actual_token if not use_cookie else None,
        base_url=actual_base_url,
        cookie_file=COOKIE_FILE if use_cookie else None,
    )
    ctx.obj["debug"] = debug

    if debug:
        print_info(f"Base URL: {actual_base_url}")
        print_info("Auth: Cookie" if use_cookie else "Auth: Token")


# ==================== Monitor Commands ====================


@cli.group()
@click.pass_context
def monitor(ctx):
    """威胁监控"""
    pass


@monitor.command(name="threats")
@click.option("--page", "-p", default=1, help="页码")
@click.option("--page-size", "-n", default=20, help="每页条数")
@click.option("--limit", "-l", default=None, type=int, help="显示条数限制（等同于 --page-size，优先于 --page-size）")
@click.option("--days", "-d", default=1, help="查询最近N天（默认1天）")
@click.option("--hours", "-H", default=None, type=int, help="查询最近N小时（优先于--days）")
@click.option("--from", "time_from", default=None, help="开始时间（优先级最高）：时间戳 / 2026-03-10 / '2026-03-10 16:00'")
@click.option("--to", "time_to", default=None, help="结束时间（默认当前时间）：时间戳 / 2026-03-10 / '2026-03-10 17:00'")
@click.option("--sql", "-s", default="(threat.level IN ('attack')) AND threat.type NOT IN ('recon')", help="过滤SQL条件")
@click.option(
    "--json-output",
    "-j",
    "json_output",
    flag_value=True,
    default=True,
    help="输出原始JSON（默认开启）",
)
@click.option("--table-output", "json_output", flag_value=False, default=True, help="切换为表格输出")
@click.pass_context
def monitor_threats(ctx, page, page_size, limit, days, hours, time_from, time_to, sql, json_output):
    """查询威胁列表 (api/web/hw/monitor/threat/list)

    时间范围优先级：--from/--to > --hours > --days

    示例:
        tdp_cli.py monitor threats
        tdp_cli.py monitor threats --days 3
        tdp_cli.py monitor threats --hours 6 --sql "threat.severity >= 2"
        tdp_cli.py monitor threats --from "2026-03-10 09:00" --to "2026-03-10 18:00"
        tdp_cli.py monitor threats --from 1741536000 --to 1741622400
        tdp_cli.py monitor threats --limit 100
        tdp_cli.py monitor threats
        tdp_cli.py monitor threats --table-output
    """
    client = ctx.obj["client"]
    effective_page_size = limit if limit is not None else page_size
    try:
        result = client.get_threat_list(
            page=page,
            page_size=effective_page_size,
            days=days,
            hours=hours,
            time_from=time_from,
            time_to=time_to,
            sql=sql,
        )

        if json_output:
            console.print(JSON.from_data(result))
            return

        if result.get("response_code") == 0:
            data = result.get("data", {})
            items = data.get("items", [])

            table = Table(title="[bold red]威胁列表[/bold red]", box=box.ROUNDED)
            table.add_column("时间", style="dim", no_wrap=True)
            table.add_column("威胁名称", style="cyan", no_wrap=False)
            table.add_column("攻击者IP/IOC", style="yellow", no_wrap=False)
            table.add_column("源IP", style="green", no_wrap=True)
            table.add_column("目的IP", style="blue", no_wrap=True)
            table.add_column("方向", style="magenta")
            table.add_column("严重级别", style="red", justify="center")
            table.add_column("检出次数", style="white", justify="right")

            for item in items:
                table.add_row(
                    format_timestamp(item.get("time")),
                    str(item.get("threat", {}).get("name") or item.get("name", "-")),
                    str(item.get("attacker", "-")),
                    str(item.get("net", {}).get("src_ip", "-")),
                    str(item.get("net", {}).get("dest_ip", "-")),
                    str(item.get("direction", "-")),
                    str(item.get("threat", {}).get("severity") or item.get("severity", "-")),
                    str(item.get("alert_count", "-")),
                )

            console.print(table)
            total = data.get("total_num", len(items))
            console.print(f"[dim]第 {page} 页，每页 {page_size} 条，共 {total} 条记录[/dim]")
            print_success("成功获取威胁列表")
        else:
            print_error(f"获取失败: {result.get('verbose_msg', result.get('message', 'Unknown error'))}")

    except ThreatBookAPIError as e:
        print_error(f"API 请求失败: {e}")


# ==================== Logs Commands ====================


@cli.group()
@click.pass_context
def logs(ctx):
    """日志查询"""
    pass


@logs.command(name="search")
@click.argument("sql_arg", default="", metavar="[SQL]")
@click.option("--sql", "-s", default=None, help="过滤SQL条件（与位置参数等效，选项优先）")
@click.option("--days", "-d", default=1, help="查询最近N天（默认1天）")
@click.option("--hours", "-H", default=None, type=int, help="查询最近N小时（优先于--days）")
@click.option("--from", "time_from", default=None, help="开始时间（优先级最高）：时间戳 / 2026-03-10 / '2026-03-10 16:00'")
@click.option("--to", "time_to", default=None, help="结束时间（默认当前时间）：时间戳 / 2026-03-10 / '2026-03-10 17:00'")
@click.option("--limit", "-l", default=20, help="显示条数限制（默认20条）")
@click.option(
    "--net-data-type",
    "-t",
    multiple=True,
    default=["attack", "risk", "action"],
    help="流量类型，可多次指定 (默认: attack risk action)",
)
@click.option("--full", "-f", is_flag=True, help="返回全部字段（不限制 columns，让后端决定）")
@click.option("--columns", "-c", default=None, help="自定义返回字段，逗号分隔，如 'threat.name,net.http.url,threat.msg'")
@click.option(
    "--json-output",
    "-j",
    "json_output",
    flag_value=True,
    default=True,
    help="输出原始JSON（默认开启）",
)
@click.option("--table-output", "json_output", flag_value=False, default=True, help="切换为表格输出")
@click.pass_context
def logs_search(ctx, sql_arg, sql, days, hours, time_from, time_to, limit, net_data_type, full, columns, json_output):
    """使用SQL搜索日志 (api/web/log/searchBySql)

    时间范围优先级：--from/--to > --hours > --days
    SQL条件：--sql 选项优先于位置参数
    字段模式：--columns 优先 > --full（全部字段）> 默认（页面展示字段）

    SQL 示例:
        (空)                           - 查询所有日志
        threat.level = 'attack'
        threat.severity >= 2 AND threat.result = 'success'
        machine = '192.168.1.100'
        net.dest_port = 443

    示例:
        tdp_cli.py logs search
        tdp_cli.py logs search "threat.level = 'attack'"
        tdp_cli.py logs search --sql "threat.level = 'attack'"
        tdp_cli.py logs search --sql "machine = '10.0.0.1'" --hours 6
        tdp_cli.py logs search --sql "threat.id='xxx'" --full
        tdp_cli.py logs search --columns "threat.name,net.http.url,threat.msg,net.http.reqs_body"
        tdp_cli.py logs search --limit 100
        tdp_cli.py logs search --limit 100 --table-output
    """
    effective_sql = sql if sql is not None else sql_arg

    # 解析 --columns 字符串为 columns 列表
    custom_columns = None
    if columns:
        fields = [f.strip() for f in columns.split(",") if f.strip()]
        custom_columns = [{"label": f, "value": f} for f in fields]

    client = ctx.obj["client"]
    try:
        result = client.search_logs_by_sql(
            sql=effective_sql,
            days=days,
            hours=hours,
            time_from=time_from,
            time_to=time_to,
            net_data_type=list(net_data_type),
            full=full,
            columns=custom_columns,
        )

        if json_output:
            console.print(JSON.from_data(result))
            return

        if result.get("response_code") == 0:
            data = result.get("data", {})
            items = data.get("data", []) if isinstance(data, dict) else []

            if not items:
                console.print("[dim]未找到匹配的日志[/dim]")
                print_success("搜索完成")
                return

            table = Table(title="[bold green]日志搜索结果[/bold green]", box=box.ROUNDED)
            table.add_column("时间", style="dim", no_wrap=True)
            table.add_column("threat.id", style="white", no_wrap=True)
            table.add_column("类型", style="cyan", no_wrap=True)
            table.add_column("源IP", style="green", no_wrap=True)
            table.add_column("源端口", style="green", justify="right", no_wrap=True)
            table.add_column("目的IP", style="yellow", no_wrap=True)
            table.add_column("目的端口", style="yellow", justify="right", no_wrap=True)
            table.add_column("协议", style="blue", no_wrap=True)
            table.add_column("威胁名称", style="red", no_wrap=False)
            table.add_column("URL/域名", style="magenta", no_wrap=False)

            display_items = items[:limit]
            for item in display_items:
                threat = item.get("threat", {}) if isinstance(item.get("threat"), dict) else {}
                net = item.get("net", {}) if isinstance(item.get("net"), dict) else {}
                table.add_row(
                    format_timestamp(item.get("time")),
                    str(threat.get("id", "-")),
                    str(threat.get("level", "-")),
                    str(net.get("src_ip", "-")),
                    str(net.get("src_port", "-")),
                    str(net.get("dest_ip", "-")),
                    str(net.get("dest_port", "-")),
                    str(net.get("type") or net.get("protocol_version", "-")),
                    str(threat.get("name", "-")),
                    str(item.get("data", "-")),
                )

            console.print(table)
            total = len(items)
            if total > limit:
                console.print(f"[dim]显示前 {len(display_items)} 条，共 {total} 条记录[/dim]")
            else:
                console.print(f"[dim]共 {total} 条记录[/dim]")
            print_success("搜索完成")
        else:
            print_error(f"搜索失败: {result.get('verbose_msg', result.get('message', 'Unknown error'))}")

    except ThreatBookAPIError as e:
        print_error(f"API 请求失败: {e}")


if __name__ == "__main__":
    cli()
