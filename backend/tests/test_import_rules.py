import json
import unittest
from decimal import Decimal

from fastapi import HTTPException
from pydantic import ValidationError

from app.import_rules import FieldRule, VersionRules, default_rules, validate_source


def rules(kind="json", typed=False):
    types = {"title": "string", "value": "decimal", "category_id": "integer", "timestamp": "datetime"}
    return VersionRules(file_format=kind, fields=[{"source_field": key, "target_field": key,
        "data_type": types[key] if typed else None} for key in types])


class ImportRulesTests(unittest.TestCase):
    def row(self, **changes):
        return {"title": "data", "value": 12.345678, "category_id": 1,
                "timestamp": "2026-10-28T10:00:00.123456+08:00", **changes}

    def validate(self, row, schema=None):
        return validate_source(json.dumps([row]).encode(), "data.json", schema or rules())[0][0]

    def test_optional_type_normalization_and_compatibility(self):
        omitted = VersionRules(file_format="json", fields=[{"source_field": k, "target_field": k}
                                                          for k in ("title", "value", "category_id", "timestamp")])
        self.assertEqual(omitted.digest(), rules().digest())
        self.assertTrue(all(f["data_type"] is None for f in omitted.snapshot()["fields"]))
        self.assertEqual(self.validate(self.row(value="12.5"), omitted).value, Decimal("12.5"))
        for row in [self.row(value="bad"), self.row(timestamp="2026-10-28T10:00:00"), self.row(title=None),
                    self.row(timestamp="9999-12-31T23:59:59-12:00")]:
            with self.assertRaises(HTTPException):
                self.validate(row, omitted)

    def test_incompatible_type_and_hidden_options_rejected(self):
        for field in [dict(source_field="a", target_field="title", data_type="decimal"),
                      dict(source_field="a", target_field="value", allow_numeric_string=False),
                      dict(source_field="a", target_field="value", max_digits=18),
                      dict(source_field="a", target_field="owner_id"),
                      dict(source_field="a", target_field="title", data_type="option")]:
            with self.assertRaises(ValidationError):
                FieldRule(**field)
        schema = rules().model_dump()
        schema["fields"][1]["source_field"] = "title"
        with self.assertRaises(ValidationError):
            VersionRules(**schema)

    def test_strict_json_types(self):
        for row in [self.row(title=123), self.row(category_id=True), self.row(category_id=1.0),
                    self.row(value=True), self.row(value="12.5"), self.row(timestamp=123)]:
            with self.assertRaises(HTTPException):
                self.validate(row, rules(typed=True))
        schema = rules(typed=True)
        schema.fields[1].allow_numeric_string = True
        self.assertEqual(self.validate(self.row(value="12.5"), schema).value, Decimal("12.5"))

    def test_numeric_precision_and_string_limits(self):
        schema = rules(typed=True)
        schema.fields[0].max_length = 2
        with self.assertRaises(HTTPException):
            self.validate(self.row(), schema)
        schema.fields[0].max_length = None
        schema.fields[1].decimal_places = 2
        with self.assertRaises(HTTPException):
            self.validate(self.row(), schema)
        schema.fields[1].decimal_places = None
        result = self.validate(self.row(), schema)
        self.assertEqual(result.value, Decimal("12.345678"))
        self.assertEqual(result.timestamp.microsecond, 123456)

    def test_structure_and_case_sensitive_mapping(self):
        for body in [b'[{"title":"one","title":"two"}]', b'[1]', b'{}', b'[{"value":1e99999999999999999999999999}]']:
            with self.assertRaises(HTTPException):
                validate_source(body, "data.json", rules())
        for body in [b"title,title,value,category_id,timestamp\na,b,1,1,z", b"title,value,category_id,timestamp\nx,1"]:
            with self.assertRaises(HTTPException):
                validate_source(body, "data.csv", default_rules("csv"))
        schema = rules()
        schema.fields[0].source_field = "Title"
        with self.assertRaises(HTTPException):
            self.validate(self.row(), schema)
        self.assertEqual(self.validate({"Title": "correct", **{k: v for k, v in self.row().items() if k != "title"}}, schema).title, "correct")

    def test_extra_fields_policy_and_error_redaction(self):
        schema = rules()
        row = self.row(secret="do-not-echo")
        with self.assertRaises(HTTPException) as exc:
            self.validate(row, schema)
        self.assertNotIn("do-not-echo", str(exc.exception.detail))
        schema.extra_policy = "ignore"
        records, ignored = validate_source(json.dumps([row]).encode(), "data.json", schema)
        self.assertEqual(ignored, ["secret"])
        self.assertEqual(len(records), 1)
        with self.assertRaises(HTTPException):
            self.validate(self.row(extra={"nested": True}), schema)
