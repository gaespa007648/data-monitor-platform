"""Declarative, bounded import validation; no code evaluation or dynamic SQL."""
import csv
import hashlib
import io
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.schemas import RecordInput, utc_time

TARGETS = ("title", "value", "category_id", "timestamp")
COMPATIBLE = {"title": {"string"}, "value": {"integer", "decimal"},
              "category_id": {"integer"}, "timestamp": {"datetime"}}


class FieldRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_field: str = Field(min_length=1, max_length=128)
    target_field: Literal["title", "value", "category_id", "timestamp"]
    data_type: Literal["string", "integer", "decimal", "datetime"] | None = None
    allow_numeric_string: bool | None = None
    max_length: int | None = Field(None, ge=1, le=200)
    max_digits: int | None = Field(None, ge=1, le=18)
    decimal_places: int | None = Field(None, ge=0, le=6)

    @model_validator(mode="after")
    def check_options(self):
        if not self.source_field.strip() or any(ord(c) < 32 for c in self.source_field):
            raise ValueError("Source field cannot be blank or contain control characters")
        if self.data_type is not None and self.data_type not in COMPATIBLE[self.target_field]:
            raise ValueError("Data type is incompatible with the target field")
        for option, types in [("allow_numeric_string", {"integer", "decimal"}),
                              ("max_length", {"string"}), ("max_digits", {"decimal"}),
                              ("decimal_places", {"decimal"})]:
            if getattr(self, option) is not None and self.data_type not in types:
                raise ValueError(f"{option} requires an explicit compatible data_type")
        if self.max_digits is not None and self.decimal_places is not None and self.decimal_places > self.max_digits:
            raise ValueError("decimal_places cannot exceed max_digits")
        return self


class VersionRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_format: Literal["csv", "json"]
    extra_policy: Literal["reject", "ignore"] = "reject"
    trim_whitespace: bool = True
    fields: list[FieldRule] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def complete_mapping(self):
        if {f.target_field for f in self.fields} != set(TARGETS) or len({f.source_field for f in self.fields}) != 4:
            raise ValueError("Provide each target once and four unique source fields")
        if self.file_format != "json" and any(f.allow_numeric_string is not None for f in self.fields):
            raise ValueError("allow_numeric_string applies only to JSON numeric fields")
        return self

    def snapshot(self):
        data = self.model_dump(mode="json")
        data["fields"].sort(key=lambda item: item["target_field"])
        return data

    def digest(self):
        return hashlib.sha256(json.dumps(self.snapshot(), sort_keys=True, ensure_ascii=False,
                                        separators=(",", ":")).encode()).hexdigest()


def default_rules(file_format):
    return VersionRules(file_format=file_format, fields=[FieldRule(source_field=key, target_field=key) for key in TARGETS])


