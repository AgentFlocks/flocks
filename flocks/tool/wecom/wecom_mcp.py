"""
wecom_mcp — 企业微信 MCP Tool

通过企微 Bot WebSocket 长连接动态获取 MCP Server URL，
然后以 Streamable HTTP (JSON-RPC 2.0) 协议调用企业微信 MCP Server，
提供文档创建/读写、智能表格增删改查等能力。

工作原理（完全对齐 wecom-openclaw-plugin 的 TypeScript 实现）:
  1. 用 WeComChannel 的 WSClient 发送 "aibot_get_mcp_config" 命令，
     获取指定品类（如 "doc"）的 MCP Server URL。
  2. 对该 URL 执行 MCP initialize 握手，建立有状态/无状态会话。
  3. 发送 tools/list 或 tools/call JSON-RPC 请求。

支持的品类（category）及常用方法：
  doc:
    create_doc          — 创建文档(doc_type=3)或智能表格(doc_type=10)
    get_doc_content     — 异步导出文档内容（Markdown，需轮询 task_id）
    edit_doc_content    — 以 Markdown 覆写文档正文
    smartsheet_get_sheet      — 查询子表列表
    smartsheet_add_sheet      — 新增子表
    smartsheet_update_sheet   — 重命名子表
    smartsheet_delete_sheet   — 删除子表
    smartsheet_get_fields     — 查询字段/列
    smartsheet_add_fields     — 新增字段/列
    smartsheet_update_fields  — 更新字段名（不可改类型）
    smartsheet_delete_fields  — 删除字段/列
    smartsheet_get_records    — 查询记录/行
    smartsheet_add_records    — 新增记录（≤500 行/次）
    smartsheet_update_records — 更新记录（需提供 record_id）
    smartsheet_delete_records — 删除记录（不可逆）
  contact:
    getContact / get_userlist 等
"""

from __future__ import annotations

import asyncio
import json as _json
from typing import Any, Optional

import httpx

from flocks.tool.registry import (
    ParameterType,
    ToolCategory,
    ToolContext,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)
from flocks.utils.log import Log

log = Log.create(service="tool.wecom_mcp")

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

_MCP_GET_CONFIG_CMD = "aibot_get_mcp_config"
_MCP_CONFIG_TIMEOUT = 15.0          # 获取 MCP URL 超时（秒）
_HTTP_TIMEOUT = 30.0                 # 普通 HTTP 请求超时（秒）
_MCP_PROTOCOL_VERSION = "2025-03-26"


# ---------------------------------------------------------------------------
# MCP 会话管理
# ---------------------------------------------------------------------------

class _McpSession:
    """单个 category 的 MCP Streamable HTTP 会话。"""
    def __init__(self) -> None:
        self.url: Optional[str] = None
        self.session_id: Optional[str] = None
        self.initialized: bool = False
        self.stateless: bool = False


# category → session
_sessions: dict[str, _McpSession] = {}
# category → MCP URL（缓存）
_url_cache: dict[str, str] = {}
# 防止并发重复 initialize
_init_locks: dict[str, asyncio.Lock] = {}


def _get_init_lock(category: str) -> asyncio.Lock:
    if category not in _init_locks:
        _init_locks[category] = asyncio.Lock()
    return _init_locks[category]


def clear_category_cache(category: str) -> None:
    """清理指定品类的所有缓存（配置 + 会话），下次调用将重新握手。"""
    _sessions.pop(category, None)
    _url_cache.pop(category, None)
    log.info("wecom_mcp.cache_cleared", {"category": category})


# ---------------------------------------------------------------------------
# 获取 MCP URL：通过 WeComChannel 的 WSClient
# ---------------------------------------------------------------------------

async def _fetch_mcp_url(category: str) -> str:
    """通过 WeComChannel 的 WSClient 发送 aibot_get_mcp_config 获取 MCP Server URL。

    需要企微 Bot 账号已开通对应品类的 MCP 能力。
    """
    if category in _url_cache:
        return _url_cache[category]

    try:
        from wecom_aibot_sdk.utils import generate_req_id
    except ImportError as e:
        raise RuntimeError("wecom-aibot-sdk 未安装") from e

    from flocks.channel.registry import default_registry
    wecom_channel = default_registry.get("wecom")
    if wecom_channel is None:
        raise RuntimeError("WeComChannel 未注册，请先在 flocks.json 中启用企业微信 channel")

    ws_client = getattr(wecom_channel, "_ws_client", None)
    if ws_client is None:
        raise RuntimeError("WeComChannel 尚未连接，请确保企业微信 Bot 已成功上线")

    req_id = generate_req_id("mcp_cfg")
    try:
        resp = await asyncio.wait_for(
            ws_client.reply(
                {"headers": {"req_id": req_id}},
                {"biz_type": category, "plugin_version": "1.0.0"},
                _MCP_GET_CONFIG_CMD,
            ),
            timeout=_MCP_CONFIG_TIMEOUT,
        )
    except asyncio.TimeoutError:
        raise RuntimeError(
            f"获取企微 MCP 配置超时（category={category}），"
            "请确认企微后台已开通对应能力"
        )

    body = resp.get("body", {}) if isinstance(resp, dict) else {}
    errcode = body.get("errcode", resp.get("errcode"))
    if errcode and errcode != 0:
        errmsg = body.get("errmsg", resp.get("errmsg", "unknown"))
        raise RuntimeError(
            f"获取企微 MCP 配置失败: errcode={errcode}, errmsg={errmsg}"
        )

    url: str = body.get("url", "")
    if not url:
        raise RuntimeError(
            f"企微 MCP 配置响应中缺少 url 字段 (category={category})，"
            "请确认企微账号已开通文档 MCP 能力"
        )

    _url_cache[category] = url
    log.info("wecom_mcp.url_fetched", {"category": category, "url": url})
    return url


