import os

import httpx
import streamlit as st

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
st.set_page_config(page_title="即時資料分析與監控系統", page_icon="📊", layout="wide")
st.title("即時資料分析與監控系統")
st.caption("初始化版本：服務連線、JWT 登入與角色權限驗證")


def request(method, path, **kwargs):
    headers = {"Authorization": f"Bearer {st.session_state['token']}"} if "token" in st.session_state else {}
    response = httpx.request(method, f"{API_BASE_URL}{path}", headers=headers, timeout=10, **kwargs)
    if response.status_code == 401 and "token" in st.session_state:
        st.session_state.pop("token", None)
        st.session_state.pop("user", None)
        st.rerun()
    response.raise_for_status()
    return response.json()


def login_page():
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
        except httpx.HTTPStatusError as exc:
            st.session_state.pop("token", None)
            st.session_state.pop("user", None)
            st.error("帳號或密碼錯誤" if exc.response.status_code == 401 else "登入失敗，請稍後再試")
        except httpx.RequestError:
            st.session_state.pop("token", None)
            st.session_state.pop("user", None)
            st.error("無法連接後端服務")


def overview_page():
    st.subheader("服務概況")
    try:
        health = request("GET", "/health/ready")
        st.success(f"FastAPI 正常 · MariaDB {health['database']}")
        st.json(st.session_state["user"])
        st.info("即時資料接收、批次分析與 WebSocket 圖表將於後續階段建置。")
    except httpx.HTTPError:
        st.error("後端或資料庫目前無法使用")


def users_page():
    st.subheader("使用者清單")
    try:
        st.dataframe(request("GET", "/api/v1/users"), use_container_width=True)
    except httpx.HTTPStatusError as exc:
        st.error("沒有管理員權限" if exc.response.status_code == 403 else "無法取得使用者")
    except httpx.RequestError:
        st.error("無法連接後端服務")


if "token" not in st.session_state:
    st.navigation([st.Page(login_page, title="登入")]).run()
else:
    st.sidebar.write(f"目前使用者：{st.session_state['user']['username']}")
    if st.sidebar.button("登出"):
        st.session_state.clear()
        st.rerun()
    pages = [st.Page(overview_page, title="服務概況")]
    if st.session_state["user"]["role"] == "admin":
        pages.append(st.Page(users_page, title="使用者管理"))
    st.navigation(pages).run()
