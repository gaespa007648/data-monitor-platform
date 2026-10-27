"""Run inside the API container against real Compose services, without extra dependencies."""
import asyncio
import json
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionFactory, engine
from app.models import User
from app.security import dummy_hash


def request(path, data=None, token=None, expected=200):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request("http://localhost:8000" + path,
                  data=json.dumps(data).encode() if data is not None else None, headers=headers)
    try:
        with urlopen(req, timeout=10) as response:
            status, body = response.status, response.read()
    except HTTPError as exc:
        status, body = exc.code, exc.read()
    assert status == expected, f"{path}: expected {expected}, got {status}"
    return json.loads(body)


async def persistence(mode):
    try:
        async with SessionFactory.begin() as session:
            marker = await session.scalar(select(User).where(User.username == "__smoke_persistence__"))
            if mode == "create":
                assert marker is None, "Previous smoke marker exists; clean up before testing"
                session.add(User(username="__smoke_persistence__", role="viewer",
                                 password_hash=dummy_hash, is_active=False))
            elif mode == "check":
                assert marker is not None and not marker.is_active, "Persistence marker missing"
                await session.delete(marker)
    finally:
        await engine.dispose()


def main():
    settings = get_settings()
    assert request("/health/ready")["database"] == "connected"
    assert "/api/v1/auth/tokens" in request("/openapi.json")["paths"]
    request("/api/v1/users/me", expected=401)
    request("/api/v1/users/me", token="invalid-token", expected=401)
    request("/api/v1/auth/tokens", {"username": "admin", "password": "wrong"}, expected=401)
    request("/api/v1/auth/tokens", {"username": "", "password": ""}, expected=422)
    for username, password, role in [
        ("admin", settings.demo_admin_password.get_secret_value(), "admin"),
        ("viewer", settings.demo_viewer_password.get_secret_value(), "viewer"),
    ]:
        token = request("/api/v1/auth/tokens", {"username": username, "password": password})["access_token"]
        user = request("/api/v1/users/me", token=token)
        assert user["username"] == username and user["role"] == role
        assert "password_hash" not in user
        request("/api/v1/users", token=token, expected=200 if role == "admin" else 403)
    with urlopen("http://frontend:8501/_stcore/health", timeout=10) as response:
        assert response.status == 200
    if len(sys.argv) > 1:
        asyncio.run(persistence(sys.argv[1]))
    print("PASS: readiness, OpenAPI, JWT, validation, RBAC, frontend health, persistence mode="
          + (sys.argv[1] if len(sys.argv) > 1 else "none"))


if __name__ == "__main__":
    main()
