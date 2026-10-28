"""Execute Streamlit pages with AppTest against the real FastAPI service."""
import os
import sys
from pathlib import Path
from unittest.mock import patch

import streamlit as st
from streamlit.testing.v1 import AppTest

sys.path.insert(0, str(Path.cwd()))


def login(username, password):
    app = AppTest.from_file("app.py", default_timeout=15).run()
    assert not app.exception
    app.text_input[0].set_value(username)
    app.text_input[1].set_value(password)
    app.button[0].click().run()
    assert not app.exception
    return app


invalid = login("admin", "wrong-password")
assert invalid.error and "帳號或密碼錯誤" in invalid.error[0].value

for username, password in [
    ("admin", os.environ["TEST_ADMIN_PASSWORD"]),
    ("viewer", os.environ["TEST_VIEWER_PASSWORD"]),
]:
    app = login(username, password)
    assert app.session_state["user"]["username"] == username
    assert app.success and "MariaDB connected" in app.success[0].value
    observed = []
    original_navigation = st.navigation

    def capture(pages, *args, **kwargs):
        observed.extend(page.title for page in pages)
        return original_navigation(pages, *args, **kwargs)

    with patch("streamlit.navigation", side_effect=capture):
        app.run()
    expected = (["服務概況", "使用者清單", "建立數據集", "管理數據集", "建立資料", "批次匯入", "資料分析", "即時監控"]
                if username == "admin" else ["服務概況", "建立數據集", "管理數據集", "資料分析", "即時監控"])
    assert observed == expected, observed
    app.sidebar.button[0].click().run()
    assert not app.exception
    assert len(app.text_input) == 2

print("PASS: Streamlit invalid login, admin/viewer login, API/DB overview, logout")