# ---------------------------------------------------------------------------
# Streamable HTTP 会话：initialize 握手
# ---------------------------------------------------------------------------

async def _initialize_session(url: str, category: str) -> _McpSession:
    """对 MCP Server 执行 initialize 握手，返回建立好的 Session。"""
    session = _McpSession()
    session.url = url

    import uuid
    init_body = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "initialize",
        "params": {
            "protocolVersion": _MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "flocks_wecom_mcp", "version": "1.0.0"},
        },
    }

    headers: dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.post(url, json=init_body, headers=headers)

    new_session_id = resp.headers.get("mcp-session-id")
    if resp.status_code >= 400:
        raise RuntimeError(f"MCP initialize 失败: HTTP {resp.status_code}")

    if not new_session_id:
        # 无状态 Server
        session.stateless = True
        session.initialized = True
        log.info("wecom_mcp.session_stateless", {"category": category})
    else:
        session.session_id = new_session_id
        # 发送 notifications/initialized
        notify_body = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        notify_headers = {**headers, "Mcp-Session-Id": session.session_id}
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            await client.post(url, json=notify_body, headers=notify_headers)

        session.initialized = True
        log.info("wecom_mcp.session_created", {"category": category, "session_id": new_session_id})

    _sessions[category] = session
    return session


async def _get_or_create_session(category: str) -> _McpSession:
    """获取或创建 MCP 会话，并发安全。"""
    existing = _sessions.get(category)
    if existing and existing.initialized:
        return existing

    async with _get_init_lock(category):
        # double-check after lock
        existing = _sessions.get(category)
        if existing and existing.initialized:
            return existing
        url = await _fetch_mcp_url(category)
        return await _initialize_session(url, category)


# ---------------------------------------------------------------------------
# JSON-RPC 请求
# ---------------------------------------------------------------------------

async def _parse_response(resp: httpx.Response) -> Any:
    """解析 MCP HTTP 响应（支持 JSON 和 SSE）。"""
    ct = resp.headers.get("content-type", "")
    if resp.status_code == 204 or resp.headers.get("content-length") == "0":
        return None

    if "text/event-stream" in ct:
        return _parse_sse(resp.text)

    text = resp.text.strip()
    if not text:
        return None
    data = _json.loads(text)
    if "error" in data:
        err = data["error"]
        code = err.get("code", -1)
        msg = err.get("message", "unknown error")
        # 特定错误码清缓存
        if code in (-32001, -32002, -32003):
            clear_category_cache("")  # will be determined by caller
        raise RuntimeError(f"MCP RPC 错误 [{code}]: {msg}")
    return data.get("result")


def _parse_sse(text: str) -> Any:
    """解析 SSE 响应，提取最后一个事件的 JSON-RPC result。"""
    current_parts: list[str] = []
    last_data = ""
    for line in text.splitlines():
        if line.startswith("data: "):
            current_parts.append(line[6:])
        elif line.startswith("data:"):
            current_parts.append(line[5:])
        elif not line.strip() and current_parts:
            last_data = "\n".join(current_parts).strip()
            current_parts = []
    if current_parts:
        last_data = "\n".join(current_parts).strip()

    if not last_data:
        raise RuntimeError("SSE 响应中无有效数据")
    rpc = _json.loads(last_data)
    if "error" in rpc:
        err = rpc["error"]
        raise RuntimeError(f"MCP RPC 错误 [{err.get('code')}]: {err.get('message')}")
    return rpc.get("result")


