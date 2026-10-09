import re

from pydantic import BaseModel, Field, field_validator, model_validator

_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class Input(BaseModel):
    model_config = {"extra": "forbid"}


class ListQuery(Input):
    page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=20, ge=1, le=100)
    q: str | None = Field(default=None, max_length=200)


def resource_id(value: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("invalid resource id")
    return value


def file_name(value: str) -> str:
    if any(ord(char) < 32 or 127 <= ord(char) < 160 for char in value):
        raise ValueError("control characters are not allowed")
    cleaned = value.strip()
    if not cleaned or cleaned in {".", ".."} or "/" in cleaned or "\\" in cleaned:
        raise ValueError("a single file or folder name is required")
    return cleaned


class FolderCreate(Input):
    name: str = Field(min_length=1, max_length=255)
    parent_id: str | None = None

    @field_validator("name")
    @classmethod
    def name_value(cls, value: str) -> str:
        return file_name(value)

    @field_validator("parent_id")
    @classmethod
    def parent(cls, value: str | None) -> str | None:
        return resource_id(value) if value is not None else None


class FileUpdate(Input):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    parent_id: str | None = None

    @field_validator("name", "parent_id", mode="before")
    @classmethod
    def non_null_fields(cls, value):
        if value is None:
            raise ValueError("updated fields must be strings")
        return value

    @field_validator("name")
    @classmethod
    def name_value(cls, value: str) -> str:
        return file_name(value)

    @field_validator("parent_id")
    @classmethod
    def parent(cls, value: str) -> str:
        return resource_id(value)

    @model_validator(mode="after")
    def has_changes(self):
        if not self.model_fields_set:
            raise ValueError("at least one updated field is required")
        return self


class IdList(Input):
    ids: list[str] = Field(min_length=1, max_length=100)

    @field_validator("ids")
    @classmethod
    def unique_ids(cls, value: list[str]) -> list[str]:
        if any(resource_id(item) != item for item in value) or len(set(value)) != len(value):
            raise ValueError("invalid resource id")
        return value


class DatasetCreate(Input):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=4000)

    @field_validator("name")
    @classmethod
    def dataset_name(cls, value: str) -> str:
        if any(ord(char) < 32 or 127 <= ord(char) < 160 for char in value):
            raise ValueError("control characters are not allowed")
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("name is required")
        return cleaned

    @field_validator("description")
    @classmethod
    def dataset_description(cls, value: str) -> str:
        # Textareas use LF or CRLF; keep their content while rejecting other
        # C0/C1 controls (including a bare carriage return).
        if any(
            (ord(char) < 32 and char not in "\n\t") or 127 <= ord(char) < 160
            for char in value.replace("\r\n", "\n")
        ):
            raise ValueError("control characters are not allowed")
        return value.strip()


class DatasetUpdate(DatasetCreate):
    # Omission means unchanged; explicit null is not a replacement value.
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)

    @field_validator("name", "description", mode="before")
    @classmethod
    def non_null_fields(cls, value):
        if value is None:
            raise ValueError("updated fields must be strings")
        return value

    @model_validator(mode="after")
    def has_changes(self):
        if not self.model_fields_set:
            raise ValueError("at least one updated field is required")
        return self


class LinkFiles(Input):
    file_ids: list[str] = Field(min_length=1, max_length=100)

    @field_validator("file_ids")
    @classmethod
    def files(cls, value: list[str]) -> list[str]:
        return IdList(ids=value).ids


class Documents(Input):
    document_ids: list[str] = Field(min_length=1, max_length=100)

    @field_validator("document_ids")
    @classmethod
    def documents(cls, value: list[str]) -> list[str]:
        return IdList(ids=value).ids


class Retrieval(Input):
    dataset_ids: list[str] = Field(min_length=1, max_length=50)
    keywords: str = Field(min_length=1, max_length=8000)
    top_k: int = Field(default=5, ge=1, le=100)
    similarity_threshold: float = Field(default=0.2, ge=0, le=1)
    vector_similarity_weight: float = Field(default=0.3, ge=0, le=1)

    @field_validator("dataset_ids")
    @classmethod
    def datasets(cls, value: list[str]) -> list[str]:
        return IdList(ids=value).ids

    @field_validator("keywords")
    @classmethod
    def question(cls, value: str) -> str:
        if any(ord(char) < 32 and char not in "\t\n" for char in value):
            raise ValueError("invalid keywords")
        return value.strip()
