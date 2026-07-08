#!/usr/bin/env python3
"""Minimal SkyEye CLI for alarm and log search."""

from __future__ import annotations

import json
import sys
from argparse import Namespace
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import click
from rich import box
from rich.console import Console
from rich.table import Table

from api_client import SkyeyeAPIError, SkyeyeClient
from config import AUTH_STATE_FILE, BASE_URL, TOKEN

console = Console()


def print_error(message: str) -> None:
    console.print(f"[bold red]✗ {message}[/bold red]")


def print_success(message: str) -> None:
    console.print(f"[bold green]✓ {message}[/bold green]")


def print_info(message: str) -> None:
    console.print(f"[cyan]ℹ {message}[/cyan]")


def format_timestamp(value: Any) -> str:
    if value in (None, "", 0):
        return "-"
    try:
        ts = int(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(ts) >= 10_000_000_000:
        ts = ts // 1000
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def resolve_auth_file() -> Path | None:
    return AUTH_STATE_FILE if AUTH_STATE_FILE.exists() else None


def ensure_browser_auth_state(
    *,
    base_url: str,
    login_path: str | None,
    username: str | None,
    password: str | None,
    captcha_code: str | None,
    force_refresh: bool,
    debug: bool,
) -> tuple[bool, str]:
    from skyeye_auth import ensure_auth_state, _runtime_config

    args = Namespace(
        base_url=base_url,
        username=username,
        password=password,
        auth_state=str(AUTH_STATE_FILE),
        login_path=login_path or "",
        captcha_path="",
        captcha_code=captcha_code or "",
        timeout=25,
        max_captcha_retry=5,
        no_ocr=False,
        save_credentials=True,
    )
    cfg = _runtime_config(args)
    result = ensure_auth_state(cfg, captcha_code=captcha_code or "", force_refresh=force_refresh)
    if debug:
        print_info(json.dumps(result, ensure_ascii=False))
    return bool(result.get("success") or result.get("valid")), cfg.base_url


def parse_pairs(pairs: Iterable[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise click.BadParameter(f"参数必须使用 key=value 形式: {pair}")
        key, value = pair.split("=", 1)
        key = key.strip()
        if not key:
            raise click.BadParameter(f"参数缺少 key: {pair}")
        result[key] = value.strip()
    return result


def parse_graph_conf(value: str) -> Any:
    if not value:
        return {}
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise click.BadParameter(f"--graph-conf 不是合法 JSON: {exc}") from exc


def result_ok(result: dict[str, Any]) -> bool:
    if not isinstance(result, dict):
        return False
    if result.get("code") in (0, None):
        return "error" not in result
    return "data" in result and "error" not in result


def get_data(result: dict[str, Any]) -> Any:
    return result.get("data", result)


def get_items(data: Any, *keys: str) -> list[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


def get_total(data: Any, items: list[Any]) -> int:
    if isinstance(data, dict):
        for key in ("total", "count", "hits_total"):
            value = data.get(key)
            if isinstance(value, int):
                return value
    return len(items)


def emit_json(result: Any) -> None:
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def should_use_table(ctx: click.Context) -> bool:
    return bool(ctx.obj.get("table_output"))


def handle_api_result(ctx: click.Context, result: dict[str, Any], success_message: str = "获取成功") -> Any:
    if not should_use_table(ctx):
        emit_json(result)
        return get_data(result)
    if not result_ok(result):
        raise click.ClickException(result.get("message") or result.get("error") or "API 返回失败")
    print_success(success_message)
    return get_data(result)


def common_time_options(func):
    func = click.option("--end-time", help="结束时间，支持毫秒时间戳或 YYYY-MM-DD[ HH:MM:SS]")(func)
    func = click.option("--start-time", help="开始时间，支持毫秒时间戳或 YYYY-MM-DD[ HH:MM:SS]")(func)
    func = click.option("--hours", type=int, help="按最近 N 小时查询，优先级高于 --days")(func)
    func = click.option("--days", default=1, show_default=True, type=int, help="按最近 N 天查询")(func)
    return func


def common_page_options(func):
    func = click.option("--page-size", default=20, show_default=True, type=int, help="每页数量")(func)
    func = click.option("--page", default=1, show_default=True, type=int, help="页码")(func)
    return func


def common_log_options(func):
    func = click.option("--param", "extra_param", multiple=True, help="额外查询参数，格式 key=value")(func)
    func = click.option("--sess-key-ids", default="", help="会话键 ID")(func)
    func = click.option("--task-id", default="", help="任务 ID")(func)
    func = click.option("--asset-group-ids", default="", help="资产分组 ID，多个值用逗号分隔")(func)
    func = click.option("--graph-conf", default="{}", show_default=True, help="图形配置 JSON")(func)
    func = click.option("--key-field", "key_fields", multiple=True, help="重点字段，可重复传入")(func)
    func = click.option("--mode", default="advance_model", show_default=True, hidden=True, help="搜索模式（仅支持 advance_model）")(func)
    func = click.option("--interval", default="20s", show_default=True, help="时间桶间隔")(func)
    func = click.option("--category", default="event", show_default=True, help="日志分类")(func)
    func = click.option("--index", default="alarm_collection", show_default=True, help="索引名称")(func)
    func = click.option("--branch-id", default="", help="级联单位 branch_id")(func)
    func = common_time_options(func)
    func = common_page_options(func)
    return func


@click.group()
@click.option("--token", "-t", help="CSRF Token，或使用 SKYEYE_CSRF_TOKEN")
@click.option("--base-url", "-u", help="平台地址，或使用 SKYEYE_BASE_URL")
@click.option("--login-path", help="Login path for browser/CDP login")
@click.option("--username", help="Username for browser/CDP login")
@click.option("--password", help="Password for browser/CDP login")
@click.option("--auto-login", is_flag=True, help="Use saved credentials to refresh browser auth-state")
@click.option("--refresh-auth", is_flag=True, help="Force browser/CDP login before running the command")
@click.option("--captcha-code", help="Captcha code; OCR is used when omitted")
@click.option("--debug", "-d", is_flag=True, help="开启调试输出")
@click.option("--table", "table_output", is_flag=True, help="输出格式化表格（默认为 JSON）")
@click.pass_context
def cli(
    ctx: click.Context,
    token: str | None,
    base_url: str | None,
    login_path: str | None,
    username: str | None,
    password: str | None,
    auto_login: bool,
    refresh_auth: bool,
    captcha_code: str | None,
    debug: bool,
    table_output: bool,
) -> None:
    """SkyEye CLI."""
    ctx.ensure_object(dict)
    actual_base_url = base_url or BASE_URL
    actual_token = token or TOKEN

    wants_browser_login = bool(username or password or auto_login or refresh_auth)
    if wants_browser_login:
        try:
            ok, resolved_base_url = ensure_browser_auth_state(
                base_url=actual_base_url,
                login_path=login_path,
                username=username,
                password=password,
                captcha_code=captcha_code,
                force_refresh=refresh_auth,
                debug=debug,
            )
            actual_base_url = resolved_base_url or actual_base_url
            if not ok:
                print_error("SkyEye 自动登录失败，请改用浏览器手动登录后保存 auth-state。")
                sys.exit(1)
        except Exception as exc:
            print_error(f"SkyEye 自动登录失败: {exc}")
            sys.exit(1)

    auth_file = resolve_auth_file()

    if auth_file is None and not actual_token:
        print_error("未提供认证信息。请提供 auth-state.json、SKYEYE_CSRF_TOKEN，或使用 --auto-login/--username/--password。")
        sys.exit(1)
    if not actual_base_url:
        print_error("未提供平台地址。请设置 SKYEYE_BASE_URL 或使用 --base-url。")
        sys.exit(1)

    ctx.obj["client"] = SkyeyeClient(
        base_url=actual_base_url,
        auth_file=auth_file,
        csrf_token=actual_token,
    )
    ctx.obj["table_output"] = table_output

    if debug:
        print_info(f"Base URL: {actual_base_url}")
        print_info(f"Auth File: {auth_file.name}" if auth_file else "Auth: Token")


@cli.group()
def alarm() -> None:
    """告警检索接口。"""


@alarm.command(name="list")
@click.option("--filter", "filters", multiple=True, help="告警筛选条件，格式 key=value，可重复传入")
@click.option("--total-flag", default="1", show_default=True, help="是否返回总数")
@click.option("--is-alarm-list", default="1", show_default=True, help="是否按告警列表模式查询")
@click.option("--data-source", default="1", show_default=True, help="数据源")
@click.option("--order-by", default="access_time:desc", show_default=True, help="排序字段")
@common_time_options
@common_page_options
@click.pass_context
def list_alarms(
    ctx: click.Context,
    page: int,
    page_size: int,
    days: int,
    hours: int | None,
    start_time: str | None,
    end_time: str | None,
    order_by: str,
    data_source: str,
    is_alarm_list: str,
    total_flag: str,
    filters: tuple[str, ...],
) -> None:
    """获取告警列表。"""
    client = ctx.obj["client"]
    try:
        result = client.get_alarm_list(
            page=page,
            page_size=page_size,
            days=days,
            hours=hours,
            start_time=start_time,
            end_time=end_time,
            order_by=order_by,
            data_source=data_source,
            is_alarm_list=is_alarm_list,
            total_flag=total_flag,
            **parse_pairs(filters),
        )
        data = handle_api_result(ctx, result)
        if not should_use_table(ctx):
            return

        items = get_items(data, "items", "list")
        table = Table(title=f"[bold red]告警列表 (第 {page} 页)[/bold red]", box=box.ROUNDED)
        table.add_column("时间", style="dim")
        table.add_column("威胁名称", style="yellow")
        table.add_column("告警类型", style="cyan")
        table.add_column("受害 IP", style="green")
        table.add_column("攻击 IP", style="magenta")
        table.add_column("级别", style="red")
        table.add_column("状态", style="blue")

        for item in items[:page_size]:
            if not isinstance(item, dict):
                continue
            table.add_row(
                format_timestamp(item.get("access_time") or item.get("event_time") or item.get("@timestamp") or item.get("time")),
                str(item.get("threat_name", item.get("alarm_name", "-")))[:32],
                str(item.get("threat_type", item.get("alarm_type", "-")))[:24],
                str(item.get("alarm_sip", item.get("host_ip", item.get("dip", "-"))))[:20],
                str(item.get("attack_sip", item.get("sip", "-")))[:20],
                str(item.get("hazard_level", item.get("level", "-")))[:10],
                str(item.get("status", item.get("dispose_status", "-")))[:12],
            )

        console.print(table if items else "[dim]暂无告警数据[/dim]")
        print_info(f"共 {get_total(data, items)} 条")
    except SkyeyeAPIError as exc:
        print_error(f"API 请求失败: {exc}")


@alarm.command(name="count")
@click.option("--filter", "filters", multiple=True, help="告警筛选条件，格式 key=value，可重复传入")
@click.option("--is-alarm-list", default="1", show_default=True, help="是否按告警列表模式查询")
@click.option("--data-source", default="1", show_default=True, help="数据源")
@common_time_options
@click.pass_context
def alarm_count(
    ctx: click.Context,
    days: int,
    hours: int | None,
    start_time: str | None,
    end_time: str | None,
    data_source: str,
    is_alarm_list: str,
    filters: tuple[str, ...],
) -> None:
    """获取告警统计。"""
    client = ctx.obj["client"]
    try:
        result = client.get_alarm_count(
            days=days,
            hours=hours,
            start_time=start_time,
            end_time=end_time,
            data_source=data_source,
            is_alarm_list=is_alarm_list,
            **parse_pairs(filters),
        )
        data = handle_api_result(ctx, result)
        if should_use_table(ctx):
            table = Table(title="[bold green]告警统计[/bold green]", box=box.ROUNDED)
            table.add_column("字段", style="cyan")
            table.add_column("值", style="yellow")
            if isinstance(data, dict):
                for key, value in data.items():
                    table.add_row(str(key), json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value))
            else:
                table.add_row("value", str(data))
            console.print(table)
    except SkyeyeAPIError as exc:
        print_error(f"API 请求失败: {exc}")


@cli.group()
def log() -> None:
    """日志检索与统计接口。"""


@log.command(name="search")
@click.argument("query", required=False, default="")
@common_log_options
@click.pass_context
def log_search(
    ctx: click.Context,
    query: str,
    page: int,
    page_size: int,
    days: int,
    hours: int | None,
    start_time: str | None,
    end_time: str | None,
    branch_id: str,
    index: str,
    category: str,
    interval: str,
    mode: str,
    key_fields: tuple[str, ...],
    graph_conf: str,
    asset_group_ids: str,
    task_id: str,
    sess_key_ids: str,
    extra_param: tuple[str, ...],
) -> None:
    """执行日志检索。"""
    client = ctx.obj["client"]
    try:
        result = client.search_log_analysis(
            keyword=query,
            page=page,
            page_size=page_size,
            days=days,
            hours=hours,
            start_time=start_time,
            end_time=end_time,
            branch_id=branch_id,
            index=index,
            category=category,
            interval=interval,
            mode=mode,
            key_fields=key_fields,
            graph_conf=parse_graph_conf(graph_conf),
            asset_group_ids=asset_group_ids,
            task_id=task_id,
            sess_key_ids=sess_key_ids,
            **parse_pairs(extra_param),
        )
        data = handle_api_result(ctx, result, success_message="搜索完成")
        if not should_use_table(ctx):
            return

        items = get_items(data, "hits", "tbBaseLogList", "items", "list")
        if not items:
            console.print("[dim]未找到匹配的日志[/dim]")
            return

        table = Table(title="[bold green]日志搜索结果[/bold green]", box=box.ROUNDED)
        table.add_column("时间", style="dim")
        table.add_column("日志类型", style="yellow")
        table.add_column("主机/IP", style="cyan")
        table.add_column("摘要", style="green")

        for item in items[:page_size]:
            if not isinstance(item, dict):
                continue
            host_or_ip = item.get("host_name") or item.get("asset_ip") or item.get("sip") or item.get("dip") or "-"
            summary = item.get("event_name") or item.get("event_type") or item.get("threat_name") or item.get("message") or item.get("uri") or "-"
            log_type = item.get("log_type") or item.get("type") or item.get("category") or "-"
            table.add_row(
                format_timestamp(item.get("@timestamp") or item.get("event_time") or item.get("time")),
                str(log_type)[:18],
                str(host_or_ip)[:24],
                str(summary)[:40],
            )

        console.print(table)
        print_info(f"共 {get_total(data, items)} 条")
    except SkyeyeAPIError as exc:
        print_error(f"API 请求失败: {exc}")


if __name__ == "__main__":
    cli()
