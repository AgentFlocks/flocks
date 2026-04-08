"""
Onboarding routes for guided first-run configuration.

This module hardcodes the ThreatBook onboarding presets for China/global
regions and orchestrates validation + apply flows across LLM, API, and MCP.
"""

from __future__ import annotations

import copy
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from flocks.config.config import Config
from flocks.provider.credential import get_api_key as get_llm_provider_api_key
from flocks.config.config_writer import ConfigWriter
from flocks.provider.provider import Provider
from flocks.provider.types import ModelType
from flocks.security import get_secret_manager
from flocks.server.routes.default_model import (
    SetDefaultModelRequest,
    set_default_model,
)
from flocks.server.routes.mcp import (
    McpCredentialRequest,
    McpTestRequest,
    connect_mcp_server,
    set_mcp_credentials,
    test_mcp_connection,
)
from flocks.server.routes.provider import (
    APIServiceUpdateRequest,
    ProviderCredentialRequest,
    TestCredentialRequest,
    _get_inline_provider_api_key,
    set_provider_credentials,
    set_service_credentials,
    test_provider_credentials,
    update_api_service,
)
from flocks.tool.tool_loader import save_mcp_config
from flocks.utils.log import Log


router = APIRouter()
log = Log.create(service="routes.onboarding")

Region = Literal["cn", "global"]


class ThirdPartyLLMConfig(BaseModel):
    provider_id: str = Field(..., description="Third-party provider ID")
    api_key: str = Field(..., description="Third-party provider API key")
    model_id: str = Field(..., description="Model ID to use as default")
    base_url: Optional[str] = Field(None, description="Optional base URL")
    provider_name: Optional[str] = Field(None, description="Optional display name")


class ResourceValidationResult(BaseModel):
    enabled: bool = Field(..., description="Whether this resource is part of the current flow")
    success: Optional[bool] = Field(None, description="Validation result when tested")
    code: Optional[str] = Field(None, description="Machine-friendly error code")
    message: Optional[str] = Field(None, description="User-facing validation message")
    latency_ms: Optional[int] = Field(None, description="Observed latency")
    details: Dict[str, Any] = Field(default_factory=dict, description="Extra details")


class OnboardingValidateRequest(BaseModel):
    region: Region
    use_threatbook_model: bool = Field(
        default=True,
        description="Whether the user wants ThreatBook as the default LLM",
    )
    threatbook_api_key: Optional[str] = Field(
        None,
        description="Optional ThreatBook key used for ThreatBook services",
    )
    third_party_llm: Optional[ThirdPartyLLMConfig] = Field(
        None,
        description="Optional third-party LLM config",
    )
    threatbook_services_only: bool = Field(
        default=False,
        description="Only validate/apply ThreatBook API/MCP services without revalidating default LLM",
    )


class OnboardingValidateResponse(BaseModel):
    success: bool
    can_apply: bool
    threatbook_enabled: bool
    threatbook_key_valid: Optional[bool] = None
    threatbook_region_match: Optional[bool] = None
    suggested_region: Optional[Region] = None
    error_code: Optional[str] = None
    message: Optional[str] = None
    threatbook_resources: List[str]
    third_party_llm_valid: Optional[bool] = None
    resource_results: Dict[str, ResourceValidationResult]


class OnboardingApplyResponse(BaseModel):
    success: bool
    message: str
    region: Region
    threatbook_enabled: bool
    configured: List[str]
    skipped: List[str]
    default_model: Optional[Dict[str, str]] = None


class OnboardingStatusResponse(BaseModel):
    """Derived onboarding completion state (no separate persistence)."""

    completed: bool = Field(
        ...,
        description="True when a default LLM is set and its provider has usable credentials.",
    )
    has_default_model: bool = Field(
        ...,
        description="True when resolve_default_llm returns a provider/model pair.",
    )
    default_model: Optional[Dict[str, str]] = Field(
        None,
        description="Current resolved default LLM when configured.",
    )


def _llm_provider_has_usable_credentials(provider_id: str) -> bool:
    """Match WebUI credential semantics: secrets + inline flocks.json apiKey."""
    if get_llm_provider_api_key(provider_id):
        return True
    return bool(_get_inline_provider_api_key(provider_id))


