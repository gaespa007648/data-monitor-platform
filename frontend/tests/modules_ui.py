"""AppTest exercises page functions against the real API (not browser rendering)."""
import os
import sys
from pathlib import Path

import httpx
from streamlit.testing.v1 import AppTest

sys.path.insert(0, str(Path.cwd()))

base = os.getenv("API_BASE_URL", "http://api:8000")
username = os.environ["TEST_UI_USERNAME"]
password = "UiTestPassword123!"
definitions = Path("app.py").read_text(encoding="utf-8").rsplit('if "token" not in st.session_state:', 1)[0]


def page(name, token=None, user=None):
    app = AppTest.from_string(definitions + "\n" + name + "()", default_timeout=20)
    if token:
        app.session_state["token"] = token
        app.session_state["user"] = user
    app.run()
    assert not app.exception
    return app


def widget(app, kind, label):
    return next(item for item in getattr(app, kind) if item.label == label)


app = page("registration_page")
app.text_input[0].set_value(username)
app.text_input[1].set_value(password)
app.text_input[2].set_value(password)
app.button[0].click().run()
assert not app.exception and app.success
with httpx.Client(base_url=base, timeout=20) as client:
    response = client.post("/api/v1/auth/tokens", json={"username": username, "password": password})
    response.raise_for_status()
    token = response.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    user = client.get("/api/v1/users/me").json()
    assert user["role"] == "user"
    app = page("create_page", token, user)
    widget(app, "text_input", "標題").set_value("UI record")
    widget(app, "text_input", "數值（最多 6 位小數）").set_value("21.500001")
    widget(app, "button", "建立資料").click().run()
    assert not app.exception and app.success
    rows = client.get("/api/v1/records", params={"owner_id": user["id"]}).json()["items"]
    assert len(rows) == 1 and rows[0]["value"] == "21.500001"
    app = page("records_page", token, user)
    widget(app, "selectbox", "選擇本頁可管理資料").set_value(rows[0]["id"]).run()
    widget(app, "text_input", "標題").set_value("UI updated")
    widget(app, "button", "儲存變更").click().run()
    assert not app.exception and app.success
    assert client.get(f"/api/v1/records/{rows[0]['id']}").json()["title"] == "UI updated"
    widget(app, "checkbox", "確認永久刪除此資料").check().run()
    widget(app, "button", "刪除資料").click().run()
    assert not app.exception and app.success
    assert client.get("/api/v1/records", params={"owner_id": user["id"]}).json()["total"] == 0
    app = page("import_page", token, user)
    assert not app.exception
print("PASS: Streamlit registration, User create/read/update/delete, import page rendering")
