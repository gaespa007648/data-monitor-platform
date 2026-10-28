"""Real API/MariaDB integration tests. Own test users/data are removed in tearDownClass."""
import asyncio
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

import jwt
from sqlalchemy import select

from app.config import get_settings
from app.database import SessionFactory, engine
from app.models import AuditLog, ImportBatch, Record, User


def call(path, method="GET", data=None, token=None, expected=200, file=None, key=None):
    headers = {}
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    if file is not None:
        name, content = file
        boundary = "test-boundary-" + uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
                'Content-Type: application/octet-stream\r\n\r\n').encode() + content + f'\r\n--{boundary}--\r\n'.encode()
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        headers["Idempotency-Key"] = key or uuid4().hex
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urlopen(Request("http://localhost:8000" + path, data=body, headers=headers, method=method), timeout=30) as response:
            status, payload = response.status, response.read()
    except HTTPError as exc:
        status, payload = exc.code, exc.read()
    assert status == expected, f"{method} {path}: expected {expected}, got {status}: {payload[:600]!r}"
    return json.loads(payload) if payload else None


class ModulesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prefix = "test_" + uuid4().hex[:12]
        cls.password = "TestPassword123!"
        cls.usernames = [cls.prefix + suffix for suffix in ["_a", "_b", "_race"]]
        cls.tokens = {}
        settings = get_settings()
        for name, password in [("admin", settings.demo_admin_password.get_secret_value()),
                               ("viewer", settings.demo_viewer_password.get_secret_value())]:
            cls.tokens[name] = call("/api/v1/auth/tokens", "POST", {"username": name, "password": password})["access_token"]
        for name in cls.usernames[:2]:
            result = call("/api/v1/users", "POST", {"username": name, "password": cls.password}, expected=201)
            assert result["role"] == "user" and "password_hash" not in result
            cls.tokens[name] = call("/api/v1/auth/tokens", "POST", {"username": name, "password": cls.password})["access_token"]
        cls.category = call("/api/v1/categories", token=cls.tokens["admin"])[0]["id"]

    @classmethod
    def tearDownClass(cls):
        async def cleanup():
            try:
                async with SessionFactory.begin() as session:
                    users = (await session.scalars(select(User).where(User.username.in_(cls.usernames)))).all()
                    ids = [u.id for u in users]
                    for model, column in [(Record, Record.owner_id), (AuditLog, AuditLog.actor_id), (ImportBatch, ImportBatch.owner_id)]:
                        for row in (await session.scalars(select(model).where(column.in_(ids)))).all():
                            await session.delete(row)
                        await session.flush()
                    for user in users:
                        await session.delete(user)
            finally:
                await engine.dispose()
        asyncio.run(cleanup())

    def payload(self, **changes):
        return {"title": self.prefix, "value": "12.345678", "category_id": self.category,
                "timestamp": "2026-10-28T10:00:00+08:00", **changes}

    def test_01_registration_and_authentication(self):
        data = {"username": self.usernames[0], "password": self.password}
        call("/api/v1/users", "POST", data, expected=409)
        call("/api/v1/users", "POST", {**data, "role": "admin"}, expected=422)
        result = call("/api/v1/users", "POST", {**data, "password": "short"}, expected=422)
        self.assertNotIn('"input"', json.dumps(result))
        def register():
            try:
                return call("/api/v1/users", "POST", {**data, "username": self.usernames[2]}, expected=201)
            except AssertionError as exc:
                self.assertIn("got 409", str(exc))
                return None
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: register(), range(2)))
        self.assertEqual(sum(r is not None for r in results), 1)
        call("/api/v1/users/me", expected=401)
        call("/api/v1/users/me", token="invalid", expected=401)
        now = datetime.now(timezone.utc)
        expired = jwt.encode({"sub": "1", "iat": now - timedelta(hours=1), "exp": now - timedelta(minutes=1),
                              "iss": "data-monitor-platform", "aud": "data-monitor-api"},
                             get_settings().jwt_secret.get_secret_value(), algorithm="HS256")
        call("/api/v1/users/me", token=expired, expected=401)
        call("/api/v1/users", token=self.tokens[self.usernames[0]], expected=403)

    def test_02_records_permissions_and_conflicts(self):
        a, b, viewer, admin = [self.tokens[name] for name in [*self.usernames[:2], "viewer", "admin"]]
        call("/api/v1/records", "POST", self.payload(), token=viewer, expected=403)
        call("/api/v1/records", "POST", self.payload(owner_id=1), token=a, expected=422)
        for payload in [self.payload(timestamp="2026-10-28T10:00:00"), self.payload(value="NaN"),
                        self.payload(value="0.1234567"), self.payload(category_id=2147483647)]:
            call("/api/v1/records", "POST", payload, token=a, expected=422)
        row = call("/api/v1/records", "POST", self.payload(), token=a, expected=201)
        path = f"/api/v1/records/{row['id']}"
        self.assertEqual(row["timestamp"], "2026-10-28T02:00:00+00:00")
        self.assertEqual(row["value"], "12.345678")
        for token in [b, viewer]:
            call(path, "PATCH", {"version": row["version"], "title": "blocked"}, token=token, expected=403)
            call(path + "?version=1", "DELETE", token=token, expected=403)
        call(path, "PATCH", {"version": 1, "title": None}, token=a, expected=422)
        call(path, "PATCH", {"version": 1, "owner_id": 1}, token=a, expected=422)
        updated = call(path, "PATCH", {"version": 1, "title": "updated"}, token=a)
        self.assertEqual(updated["version"], 2)
        call(path, "PATCH", {"version": 1, "title": "stale"}, token=a, expected=409)
        def change(title):
            try:
                return call(path, "PATCH", {"version": 2, "title": title}, token=a)
            except AssertionError as exc:
                self.assertIn("got 409", str(exc))
                return None
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(change, ["concurrent_a", "concurrent_b"]))
        self.assertEqual(sum(r is not None for r in results), 1)
        row = call(path, token=viewer)
        row = call(path, "PATCH", {"version": row["version"], "title": "admin updated"}, token=admin)
        call(path + "?version=1", "DELETE", token=a, expected=409)
        call(path + f"?version={row['version']}", "DELETE", token=admin, expected=204)
        call(path, token=a, expected=404)

    def test_03_pagination_filter_and_sort(self):
        token = self.tokens[self.usernames[1]]
        owner = call("/api/v1/users/me", token=token)["id"]
        ids = []
        for i in range(3):
            ids.append(call("/api/v1/records", "POST", self.payload(value=str(i)), token=token, expected=201)["id"])
        params = {"owner_id": owner, "page_size": 2, "sort_by": "value", "order": "asc", "category_id": self.category,
                  "start": "2026-10-28T02:00:00Z", "end": "2026-10-28T02:00:01Z", "source": "manual"}
        first = call("/api/v1/records?" + urlencode(params), token=token)
        second = call("/api/v1/records?" + urlencode({**params, "page": 2}), token=token)
        self.assertEqual(first["total"], 3)
        self.assertEqual([r["id"] for r in first["items"] + second["items"]], ids)
        call("/api/v1/records?sort_by=password_hash", token=token, expected=422)
        call("/api/v1/records?page_size=101", token=token, expected=422)
        call("/api/v1/records?start=2026-10-29T00:00:00Z&end=2026-10-28T00:00:00Z", token=token, expected=422)

    def test_04_atomic_import_and_idempotency(self):
        token = self.tokens[self.usernames[0]]
        owner = call("/api/v1/users/me", token=token)["id"]
        count = lambda: call(f"/api/v1/records?owner_id={owner}", token=token)["total"]
        baseline = count()
        payload = [self.payload(title="JSON A"), self.payload(title="JSON B")]
        file = ("data.json", json.dumps(payload).encode())
        preview = call("/api/v1/record-imports?preview=true", "POST", file=file, token=token, expected=201)
        self.assertTrue(preview["preview"])
        self.assertEqual(count(), baseline)
        invalid = ("data.json", json.dumps([self.payload(), self.payload(value="invalid")]).encode())
        error = call("/api/v1/record-imports", "POST", file=invalid, token=token, expected=422)
        self.assertEqual(error["detail"]["rows"][0]["row"], 2)
        self.assertEqual(count(), baseline)
        key = uuid4().hex
        result = call("/api/v1/record-imports", "POST", file=file, token=token, key=key, expected=201)
        replay = call("/api/v1/record-imports", "POST", file=file, token=token, key=key, expected=201)
        self.assertEqual(result["batch_id"], replay["batch_id"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(count(), baseline + 2)
        call("/api/v1/record-imports", "POST", file=("data.json", b"[]"), token=token, key=key, expected=409)
        different = ("data.json", json.dumps([self.payload(title="different")]).encode())
        call("/api/v1/record-imports", "POST", file=different, token=token, key=key, expected=409)
        csv = f'title,value,category_id,timestamp\nCSV,7.25,{self.category},2026-10-28T10:00:00+08:00\n'.encode()
        call("/api/v1/record-imports", "POST", file=("data.csv", csv), token=token, expected=201)
        call("/api/v1/record-imports", "POST", file=file, token=self.tokens["viewer"], expected=403)
        call("/api/v1/record-imports", "POST", file=("data.csv", b"bad,headers\na,b"), token=token, expected=422)
        call("/api/v1/record-imports", "POST", file=("data.json", b"x" * (2 * 1024 * 1024 + 1)), token=token, expected=413)
        large = ("data.json", json.dumps([self.payload()] * 1001).encode())
        call("/api/v1/record-imports", "POST", file=large, token=token, expected=422)
        concurrent_key = uuid4().hex
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: call("/api/v1/record-imports", "POST", file=file, token=token,
                                                   key=concurrent_key, expected=201), range(2)))
        self.assertEqual(results[0]["batch_id"], results[1]["batch_id"])
        self.assertEqual(count(), baseline + 5)

    def test_05_current_account_state_is_rechecked(self):
        name = self.usernames[1]
        token = self.tokens[name]
        async def change(role, active):
            try:
                async with SessionFactory.begin() as session:
                    user = await session.scalar(select(User).where(User.username == name))
                    user.role, user.is_active = role, active
            finally:
                await engine.dispose()
        try:
            asyncio.run(change("viewer", True))
            call("/api/v1/records", "POST", self.payload(), token=token, expected=403)
            asyncio.run(change("user", False))
            call("/api/v1/users/me", token=token, expected=401)
        finally:
            asyncio.run(change("user", True))

    def test_06_login_rate_limit(self):
        limited = False
        for _ in range(31):
            try:
                call("/api/v1/auth/tokens", "POST", {"username": "nonexistent", "password": "wrong"}, expected=401)
            except AssertionError as exc:
                self.assertIn("got 429", str(exc))
                limited = True
                break
        self.assertTrue(limited)


if __name__ == "__main__":
    unittest.main(verbosity=2)
