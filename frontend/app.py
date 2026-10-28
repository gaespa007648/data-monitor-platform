import json
import os

import httpx
import streamlit as st

from auth_storage import browser_token
from dataset_ui import render_dataset_page, render_manage_records_page, render_analysis_page
from monitor_ui import render_monitor_page, stop_generator
from system_admin_ui import render_system_admin_page
from ui_heading import page_title, section

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
            st.switch_page(LOGIN_PAGE)
        except httpx.HTTPError as exc:
            show_error(exc)


def overview_page():
    page_title("產品簡介")
    st.write("即時資料分析與監控系統讓您建立具自訂欄位的數據集、匯入與分析資料，並追蹤新寫入的資料。從左側選單進入各功能。")

    section("系統管理")
    st.write("Admin 可在使用者列表編輯帳號角色，查看 FastAPI 與資料庫狀態，以及查詢操作日誌。")
    st.markdown("1. 從左側開啟「系統管理」，在「使用者列表」修改角色後按「儲存角色變更」。\n"
                "2. 向下查看「系統狀態監控」、「資料庫狀態監控」與「系統日誌」；列表與日誌可在底部切換頁面。")
    st.caption("此頁僅供 Admin 查看與操作。")

    section("建立數據集")
    st.write("建立數據集時可定義 Schema，包括欄位名稱、資料型別、必填、允許 null 與唯一值規則；既有數據集只能新增欄位。")
    st.markdown("1. 開啟「建立數據集」，輸入名稱並設定欄位，按「建立數據集」。\n"
                "2. 要擴充 Schema，將「數據集操作」切換為「編輯數據集」，選取數據集後新增欄位。")

    section("管理數據集")
    st.write("選取數據集後，可檢視、排序、分頁查看其中的資料；有權限的使用者也能新增或匯入資料。")
    st.markdown("1. 開啟「管理數據集」並選擇數據集，在「數據檢視」查看資料。\n"
                "2. 在下方「匯入資料」選擇「手動新增」填寫單筆資料，或選擇「批次匯入」上傳 CSV／JSON，預檢後確認匯入。")

    section("資料分析")
    st.write("可依數據集欄位篩選資料，對數值、類別或時間欄位產生統計與圖表。")
    st.markdown("開啟「資料分析」，選擇數據集，在「篩選器」設定條件，再於「統計分析」指定分析欄位。")

    section("即時監控")
    st.write("可建立監控設定，觀察數據集各時間區間的新寫入筆數與異常資料；模擬生成器可每秒產生測試資料。")
    st.markdown("1. 開啟「即時監控」，在「新增即時監控」選擇數據集、圖表與告警條件，儲存或建立並啟用。\n"
                "2. 在「查看即時監控」選取設定查看圖表與告警；選取數據集後可展開「模擬資料生成器」並按「開始」或「停止」。")


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
LOGIN_PAGE = st.Page(login_page, title="登入")
REGISTRATION_PAGE = st.Page(registration_page, title="註冊")


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
    st.navigation([LOGIN_PAGE, REGISTRATION_PAGE]).run()
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
    pages = [st.Page(overview_page, title="產品簡介")]
    pages.append(st.Page(users_page, title="系統管理"))
    pages.append(st.Page(datasets_page, title="建立數據集"))
    pages.append(MANAGE_DATASET_PAGE)
    pages.append(st.Page(analysis_page, title="資料分析"))
    pages.append(st.Page(monitor_page, title="即時監控"))
    st.navigation(pages).run()
