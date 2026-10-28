"""Dataset schemas are arbitrary fields; no fixed RecordInput mapping."""
import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas import utc_time


class DatasetField(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=64)
    data_type: Literal["string", "integer", "decimal", "datetime", "boolean"] | None = None
    required: bool = True
    nullable: bool = False

    @model_validator(mode="after")
    def valid_name(self):
        if self.name != self.name.strip() or self.name.startswith("__") or any(ord(c) < 32 for c in self.name):
            raise ValueError("Field names cannot have edge whitespace, control characters or the reserved __ prefix")
        return self


class DatasetDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fields: list[DatasetField] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_names(self):
        if len({f.name for f in self.fields}) != len(self.fields):
            raise ValueError("Field names must be unique (case sensitive)")
        return self

    def digest(self):
        return hashlib.sha256(json.dumps(self.model_dump(), ensure_ascii=False,
                                        sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def encode_value(value, data_type, mode):
    if isinstance(value, (dict, list)):
        raise ValueError("Nested objects/arrays are unsupported")
    if isinstance(value, str) and len(value) > 4096:
        raise ValueError("Text exceeds 4096 characters")
    if data_type == "string":
        if not isinstance(value, str):
            raise ValueError("Expected string")
    elif data_type == "integer":
        if type(value) is not int:
            if mode in {"csv", "manual"} and isinstance(value, str) and re.fullmatch(r"[+-]?[0-9]+", value):
                value = int(value)
            else:
                raise ValueError("Expected integer, not a boolean or fractional number")
    elif data_type == "decimal":
        if isinstance(value, bool) or not isinstance(value, (int, str, Decimal)):
            raise ValueError("Expected decimal")
        if isinstance(value, str):
            if mode == "json" or not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", value):
                raise ValueError("Expected number; JSON numeric strings are not accepted")
        value = Decimal(value)
    elif data_type == "boolean":
        if type(value) is not bool:
            if mode in {"csv", "manual"} and isinstance(value, str) and value.lower() in {"true", "false"}:
                value = value.lower() == "true"
            else:
                raise ValueError("Expected boolean true or false")
    elif data_type == "datetime":
        if not isinstance(value, str):
            raise ValueError("Expected ISO 8601 string with timezone")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            value = utc_time(parsed).replace(tzinfo=timezone.utc)
        except (ValueError, OverflowError):
            raise ValueError("Expected ISO 8601 datetime with timezone within supported range") from None
    # Tagged storage preserves untyped JSON decimal vs string and datetime vs text.
    if isinstance(value, datetime):
        return {"kind": "datetime", "value": value.isoformat()}
    if type(value) is bool:
        return {"kind": "boolean", "value": value}
    if type(value) is int:
        if len(str(abs(value))) > 38:
            raise ValueError("Integer exceeds 38 digits")
        return {"kind": "integer", "value": str(value)}
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Expected finite number")
        _, digits, exponent = value.as_tuple()
        if max(len(digits) + exponent, 0) + max(-exponent, 0) > 38 or max(-exponent, 0) > 12:
            raise ValueError("Decimal exceeds 38 total digits or 12 decimal places")
        return {"kind": "decimal", "value": str(value)}
    if isinstance(value, str):
        return {"kind": "string", "value": value}
    raise ValueError("Unsupported value type")


def validate_rows(rows: list[dict[str, Any]], definition: DatasetDefinition, mode):
    names = {f.name for f in definition.fields}
    results, errors = [], []
    for index, row in enumerate(rows, 1):
        problems, encoded = [], {}
        if set(row) - names:
            problems.append({"stage": "structure", "message": "Unexpected fields", "fields": sorted(set(row) - names)[:20]})
        for field in definition.fields:
            if field.name not in row:
                if field.required:
                    problems.append({"field": field.name, "message": "Required field is missing"})
                continue
            value = row[field.name]
            try:
                if value is None:
                    if not field.nullable:
                        raise ValueError("Null is not allowed")
                    encoded[field.name] = {"kind": "null", "value": None}
                else:
                    encoded[field.name] = encode_value(value, field.data_type, mode)
            except (ValueError, InvalidOperation, OverflowError) as exc:
                problems.append({"field": field.name, "expected_type": field.data_type,
                                 "message": str(exc) if not isinstance(exc, InvalidOperation) else "Invalid decimal"})
        if len(json.dumps(encoded, ensure_ascii=False).encode()) > 256 * 1024:
            problems.append({"message": "Normalized record exceeds 256 KiB"})
        if problems:
            errors.append({"row": index, "errors": problems})
        results.append(encoded)
    if errors:
        raise HTTPException(422, {"message": "No rows saved", "rows": errors[:50], "error_rows": len(errors)})
    return results


def decode_payload(payload):
    # Decimal is returned as exact text, identified by schema/value_types in the API.
    return {name: (int(cell["value"]) if cell["kind"] == "integer" and abs(int(cell["value"])) <= 9007199254740991 else cell["value"])
            for name, cell in payload.items()}
