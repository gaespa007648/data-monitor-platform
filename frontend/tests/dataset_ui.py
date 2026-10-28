"""Exercise the dataset pages against the running API with Streamlit AppTest."""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import streamlit as st
from streamlit.testing.v1 import AppTest

sys.path.insert(0, str(Path.cwd()))
from dataset_ui import preview_rows

many_csv = b"name,value\n" + b"".join(f"row{i},{i}\n".encode() for i in range(12))
assert len(preview_rows(many_csv, "csv", True, [{"name": "name"}, {"name": "value"}])) == 5
assert preview_rows(many_csv, "csv", True, [{"name": "name"}, {"name": "value"}])[0]["name"] == "row0"
assert len(preview_rows(b"".join(f"row{i},{i}\n".encode() for i in range(12)),
                        "csv", False, [{"name": "name"}, {"name": "value"}])) == 5
assert len(preview_rows(json.dumps([{"value": i} for i in range(12)]).encode(),
                        "json", False, [])) == 5

base = os.getenv("API_BASE_URL", "http://api:8000")
username = os.environ["TEST_UI_USERNAME"]
password = "DatasetUiTest123!"
definitions = Path("app.py").read_text(encoding="utf-8").rsplit('if "token" not in st.session_state:', 1)[0]
fields = [
    {"name": "sepal_length", "remark": "花萼長度", "data_type": "decimal", "required": True, "nullable": False},
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
    assert dataset["fields"][0]["remark"] == "花萼長度"
    dataset_id = dataset["id"]

    widget(app, "radio", "數據集操作").set_value("管理數據集")
    with patch("streamlit.data_editor", return_value=[{"name": "notes", "remark": "觀察註記", "data_type": "未指定", "required": False, "nullable": True, "unique": True}]):
        app.run()
        widget(app, "selectbox", "選擇數據集").set_value(dataset_id).run()
        widget(app, "button", "新增欄位").click().run()
    assert not app.exception and app.success
    current = client.get(f"/api/v1/datasets/{dataset_id}").json()
    assert current["schema_version"] == 2
    assert current["fields"][:5] == [{**field, "remark": field.get("remark", ""), "unique": False} for field in fields]
    assert current["fields"][5]["name"] == "notes"
    assert current["fields"][5]["remark"] == "觀察註記"
    assert current["fields"][5]["unique"] is True

    app = page("create_page")
    assert not any(item.label == "數據集清單頁碼" for item in app.number_input)
    widget(app, "selectbox", "選擇數據集").set_value(dataset_id).run()
    for name, value in [("sepal_length", "5.1"), ("sepal_width", "3.5"),
                        ("petal_length", "1.4"), ("petal_width", "0.2"),
                        ("species", "setosa")]:
        widget(app, "text_input", name).set_value(value)
    widget(app, "button", "建立").click().run()
    assert not app.exception and app.success
    assert client.get(f"/api/v1/datasets/{dataset_id}/records").json()["total"] == 1

    app = page("import_page")
    assert not any(item.label == "數據集清單頁碼" for item in app.number_input)
    assert not any(item.label == "產生範本資料" for item in app.button)
    widget(app, "selectbox", "選擇數據集").set_value(dataset_id).run()
    content = (b"sepal_length,sepal_width,petal_length,petal_width,species,notes\n"
               b"5.1,3.5,1.4,0.2,setosa,observed\n")
    upload = SimpleNamespace(name="iris.csv", getvalue=lambda: content)
    with patch("streamlit.file_uploader", return_value=upload):
        app.run()
        assert widget(app, "checkbox", "排除第一列欄名").value
        widget(app, "button", "預檢檔案").click().run()
        assert not app.exception and app.session_state["dataset_import_valid"]
        widget(app, "button", "確認匯入").click().run()
        assert not app.exception and app.session_state["dataset_import_done"]
    assert client.get(f"/api/v1/datasets/{dataset_id}/records").json()["total"] == 2
    with patch("streamlit.file_uploader", return_value=upload):
        widget(app, "checkbox", "排除第一列欄名").uncheck().run()
        assert not app.exception and not app.session_state["dataset_import_valid"]
        assert widget(app, "button", "確認匯入").disabled
    no_header = SimpleNamespace(name="iris.csv", getvalue=lambda: b"4.9,3.0,1.4,0.2,setosa,second\n")
    with patch("streamlit.file_uploader", return_value=no_header):
        app.run()
        widget(app, "button", "預檢檔案").click().run()
        assert not app.exception and app.session_state["dataset_import_valid"]
        widget(app, "button", "確認匯入").click().run()
        assert not app.exception and app.session_state["dataset_import_done"]
    assert client.get(f"/api/v1/datasets/{dataset_id}/records").json()["total"] == 3

    with patch("dataset_ui.render_table", return_value=None):
        app = page("analysis_page")
        assert [element.value for element in app.subheader] == ["選擇數據集"]
        widget(app, "selectbox", "選擇數據集").set_value(dataset_id).run()
    assert not app.exception
    assert widget(app, "number_input", "頁碼")
    assert [element.value for element in app.subheader] == ["選擇數據集", "資料分析", "數據檢視"]
    assert not any(element.label == "數據集清單頁碼" for element in app.number_input)
    with patch("dataset_ui.render_table", return_value={
            "event_id": "sort-asc", "action": "sort", "dataset_id": dataset_id, "column": "sepal_length"}):
        app.run()
    assert not app.exception
    assert app.session_state[f"analysis_sort_{dataset_id}"] == {
        "column": "sepal_length", "direction": "asc"}
    with patch("dataset_ui.render_table", return_value={
            "event_id": "sort-desc", "action": "sort", "dataset_id": dataset_id, "column": "sepal_length"}):
        app.run()
    assert not app.exception
    assert app.session_state[f"analysis_sort_{dataset_id}"]["direction"] == "desc"
    victim = client.get(f"/api/v1/datasets/{dataset_id}/records").json()["items"][0]["id"]
    with patch("dataset_ui.render_table", return_value={
            "event_id": "delete-one", "action": "delete", "dataset_id": dataset_id, "record_id": victim}):
        app.run()
    assert not app.exception
    assert client.get(f"/api/v1/datasets/{dataset_id}/records").json()["total"] == 2

    for index in range(9):
        response = client.post(f"/api/v1/datasets/{dataset_id}/records", json={
            "schema_version": 2,
            "data": {"sepal_length": "5.1", "sepal_width": "3.5",
                     "petal_length": "1.4", "petal_width": "0.2", "species": "setosa",
                     "notes": f"pagination-{index}"}})
        response.raise_for_status()
    with patch("dataset_ui.render_table", return_value=None) as table:
        app.run()
    assert not app.exception
    assert len(table.call_args.args[2]) == 10
    assert widget(app, "number_input", "頁碼").max == 2
    with patch("dataset_ui.render_table", return_value=None) as table:
        widget(app, "number_input", "頁碼").set_value(2).run()
    assert not app.exception
    assert len(table.call_args.args[2]) == 1

print("PASS: dataset create, import, analysis sorting, deletion and ten-row pagination")
