"""Sangfor EDR asset-inventory API collection using the verified auth pair."""

from __future__ import annotations

from typing import Any

import sangfor_edr_http_login as auth


# The classify response contains the counts shown on the asset-inventory page.
DEFAULT_SECTIONS = ("inventory",)
DEFAULT_SCENE_TYPE = "server_and_pc"

CLASSIFY_GROUP_LABELS = {
    "ProcessPort": ("进程端口", "Process and Ports"),
    "ApplicationAsset": ("应用资产", "Application Assets"),
    "WebAsset": ("Web资产", "Web Assets"),
    "InstallAndJar": ("安装包与类库", "Installation Packages and Libraries"),
    "SystemInfo": ("系统信息", "System Information"),
}
CLASSIFY_ASSET_LABELS = {
    "MonitorPort": ("监听端口", "Monitoring Ports"),
    "Process": ("运行进程", "Running Processes"),
    "ApplicationSoftWare": ("软件盘点", "Software Inventory"),
    "DataBase": ("数据库", "Databases"),
    "MiddleWare": ("中间件", "Middleware"),
    "SoftwareMeasurement": ("软件计量", "Software Measurement"),
    "WebApplication": ("Web应用", "Web Applications"),
    "WebSite": ("Web站点", "Websites"),
    "WebService": ("Web服务", "Web Services"),
    "WebFrame": ("Web框架", "Web Frameworks"),
    "InstallPkg": ("系统安装包", "System Installation Packages"),
    "JarPkg": ("Jar包", "JAR Packages"),
    "PythonPkg": ("Python包", "Python Packages"),
    "NpmPkg": ("NPM包", "NPM Packages"),
    "Os": ("操作系统", "Operating Systems"),
    "Replace": ("真替真用", "Replace"),
    "TerminalAccount": ("终端账户", "Terminal Accounts"),
    "EnvironmentVariable": ("环境变量", "Environment Variables"),
    "Lkm": ("内核模块", "Kernel Modules"),
    "Server": ("服务器", "Servers"),
    "Startup": ("启动项", "Startup Items"),
    "Cron": ("定时任务", "Scheduled Tasks"),
    "Openshare": ("开放共享", "Open Shares"),
    "Registry": ("注册表", "Registry"),
    "Network": ("网络", "Network"),
    "Cert": ("证书", "Certificates"),
    "CertAuth": ("证书认证", "Certificate Authorities"),
}


def _inventory_requests(
    token: str,
    *,
    scene_type: str = DEFAULT_SCENE_TYPE,
) -> dict[str, tuple[str, str, dict[str, Any]]]:
    """Build the requests observed when loading the asset-inventory page."""
    return {
        "inventory": (
            "POST",
            f"/api/edrgoweb/v1/asset/inventory/classify?s={token}",
            {"sceneType": scene_type},
        ),
    }


def _validate_response(result: Any) -> Any:
    if isinstance(result, dict):
        code = result.get("code")
        if code not in (None, 0, "0"):
            raise RuntimeError(str(result.get("msg") or f"EDR asset API rejected request (code={code})."))
        if result.get("success") is False:
            raise RuntimeError(str(result.get("msg") or "EDR asset API rejected request."))
    return result


def _classify_readable_response(result: Any) -> Any:
    """Add stable Chinese/English labels while retaining the raw API fields."""
    if not isinstance(result, dict) or not isinstance(result.get("data"), list):
        return result
    readable: list[dict[str, Any]] = []
    for category in result["data"]:
        if not isinstance(category, dict):
            continue
        category_key = str(category.get("assetGroupName") or "")
        category_zh, category_en = CLASSIFY_GROUP_LABELS.get(
            category_key, (category_key, category_key)
        )
        items: list[dict[str, Any]] = []
        for item in category.get("groups") or []:
            if not isinstance(item, dict):
                continue
            asset_key = str(item.get("assetName") or "")
            asset_zh, asset_en = CLASSIFY_ASSET_LABELS.get(asset_key, (asset_key, asset_key))
            items.append(
                {
                    "asset_name": asset_key,
                    "asset_name_zh": asset_zh,
                    "asset_name_en": asset_en,
                    "count": item.get("count", 0),
                }
            )
        readable.append(
            {
                "asset_group": category_key,
                "asset_group_zh": category_zh,
                "asset_group_en": category_en,
                "items": items,
            }
        )
    return readable


def _request_json(
    session: Any,
    cfg: auth.RuntimeConfig,
    path: str,
    payload: dict[str, Any],
) -> Any:
    response = session.post(
        auth._url(cfg, path),
        headers=auth._http_headers(cfg),
        json=payload,
        timeout=cfg.timeout,
    )
    response.raise_for_status()
    return _validate_response(response.json())


def collect_asset_inventory(
    cfg: auth.RuntimeConfig,
    *,
    scene_type: str = DEFAULT_SCENE_TYPE,
) -> dict[str, Any]:
    auth_result = auth.ensure_http_auth_pair(cfg)
    if not auth_result.get("success"):
        raise RuntimeError(
            "EDR authentication refresh failed: "
            f"{auth_result.get('error') or auth_result.get('reason') or auth_result.get('status')}"
        )
    state, token = auth.load_verified_auth_pair(cfg)
    session = auth.dashboard_session(cfg, state)
    definitions = _inventory_requests(
        token,
        scene_type=scene_type,
    )
    selected = list(DEFAULT_SECTIONS)

    data: dict[str, Any] = {}
    readable_data: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for section in selected:
        _, path, payload = definitions[section]
        try:
            data[section] = _request_json(session, cfg, path, payload)
            if section == "inventory":
                readable_data[section] = _classify_readable_response(data[section])
        except Exception as exc:
            errors[section] = auth._safe_error(exc, token)

    return {
        "success": not errors,
        "status": "asset_inventory_collected" if not errors else "asset_inventory_partially_collected",
        "base_url": cfg.base_url,
        "sections": selected,
        "filters": {
            "scene_type": scene_type,
        },
        "data": data,
        "readable_data": readable_data,
        "errors": errors,
        "auth_pair_verified": True,
        "authentication": {
            "status": auth_result.get("status"),
            "login_skipped": bool(auth_result.get("login_skipped")),
        },
    }


def run_asset_inventory(params: dict[str, Any]) -> dict[str, Any]:
    cfg = auth.resolve_runtime_config({**params, "persist_credentials": False})
    return collect_asset_inventory(
        cfg,
        scene_type=str(params.get("scene_type") or DEFAULT_SCENE_TYPE),
    )
