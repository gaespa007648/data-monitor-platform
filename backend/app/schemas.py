from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator, field_serializer

Title = Annotated[str, Field(min_length=1, max_length=200)]
Value = Annotated[Decimal, Field(max_digits=18, decimal_places=6, allow_inf_nan=False)]
Role = Literal["admin", "user", "viewer"]


def utc_time(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must include timezone, for example +08:00 or Z")
    try:
        result = value.astimezone(timezone.utc).replace(tzinfo=None)
    except OverflowError:
        raise ValueError("Timestamp is outside the supported database range") from None
    if result.year < 1000 or result.year > 9999:
        raise ValueError("Timestamp is outside the supported database range")
    return result


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Registration(StrictModel):
    username: str = Field(pattern=r"^[a-zA-Z0-9_]{3,64}$")
    # Preserve password whitespace; never normalize credentials.
    password: str = Field(min_length=12, max_length=256)
    model_config = ConfigDict(extra="forbid")

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value):
        return value.lower()


class RecordInput(StrictModel):
    title: Title
    value: Value
    category_id: int = Field(gt=0)
    timestamp: datetime

    @field_validator("timestamp")
    @classmethod
    def timestamp_utc(cls, value):
        return utc_time(value)


class RecordPatch(StrictModel):
    version: int = Field(ge=1)
    title: Title | None = None
    value: Value | None = None
    category_id: int | None = Field(default=None, gt=0)
    timestamp: datetime | None = None

    @field_validator("timestamp")
    @classmethod
    def timestamp_utc(cls, value):
        return utc_time(value) if value is not None else None

    @model_validator(mode="after")
    def require_changes(self):
        fields = self.model_fields_set - {"version"}
        if not fields or any(getattr(self, field) is None for field in fields):
            raise ValueError("Provide at least one non-null changed field")
        return self


class RecordOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    value: Decimal
    category_id: int
    owner_id: int
    timestamp: datetime
    source: str
    import_batch_id: int | None
    version: int
    created_at: datetime
    updated_at: datetime

    @field_serializer("timestamp", "created_at", "updated_at")
    def serialize_time(self, value):
        return value.replace(tzinfo=timezone.utc).isoformat()


class RecordPage(BaseModel):
    items: list[RecordOutput]
    total: int
    page: int
    page_size: int


class CategoryInput(StrictModel):
    name: str = Field(min_length=1, max_length=64)


class CategoryOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str


class ImportResult(BaseModel):
    batch_id: int | None = None
    row_count: int
    preview: bool
    replayed: bool = False
    schema_version_id: int | None = None
    rules_hash: str | None = None
    fingerprint: str | None = None
    normalized_sample: list[dict] = Field(default_factory=list)
    ignored_fields: list[str] = Field(default_factory=list)
