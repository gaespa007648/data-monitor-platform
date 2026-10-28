"""Check saved-token restoration and invalid-token clearing against FastAPI."""
import os
import sys
from pathlib import Path
from unittest.mock import patch

import httpx
from streamlit.testing.v1 import AppTest

sys.path.insert(0, str(Path.cwd()))

username = os.environ["TEST_UI_USERNAME"]
password = "AuthRestoreTest123!"
base = os.environ.get("API_BASE_URL", "http://api:8000")
with httpx.Client(base_url=base, timeout=20) as client:
    response = client.post("/api/v1/users", json={"username": username, "password": password})
    response.raise_for_status()
    response = client.post("/api/v1/auth/tokens", json={"username": username, "password": password})
    response.raise_for_status()
    token = response.json()["access_token"]


def run_with_saved_token(saved, logout=False):
    actions = []

    def storage(action, token=None):
        actions.append(action)
        return {"action": action, "token": saved if action == "read" else None}

    with patch("auth_storage.browser_token", side_effect=storage):
        app = AppTest.from_file("app.py", default_timeout=20).run()
        if logout:
            app.sidebar.button[0].click().run()
    assert not app.exception, app.exception
    return app, actions


restored, actions = run_with_saved_token(token)
assert restored.session_state["user"]["username"] == username
assert "read" in actions and "save" in actions, actions

logged_out, actions = run_with_saved_token(token, logout=True)
assert "token" not in logged_out.session_state
assert "clear" in actions, actions

invalid, actions = run_with_saved_token("invalid-token")
assert "token" not in invalid.session_state
assert "clear" in actions, actions
assert len(invalid.text_input) == 2

print("PASS: valid browser token restores login; logout and invalid token clear storage")
