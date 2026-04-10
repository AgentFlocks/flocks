"""
Model management types and data models

Defines enums, Pydantic models, and data structures for the model management module.
Reference: docs/model-management-design.md
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# ==================== Enums ====================


class AuthMethod(str, Enum):
    """认证方式"""
    API_KEY = "api_key"
    SUBSCRIPTION = "subscription"
    OAUTH = "oauth"
    AWS_SDK = "aws_sdk"


class ModelType(str, Enum):
    """模型类型"""
    LLM = "llm"
    TEXT_EMBEDDING = "text-embedding"
    RERANK = "rerank"
    SPEECH2TEXT = "speech2text"
    TTS = "tts"
    MODERATION = "moderation"
    IMAGE = "image"


class ModelFeature(str, Enum):
    """模型能力特性"""
    TOOL_CALL = "tool-call"
    MULTI_TOOL_CALL = "multi-tool-call"
    VISION = "vision"
    REASONING = "reasoning"
    STREAM_TOOL_CALL = "stream-tool-call"
    STRUCTURED_OUTPUT = "structured-output"
    DOCUMENT = "document"
    AUDIO = "audio"
    VIDEO = "video"


class ParameterType(str, Enum):
    """参数类型"""
    FLOAT = "float"
    INT = "int"
    STRING = "string"
    BOOLEAN = "boolean"
    TEXT = "text"


class ConfigurateMethod(str, Enum):
    """配置方式"""
    PREDEFINED_MODEL = "predefined-model"
    CUSTOMIZABLE_MODEL = "customizable-model"


class FetchFrom(str, Enum):
    """模型来源"""
    PREDEFINED = "predefined"
    CUSTOMIZABLE = "customizable"


class ModelStatus(str, Enum):
    """模型状态"""
    ACTIVE = "active"
    BETA = "beta"
    DEPRECATED = "deprecated"
    ALPHA = "alpha"


class CredentialStatus(str, Enum):
    """凭据状态"""
    ACTIVE = "active"
    INVALID = "invalid"
    EXPIRED = "expired"
    COOLDOWN = "cooldown"
    UNTESTED = "untested"


# ==================== Provider Metadata ====================


class CredentialFieldSchema(BaseModel):
    """凭据字段 Schema"""
    name: str
    label: str
    type: Literal["text", "secret", "select"] = "secret"
    required: bool = True
    placeholder: Optional[str] = None
    help_text: Optional[str] = None
    options: Optional[List[Dict[str, str]]] = None


class CredentialSchema(BaseModel):
    """凭据 Schema (按 AuthMethod 分组)"""
    auth_method: AuthMethod
    fields: List[CredentialFieldSchema]


class ProviderMeta(BaseModel):
    """Provider 元数据"""
    id: str
    name: str
    description: Optional[str] = None
    icon_url: Optional[str] = None
    supported_auth_methods: List[AuthMethod] = Field(
        default_factory=lambda: [AuthMethod.API_KEY]
    )
    supported_model_types: List[ModelType] = Field(
        default_factory=lambda: [ModelType.LLM]
    )
    configurate_methods: List[ConfigurateMethod] = Field(
        default_factory=lambda: [ConfigurateMethod.PREDEFINED_MODEL]
    )
    credential_schemas: List[CredentialSchema] = Field(default_factory=list)
    env_vars: List[str] = Field(default_factory=list)


# ==================== Credential ====================


class CredentialConfig(BaseModel):
    """凭据配置内容 (按 auth_method 不同字段不同)"""
    # 通用
    base_url: Optional[str] = None

    # api_key
    api_key: Optional[str] = None
    org_id: Optional[str] = None

    # subscription
    token: Optional[str] = None
    expires_at: Optional[datetime] = None

    # oauth (预留)
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    client_id: Optional[str] = None

    # aws_sdk
    aws_access_key_id: Optional[str] = None
    aws_secret_access_key: Optional[str] = None
    aws_region: Optional[str] = None

    # 扩展
    extra: Dict[str, Any] = Field(default_factory=dict)

    def get_display_key(self, auth_method: AuthMethod) -> Optional[str]:
        """获取用于展示的主要密钥值（用于脱敏显示）"""
        if auth_method == AuthMethod.API_KEY:
            return self.api_key
        elif auth_method == AuthMethod.SUBSCRIPTION:
            return self.token
        elif auth_method == AuthMethod.OAUTH:
            return self.access_token
        elif auth_method == AuthMethod.AWS_SDK:
            return self.aws_access_key_id
        return None


class Credential(BaseModel):
    """凭据实例"""
    id: str
    provider_id: str
    name: str
    auth_method: AuthMethod
    encrypted_config: str  # 加密后的 CredentialConfig JSON
    is_current: bool = False
    status: CredentialStatus = CredentialStatus.UNTESTED
    last_test_error: Optional[str] = None
    last_tested_at: Optional[datetime] = None
    failure_count: int = 0
    cooldown_until: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class CredentialSummary(BaseModel):
    """凭据摘要 (API 返回用，不含敏感信息)"""
    id: str
    provider_id: str
    name: str
    auth_method: AuthMethod
    is_current: bool
    status: CredentialStatus
    masked_key: Optional[str] = None
    last_test_error: Optional[str] = None
    last_tested_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class CredentialValidateResult(BaseModel):
    """凭据验证结果"""
    valid: bool
    latency_ms: Optional[int] = None
    error: Optional[str] = None
    model_count: Optional[int] = None


# ==================== Model Definition ====================


class Modalities(BaseModel):
    """模型输入/输出模态"""
    input: List[Literal["text", "image", "audio", "video", "pdf"]] = Field(
        default_factory=lambda: ["text"]
    )
    output: List[Literal["text", "image", "audio", "video", "pdf"]] = Field(
        default_factory=lambda: ["text"]
    )


class ModelLimits(BaseModel):
    """模型限制"""
    context_window: int = 128000
    max_input_tokens: Optional[int] = None
    max_output_tokens: int = 4096


class PriceConfig(BaseModel):
    """价格配置 (每百万 token)"""
    input: float = 0.0
    output: float = 0.0
    unit: float = 1_000_000
    currency: str = "USD"
    cache_read: Optional[float] = None
    cache_write: Optional[float] = None


class ModelCapabilitiesV2(BaseModel):
    """模型能力 (V2, 增强版)"""
    features: List[ModelFeature] = Field(default_factory=list)
    modalities: Modalities = Field(default_factory=Modalities)
    # 快捷布尔 (向后兼容)
    supports_streaming: bool = True
    supports_tools: bool = True
    supports_vision: bool = False
    supports_reasoning: bool = False
    supports_temperature: bool = True
    supports_json_mode: bool = False
    supports_structured_output: bool = False


class ParameterRule(BaseModel):
    """模型参数规则"""
    name: str
    label: str
    type: ParameterType
    required: bool = False
    default: Optional[Any] = None
    min: Optional[float] = None
    max: Optional[float] = None
    precision: Optional[int] = None
    options: Optional[List[str]] = None
    help_text: Optional[str] = None


class ModelDefinition(BaseModel):
    """模型完整定义"""
    id: str
    name: str
    provider_id: str
    model_type: ModelType = ModelType.LLM
    family: Optional[str] = None
    status: ModelStatus = ModelStatus.ACTIVE
    fetch_from: FetchFrom = FetchFrom.PREDEFINED
    capabilities: ModelCapabilitiesV2 = Field(default_factory=ModelCapabilitiesV2)
    limits: ModelLimits = Field(default_factory=ModelLimits)
    pricing: Optional[PriceConfig] = None
    parameter_rules: List[ParameterRule] = Field(default_factory=list)
    release_date: Optional[str] = None
    deprecated_at: Optional[str] = None


# ==================== Model Settings ====================


class ModelSetting(BaseModel):
    """模型设置 (用户配置覆盖)"""
    provider_id: str
    model_id: str
    enabled: bool = True
    credential_id: Optional[str] = None
    default_parameters: Dict[str, Any] = Field(default_factory=dict)


class DefaultModelConfig(BaseModel):
    """默认模型配置"""
    model_type: ModelType
    provider_id: str
    model_id: str


# ==================== Usage ====================


class UsageRecord(BaseModel):
    """用量记录"""
    id: str
    provider_id: str
    model_id: str
    credential_id: Optional[str] = None
    session_id: Optional[str] = None
    message_id: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    input_cost: float = 0.0
    output_cost: float = 0.0
    total_cost: float = 0.0
    currency: str = "USD"
    latency_ms: Optional[int] = None
    source: str = "live"
    created_at: datetime
    backfilled_at: Optional[datetime] = None


class UsageCost(BaseModel):
    """成本计算结果"""
    input_cost: float = 0.0
    output_cost: float = 0.0
    cache_cost: float = 0.0
    total_cost: float = 0.0
    currency: str = "USD"
