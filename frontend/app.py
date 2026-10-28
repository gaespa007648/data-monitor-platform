import hashlib
import json
import os
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import streamlit as st

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
st.set_page_config(page_title="即時資料分析與監控系統", page_icon="📊", layout="wide")
st.title("即時資料分析與監控系統")
st.caption("使用者與資料管理")


def request(method, path, **kwargs):
    headers = kwargs.pop("headers", {})
    if "token" in st.session_state:
        headers["Authorization"] = f"Bearer {st.session_state['token']}"
    response = httpx.request(method, f"{API_BASE_URL}{path}", headers=headers, timeout=30, **kwargs)
    if response.status_code == 401 and "token" in st.session_state:
        st.session_state.clear()
        st.rerun()
    response.raise_for_status()
    return response.json() if response.content else None


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
    st.subheader("註冊使用者")
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
    st.subheader("服務概況")
    try:
        health = request("GET", "/health/ready")
        st.success(f"FastAPI 正常 · MariaDB {health['database']}")
        st.json(st.session_state["user"])
        st.info("User 可建立與管理自己的資料，Viewer 僅可讀取，Admin 可管理全部資料。")
    except httpx.HTTPError as exc:
        show_error(exc)


def users_page():
    st.subheader("使用者清單")
    page = st.number_input("使用者頁碼", min_value=1, step=1)
    try:
        st.dataframe(request("GET", "/api/v1/users", params={"page": page, "page_size": 50}), use_container_width=True)
    except httpx.HTTPError as exc:
        show_error(exc)


def category_map():
    return {row["id"]: row["name"] for row in request("GET", "/api/v1/categories")}


def timestamp_default():
    return datetime.now(ZoneInfo("Asia/Taipei")).replace(microsecond=0).isoformat()


def record_form(prefix, categories, row=None):
    with st.form(prefix):
        title = st.text_input("標題", value=row["title"] if row else "", max_chars=200)
        value = st.text_input("數值（最多 6 位小數）", value=str(row["value"]) if row else "0")
        options = list(categories)
        index = options.index(row["category_id"]) if row and row["category_id"] in options else 0
        category_id = st.selectbox("分類", options, index=index, format_func=lambda key: categories[key])
        timestamp = st.text_input("時間（ISO 8601，需含時區）", value=row["timestamp"] if row else timestamp_default())
        submit = st.form_submit_button("儲存變更" if row else "建立資料")
    if submit:
        payload = {"title": title, "value": value, "category_id": category_id, "timestamp": timestamp}
        try:
            if row:
                payload["version"] = row["version"]
                request("PATCH", f"/api/v1/records/{row['id']}", json=payload)
            else:
                request("POST", "/api/v1/records", json=payload)
            st.session_state["notice"] = "資料已儲存"
            st.rerun()
        except httpx.HTTPError as exc:
            show_error(exc)


def records_page():
    st.subheader("資料列表")
    if "notice" in st.session_state:
        st.success(st.session_state.pop("notice"))
    try:
        categories = category_map()
        with st.expander("篩選與排序", expanded=True):
            category = st.selectbox("分類篩選", [None, *categories], format_func=lambda key: "全部" if key is None else categories[key])
            owner = st.number_input("建立者 ID（0 表示全部）", min_value=0, step=1)
            source = st.selectbox("來源", ["全部", "manual", "import"])
            start = st.text_input("起始時間（含，ISO 8601 含時區）")
            end = st.text_input("結束時間（不含，ISO 8601 含時區）")
            sort_by = st.selectbox("排序欄位", ["timestamp", "value", "title", "id", "created_at"])
            order = st.selectbox("排序方向", ["desc", "asc"])
            page = st.number_input("資料頁碼", min_value=1, step=1)
            page_size = st.selectbox("每頁筆數", [20, 50, 100])
        params = {"page": page, "page_size": page_size, "sort_by": sort_by, "order": order}
        for key, item in {"category_id": category, "owner_id": owner or None,
                          "source": None if source == "全部" else source, "start": start, "end": end}.items():
            if item is not None and item != "":
                params[key] = item
        result = request("GET", "/api/v1/records", params=params)
        st.caption(f"共 {result['total']} 筆；時間顯示為 UTC。")
        st.dataframe(result["items"], use_container_width=True)
        user = st.session_state["user"]
        editable = {row["id"]: row for row in result["items"]
                    if user["role"] == "admin" or (user["role"] == "user" and row["owner_id"] == user["id"])}
        if editable:
            selected = st.selectbox("選擇本頁可管理資料", list(editable))
            row = editable[selected]
            st.caption(f"記錄 ID {selected}，版本 {row['version']}")
            record_form(f"edit_{selected}_{row['version']}", categories, row)
            confirmed = st.checkbox("確認永久刪除此資料", key=f"confirm_{selected}_{row['version']}")
            if st.button("刪除資料", disabled=not confirmed):
                request("DELETE", f"/api/v1/records/{selected}", params={"version": row["version"]})
                st.session_state["notice"] = "資料已刪除"
                st.rerun()
    except httpx.HTTPError as exc:
        show_error(exc)