def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def parse_source(content, filename, rules, csv_fieldnames=None, skip_first_row=True):
    if not filename.lower().endswith("." + rules.file_format):
        raise HTTPException(422, {"stage": "structure", "message": "File extension does not match schema format"})
    try:
        text = content.decode("utf-8-sig")
        if rules.file_format == "json":
            rows = json.loads(text, parse_float=Decimal, object_pairs_hook=no_duplicates,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite value")))
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError("JSON must contain an array of objects")
        else:
            reader = csv.reader(io.StringIO(text, newline=""), strict=True)
            headers = next(reader, [])
            if not skip_first_row:
                if not csv_fieldnames:
                    raise ValueError("CSV field names are required without a header")
                headers = csv_fieldnames
                reader = csv.reader(io.StringIO(text, newline=""), strict=True)
            if not headers or len(headers) != len(set(headers)) or any(not h.strip() for h in headers):
                raise ValueError("CSV headers must be unique and nonempty")
            rows = []
            for index, cells in enumerate(reader, 1):
                if len(cells) != len(headers):
                    raise HTTPException(422, {"stage": "structure", "row": index, "message": "CSV row width mismatch"})
                rows.append(dict(zip(headers, cells)))
                if len(rows) > 1000:
                    break
    except (UnicodeDecodeError, ValueError, csv.Error, RecursionError, InvalidOperation, OverflowError):
        raise HTTPException(422, {"stage": "structure", "message": "Invalid UTF-8 CSV/JSON, duplicate keys or headers"}) from None
    if not 1 <= len(rows) <= 1000:
        raise HTTPException(422, "Import must contain 1 to 1000 rows")
    return rows


def typed_value(value, rule, file_format):
    kind = rule.data_type
    if kind is None:
        return value
    numeric_string = file_format == "csv" or bool(rule.allow_numeric_string)
    if kind == "string":
        if not isinstance(value, str):
            raise ValueError("Expected string")
        if rule.max_length is not None and len(value) > rule.max_length:
            raise ValueError("String exceeds configured max_length")
    elif kind == "integer":
        if type(value) is int:
            return value
        if numeric_string and isinstance(value, str) and re.fullmatch(r"[+-]?[0-9]+", value):
            return int(value)
        raise ValueError("Expected integer (boolean and fractional numbers are not accepted)")
    elif kind == "decimal":
        if isinstance(value, bool) or not isinstance(value, (int, Decimal, str)):
            raise ValueError("Expected decimal")
        if isinstance(value, str) and (not numeric_string or not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", value)):
            raise ValueError("Expected number; numeric strings require explicit permission for JSON")
        try:
            number = Decimal(value)
            if not number.is_finite():
                raise ValueError("Expected finite decimal")
            # Count meaningful precision without expanding potentially huge exponents.
            _, digits, exponent = number.as_tuple()
            fraction = max(-exponent, 0)
            whole = max(len(digits) + exponent, 0)
            if rule.decimal_places is not None and fraction > rule.decimal_places:
                raise ValueError("Decimal exceeds configured decimal_places")
            if rule.max_digits is not None and whole + fraction > rule.max_digits:
                raise ValueError("Decimal exceeds configured max_digits")
            return number
        except InvalidOperation:
            raise ValueError("Expected finite decimal") from None
    elif kind == "datetime":
        if not isinstance(value, str):
            raise ValueError("Expected ISO 8601 datetime string")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            utc_time(parsed)
        except (ValueError, OverflowError):
            raise ValueError("Expected ISO 8601 datetime with timezone") from None
    return value


def validate_source(content, filename, rules):
    rows = parse_source(content, filename, rules)
    source_names = {field.source_field for field in rules.fields}
    by_target = {field.target_field: field for field in rules.fields}
    records, errors, ignored = [], [], set()
    for index, row in enumerate(rows, 1):
        row_errors, mapped = [], {}
        missing, extra = source_names - set(row), set(row) - source_names
        ignored.update(extra)
        for name in sorted(missing):
            row_errors.append({"source_field": name, "stage": "structure", "message": "Missing required field"})
        if extra and rules.extra_policy == "reject":
            row_errors.append({"stage": "structure", "message": "Unexpected fields", "fields": sorted(extra)[:20]})
        if any(isinstance(value, (dict, list)) for value in row.values()):
            row_errors.append({"stage": "structure", "message": "Nested objects and arrays are unsupported"})
        for rule in rules.fields:
            if rule.source_field not in row:
                continue
            value = row[rule.source_field]
            if rules.trim_whitespace and isinstance(value, str):
                value = value.strip()
            try:
                if value is None or value == "":
                    raise ValueError("Required value cannot be empty or null")
                mapped[rule.target_field] = typed_value(value, rule, rules.file_format)
            except (ValueError, OverflowError):
                row_errors.append({"stage": "type", "source_field": rule.source_field,
                    "target_field": rule.target_field, "expected_type": rule.data_type,
                    "actual_type": type(value).__name__, "message": "Value does not match configured type or constraints"})
        if not row_errors:
            try:
                records.append(RecordInput.model_validate(mapped))
            except ValidationError as exc:
                for error in exc.errors():
                    target = error["loc"][0]
                    rule = by_target[target]
                    row_errors.append({"stage": "record", "source_field": rule.source_field,
                        "target_field": target, "expected_type": rule.data_type,
                        "message": error["msg"]})
        if row_errors:
            errors.append({"row": index, "errors": row_errors})
    if errors:
        raise HTTPException(422, {"message": "No rows imported", "rows": errors[:50], "error_rows": len(errors)})
    return records, sorted(ignored)
