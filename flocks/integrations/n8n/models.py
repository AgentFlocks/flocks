"""Typed models for the Flocks n8n workflow builder.

The models intentionally describe a small, stable intermediate representation
instead of mirroring n8n's full node JSON surface.  Renderers own n8n-specific
node versions and parameter shapes.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


N8nStepKind = Literal[
    "code",
    "set",
    "if",
    "http_request",
    "respond_to_webhook",
    "convert_to_file",
    "write_file",
    "write_binary_file",
    "noop",
]


class N8nCredentialRef(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: Optional[str] = None
    name: Optional[str] = None
    type: Optional[str] = None


class N8nCredentialRequirement(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    name: str
    type: str
    secret_ref: Optional[str] = Field(None, alias="secretRef")
    data: Dict[str, Any] = Field(default_factory=dict)
    update_existing: bool = Field(False, alias="updateExisting")

    @field_validator("name", "type")
    @classmethod
    def _valid_required_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("credential name and type are required")
        return cleaned


class N8nTrigger(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    type: Literal["webhook", "kafka"] = "webhook"
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "POST"
    path: Optional[str] = None
    response_mode: Literal["responseNode"] = Field("responseNode", alias="responseMode")
    topic: Optional[str] = None
    group_id: Optional[str] = Field(None, alias="groupId")
    group_prefix: Optional[str] = Field(None, alias="groupPrefix")
    credential_ref: Optional[N8nCredentialRef] = Field(None, alias="credentialRef")
    from_beginning: bool = Field(False, alias="fromBeginning")
    batch_size: int = Field(1, alias="batchSize")
    resolve_offset: Literal["onCompletion", "onSuccess", "immediately", "onStatus"] = Field("onCompletion", alias="resolveOffset")
    use_schema_registry: bool = Field(False, alias="useSchemaRegistry")
    schema_registry_credential_ref: Optional[N8nCredentialRef] = Field(None, alias="schemaRegistryCredentialRef")
    schema_registry_url: Optional[str] = Field(None, alias="schemaRegistryUrl")
    options: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("topic", "group_id", "group_prefix", "path")
    @classmethod
    def _strip_optional_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class N8nStep(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: str
    kind: N8nStepKind
    description: str = ""
    name: Optional[str] = None
    next: Optional[str] = None
    true_next: Optional[str] = None
    false_next: Optional[str] = None
    js_code: Optional[str] = None
    assignments: Dict[str, Any] = Field(default_factory=dict)
    condition: Optional[str] = None
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    url: Optional[str] = None
    authentication: Optional[str] = None
    generic_auth_type: Optional[str] = Field(None, alias="genericAuthType")
    headers: Dict[str, str] = Field(default_factory=dict)
    body: Optional[Any] = None
    credentials: Dict[str, N8nCredentialRef] = Field(default_factory=dict)
    respond_with: Literal["json", "text", "firstIncomingItem"] = "json"
    response_body: Optional[Any] = None
    file_name: Optional[str] = Field(None, alias="fileName")
    output_file_name: Optional[str] = Field(None, alias="outputFileName")
    data_property_name: str = Field("data", alias="dataPropertyName")
    mode: Literal["once", "each"] = "once"
    encoding: str = "utf8"
    format_json: bool = Field(False, alias="formatJson")
    append: bool = False

    @field_validator("id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("step id is required")
        return cleaned


class N8nExpectation(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: Optional[int] = 200
    json_contains: Dict[str, Any] = Field(default_factory=dict, alias="jsonContains")
    text_contains: Optional[str] = Field(default=None, alias="textContains")


class N8nTestCase(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    name: str
    method: Optional[Literal["GET", "POST", "PUT", "PATCH", "DELETE"]] = None
    input: Any = Field(default_factory=dict)
    headers: Dict[str, str] = Field(default_factory=dict)
    expect: N8nExpectation = Field(default_factory=N8nExpectation)


class N8nIR(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    name: str
    description: str = ""
    trigger: N8nTrigger = Field(default_factory=N8nTrigger)
    steps: List[N8nStep] = Field(default_factory=list)
    tests: List[N8nTestCase] = Field(default_factory=list)
    credential_requirements: List[N8nCredentialRequirement] = Field(default_factory=list, alias="credentialRequirements")
    max_iterations: int = Field(8, alias="maxIterations")

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("workflow name is required")
        return cleaned

    @model_validator(mode="after")
    def _has_steps(self) -> "N8nIR":
        if not self.steps:
            raise ValueError("at least one step is required")
        return self


class N8nAutobuildRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    ir: N8nIR
    n8n_base_url: str = Field("http://localhost:5678", alias="n8nBaseUrl")
    n8n_api_key_secret_ref: str = Field("N8N_API_KEY", alias="n8nApiKeySecretRef")
    activate: bool = True
    cleanup_on_success: bool = Field(False, alias="cleanupOnSuccess")
    max_iterations: int = Field(8, alias="maxIterations")