async def _send_rpc(
    category: str,
    method: str,
    params: Optional[dict] = None,
    *,
    retry_on_404: bool = True,
) -> Any:
    """向 MCP Server 发送 JSON-RPC 请求，自动管理 Session。"""
    import uuid
    session = await _get_or_create_session(category)

    body: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": method,
    }
    if params is not None:
        body["params"] = params

    req_headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if not session.stateless and session.session_id:
        req_headers["Mcp-Session-Id"] = session.session_id

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(session.url or "", json=body, headers=req_headers)

        # 更新 session_id（如有新值）
        new_sid = resp.headers.get("mcp-session-id")
        if new_sid and not session.stateless:
            session.session_id = new_sid

        # Session 失效（404），重建一次
        if resp.status_code == 404 and retry_on_404 and not session.stateless:
            log.info("wecom_mcp.session_expired", {"category": category})
            clear_category_cache(category)
            return await _send_rpc(category, method, params, retry_on_404=False)

        if resp.status_code >= 400:
            raise RuntimeError(f"MCP HTTP 错误 {resp.status_code}")

        return await _parse_response(resp)

    except httpx.TimeoutException:
        raise RuntimeError(f"MCP 请求超时 (category={category}, method={method})")


# ---------------------------------------------------------------------------
# 操作处理
# ---------------------------------------------------------------------------

def _text_result(data: Any) -> str:
    return _json.dumps(data, ensure_ascii=False, indent=2)


async def _handle_list(category: str) -> str:
    result = await _send_rpc(category, "tools/list")
    tools = (result or {}).get("tools", [])
    if not tools:
        return _text_result({"message": f"品类 '{category}' 下暂无可用工具", "tools": []})
    return _text_result({
        "category": category,
        "count": len(tools),
        "tools": [
            {
                "name": t.get("name"),
                "description": t.get("description", ""),
                "inputSchema": t.get(
                    "inputSchema",
                    {"type": "object", "properties": {}},
                ),
            }
            for t in tools
        ],
    })


async def _handle_call(category: str, method: str, args: dict) -> str:
    result = await _send_rpc(category, "tools/call", {
        "name": method,
        "arguments": args,
    })

    # 检测业务层错误码 850002（权限不足），清理缓存
    if isinstance(result, dict):
        content = result.get("content", [])
        for item in content if isinstance(content, list) else []:
            if item.get("type") == "text":
                try:
                    biz = _json.loads(item["text"])
                    if isinstance(biz, dict) and biz.get("errcode") == 850002:
                        clear_category_cache(category)
                except Exception:
                    pass

    return _text_result(result)


def _parse_args(args: Any) -> dict:
    if not args:
        return {}
    if isinstance(args, dict):
        return args
    try:
        parsed = _json.loads(args)
        if isinstance(parsed, dict):
            return parsed
        raise ValueError("args 必须是 JSON 对象")
    except (_json.JSONDecodeError, ValueError) as e:
        raise ValueError(f"args 不是合法的 JSON 对象: {e}") from e


# ---------------------------------------------------------------------------
# Tool 注册
# ---------------------------------------------------------------------------

@ToolRegistry.register_function(
    name="wecom_mcp",
    description=(
        "Discover and call tools provided by the connected WeCom MCP server. "
        "Use action='list' to inspect method names and input schemas for a category, "
        "then action='call' with the selected method and an args object. "
        "Common categories are 'doc' and 'contact'."
    ),
    category=ToolCategory.CUSTOM,
    parameters=[
        ToolParameter(
            name="action",
            type=ParameterType.STRING,
            description="操作类型：list（列出工具）或 call（调用工具）",
            required=True,
            enum=["list", "call"],
        ),
        ToolParameter(
            name="category",
            type=ParameterType.STRING,
            description="MCP 品类名称，如 doc（文档/智能表格）、contact（通讯录）",
            required=True,
        ),
        ToolParameter(
            name="method",
            type=ParameterType.STRING,
            description="要调用的 MCP 工具名（action=call 时必填），如 create_doc、smartsheet_add_records",
            required=False,
        ),
        ToolParameter(
            name="args",
            type=ParameterType.OBJECT,
            description="MCP method arguments as an object; used with action=call.",
            required=False,
            json_schema={"type": "object", "additionalProperties": True},
        ),
    ],
)
async def wecom_mcp(
    ctx: ToolContext,
    action: str,
    category: str,
    method: Optional[str] = None,
    args: Optional[Any] = None,
) -> ToolResult:
    """企业微信 MCP Tool — 文档与智能表格操作入口。"""
    try:
        if action == "list":
            output = await _handle_list(category)
            return ToolResult(success=True, output=output)

        if action == "call":
            if not method:
                return ToolResult(
                    success=False,
                    error="action=call 时必须提供 method 参数",
                )
            parsed_args = _parse_args(args)
            output = await _handle_call(category, method, parsed_args)
            return ToolResult(success=True, output=output)

        return ToolResult(
            success=False,
            error=f"未知操作类型: {action!r}，支持 list 和 call",
        )

    except RuntimeError as e:
        return ToolResult(success=False, error=str(e))
    except Exception as e:
        log.error("wecom_mcp.error", {"action": action, "category": category, "error": str(e)})
        return ToolResult(success=False, error=f"wecom_mcp 执行错误: {e}")
