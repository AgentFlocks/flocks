#!/usr/bin/env python3
"""Minimal SkyEye Sensor CLI for alarm list/count."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import click
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from api_client import SkyeyeSensorAPIError, SkyeyeSensorClient
from config import AUTH_STATE_FILE, BASE_URL, COOKIE_FILE, CSRF_TOKEN

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
    if AUTH_STATE_FILE.exists():
        return AUTH_STATE_FILE
    if COOKIE_FILE.exists():
        return COOKIE_FILE
    return None


def build_alarm_filters(
    hazard_level: str | None = None,
    threat_type: str | None = None,
    sip: str | None = None,
    dip: str | None = None,
    host_state: str | None = None,
    user_label: str | None = None,
    attack_result: str | None = None,
    status: str | None = None,
    alarm_sip: str | None = None,
    attack_sip: str | None = None,
    attack_type: str | None = None,
    ioc: str | None = None,
    threat_name: str | None = None,
    attack_stage: str | None = None,
    proto: str | None = None,
    x_forwarded_for: str | None = None,
    attack_dimension: str | None = None,
    is_web_attack: str | None = None,
    host: str | None = None,
    status_http: str | None = None,
    attck_org: str | None = None,
    attck: str | None = None,
    uri: str | None = None,
    alert_rule: str | None = None,
    is_read: str | None = None,
    sport: str | None = None,
    dport: str | None = None,
    src_mac: str | None = None,
    dst_mac: str | None = None,
    vlan_id: str | None = None,
    vxlan_id: str | None = None,
    gre_key: str | None = None,
    marks: str | None = None,
    ip_labels: str | None = None,
    start_update_time: str | None = None,
    end_update_time: str | None = None,
    alarm_source: str | None = None,
    pcap_filename: str | None = None,
) -> dict[str, str | None]:
    return {
        "hazard_level": hazard_level,
        "threat_type": threat_type,
        "sip": sip,
        "dip": dip,
        "host_state": host_state,
        "user_label": user_label,
        "attack_result": attack_result,
        "status": status,
        "alarm_sip": alarm_sip,
        "attack_sip": attack_sip,
        "attack_type": attack_type,
        "ioc": ioc,
        "threat_name": threat_name,
        "attack_stage": attack_stage,
        "proto": proto,
        "x_forwarded_for": x_forwarded_for,
        "attack_dimension": attack_dimension,
        "is_web_attack": is_web_attack,
        "host": host,
        "status_http": status_http,
        "attck_org": attck_org,
        "attck": attck,
        "uri": uri,
        "alert_rule": alert_rule,
        "is_read": is_read,
        "sport": sport,
        "dport": dport,
        "src_mac": src_mac,
        "dst_mac": dst_mac,
        "vlan_id": vlan_id,
        "vxlan_id": vxlan_id,
        "gre_key": gre_key,
        "marks": marks,
        "ip_labels": ip_labels,
        "start_update_time": start_update_time,
        "end_update_time": end_update_time,
        "alarm_source": alarm_source,
        "pcap_filename": pcap_filename,
    }


def get_alarm_items(result: dict) -> list | dict:
    return result.get("items", [])


def pick_first(item: dict, *keys: str, default: str = "-") -> str:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return str(value)
    return default


@click.group()
@click.option("--token", "-t", help="CSRF Token")
@click.option("--base-url", "-u", help="Base URL")
@click.option("--debug", "-d", is_flag=True, help="Debug mode")
@click.pass_context
def cli(ctx: click.Context, token: str | None, base_url: str | None, debug: bool) -> None:
    """SkyEye Sensor CLI."""
    ctx.ensure_object(dict)
    actual_base_url = base_url or BASE_URL
    auth_file = resolve_auth_file()
    actual_token = token or CSRF_TOKEN

    if auth_file is None and not actual_token:
        print_error("未提供认证信息。请提供 auth-state.json、cookie.json 或 --token")
        sys.exit(1)
    if not actual_base_url:
        print_error("未提供平台地址。请设置 SKYEYE_SENSOR_BASE_URL 或使用 --base-url。")
        sys.exit(1)

    ctx.obj["client"] = SkyeyeSensorClient(
        base_url=actual_base_url,
        auth_file=auth_file,
        csrf_token=actual_token,
    )
    if debug:
        print_info(f"Base URL: {actual_base_url}")
        if auth_file:
            print_info(f"Auth File: {auth_file.name}")


@cli.group()
def alarm() -> None:
    """告警查询。"""


def _common_alarm_options(f: Any) -> Any:
    """共享的告警过滤参数装饰器。"""
    options = [
        click.option("--days", "-d", default=7, show_default=True, help="查询最近 N 天"),
        click.option("--hours", "-H", default=None, type=int, help="查询最近 N 小时，优先于 --days"),
        click.option("--hazard-level", "-l", default=None, help="威胁级别，如 3,2,1,0（3=严重 2=高危 1=中危 0=低危）"),
        click.option("--threat-type", default=None, help="威胁类型 ID，多个值逗号分隔"),
        click.option("--host-state", default=None, help="主机状态，多个值逗号分隔"),
        click.option("--user-label", default=None, help="是否仅看已标记，常用值 1"),
        click.option("--attack-result", default=None, help="攻击结果，如 攻击成功、攻击失败"),
        click.option("--status", default=None, help="处理状态，如 0=未处置"),
        click.option("--sip", default=None, help="源 IP（流量层）"),
        click.option("--dip", default=None, help="目的 IP（流量层）"),
        click.option("--alarm-sip", default=None, help="受害 IP"),
        click.option("--attack-sip", default=None, help="攻击 IP"),
        click.option("--attack-type", default=None, help="告警类型，如 代码执行、webshell上传"),
        click.option("--ioc", default=None, help="IOC / 规则 ID"),
        click.option("--threat-name", default=None, help="威胁名称，模糊匹配"),
        click.option("--attack-stage", default=None, help="攻击阶段"),
        click.option("--proto", default=None, help="协议，如 http、tcp"),
        click.option("--xff", default=None, help="XFF 代理 IP"),
        click.option("--attack-dimension", default=None, help="攻击维度"),
        click.option("--is-web-attack", default=None, help="是否 WEB 攻击，0=否 1=是"),
        click.option("--host", default=None, help="域名 / Host"),
        click.option("--status-http", default=None, help="HTTP 状态码"),
        click.option("--attck-org", default=None, help="ATT&CK 攻击组织"),
        click.option("--attck", default=None, help="ATT&CK 技战术"),
        click.option("--uri", default=None, help="URI"),
        click.option("--alert-rule", default=None, help="告警规则"),
        click.option("--is-read", default=None, help="是否已读，0=未读 1=已读"),
        click.option("--sport", default=None, help="源端口"),
        click.option("--dport", default=None, help="目的端口"),
        click.option("--src-mac", default=None, help="源 MAC 地址"),
        click.option("--dst-mac", default=None, help="目的 MAC 地址"),
        click.option("--vlan-id", default=None, help="VLAN ID"),
        click.option("--vxlan-id", default=None, help="VXLAN ID"),
        click.option("--gre-key", default=None, help="GRE KEY"),
        click.option("--marks", default=None, help="告警标签"),
        click.option("--ip-labels", default=None, help="IP 资产标签"),
        click.option("--start-update-time", default=None, help="规则更新时间起（毫秒时间戳）"),
        click.option("--end-update-time", default=None, help="规则更新时间止（毫秒时间戳）"),
        click.option("--alarm-source", default=None, help="告警来源，如 全部"),
        click.option("--pcap-filename", default=None, help="PCAP 文件名"),
        click.option("--table", "output_table", is_flag=True, default=False, help="输出格式化表格（默认为 JSON）"),
    ]
    for option in reversed(options):
        f = option(f)
    return f


def _extract_filters(kwargs: dict) -> dict:
    """从 click 参数字典中提取过滤字段，处理 xff -> x_forwarded_for 等命名映射。"""
    return build_alarm_filters(
        hazard_level=kwargs.get("hazard_level"),
        threat_type=kwargs.get("threat_type"),
        sip=kwargs.get("sip"),
        dip=kwargs.get("dip"),
        host_state=kwargs.get("host_state"),
        user_label=kwargs.get("user_label"),
        attack_result=kwargs.get("attack_result"),
        status=kwargs.get("status"),
        alarm_sip=kwargs.get("alarm_sip"),
        attack_sip=kwargs.get("attack_sip"),
        attack_type=kwargs.get("attack_type"),
        ioc=kwargs.get("ioc"),
        threat_name=kwargs.get("threat_name"),
        attack_stage=kwargs.get("attack_stage"),
        proto=kwargs.get("proto"),
        x_forwarded_for=kwargs.get("xff"),
        attack_dimension=kwargs.get("attack_dimension"),
        is_web_attack=kwargs.get("is_web_attack"),
        host=kwargs.get("host"),
        status_http=kwargs.get("status_http"),
        attck_org=kwargs.get("attck_org"),
        attck=kwargs.get("attck"),
        uri=kwargs.get("uri"),
        alert_rule=kwargs.get("alert_rule"),
        is_read=kwargs.get("is_read"),
        sport=kwargs.get("sport"),
        dport=kwargs.get("dport"),
        src_mac=kwargs.get("src_mac"),
        dst_mac=kwargs.get("dst_mac"),
        vlan_id=kwargs.get("vlan_id"),
        vxlan_id=kwargs.get("vxlan_id"),
        gre_key=kwargs.get("gre_key"),
        marks=kwargs.get("marks"),
        ip_labels=kwargs.get("ip_labels"),
        start_update_time=kwargs.get("start_update_time"),
        end_update_time=kwargs.get("end_update_time"),
        alarm_source=kwargs.get("alarm_source"),
        pcap_filename=kwargs.get("pcap_filename"),
    )


@alarm.command(name="count")
@_common_alarm_options
@click.pass_context
def get_alarm_count(ctx: click.Context, **kwargs: Any) -> None:
    """获取告警统计。"""
    client = ctx.obj["client"]
    output_table = kwargs.get("output_table", False)
    try:
        result = client.get_alarm_count_filtered(
            days=kwargs["days"],
            hours=kwargs.get("hours"),
            **_extract_filters(kwargs),
        )
        if not output_table:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        if result.get("status") == 200:
            items = get_alarm_items(result)
            if isinstance(items, list):
                table = Table(title="[bold red]告警趋势[/bold red]", box=box.ROUNDED)
                table.add_column("时间", style="cyan")
                table.add_column("数量", style="yellow", justify="right")
                for item in items:
                    if isinstance(item, dict):
                        table.add_row(format_timestamp(item.get("time")), str(item.get("value", 0)))
                console.print(table)
            else:
                console.print(Panel(str(items), title="[bold red]告警统计[/bold red]"))
            print_success("获取成功")
            return
        print_error(f"获取失败: {result.get('message', 'Unknown error')}")
    except SkyeyeSensorAPIError as exc:
        print_error(f"API 请求失败: {exc}")


@alarm.command(name="list")
@_common_alarm_options
@click.option("--page", "-p", default=1, show_default=True, help="页码")
@click.option("--page-size", "-n", default=10, show_default=True, help="每页条数")
@click.option("--order-by", default="access_time:desc", show_default=True, help="排序字段")
@click.option("--accurate/--fuzzy", default=False, help="是否开启精确匹配")
@click.pass_context
def get_alarm_list(
    ctx: click.Context,
    page: int,
    page_size: int,
    order_by: str,
    accurate: bool,
    **kwargs: Any,
) -> None:
    """分页获取告警列表。"""
    client = ctx.obj["client"]
    output_table = kwargs.get("output_table", False)
    try:
        result = client.get_alarm_list(
            days=kwargs["days"],
            hours=kwargs.get("hours"),
            page=page,
            page_size=page_size,
            order_by=order_by,
            is_accurate=1 if accurate else 0,
            **_extract_filters(kwargs),
        )
        if not output_table:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        if result.get("status") == 200:
            items = get_alarm_items(result)
            if not items:
                console.print("[dim]未找到匹配的告警[/dim]")
                print_success("查询完成")
                return

            table = Table(title="[bold red]告警列表[/bold red]", box=box.ROUNDED)
            table.add_column("时间", style="dim", no_wrap=True)
            table.add_column("危害", style="yellow")
            table.add_column("名称", style="red", max_width=28)
            table.add_column("源", style="cyan", max_width=18)
            table.add_column("目的", style="green", max_width=18)
            table.add_column("状态", style="magenta")

            for item in items:
                if not isinstance(item, dict):
                    continue
                table.add_row(
                    format_timestamp(int(pick_first(item, "access_time", "time", "create_time", "start_time", default="0"))),
                    pick_first(item, "hazard_level", "level", "risk_level"),
                    pick_first(item, "threat_name", "name", "title", "rule_name", "alarm_name")[:28],
                    pick_first(item, "sip", "src_ip", "source_ip", "alarm_sip")[:18],
                    pick_first(item, "dip", "dst_ip", "dest_ip", "target_ip")[:18],
                    pick_first(item, "status", "host_state", "dispose_status"),
                )

            console.print(table)
            console.print(f"[dim]第 {page} 页，每页 {page_size} 条，共 {result.get('total', len(items))} 条[/dim]")
            print_success("获取成功")
            return
        print_error(f"获取失败: {result.get('message', 'Unknown error')}")
    except SkyeyeSensorAPIError as exc:
        print_error(f"API 请求失败: {exc}")


if __name__ == "__main__":
    cli()