ONBOARDING_REGION_PRESETS: Dict[Region, Dict[str, Any]] = {
    "cn": {
        "activation_url": "https://x.threatbook.com/flocks/activate",
        "threatbook_llm_provider_id": "threatbook-cn-llm",
        "threatbook_default_model_id": "qwen3.6-plus",
        "threatbook_api_service_id": "threatbook-cn",
        "threatbook_mcp_name": "threatbook_mcp",
        "threatbook_mcp_url": "https://mcp.threatbook.cn/mcp?apikey={api_key}",
        "threatbook_mcp_secret_id": "threatbook_mcp_key",
        "requires_mcp": True,
    },
    "global": {
        "activation_url": "https://threatbook.io/flocks/activate",
        "threatbook_llm_provider_id": "threatbook-io-llm",
        "threatbook_default_model_id": "qwen3.6-plus",
        "threatbook_api_service_id": "threatbook-io",
        "threatbook_mcp_name": None,
        "threatbook_mcp_url": None,
        "threatbook_mcp_secret_id": None,
        "requires_mcp": False,
    },
}


def _get_threatbook_resources(region: Region, *, include_llm: bool = True) -> List[str]:
    resources: List[str] = ["threatbook_api"]
    if include_llm:
        resources.insert(0, "threatbook_llm")
    if ONBOARDING_REGION_PRESETS[region]["requires_mcp"]:
        resources.append("threatbook_mcp")
    return resources


def _empty_result(enabled: bool, message: str, code: Optional[str] = None) -> ResourceValidationResult:
    return ResourceValidationResult(
        enabled=enabled,
        success=None,
        code=code,
        message=message,
    )


def _normalize_test_result(
    raw: Dict[str, Any],
    *,
    enabled: bool = True,
    fallback_code: str = "validation_failed",
) -> ResourceValidationResult:
    success = bool(raw.get("success"))
    code = None
    if not success:
        code = (
            raw.get("code")
            or raw.get("error_code")
            or raw.get("error")
            or fallback_code
        )
    return ResourceValidationResult(
        enabled=enabled,
        success=success,
        code=code,
        message=raw.get("message"),
        latency_ms=raw.get("latency_ms"),
        details={
            k: v
            for k, v in raw.items()
            if k not in {"success", "message", "latency_ms", "code", "error", "error_code"}
        },
    )


async def _reload_runtime_state() -> None:
    """Best-effort runtime reload after temporary validation writes."""
    try:
        Config.clear_cache()
    except Exception:
        pass

    try:
        config = await Config.get()
        Provider._ensure_initialized()
        await Provider.apply_config(config)
    except Exception as exc:
        log.warning("onboarding.reload_runtime.failed", {"error": str(exc)})


