import asyncio
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from app.config import get_settings
from tests.cleanup_ui import cleanup
from tests.modules_integration import call


class DatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.names = ["uitest_ds_"+uuid4().hex[:12] for _ in range(2)]
        cls.tokens = {}
        settings = get_settings()
        for name, password in [("admin", settings.demo_admin_password.get_secret_value()),
                               ("viewer", settings.demo_viewer_password.get_secret_value())]:
            login = call("/api/v1/auth/tokens", "POST", {"username": name, "password": password})
            assert login["expires_in"] == 3600
            cls.tokens[name] = login["access_token"]
        for name in cls.names:
            call("/api/v1/users", "POST", {"username": name, "password": "DatasetTest123!"}, expected=201)
            cls.tokens[name] = call("/api/v1/auth/tokens", "POST", {"username": name, "password": "DatasetTest123!"})["access_token"]

    @classmethod
    def tearDownClass(cls):
        for name in cls.names:
            asyncio.run(cleanup(name))

    def fields(self):
        return [{"name": name, "data_type": kind} for name, kind in [
            ("sepal_length", "decimal"), ("sepal_width", "decimal"),
            ("petal_length", "decimal"), ("petal_width", "decimal"), ("species", "string")]]

    def create(self):
        return call("/api/v1/datasets", "POST", {"name": "iris_"+uuid4().hex[:8], "fields": self.fields()},
                    token=self.tokens[self.names[0]], expected=201)

    def row(self):
        return {"sepal_length": 5.1, "sepal_width": 3.5, "petal_length": 1.4, "petal_width": 0.2, "species": "setosa"}

    def test_01_arbitrary_schema_and_append_only(self):
        token = self.tokens[self.names[0]]
        ds = self.create()
        path = f"/api/v1/datasets/{ds['id']}"
        self.assertEqual(len(ds["fields"]), 5)
        self.assertTrue(all(field["remark"] == "" for field in ds["fields"]))
        for method in ["PUT", "PATCH", "DELETE"]:
            call(path+"/fields", method, {"fields": []} if method != "DELETE" else None, token=token, expected=405)
        call(path, "DELETE", token=token, expected=405)
        call(path+"/fields", "POST", {"schema_version": 1, "fields": [{"name": "species", "data_type": "integer"}]}, token=token, expected=422)
        call(path+"/fields", "POST", {"schema_version": 1, "fields": [{"name": "other"}]}, token=self.tokens[self.names[1]], expected=403)
        old_row = call(path+"/records", "POST", {"schema_version": 1, "data": self.row()}, token=token, expected=201)
        added = call(path+"/fields", "POST", {"schema_version": 1, "fields": [
            {"name": "notes", "remark": "補充觀察", "required": False, "nullable": True}]}, token=token, expected=201)
        self.assertEqual(added["fields"][:5], ds["fields"])
        self.assertEqual(added["fields"][5]["remark"], "補充觀察")
        self.assertEqual(added["schema_version"], 2)
        call(path+"/fields", "POST", {"schema_version": 1, "fields": [{"name": "stale"}]}, token=token, expected=409)
        call(path+"/records", "POST", {"schema_version": 1, "data": self.row()}, token=token, expected=409)
        row = call(path+"/records", "POST", {"schema_version": 2, "data": self.row()}, token=token, expected=201)
        self.assertEqual(row["data"]["sepal_length"], "5.1")
        self.assertNotIn("notes", row["data"])
        historic = next(item for item in call(path+"/records", token=token)["items"] if item["id"] == old_row["id"])
        self.assertEqual(historic["schema_version"], 1)
        self.assertNotIn("notes", historic["data"])

    def test_02_manual_validation_and_permissions(self):
        ds = self.create()
        path = f"/api/v1/datasets/{ds['id']}/records"
        token = self.tokens[self.names[0]]
        for bad in [{**self.row(), "species": 1}, {**self.row(), "sepal_width": "bad"},
                    {**self.row(), "extra": 1}]:
            call(path, "POST", {"schema_version": 1, "data": bad}, token=token, expected=422)
        self.assertEqual(call(path, token=token)["total"], 0)
        for name in [self.names[1], "viewer"]:
            call(path, "POST", {"schema_version": 1, "data": self.row()}, token=self.tokens[name], expected=403)
        call(path, "POST", {"schema_version": 1, "data": self.row()}, token=token, expected=201)
        self.assertEqual(call(path, token=self.tokens["viewer"])["total"], 1)
        call("/api/v1/datasets", "POST", {"name": "blocked", "fields": self.fields()}, token=self.tokens["viewer"], expected=403)

    def test_03_csv_json_import_atomicity_and_pagination(self):
        ds = self.create()
        token = self.tokens[self.names[0]]
        path = f"/api/v1/datasets/{ds['id']}"
        csv_content = ("sepal_length,sepal_width,petal_length,petal_width,species\n"
                       "5.1,3.5,1.4,0.2,setosa\n").encode()
        json_content = json.dumps([self.row()]).encode()
        for kind, content in [("csv", csv_content), ("json", json_content)]:
            preview = call(path+"/imports?schema_version=1&preview=true", "POST", file=("sample."+kind, content), token=token, expected=201)
            self.assertEqual(preview["row_count"], 1)
            key = uuid4().hex
            imported = call(path+"/imports?schema_version=1", "POST", file=("sample."+kind, content), key=key, token=token, expected=201)
            replay = call(path+"/imports?schema_version=1", "POST", file=("sample."+kind, content), key=key, token=token, expected=201)
            self.assertTrue(replay["replayed"])
            self.assertEqual(imported["batch_id"], replay["batch_id"])
        invalid = json.dumps([self.row(), {**self.row(), "species": 123}]).encode()
        call(path+"/imports?schema_version=1", "POST", file=("bad.json", invalid), token=token, expected=422)
        self.assertEqual(call(path+"/records", token=token)["total"], 2)
        rows = json.dumps([self.row() for _ in range(23)]).encode()
        call(path+"/imports?schema_version=1", "POST", file=("iris.json", rows), token=token, expected=201)
        first = call(path+"/records?page=1", token=token)
        second = call(path+"/records?page=2", token=token)
        self.assertEqual((first["total"], len(first["items"]), len(second["items"])), (25, 20, 5))
        self.assertFalse({r["id"] for r in first["items"]} & {r["id"] for r in second["items"]})
        call(path+"/records?page_size=21", token=token, expected=422)

    def test_05_csv_first_row_option_and_idempotency(self):
        ds = self.create()
        token = self.tokens[self.names[0]]
        path = f"/api/v1/datasets/{ds['id']}"
        data = ("sepal_length,sepal_width,petal_length,petal_width,species\n"
                "5.1,3.5,1.4,0.2,setosa\n").encode()
        header = call(path+"/imports?schema_version=1&preview=true&skip_first_row=true",
            "POST", file=("iris.csv", data), token=token, expected=201)
        self.assertEqual(header["row_count"], 1)
        call(path+"/imports?schema_version=1&preview=true&skip_first_row=false",
            "POST", file=("iris.csv", data), token=token, expected=422)
        no_header = b"5.1,3.5,1.4,0.2,setosa\n4.9,3.0,1.4,0.2,setosa\n"
        preview = call(path+"/imports?schema_version=1&preview=true&skip_first_row=false",
            "POST", file=("iris.csv", no_header), token=token, expected=201)
        self.assertEqual(preview["row_count"], 2)
        key = uuid4().hex
        imported = call(path+"/imports?schema_version=1&skip_first_row=false",
            "POST", file=("iris.csv", no_header), key=key, token=token, expected=201)
        self.assertEqual(imported["row_count"], 2)
        call(path+"/imports?schema_version=1&skip_first_row=true",
            "POST", file=("iris.csv", no_header), key=key, token=token, expected=409)
        self.assertEqual(call(path+"/records", token=token)["total"], 2)

    def test_04_concurrent_append_and_import_schema_conflict(self):
        ds = self.create()
        token = self.tokens[self.names[0]]
        path = f"/api/v1/datasets/{ds['id']}"
        def append(name):
            try:
                return call(path+"/fields", "POST", {"schema_version": 1,
                    "fields": [{"name": name, "required": False}]}, token=token, expected=201)
            except AssertionError as exc:
                self.assertIn("got 409", str(exc))
                return None
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(append, ["one", "two"]))
        self.assertEqual(sum(result is not None for result in results), 1)
        content = json.dumps([self.row()]).encode()
        call(path+"/imports?schema_version=1", "POST", file=("iris.json", content), token=token, expected=409)
        key = uuid4().hex
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: call(path+"/imports?schema_version=2", "POST",
                file=("iris.json", content), key=key, token=token, expected=201), range(2)))
        self.assertEqual(results[0]["batch_id"], results[1]["batch_id"])
        self.assertEqual(call(path+"/records", token=token)["total"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
