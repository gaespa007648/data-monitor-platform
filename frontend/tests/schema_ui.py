"""AppTest with a bounded in-memory upload; exercises real schema/import APIs."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import streamlit as st
from streamlit.testing.v1 import AppTest

sys.path.insert(0, str(Path.cwd()))

base = os.getenv("API_BASE_URL", "http://api:8000")
username = os.environ["TEST_UI_USERNAME"]
definitions = Path("app.py").read_text(encoding="utf-8").rsplit('if "token" not in st.session_state:', 1)[0]


def widget(app, kind, label):
    return next(element for element in getattr(app, kind) if element.label == label)


with httpx.Client(base_url=base, timeout=30) as client:
    response = client.post("/api/v1/users", json={"username": username, "password": "SchemaUiTest123!"})
    response.raise_for_status()
    response = client.post("/api/v1/auth/tokens", json={"username": username, "password": "SchemaUiTest123!"})
    response.raise_for_status()
    token = response.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    user = client.get("/api/v1/users/me").json()
    observed = []
    original_navigation = st.navigation
    def navigation(pages, *args, **kwargs):
        observed.extend(p.title for p in pages)
        return original_navigation(pages, *args, **kwargs)
    full_app = AppTest.from_file("app.py", default_timeout=20)
    full_app.session_state["token"], full_app.session_state["user"] = token, user
    with patch("streamlit.navigation", side_effect=navigation):
        full_app.run()
    assert not full_app.exception and "匯入 Schema 範本" in observed
    def page(name):
        app = AppTest.from_string(definitions+"\n"+name+"()", default_timeout=20)
        app.session_state["token"], app.session_state["user"] = token, user
        app.run()
        assert not app.exception
        return app
    app = page("schema_page")
    widget(app, "text_input", "範本名稱").set_value(username)
    for target, source in zip(["title", "value", "category_id", "timestamp"], ["名稱", "量測值", "分類編號", "量測時間"]):
        widget(app, "text_input", f"{target} 的來源欄名").set_value(source)
    widget(app, "selectbox", "value 的 data_type（選填）").set_value("decimal").run()
    widget(app, "checkbox", "value 設定數值精度").check().run()
    widget(app, "selectbox", "value 的 data_type（選填）").set_value("未指定").run()
    widget(app, "button", "儲存範本草稿").click().run()
    assert not app.exception and app.success
    template = next(t for t in client.get("/api/v1/import-schemas").json() if t["name"] == username)
    version = client.get(f"/api/v1/import-schemas/{template['id']}/versions").json()[0]
    assert all(f["data_type"] is None and f["max_digits"] is None for f in version["rules"]["fields"])
    widget(app, "radio", "範本操作").set_value("管理範本").run()
    widget(app, "selectbox", "選擇範本").set_value(template["id"]).run()
    widget(app, "button", "發布草稿").click().run()
    assert not app.exception and app.success
    app = page("import_page")
    widget(app, "selectbox", "選擇範本").set_value(template["id"]).run()
    widget(app, "button", "產生範本資料").click().run()
    assert not app.exception
    content = app.session_state[f"sample_{version['id']}_{version['rules_hash']}"]
    assert "名稱,量測值,分類編號,量測時間" in content.decode("utf-8-sig")
    file = SimpleNamespace(name="generated.csv", getvalue=lambda: content)
    with patch("streamlit.file_uploader", return_value=file):
        app.run()
        widget(app, "button", "預檢檔案").click().run()
        assert not app.exception and app.session_state["import_valid"]
        widget(app, "button", "確認匯入").click().run()
        assert not app.exception and app.session_state["import_done"]
        records = client.get("/api/v1/records", params={"owner_id": user["id"]}).json()
        assert records["total"] == 1
        widget(app, "selectbox", "選擇範本").set_value(next(t["id"] for t in client.get("/api/v1/import-schemas").json() if t["builtin_key"] == "default-csv")).run()
        assert not app.exception and not app.session_state["import_valid"]
        assert widget(app, "button", "確認匯入").disabled
print("PASS: UI schema definition, optional type reset, draft publish, sample generation, preview, import, schema-change invalidation")
