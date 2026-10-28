"""Admin-only user list, role editing and page navigation."""
from math import ceil

import httpx
import streamlit as st


PAGE_SIZE = 20
ROLES = ("admin", "user", "viewer")


def render_system_admin_page(request, show_error, current_user):
    st.subheader("系統管理")
    if current_user["role"] != "admin":
        st.warning("無權限查看。此頁僅供 Admin 使用。")
        return

    st.markdown("### 使用者列表")
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
    previous, choose, following = st.columns([1, 2, 1])
    if previous.button("◀ 上一頁", disabled=page == 1):
        st.session_state["system_admin_page"] = page - 1
        st.rerun()
    chosen = choose.selectbox("選擇頁面", range(1, total_pages + 1), index=page - 1,
                              key=f"system_page_choice_{page}")
    if chosen != page:
        st.session_state["system_admin_page"] = chosen
        st.rerun()
    if following.button("下一頁 ▶", disabled=page == total_pages):
        st.session_state["system_admin_page"] = page + 1
        st.rerun()
