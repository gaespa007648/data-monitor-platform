"""Test schema lifecycle, generated files and atomic imports against Compose."""
import asyncio
import hashlib
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionFactory, engine
from app.models import (AuditLog, ImportBatch, ImportSchemaField, ImportSchemaTemplate,
                        ImportSchemaVersion, Record, User)
from tests.modules_integration import call


class SchemaIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prefix = "schema_"+uuid4().hex[:10]
        cls.names = [cls.prefix+"_a", cls.prefix+"_b"]
        cls.tokens = {}
        settings = get_settings()
        for name, password in [("admin", settings.demo_admin_password.get_secret_value()),
                               ("viewer", settings.demo_viewer_password.get_secret_value())]:
            cls.tokens[name] = call("/api/v1/auth/tokens", "POST", {"username": name, "password": password})["access_token"]
        for name in cls.names:
            call("/api/v1/users", "POST", {"username": name, "password": "SchemaTest123!"}, expected=201)
            cls.tokens[name] = call("/api/v1/auth/tokens", "POST", {"username": name, "password": "SchemaTest123!"})["access_token"]
        cls.category = call("/api/v1/categories", token=cls.tokens["admin"])[0]["id"]
        cls.template_ids = []

    @classmethod
    def tearDownClass(cls):
        async def cleanup():
            try:
                async with SessionFactory.begin() as session:
                    users = (await session.scalars(select(User).where(User.username.in_(cls.names)))).all()
                    ids = [u.id for u in users]
                    for model, condition in [
                        (Record, Record.owner_id.in_(ids)), (ImportBatch, ImportBatch.owner_id.in_(ids)),
                        (AuditLog, AuditLog.actor_id.in_(ids)),
                        (ImportSchemaField, ImportSchemaField.version_id.in_(select(ImportSchemaVersion.id).where(ImportSchemaVersion.template_id.in_(cls.template_ids)))),
                        (ImportSchemaVersion, ImportSchemaVersion.template_id.in_(cls.template_ids)),
                        (ImportSchemaTemplate, ImportSchemaTemplate.id.in_(cls.template_ids))]:
                        for row in (await session.scalars(select(model).where(condition))).all():
                            await session.delete(row)
                        await session.flush()
                    for user in users:
                        await session.delete(user)
            finally:
                await engine.dispose()
        asyncio.run(cleanup())

    def rules(self, kind="json", typed=True):
        types = {"title": "string", "value": "decimal", "category_id": "integer", "timestamp": "datetime"}
        names = ["名稱", "量測值", "分類編號", "量測時間"]
        return {"file_format": kind, "fields": [{"source_field": name, "target_field": target,
            "data_type": types[target] if typed else None} for name, target in zip(names, types)]}

    def create(self, kind="json", typed=True, token=None, visibility="private"):
        token = token or self.tokens[self.names[0]]
        result = call("/api/v1/import-schemas", "POST", {"name": self.prefix+uuid4().hex[:6],
            "visibility": visibility, "rules": self.rules(kind, typed)}, token=token, expected=201)
        self.template_ids.append(result["id"])
        return result["id"], result["version"]

    def publish(self, tid, vid, token=None):
        return call(f"/api/v1/import-schemas/{tid}/versions/{vid}/publication", "POST", token=token or self.tokens[self.names[0]])

    def sample(self, tid, vid, token=None):
        token = token or self.tokens[self.names[0]]
        req = Request(f"http://localhost:8000/api/v1/import-schemas/{tid}/versions/{vid}/sample",
                      headers={"Authorization": f"Bearer {token}"})
        with urlopen(req, timeout=15) as response:
            return response.read()

    def upload(self, vid, content, kind="json", preview=False, key=None, token=None, expected=201):
        return call("/api/v1/record-imports?"+urlencode({"schema_version_id": vid, "preview": str(preview).lower()}),
            "POST", token=token or self.tokens[self.names[0]], file=("generated."+kind, content), key=key, expected=expected)

    def test_01_generated_csv_json_optional_types_and_snapshots(self):
        for kind, typed in [("csv", True), ("json", True), ("csv", False), ("json", False)]:
            tid, version = self.create(kind, typed)
            self.publish(tid, version["id"])
            content = self.sample(tid, version["id"])
            preview = self.upload(version["id"], content, kind, preview=True)
            self.assertEqual(preview["row_count"], 1)
            self.assertEqual(preview["normalized_sample"][0]["title"], "範例資料")
            key = uuid4().hex
            result = self.upload(version["id"], content, kind, key=key)
            replay = self.upload(version["id"], content, kind, key=key)
            self.assertTrue(replay["replayed"])
            self.assertEqual(result["batch_id"], replay["batch_id"])
            batch = call(f"/api/v1/record-imports/{result['batch_id']}", token=self.tokens[self.names[0]])
            self.assertEqual(batch["rules_hash"], version["rules_hash"])
            self.assertEqual({f["data_type"] for f in batch["schema_snapshot"]["fields"]},
                             {"string", "decimal", "integer", "datetime"} if typed else {None})

    def test_02_acl_and_draft_immutability(self):
        tid, version = self.create()
        path = f"/api/v1/import-schemas/{tid}/versions/{version['id']}"
        for name in [self.names[1], "viewer"]:
            call(path, token=self.tokens[name], expected=404)
        call("/api/v1/import-schemas", "POST", {"name": "blocked", "rules": self.rules()}, token=self.tokens["viewer"], expected=403)
        call("/api/v1/import-schemas", "POST", {"name": "blocked", "visibility": "shared", "rules": self.rules()}, token=self.tokens[self.names[0]], expected=403)
        self.upload(version["id"], b"[]", expected=409)
        modified = self.rules(typed=False)
        call(path, "PUT", modified, token=self.tokens[self.names[0]])
        self.publish(tid, version["id"])
        call(path, "PUT", modified, token=self.tokens[self.names[0]], expected=409)
        call(path, "DELETE", token=self.tokens[self.names[0]], expected=409)
        shared_id, shared = self.create(token=self.tokens["admin"], visibility="shared")
        self.publish(shared_id, shared["id"], self.tokens["admin"])
        shared_path = f"/api/v1/import-schemas/{shared_id}/versions/{shared['id']}"
        call(shared_path, token=self.tokens["viewer"])
        call(shared_path, "PUT", modified, token=self.tokens[self.names[0]], expected=403)
        draft = call(f"/api/v1/import-schemas/{tid}/versions", "POST", modified, token=self.tokens[self.names[0]], expected=201)
        call(f"/api/v1/import-schemas/{tid}/versions/{draft['id']}", "DELETE", token=self.tokens[self.names[0]], expected=204)

    def test_03_validation_and_atomicity(self):
        tid, version = self.create()
        self.publish(tid, version["id"])
        row = json.loads(self.sample(tid, version["id"]))[0]
        owner = call("/api/v1/users/me", token=self.tokens[self.names[0]])["id"]
        count = lambda: call(f"/api/v1/records?owner_id={owner}", token=self.tokens[self.names[0]])["total"]
        baseline = count()
        invalid = {**row, "名稱": 123}
        error = self.upload(version["id"], json.dumps([row, invalid]).encode(), expected=422)
        self.assertEqual(error["detail"]["rows"][0]["row"], 2)
        self.assertEqual(error["detail"]["rows"][0]["errors"][0]["source_field"], "名稱")
        self.assertEqual(count(), baseline)
        self.upload(version["id"], json.dumps([{**row, "量測值": "1.5"}]).encode(), expected=422)
        self.upload(version["id"], json.dumps([{**row, "分類編號": 2147483647}]).encode(), expected=422)
        changed = self.rules()
        changed["extra_policy"] = "ignore"
        changed["fields"][1]["allow_numeric_string"] = True
        second = call(f"/api/v1/import-schemas/{tid}/versions", "POST", changed, token=self.tokens[self.names[0]], expected=201)
        self.publish(tid, second["id"])
        payload = json.dumps([{**row, "量測值": "1.5", "額外": "ignored"}]).encode()
        result = self.upload(second["id"], payload, preview=True)
        self.assertEqual(result["ignored_fields"], ["額外"])
        self.upload(second["id"], payload)

    def test_04_version_fingerprint_deactivation_and_replay(self):
        tid, version = self.create(typed=False)
        self.publish(tid, version["id"])
        content = self.sample(tid, version["id"])
        key = uuid4().hex
        first = self.upload(version["id"], content, key=key)
        second = call(f"/api/v1/import-schemas/{tid}/versions", "POST", self.rules(typed=False), token=self.tokens[self.names[0]], expected=201)
        self.publish(tid, second["id"])
        self.upload(second["id"], content, key=key, expected=409)
        call(f"/api/v1/import-schemas/{tid}/versions/{version['id']}/activation", "PATCH", {"is_active": False}, token=self.tokens[self.names[0]])
        self.assertEqual(self.upload(version["id"], content, key=key)["batch_id"], first["batch_id"])
        self.upload(version["id"], content, key=uuid4().hex, expected=409)
        batch = call(f"/api/v1/record-imports/{first['batch_id']}", token=self.tokens[self.names[0]])
        self.assertEqual(batch["schema_version_id"], version["id"])
        concurrent_key = uuid4().hex
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: self.upload(second["id"], content, key=concurrent_key), range(2)))
        self.assertEqual(results[0]["batch_id"], results[1]["batch_id"])

    def test_05_legacy_batch_and_invalid_schema_definition(self):
        token = self.tokens[self.names[0]]
        invalid = self.rules(typed=False)
        invalid["fields"][1]["max_digits"] = 18
        call("/api/v1/import-schemas", "POST", {"name": "invalid", "rules": invalid}, token=token, expected=422)
        owner = call("/api/v1/users/me", token=token)["id"]
        content = json.dumps([{"title": "legacy", "value": "1", "category_id": self.category,
                               "timestamp": "2026-10-28T10:00:00+08:00"}]).encode()
        key = uuid4().hex
        async def seed_legacy():
            try:
                async with SessionFactory.begin() as session:
                    batch = ImportBatch(owner_id=owner, idempotency_key=key,
                        content_hash=hashlib.sha256(content+b"\0json").hexdigest(), row_count=1)
                    session.add(batch)
                    await session.flush()
                    from app.schemas import RecordInput
                    data = RecordInput.model_validate(json.loads(content)[0])
                    session.add(Record(**data.model_dump(), owner_id=owner, source="import", import_batch_id=batch.id))
                    return batch.id
            finally:
                await engine.dispose()
        batch_id = asyncio.run(seed_legacy())
        replay = call("/api/v1/record-imports", "POST", file=("legacy.json", content), token=token, key=key, expected=201)
        self.assertTrue(replay["replayed"])
        self.assertIsNone(replay["schema_version_id"])
        self.assertTrue(call(f"/api/v1/record-imports/{batch_id}", token=token)["legacy"])
        builtin = next(t for t in call("/api/v1/import-schemas", token=token) if t["builtin_key"] == "default-json")
        version = call(f"/api/v1/import-schemas/{builtin['id']}/versions", token=token)[0]
        self.upload(version["id"], content, key=key, expected=409)


if __name__ == "__main__":
    unittest.main(verbosity=2)