def _snapshot_config_and_secret_state() -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Capture config/secrets so callers can safely restore on failure."""
    secrets = get_secret_manager()
    return copy.deepcopy(ConfigWriter._read_raw()), copy.deepcopy(secrets._load())


async def _restore_config_and_secret_state(
    config_snapshot: Dict[str, Any],
    secret_snapshot: Dict[str, Any],
) -> None:
    """Restore config/secrets and refresh runtime state."""
    secrets = get_secret_manager()
    ConfigWriter._write_raw(config_snapshot)
    secrets._save(secret_snapshot)
    await _reload_runtime_state()


@asynccontextmanager
async def _temporary_config_and_secret_state():
    """Snapshot config + secrets so validate can reuse real write/test paths safely."""
    config_snapshot, secret_snapshot = _snapshot_config_and_secret_state()

    try:
        yield
    finally:
        await _restore_config_and_secret_state(config_snapshot, secret_snapshot)


@asynccontextmanager
async def _rollback_on_apply_failure():
    """Rollback onboarding writes if apply fails part-way through."""
    config_snapshot, secret_snapshot = _snapshot_config_and_secret_state()
    try:
        yield
    except Exception:
        await _restore_config_and_secret_state(config_snapshot, secret_snapshot)
        raise


async def _test_provider_or_service_with_temp_credentials(
    provider_id: str,
    api_key: str,
    *,
    model_id: Optional[str] = None,
    base_url: Optional[str] = None,
    provider_name: Optional[str] = None,
    service: bool = False,
) -> Dict[str, Any]:
    async with _temporary_config_and_secret_state():
        request = ProviderCredentialRequest(
            api_key=api_key,
            base_url=base_url,
            provider_name=provider_name,
        )
        if service:
            await set_service_credentials(provider_id, request)
        else:
            await set_provider_credentials(provider_id, request)
        body = TestCredentialRequest(model_id=model_id) if model_id else None
        return await test_provider_credentials(provider_id, body)


async def _test_mcp_with_temp_key(region: Region, api_key: str) -> Dict[str, Any]:
    preset = ONBOARDING_REGION_PRESETS[region]
    mcp_name = preset["threatbook_mcp_name"]
    mcp_url = preset["threatbook_mcp_url"]
    if not mcp_name or not mcp_url:
        return {
            "success": True,
            "message": "MCP not required for this region",
        }

    safe_key = quote(api_key, safe="")
    request = McpTestRequest(
        name=mcp_name,
        config={
            "type": "remote",
            "url": mcp_url.format(api_key=safe_key),
        },
    )
    return await test_mcp_connection(request)


async def _validate_threatbook_resources(
    region: Region,
    threatbook_api_key: str,
    *,
    include_llm: bool = True,
) -> Dict[str, Any]:
    preset = ONBOARDING_REGION_PRESETS[region]
    resource_results: Dict[str, ResourceValidationResult] = {}

    if include_llm:
        llm_result = await _test_provider_or_service_with_temp_credentials(
            preset["threatbook_llm_provider_id"],
            threatbook_api_key,
            model_id=preset["threatbook_default_model_id"],
        )
        resource_results["threatbook_llm"] = _normalize_test_result(llm_result)
    api_result = await _test_provider_or_service_with_temp_credentials(
        preset["threatbook_api_service_id"],
        threatbook_api_key,
        service=True,
    )

    resource_results["threatbook_api"] = _normalize_test_result(api_result)

    if preset["requires_mcp"]:
        mcp_result = await _test_mcp_with_temp_key(region, threatbook_api_key)
        resource_results["threatbook_mcp"] = _normalize_test_result(mcp_result)

    formal_success = all(
        result.success is True
        for result in resource_results.values()
        if result.enabled
    )
    if formal_success:
        return {
            "success": True,
            "region_match": True,
            "resource_results": resource_results,
            "error_code": None,
            "message": "ThreatBook resources validated successfully.",
            "suggested_region": None,
        }

    other_region: Region = "global" if region == "cn" else "cn"
    other_preset = ONBOARDING_REGION_PRESETS[other_region]
    fallback_api_result = await _test_provider_or_service_with_temp_credentials(
        other_preset["threatbook_api_service_id"],
        threatbook_api_key,
        service=True,
    )
    fallback_api = _normalize_test_result(
        fallback_api_result,
        fallback_code="region_probe_failed",
    )

    if fallback_api.success:
        return {
            "success": False,
            "region_match": False,
            "resource_results": resource_results,
            "error_code": "region_mismatch",
            "message": (
                "检测到该 ThreatBook key 可用于另一地区，请切换区域后重试。"
            ),
            "suggested_region": other_region,
            "probe": fallback_api.model_dump(),
        }

    partial_success = any(result.success is True for result in resource_results.values())
    return {
        "success": False,
        "region_match": None,
        "resource_results": resource_results,
        "error_code": "partial_service_failure" if partial_success else "invalid_key",
        "message": "ThreatBook key 验证失败，请检查 key 是否有效并确认所属区域。",
        "suggested_region": None,
        "probe": fallback_api.model_dump(),
    }


def _validate_third_party_shape(
    third_party: Optional[ThirdPartyLLMConfig],
) -> Optional[str]:
    if third_party is None:
        return "请选择并填写一个第三方模型配置。"
    if not third_party.provider_id.strip():
        return "第三方模型 Provider 不能为空。"
    if not third_party.model_id.strip():
        return "第三方模型不能为空。"
    if not third_party.api_key.strip():
        return "第三方模型 API key 不能为空。"
    return None


async def _validate_onboarding_request(
    request: OnboardingValidateRequest,
) -> OnboardingValidateResponse:
    threatbook_resources = _get_threatbook_resources(
        request.region,
        include_llm=request.use_threatbook_model,
    )
    resource_results: Dict[str, ResourceValidationResult] = {}
    threatbook_api_key = (request.threatbook_api_key or "").strip()
    third_party = request.third_party_llm

    threatbook_enabled = bool(threatbook_api_key)
    threatbook_key_valid: Optional[bool] = None
    threatbook_region_match: Optional[bool] = None
    suggested_region: Optional[Region] = None
    error_code: Optional[str] = None
    message: Optional[str] = None
    third_party_llm_valid: Optional[bool] = None

    if not threatbook_enabled:
        for resource in threatbook_resources:
            resource_results[resource] = _empty_result(
                enabled=False,
                message="未填写 ThreatBook key，已跳过该资源配置。",
                code="skipped",
            )

        if request.use_threatbook_model:
            resource_results["threatbook_llm"] = ResourceValidationResult(
                enabled=True,
                success=False,
                code="missing_threatbook_key",
                message="你已选择使用 ThreatBook 模型，因此必须填写对应区域的 ThreatBook key。",
            )
            return OnboardingValidateResponse(
                success=False,
                can_apply=False,
                threatbook_enabled=False,
                threatbook_key_valid=False,
                threatbook_region_match=None,
                suggested_region=None,
                error_code="missing_threatbook_key",
                message=resource_results["threatbook_llm"].message,
                threatbook_resources=threatbook_resources,
                third_party_llm_valid=None,
                resource_results=resource_results,
            )
    else:
        threatbook_validation = await _validate_threatbook_resources(
            request.region,
            threatbook_api_key,
            include_llm=request.use_threatbook_model,
        )
        threatbook_key_valid = bool(threatbook_validation["success"])
        threatbook_region_match = threatbook_validation["region_match"]
        suggested_region = threatbook_validation["suggested_region"]
        error_code = threatbook_validation["error_code"]
        message = threatbook_validation["message"]
        resource_results.update(threatbook_validation["resource_results"])

        if error_code == "region_mismatch":
            return OnboardingValidateResponse(
                success=False,
                can_apply=False,
                threatbook_enabled=True,
                threatbook_key_valid=False,
                threatbook_region_match=False,
                suggested_region=suggested_region,
                error_code="region_mismatch",
                message=message,
                threatbook_resources=threatbook_resources,
                third_party_llm_valid=None,
                resource_results=resource_results,
            )

    if not request.use_threatbook_model:
        if request.threatbook_services_only:
            if threatbook_enabled and threatbook_key_valid:
                return OnboardingValidateResponse(
                    success=True,
                    can_apply=True,
                    threatbook_enabled=True,
                    threatbook_key_valid=True,
                    threatbook_region_match=True,
                    suggested_region=None,
                    error_code=None,
                    message="ThreatBook 服务验证通过，可应用 API / MCP 配置。",
                    threatbook_resources=threatbook_resources,
                    third_party_llm_valid=None,
                    resource_results=resource_results,
                )

            return OnboardingValidateResponse(
                success=False,
                can_apply=False,
                threatbook_enabled=threatbook_enabled,
                threatbook_key_valid=threatbook_key_valid,
                threatbook_region_match=threatbook_region_match,
                suggested_region=suggested_region,
                error_code=error_code or "nothing_to_apply",
                message=message or "请先填写 ThreatBook key。",
                threatbook_resources=threatbook_resources,
                third_party_llm_valid=None,
                resource_results=resource_results,
            )

        third_party_shape_error = _validate_third_party_shape(third_party)
        if third_party_shape_error:
            if threatbook_enabled and threatbook_key_valid:
                return OnboardingValidateResponse(
                    success=True,
                    can_apply=True,
                    threatbook_enabled=True,
                    threatbook_key_valid=True,
                    threatbook_region_match=True,
                    suggested_region=None,
                    error_code=None,
                    message="ThreatBook 资源验证通过，可仅应用 ThreatBook 服务配置。",
                    threatbook_resources=threatbook_resources,
                    third_party_llm_valid=None,
                    resource_results=resource_results,
                )

            resource_results["third_party_llm"] = ResourceValidationResult(
                enabled=True,
                success=False,
                code="missing_third_party_llm",
                message=third_party_shape_error,
            )
            return OnboardingValidateResponse(
                success=False,
                can_apply=False,
                threatbook_enabled=threatbook_enabled,
                threatbook_key_valid=threatbook_key_valid,
                threatbook_region_match=threatbook_region_match,
                suggested_region=suggested_region,
                error_code="missing_third_party_llm",
                message=third_party_shape_error,
                threatbook_resources=threatbook_resources,
                third_party_llm_valid=False,
                resource_results=resource_results,
            )

        assert third_party is not None
        third_party_result = await _test_provider_or_service_with_temp_credentials(
            third_party.provider_id,
            third_party.api_key.strip(),
            model_id=third_party.model_id.strip(),
            base_url=(third_party.base_url or "").strip() or None,
            provider_name=(third_party.provider_name or "").strip() or None,
        )
        third_party_normalized = _normalize_test_result(
            third_party_result,
            fallback_code="third_party_validation_failed",
        )
        resource_results["third_party_llm"] = third_party_normalized
        third_party_llm_valid = third_party_normalized.success

        if third_party_llm_valid and (not threatbook_enabled or threatbook_key_valid):
            return OnboardingValidateResponse(
                success=True,
                can_apply=True,
                threatbook_enabled=threatbook_enabled,
                threatbook_key_valid=threatbook_key_valid,
                threatbook_region_match=threatbook_region_match,
                suggested_region=suggested_region,
                error_code=None,
                message="配置验证通过，可应用所选模型与服务。",
                threatbook_resources=threatbook_resources,
                third_party_llm_valid=True,
                resource_results=resource_results,
            )

        return OnboardingValidateResponse(
            success=False,
            can_apply=False,
            threatbook_enabled=threatbook_enabled,
            threatbook_key_valid=threatbook_key_valid,
            threatbook_region_match=threatbook_region_match,
            suggested_region=suggested_region,
            error_code=error_code or third_party_normalized.code or "validation_failed",
            message=message or third_party_normalized.message or "配置验证失败。",
            threatbook_resources=threatbook_resources,
            third_party_llm_valid=third_party_llm_valid,
            resource_results=resource_results,
        )

    if threatbook_enabled and threatbook_key_valid:
        return OnboardingValidateResponse(
            success=True,
            can_apply=True,
            threatbook_enabled=True,
            threatbook_key_valid=True,
            threatbook_region_match=True,
            suggested_region=None,
            error_code=None,
            message="ThreatBook 资源验证通过，可应用配置。",
            threatbook_resources=threatbook_resources,
            third_party_llm_valid=None,
            resource_results=resource_results,
        )

    return OnboardingValidateResponse(
        success=False,
        can_apply=False,
        threatbook_enabled=threatbook_enabled,
        threatbook_key_valid=threatbook_key_valid,
        threatbook_region_match=threatbook_region_match,
        suggested_region=suggested_region,
        error_code=error_code or "nothing_to_apply",
        message=message or "请先填写 ThreatBook key 或第三方模型配置。",
        threatbook_resources=threatbook_resources,
        third_party_llm_valid=third_party_llm_valid,
        resource_results=resource_results,
    )


def _ensure_threatbook_mcp_config(region: Region) -> None:
    preset = ONBOARDING_REGION_PRESETS[region]
    mcp_name = preset["threatbook_mcp_name"]
    mcp_secret_id = preset["threatbook_mcp_secret_id"]
    if not mcp_name or not mcp_secret_id:
        return

    config = {
        "type": "remote",
        "url": f"https://mcp.threatbook.cn/mcp?apikey={{secret:{mcp_secret_id}}}",
        "enabled": True,
    }
    ConfigWriter.add_mcp_server(mcp_name, config)
    save_mcp_config(mcp_name, config)


@router.get(
    "/status",
    response_model=OnboardingStatusResponse,
    summary="Get onboarding completion status",
    description=(
        "Derives whether first-run LLM setup is complete from existing default model "
        "and provider credentials (no separate onboarding store)."
    ),
)
async def get_onboarding_status() -> OnboardingStatusResponse:
    resolved = await Config.resolve_default_llm()
    if not resolved:
        return OnboardingStatusResponse(
            completed=False,
            has_default_model=False,
            default_model=None,
        )
    provider_id = resolved["provider_id"]
    model_id = resolved["model_id"]
    default_model = {"provider_id": provider_id, "model_id": model_id}
    has_cred = _llm_provider_has_usable_credentials(provider_id)
    return OnboardingStatusResponse(
        completed=has_cred,
        has_default_model=True,
        default_model=default_model,
    )


@router.post(
    "/validate",
    response_model=OnboardingValidateResponse,
    summary="Validate onboarding configuration",
    description="Validate ThreatBook and/or third-party model configuration for onboarding.",
)
async def validate_onboarding(request: OnboardingValidateRequest) -> OnboardingValidateResponse:
    return await _validate_onboarding_request(request)


@router.post(
    "/apply",
    response_model=OnboardingApplyResponse,
    summary="Apply onboarding configuration",
    description="Persist onboarding configuration after validation succeeds.",
)
async def apply_onboarding(request: OnboardingValidateRequest) -> OnboardingApplyResponse:
    validation = await _validate_onboarding_request(request)
    if not validation.can_apply:
        raise HTTPException(status_code=400, detail=validation.message or "Validation failed")

    preset = ONBOARDING_REGION_PRESETS[request.region]
    threatbook_api_key = (request.threatbook_api_key or "").strip()
    configured: List[str] = []
    skipped: List[str] = []
    default_model: Optional[Dict[str, str]] = None

    try:
        async with _rollback_on_apply_failure():
            if threatbook_api_key:
                if request.use_threatbook_model:
                    await set_provider_credentials(
                        preset["threatbook_llm_provider_id"],
                        ProviderCredentialRequest(api_key=threatbook_api_key),
                    )
                    configured.append("threatbook_llm")

                await set_service_credentials(
                    preset["threatbook_api_service_id"],
                    ProviderCredentialRequest(api_key=threatbook_api_key),
                )
                await update_api_service(
                    preset["threatbook_api_service_id"],
                    APIServiceUpdateRequest(enabled=True),
                )
                configured.append("threatbook_api")

                if preset["requires_mcp"]:
                    _ensure_threatbook_mcp_config(request.region)
                    await set_mcp_credentials(
                        preset["threatbook_mcp_name"],
                        McpCredentialRequest(
                            api_key=threatbook_api_key,
                            secret_id=preset["threatbook_mcp_secret_id"],
                        ),
                    )
                    await connect_mcp_server(preset["threatbook_mcp_name"])
                    configured.append("threatbook_mcp")
            else:
                skipped.extend(
                    _get_threatbook_resources(
                        request.region,
                        include_llm=request.use_threatbook_model,
                    )
                )

            if request.use_threatbook_model:
                await set_default_model(
                    ModelType.LLM,
                    SetDefaultModelRequest(
                        provider_id=preset["threatbook_llm_provider_id"],
                        model_id=preset["threatbook_default_model_id"],
                    ),
                )
                default_model = {
                    "provider_id": preset["threatbook_llm_provider_id"],
                    "model_id": preset["threatbook_default_model_id"],
                }
                configured.append("default_llm")
            elif request.third_party_llm:
                third_party = request.third_party_llm
                await set_provider_credentials(
                    third_party.provider_id,
                    ProviderCredentialRequest(
                        api_key=third_party.api_key.strip(),
                        base_url=(third_party.base_url or "").strip() or None,
                        provider_name=(third_party.provider_name or "").strip() or None,
                    ),
                )
                await set_default_model(
                    ModelType.LLM,
                    SetDefaultModelRequest(
                        provider_id=third_party.provider_id,
                        model_id=third_party.model_id,
                    ),
                )
                default_model = {
                    "provider_id": third_party.provider_id,
                    "model_id": third_party.model_id,
                }
                configured.extend(["third_party_llm", "default_llm"])
            elif not request.threatbook_services_only:
                skipped.append("default_llm")
    except HTTPException:
        log.warning(
            "onboarding.apply.failed",
            {"region": request.region, "configured_before_rollback": configured},
        )
        raise
    except Exception as exc:
        log.error(
            "onboarding.apply.failed",
            {
                "region": request.region,
                "configured_before_rollback": configured,
                "error": str(exc),
            },
        )
        raise HTTPException(status_code=500, detail=f"Failed to apply onboarding configuration: {exc}")

    return OnboardingApplyResponse(
        success=True,
        message="Onboarding configuration applied successfully.",
        region=request.region,
        threatbook_enabled=bool(threatbook_api_key),
        configured=configured,
        skipped=skipped,
        default_model=default_model,
    )