def create_page():
    st.subheader("建立資料")
    if "notice" in st.session_state:
        st.success(st.session_state.pop("notice"))
    try:
        categories = category_map()
        if not categories:
            st.info("請先由 Admin 建立分類")
            return
        record_form("create_record", categories)
    except httpx.HTTPError as exc:
        show_error(exc)


def import_page():
    st.subheader("批次匯入 CSV／JSON")
    st.caption("UTF-8、上限 2 MiB／1000 筆；全部通過才寫入。請先預檢，再確認匯入。")
    st.code('title,value,category_id,timestamp\n範例,12.5,1,2026-10-28T10:00:00+08:00', language="text")
    st.caption("JSON 使用相同欄位的物件陣列；category_id 請參考分類清單。")
    try:
        st.dataframe(request("GET", "/api/v1/categories"))
        file = st.file_uploader("選擇檔案", type=["csv", "json"])
        if file is None:
            return
        content = file.getvalue()
        if len(content) > 2 * 1024 * 1024:
            st.error("檔案不可超過 2 MiB")
            return
        digest = hashlib.sha256(content + file.name.encode()).hexdigest()
        if st.session_state.get("import_digest") != digest:
            st.session_state.update(import_digest=digest, import_key=uuid4().hex, import_valid=False, import_done=False)
        if st.button("預檢檔案"):
            result = request("POST", "/api/v1/record-imports", params={"preview": True},
                headers={"Idempotency-Key": st.session_state["import_key"]}, files={"file": (file.name, content)})
            st.session_state["import_valid"] = True
            st.success(f"預檢成功，共 {result['row_count']} 筆")
        if st.button("確認匯入", disabled=not st.session_state["import_valid"] or st.session_state["import_done"]):
            result = request("POST", "/api/v1/record-imports",
                headers={"Idempotency-Key": st.session_state["import_key"]}, files={"file": (file.name, content)})
            st.session_state["import_done"] = True
            st.success(f"匯入完成：批次 {result['batch_id']}，{result['row_count']} 筆")
    except httpx.HTTPError as exc:
        show_error(exc)


def categories_page():
    st.subheader("分類管理")
    try:
        st.dataframe(request("GET", "/api/v1/categories"))
        with st.form("category"):
            name = st.text_input("新分類名稱", max_chars=64)
            submitted = st.form_submit_button("新增分類")
        if submitted:
            request("POST", "/api/v1/categories", json={"name": name})
            st.rerun()
    except httpx.HTTPError as exc:
        show_error(exc)


if "token" not in st.session_state:
    st.navigation([st.Page(login_page, title="登入"), st.Page(registration_page, title="註冊")]).run()
else:
    try:
        st.session_state["user"] = request("GET", "/api/v1/users/me")
    except httpx.HTTPError as exc:
        show_error(exc)
        st.stop()
    user = st.session_state["user"]
    st.sidebar.write(f"目前使用者：{user['username']} · {user['role'].title()}")
    if st.sidebar.button("登出"):
        st.session_state.clear()
        st.rerun()
    pages = [st.Page(overview_page, title="服務概況"), st.Page(records_page, title="資料列表")]
    if user["role"] in {"admin", "user"}:
        pages += [st.Page(create_page, title="建立資料"), st.Page(import_page, title="批次匯入")]
    if user["role"] == "admin":
        pages += [st.Page(users_page, title="使用者清單"), st.Page(categories_page, title="分類管理")]
    st.navigation(pages).run()
