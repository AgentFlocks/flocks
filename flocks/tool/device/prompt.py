"""Build the "已接入安全设备" section injected into the Agent system prompt.

Gives the Agent a structured view of:
  机房 (Machine Room) → 设备 (Device) → 可用工具 (Available Tools)

so it knows which tool name corresponds to which physical device when
multiple instances of the same device type are connected.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from flocks.utils.log import Log

from .models import DeviceGroup, DeviceIntegration
from .store import list_devices, list_groups

log = Log.create(service="tool.device.prompt")


async def build_device_context_section() -> Optional[str]:
    """Return a Markdown block describing the device asset context.

    Returns None when no devices are registered, so the caller can skip injection.

    Tool descriptions are deduplicated globally: when multiple devices of the
    same type are connected, the per-tool description and action list appear
    only once in a shared "工具说明" section, keeping the prompt size O(tools)
    rather than O(tools × devices).
    """
    try:
        groups = await list_groups()
        devices = await list_devices()
    except Exception as exc:
        log.warn("tool.device.prompt.load_failed", {"error": str(exc)})
        return None

    if not devices:
        return None

    tool_map = _build_tool_map()
    group_map: Dict[str, DeviceGroup] = {g.id: g for g in groups}

    by_group: Dict[str, List[DeviceIntegration]] = {}
    for device in devices:
        by_group.setdefault(device.group_id, []).append(device)

    # Tool capabilities are grouped by storage_key/tool-set rather than by
    # device instance so multiple devices of the same product only contribute
    # one shared capability description block.
    all_tools_by_provider: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for device in devices:
        if not device.enabled:
            continue
        for meta in tool_map.get(device.storage_key, []):
            provider = meta["provider"]
            name = meta["name"]
            all_tools_by_provider.setdefault(provider, {}).setdefault(name, meta)

    lines: List[str] = [
        "<DeviceAssetContext>",
        "## 已接入安全设备",
        "",
        "以下是当前机房中已接入的安全设备及其工具集映射。",
        "调用工具时必须通过 `device_id` 参数指定目标设备，工具将自动使用该设备的凭据，例如：",
        '`tdp_event_list(action="list", device_id="<device_id>")`',
        "",
    ]

    # --- Section 1: tool-set descriptions (written once per provider/tool set) ---
    if all_tools_by_provider:
        lines.append("### 工具集")
        lines.append("")
        all_tool_names: List[str] = [
            name
            for tools in all_tools_by_provider.values()
            for name in tools
        ]
        has_name_collision = len(all_tool_names) != len(set(all_tool_names))

        for provider, tools_by_name in sorted(all_tools_by_provider.items()):
            sample = next(iter(tools_by_name.values()))
            vendor_label = f" | 厂商: `{sample['vendor']}`" if sample.get("vendor") else ""
            lines.append(f"- `tool_set_id={provider}`{vendor_label}")
            lines.append("  工具能力:")
            for tool_name, meta in sorted(tools_by_name.items()):
                desc = (meta.get("description_cn") or meta.get("description") or "").strip()
                first_sentence = desc.split("。")[0] if desc else ""
                actions = meta.get("actions", [])
                action_hint = f"  action 可选: {' | '.join(actions)}" if actions else ""
                prefix = "    " if has_name_collision else "  "
                if first_sentence:
                    lines.append(f"{prefix}- `{tool_name}`: {first_sentence}。{action_hint}")
                else:
                    lines.append(f"{prefix}- `{tool_name}`{action_hint}")
            lines.append("")

    # --- Section 2: device list (references tool_set_id only) ---
    lines.append("### 设备列表")
    lines.append("")
    for group_id, group_devices in by_group.items():
        group = group_map.get(group_id)
        group_name = group.name if group else group_id
        lines.append(f"**机房: {group_name}**")
        lines.append("")

        for d in group_devices:
            status = "✅ 已启用" if d.enabled else "❌ 已禁用"
            tools = tool_map.get(d.storage_key, [])
            vendor = tools[0].get("vendor") if tools else ""
            vendor_label = f" | 厂商: `{vendor}`" if vendor else ""

            lines.append(f"- **{d.name}** | device_id: `{d.id}`{vendor_label} | {status}")
            if d.enabled and tools:
                lines.append(f"  tool_set_id: `{d.storage_key}`")
                lines.append(f"  调用方式: 附带 `device_id=\"{d.id}\"` 参数")
            elif not d.enabled:
                lines.append(f"  tool_set_id: `{d.storage_key}`")
                lines.append("  可用工具: (已禁用，不可调用)")
            else:
                lines.append(f"  tool_set_id: `{d.storage_key}`")
                lines.append("  可用工具: (未发现已注册工具)")
        lines.append("")

    lines.append("</DeviceAssetContext>")
    return "\n".join(lines)


def _build_tool_map() -> Dict[str, List[Dict[str, Any]]]:
    """Return {storage_key: [tool_meta_dict, ...]} from the live ToolRegistry.

    Only ``source == "device"`` tools are included; intelligence/cloud API
    tools (fofa, virustotal, …) live in ``tools/api/`` and are intentionally
    excluded from the device-asset context.

    Each dict contains:
      - name: tool name
      - provider: provider / storage_key this tool belongs to
      - description: English description
      - description_cn: Chinese description (may be empty)
      - actions: list of valid ``action`` enum values (empty when not applicable)
      - vendor: manufacturer key (e.g. 'threatbook', 'qianxin', 'sangfor')
    """
    result: Dict[str, List[Dict[str, Any]]] = {}
    try:
        from flocks.tool.registry import ToolRegistry

        for tool in ToolRegistry.list_tools():
            if not tool.provider or not tool.enabled:
                continue
            # Only include device-type tools; intelligence/cloud API tools
            # (fofa, virustotal, …) live in tools/api/ and are source='api'.
            if tool.source != "device":
                continue
            actions: List[str] = []
            for param in tool.parameters:
                if param.name == "action" and param.enum:
                    actions = [str(v) for v in param.enum]
                    break
            meta: Dict[str, Any] = {
                "name": tool.name,
                "provider": tool.provider,
                "description": tool.description or "",
                "description_cn": tool.description_cn or "",
                "actions": actions,
                "vendor": tool.vendor or "",
            }
            result.setdefault(tool.provider, []).append(meta)
    except Exception as exc:
        log.warn("tool.device.prompt.tool_map_failed", {"error": str(exc)})
    return result
