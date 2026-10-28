import unittest
import hashlib
import json
from decimal import Decimal

from fastapi import HTTPException
from pydantic import ValidationError

from app.dataset_rules import DatasetDefinition, decode_payload, unique_value_hash, validate_rows


class DatasetRulesTests(unittest.TestCase):
    def definition(self):
        return DatasetDefinition(fields=[
            {"name": "species", "data_type": "string"},
            {"name": "length", "data_type": "decimal"},
            {"name": "count", "data_type": "integer"},
            {"name": "active", "data_type": "boolean"},
            {"name": "time", "data_type": "datetime"},
            {"name": "optional", "data_type": None, "required": False, "nullable": True}])

    def row(self):
        return {"species": "setosa", "length": Decimal("1.234567890123"), "count": 1,
                "active": True, "time": "2026-10-28T10:00:00.123456+08:00"}

    def test_arbitrary_fields_and_lossless_roundtrip(self):
        encoded = validate_rows([self.row()], self.definition(), "json")[0]
        result = decode_payload(encoded)
        self.assertEqual(result["length"], "1.234567890123")
        self.assertEqual(result["time"], "2026-10-28T02:00:00.123456+00:00")
        self.assertNotIn("optional", result)
        encoded = validate_rows([{**self.row(), "optional": None}], self.definition(), "json")[0]
        self.assertIsNone(decode_payload(encoded)["optional"])

    def test_type_and_shape_rejections(self):
        for changes in [{"species": 123}, {"length": "1.25"}, {"count": True}, {"active": 1},
                        {"time": "invalid-sensitive-value"}, {"extra": "hidden"}, {"count": None}]:
            with self.assertRaises(HTTPException) as error:
                validate_rows([{**self.row(), **changes}], self.definition(), "json")
            self.assertNotIn("invalid-sensitive-value", str(error.exception.detail))
        with self.assertRaises(ValidationError):
            DatasetDefinition(fields=[{"name": "x"}, {"name": "x"}])
        with self.assertRaises(ValidationError):
            DatasetDefinition(fields=[{"name": "__record_id"}])

    def test_manual_csv_and_untyped_values(self):
        row = self.row()
        row.update(length="1.25", count="3", active="false")
        data = decode_payload(validate_rows([row], self.definition(), "csv")[0])
        self.assertEqual((data["length"], data["count"], data["active"]), ("1.25", 3, False))
        schema = DatasetDefinition(fields=[{"name": "value", "data_type": None}])
        one = validate_rows([{"value": Decimal("1.20")}], schema, "json")[0]
        two = validate_rows([{"value": "1.20"}], schema, "json")[0]
        self.assertEqual(one["value"]["kind"], "decimal")
        self.assertEqual(two["value"]["kind"], "string")

    def test_resource_limits(self):
        schema = DatasetDefinition(fields=[{"name": "value"}])
        for value in ["x"*4097, Decimal("1e100"), Decimal("0.1234567890123"), {"nested": 1}]:
            with self.assertRaises(HTTPException):
                validate_rows([{"value": value}], schema, "json")

    def test_optional_remark_and_legacy_checksum(self):
        legacy = {"fields": [{"name": "species", "data_type": "string",
                              "required": True, "nullable": False}]}
        old_hash = hashlib.sha256(json.dumps(legacy, ensure_ascii=False,
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        definition = DatasetDefinition.model_validate(legacy)
        self.assertEqual(definition.fields[0].remark, "")
        self.assertEqual(definition.digest(), old_hash)
        self.assertFalse(definition.fields[0].unique)
        documented = DatasetDefinition(fields=[{**legacy["fields"][0], "remark": "鳶尾花品種"}])
        self.assertEqual(documented.fields[0].remark, "鳶尾花品種")
        self.assertNotEqual(documented.digest(), old_hash)
        with self.assertRaises(ValidationError):
            DatasetDefinition(fields=[{"name": "x", "remark": "x" * 501}])

    def test_unique_flag_and_canonical_values(self):
        original = DatasetDefinition(fields=[{"name": "id", "data_type": "integer"}])
        enabled = DatasetDefinition(fields=[{"name": "id", "data_type": "integer", "unique": True}])
        self.assertNotEqual(original.digest(), enabled.digest())
        self.assertFalse(original.fields[0].unique)
        self.assertTrue(enabled.fields[0].unique)
        with self.assertRaises(ValidationError):
            DatasetDefinition(fields=[{"name": "id", "unique": "true"}])
        self.assertEqual(unique_value_hash({"kind": "integer", "value": "1"}),
                         unique_value_hash({"kind": "decimal", "value": "1.00"}))
        self.assertNotEqual(unique_value_hash({"kind": "string", "value": "1"}),
                            unique_value_hash({"kind": "integer", "value": "1"}))
        self.assertNotEqual(
            unique_value_hash({"kind": "decimal", "value": "12345678901234567890123456789012345678"}),
            unique_value_hash({"kind": "decimal", "value": "12345678901234567890123456789012345679"}))
