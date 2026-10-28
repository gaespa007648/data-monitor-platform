"""Exercise the dataset pages against the running API with Streamlit AppTest."""
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
password = "DatasetUiTest123!"
definitions = Path("app.py").read_text(encoding="utf-8").rsplit('if "token" not in st.session_state:', 1)[0]
fields = [
    {"name": "sepal_length", "data_type": "decimal", "required": True, "nullable": False},
    {"name": "sepal_width", "data_type": "decimal", "required": True, "nullable": False},
    {"name": "petal_length", "data_type": "decimal", "required": True, "nullable": False},
    {"name": "petal_width", "data_type": "decimal", "required": True, "nullable": False},
    {"name": "species", "data_type": "string", "required": True, "nullable": False},
]


def widget(app, kind, label):
    return next(element for element in getattr(app, kind) if element.label == label)


with httpx.Client(base_url=base, timeout=30) as client:
    response = client.post("/api/v1/users", json={"username": username, "password": password})
    response.raise_for_status()
    response = client.post("/api/v1/auth/tokens", json={"username": username, "password": password})
    response.raise_for_status()
    token = response.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    user = client.get("/api/v1/users/me").json()
    observed = []
    navigation = st.navigation

    def capture(pages, *args, **kwargs):
        observed.extend(page.title for page in pages)
        return navigation(pages, *args, **kwargs)

    full_app = AppTest.from_file("app.py", default_timeout=30)
    full_app.session_state["token"], full_app.session_state["user"] = token, user
    with patch("streamlit.navigation", side_effect=capture):
        full_app.run()
    assert not full_app.exception
    assert observed == ["服務概況", "建立數據集", "建立資料", "批次匯入", "資料分析"]

    def page(name, editor=None):
        app = AppTest.from_string(definitions + "\n" + name + "()", default_timeout=30)
        app.session_state["token"], app.session_state["user"] = token, user
        if editor is None:
            app.run()
        else:
            with patch("streamlit.data_editor", return_value=editor):
                app.run()
        assert not app.exception, app.exception
        return app

    app = page("datasets_page", fields)
    widget(app, "text_input", "數據集名稱").set_value("iris_" + username)
    with patch("streamlit.data_editor", return_value=fields):
        widget(app, "button", "建立數據集").click().run()
    assert not app.exception and app.success
    dataset = next(item for item in client.get("/api/v1/datasets").json()["items"]
                   if item["name"] == "iris_" + username)
    assert len(dataset["fields"]) == 5
    dataset_id = dataset["id"]

    widget(app, "radio", "數據集操作").set_value("管理數據集")
    with patch("streamlit.data_editor", return_value=[{"name": "notes", "data_type": "未指定", "required": False, "nullable": True}]):
        app.run()
        widget(app, "selectbox", "選擇數據集").set_value(dataset_id).run()
        widget(app, "button", "新增欄位").click().run()
    assert not app.exception and app.success
    current = client.get(f"/api/v1/datasets/{dataset_id}").json()
    assert current["schema_version"] == 2
    assert current["fields"][:5] == fields
    assert current["fields"][5]["name"] == "notes"

    app = page("create_page")
    widget(app, "selectbox", "選擇數據集").set_value(dataset_id).run()
    for name, value in [("sepal_length", "5.1"), ("sepal_width", "3.5"),
                        ("petal_length", "1.4"), ("petal_width", "0.2"),
                        ("species", "setosa")]:
        widget(app, "text_input", name).set_value(value)
    widget(app, "button", "建立").click().run()
    assert not app.exception and app.success
    assert client.get(f"/api/v1/datasets/{dataset_id}/records").json()["total"] == 1

    app = page("import_page")
    widget(app, "selectbox", "選擇數據集").set_value(dataset_id).run()
    widget(app, "button", "產生範本資料").click().run()
    assert not app.exception
    sample_key = f"dataset_sample_{dataset_id}_2_csv"
    content = app.session_state[sample_key]
    assert b"species" in content
    upload = SimpleNamespace(name="iris.csv", getvalue=lambda: content)
    with patch("streamlit.file_uploader", return_value=upload):
        app.run()
        widget(app, "button", "預檢檔案").click().run()
        assert not app.exception and app.session_state["dataset_import_valid"]
        widget(app, "button", "確認匯入").click().run()
        assert not app.exception and app.session_state["dataset_import_done"]
    assert client.get(f"/api/v1/datasets/{dataset_id}/records").json()["total"] == 2

    app = page("analysis_page")
    widget(app, "selectbox", "選擇數據集").set_value(dataset_id).run()
    assert not app.exception
    assert widget(app, "number_input", "資料頁碼")
    assert not any(element.label == "篩選條件" for element in app.selectbox)

print("PASS: dataset create, append-only fields, manual write, generated CSV import, analysis page")
