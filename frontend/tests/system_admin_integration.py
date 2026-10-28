"""Check user pagination and role changes through the live API."""
import os

import httpx


def main():
    base = os.getenv("API_BASE_URL", "http://api:8000")
    username = os.environ["TEST_UI_USERNAME"]
    password = "SystemAdminCheck123!"
    admin_password = os.environ["TEST_ADMIN_PASSWORD"]
    with httpx.Client(base_url=base, timeout=30) as client:
        response = client.post("/api/v1/users", json={"username": username, "password": password})
        response.raise_for_status()
        target_id = response.json()["id"]
        user_token = client.post("/api/v1/auth/tokens", json={
            "username": username, "password": password}).json()["access_token"]
        admin_token = client.post("/api/v1/auth/tokens", json={
            "username": "admin", "password": admin_password}).json()["access_token"]
        client.headers["Authorization"] = f"Bearer {user_token}"
        assert client.get("/api/v1/users").status_code == 403
        assert client.patch(f"/api/v1/users/{target_id}/role", json={"role": "admin"}).status_code == 403
        client.headers["Authorization"] = f"Bearer {admin_token}"
        page = client.get("/api/v1/users", params={"page": 1, "page_size": 1})
        page.raise_for_status()
        result = page.json()
        assert result["page"] == 1 and result["page_size"] == 1
        assert result["total"] >= 2 and len(result["items"]) == 1
        assert client.patch(f"/api/v1/users/{target_id}/role", json={"role": "invalid"}).status_code == 422
        assert client.patch("/api/v1/users/999999999/role", json={"role": "viewer"}).status_code == 404
        updated = client.patch(f"/api/v1/users/{target_id}/role", json={"role": "admin"})
        updated.raise_for_status()
        assert updated.json()["role"] == "admin"
        client.headers["Authorization"] = f"Bearer {user_token}"
        assert client.get("/api/v1/users/me").json()["role"] == "admin"
        assert client.get("/api/v1/users").status_code == 200
        updated = client.patch(f"/api/v1/users/{target_id}/role", json={"role": "viewer"})
        updated.raise_for_status()
        assert updated.json()["role"] == "viewer"
        assert client.get("/api/v1/users").status_code == 403
    print(f"System management API passed; clean fixture {username}")


if __name__ == "__main__":
    main()
