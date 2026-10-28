"""Admin-only user list, role editing and page navigation."""
from math import ceil

import httpx
import streamlit as st

from ui_heading import page_title, section


PAGE_SIZE = 20
ROLES = ("admin", "user", "viewer")


def byte_size(value):
    if value is None:
        return "無資料"
    for unit in ("B", "KiB", "MiB", "GiB"):
        if abs(value) < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}"
        value /= 1024


def render_operations(request, show_error):
    section("系統狀態監控")
    for title, path, key in (("FastAPI", "/health/live", "status"),
                             ("MariaDB", "/health/ready", "database")):
        try:
            result = request("GET", path)
            healthy = result.get(key) in {"ok", "connected"}
            st.write(f"{title}：{'正常' if healthy else '異常'}")
        except httpx.HTTPError:
            st.error(f"{title}：無法連線或檢查失敗")

    section("資料庫狀態監控")
    try:
        metrics = request("GET", "/api/v1/system/database")
        columns = st.columns(4)
        columns[0].metric("目前連線", f"{metrics['connections']} / {metrics['max_connections']}")
        columns[1].metric("執行中查詢", metrics["running_queries"])
        columns[2].metric("慢查詢累計", metrics["slow_queries_total"])
        hit = metrics["buffer_pool_hit_ratio"]
        columns[3].metric("InnoDB 快取命中率", f"{hit:.1%}" if hit is not None else "無資料")
        columns = st.columns(3)
        cpu = metrics["cpu_seconds_total"]
        columns[0].metric("MariaDB 累計 CPU 時間", f"{cpu:.1f} 秒" if cpu is not None else "無資料")
        columns[1].metric("InnoDB 快取資料量", byte_size(metrics["buffer_pool_used_bytes"]))
        columns[2].metric("資料與索引佔用量", byte_size(metrics["data_bytes"] + metrics["index_bytes"]))
        st.caption(f"資料 {byte_size(metrics['data_bytes'])} · 索引 {byte_size(metrics['index_bytes'])}"
                   f" · 快取容量 {byte_size(metrics['buffer_pool_size_bytes'])}"
                   f" · 峰值連線 {metrics['peak_connections']} · 運行 {metrics['uptime_seconds']} 秒")
        st.caption("CPU 為 MariaDB 累計使用時間，非即時使用率；磁碟為資料表估計佔用量，非剩餘空間。")
    except httpx.HTTPError as exc:
        show_error(exc)

    section("系統日誌")
    page = st.session_state.setdefault("system_audit_page", 1)
    try:
        result = request("GET", "/api/v1/system/audit-logs", params={"page": page, "page_size": PAGE_SIZE})
    except httpx.HTTPError as exc:
        show_error(exc)
        return
    total_pages = max(1, ceil(result["total"] / PAGE_SIZE))
    if page > total_pages:
        st.session_state["system_audit_page"] = total_pages
        st.rerun()
    if result["items"]:
        st.dataframe([{"時間 (UTC)": row["created_at"], "帳號": row["username"],
                       "操作": row["action"], "資源 ID": row["resource_id"], "說明": row["details"]}
                      for row in result["items"]], hide_index=True, use_container_width=True)
    else:
        st.info("目前沒有操作紀錄。")
    st.caption(f"共 {result['total']} 筆 · 第 {page}／{total_pages} 頁。記錄成功寫入的操作；瀏覽與登入不列入。")
    render_pagination(page, total_pages, "system_audit_page")


def render_pagination(page, total_pages, state_key):
    left, middle, right = st.columns([1, 2, 1])
    if left.button("上一頁", key=f"{state_key}_previous", disabled=page == 1):
        st.session_state[state_key] = page - 1
        st.rerun()
    middle.markdown(f"<div style='text-align:center'>{page} / {total_pages}</div>",
                    unsafe_allow_html=True)
    if right.button("下一頁", key=f"{state_key}_next", disabled=page == total_pages):
        st.session_state[state_key] = page + 1
        st.rerun()


def render_system_admin_page(request, show_error, current_user):
    page_title("系統管理")
    if current_user["role"] != "admin":
        st.warning("無權限查看。此頁僅供 Admin 使用。")
        return

    section("使用者列表")
    if st.session_state.pop("system_admin_saved", False):
        st.success("使用者角色已更新。")
    page = st.session_state.setdefault("system_admin_page", 1)
    try:
        result = request("GET", "/api/v1/users", params={"page": page, "page_size": PAGE_SIZE})
    except httpx.HTTPError as exc:
        show_error(exc)
        return
    total_pages = max(1, ceil(result["total"] / PAGE_SIZE))
    if page > total_pages:
        st.session_state["system_admin_page"] = total_pages
        st.rerun()

    rows = [{"id": item["id"], "username": item["username"],
             "role": item["role"], "is_active": item["is_active"]}
            for item in result["items"]]
    if not rows:
        st.info("目前沒有使用者。")
    else:
        edited = st.data_editor(rows, key=f"system_roles_{page}_{st.session_state.get('system_admin_revision', 0)}",
            num_rows="fixed", hide_index=True, use_container_width=True,
            disabled=["id", "username", "is_active"],
            column_config={"id": st.column_config.NumberColumn("ID"),
                           "username": st.column_config.TextColumn("帳號"),
                           "role": st.column_config.SelectboxColumn("角色", options=list(ROLES), required=True),
                           "is_active": st.column_config.CheckboxColumn("啟用")})
        if hasattr(edited, "to_dict"):
            edited = edited.to_dict("records")
        changes = [(old["id"], new["role"]) for old, new in zip(rows, edited)
                   if old["role"] != new["role"]]
        if st.button("儲存角色變更", disabled=not changes):
            # A self-demotion is sent last so preceding changes can finish.
            changes.sort(key=lambda item: item[0] == current_user["id"])
            try:
                for user_id, role in changes:
                    request("PATCH", f"/api/v1/users/{user_id}/role", json={"role": role})
                st.session_state["system_admin_revision"] = st.session_state.get("system_admin_revision", 0) + 1
                st.session_state["system_admin_saved"] = True
                st.rerun()
            except httpx.HTTPError as exc:
                show_error(exc)

    st.caption(f"共 {result['total']} 位使用者 · 第 {page}／{total_pages} 頁")
    render_pagination(page, total_pages, "system_admin_page")
    render_operations(request, show_error)
