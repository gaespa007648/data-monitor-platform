import json
import os

import httpx
import streamlit as st

from auth_storage import browser_token
from dataset_ui import render_dataset_page, render_manage_records_page, render_analysis_page
from monitor_ui import render_monitor_page, stop_generator
from system_admin_ui import render_system_admin_page
from ui_heading import page_title

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
st.set_page_config(page_title="即時資料分析與監控系統", page_icon="📊", layout="wide")


def request(method, path, **kwargs):
    raw = kwargs.pop("raw", False)
    headers = kwargs.pop("headers", {})
    if "token" in st.session_state:
        headers["Authorization"] = f"Bearer {st.session_state['token']}"
    response = httpx.request(method, f"{API_BASE_URL}{path}", headers=headers, timeout=30, **kwargs)
    if response.status_code == 401 and "token" in st.session_state:
        st.session_state.clear()
        st.session_state["clear_browser_token"] = True
        st.rerun()
    response.raise_for_status()
    return response.content if raw else (response.json() if response.content else None)


def show_error(exc):
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            detail = exc.response.json().get("detail", "請求失敗")
        except ValueError:
            detail = "請求失敗"
        st.error(f"HTTP {exc.response.status_code}：{json.dumps(detail, ensure_ascii=False) if not isinstance(detail, str) else detail}")
    else:
        st.error("無法連接後端服務，請稍後重試")


def login_page():
    page_title("登入")
    with st.form("login"):
        username = st.text_input("帳號", max_chars=64)
        password = st.text_input("密碼", type="password", max_chars=256)
        submitted = st.form_submit_button("登入")
    if submitted:
        try:
            token = request("POST", "/api/v1/auth/tokens", json={"username": username, "password": password})
            st.session_state["token"] = token["access_token"]
            st.session_state["user"] = request("GET", "/api/v1/users/me")
            st.rerun()
        except httpx.HTTPError as exc:
            st.session_state.pop("token", None)
            st.session_state.pop("user", None)
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 401:
                st.error("帳號或密碼錯誤")
            else:
                show_error(exc)


def registration_page():
    page_title("註冊使用者")
    st.caption("帳號為 3–64 個英文字母、數字或底線；密碼至少 12 字元。新帳號固定為 User。")
    with st.form("register"):
        username = st.text_input("新帳號", max_chars=64)
        password = st.text_input("新密碼", type="password", max_chars=256)
        confirmation = st.text_input("確認密碼", type="password", max_chars=256)
        submitted = st.form_submit_button("註冊")
    if submitted:
        if password != confirmation:
            st.error("兩次密碼不一致")
            return
        try:
            request("POST", "/api/v1/users", json={"username": username, "password": password})
            st.success("註冊成功，請前往登入頁登入。")
        except httpx.HTTPError as exc:
            show_error(exc)


def overview_page():
    page_title("服務概況")
    try:
        health = request("GET", "/health/ready")
        st.success(f"FastAPI 正常 · MariaDB {health['database']}")
        st.json(st.session_state["user"])
        st.info("User 可建立與管理自己的資料，Viewer 僅可讀取，Admin 可管理全部資料。")
    except httpx.HTTPError as exc:
        show_error(exc)


def users_page():
    render_system_admin_page(request, show_error, st.session_state["user"])


def datasets_page():
    render_dataset_page(request, show_error, go_manage_dataset)


def manage_page():
    render_manage_records_page(request, show_error)


def go_manage_dataset(dataset_id):
    st.session_state["manage_records_dataset"] = dataset_id
    st.switch_page(MANAGE_DATASET_PAGE)


def analysis_page():
    render_analysis_page(request, show_error)


def monitor_page():
    render_monitor_page(request, show_error, API_BASE_URL, st.session_state["token"])


MANAGE_DATASET_PAGE = st.Page(manage_page, title="管理數據集")


if st.session_state.get("clear_browser_token"):
    result = browser_token("clear")
    if isinstance(result, dict) and result.get("action") == "clear":
        st.session_state.pop("clear_browser_token", None)
elif "token" not in st.session_state:
    result = browser_token("read")
    if isinstance(result, dict) and result.get("action") == "read" and result.get("token"):
        st.session_state["token"] = result["token"]
        try:
            st.session_state["user"] = request("GET", "/api/v1/users/me")
        except httpx.HTTPError:
            st.session_state.pop("token", None)
            st.session_state.pop("user", None)
            st.session_state["clear_browser_token"] = True
        st.rerun()
if "token" not in st.session_state:
    st.navigation([st.Page(login_page, title="登入"), st.Page(registration_page, title="註冊")]).run()
else:
    browser_token("save", st.session_state["token"])
    try:
        st.session_state["user"] = request("GET", "/api/v1/users/me")
    except httpx.HTTPError as exc:
        show_error(exc)
        st.stop()
    user = st.session_state["user"]
    st.sidebar.write(f"目前使用者：{user['username']} · {user['role'].title()}")
    if st.sidebar.button("登出"):
        stop_generator()
        st.session_state.clear()
        st.session_state["clear_browser_token"] = True
        st.rerun()
    pages = [st.Page(overview_page, title="服務概況")]
    pages.append(st.Page(users_page, title="系統管理"))
    pages.append(st.Page(datasets_page, title="建立數據集"))
    pages.append(MANAGE_DATASET_PAGE)
    pages.append(st.Page(analysis_page, title="資料分析"))
    pages.append(st.Page(monitor_page, title="即時監控"))
    st.navigation(pages).run()
