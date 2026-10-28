"""Admin-only user list, role editing and page navigation."""
from math import ceil

import httpx
import streamlit as st


PAGE_SIZE = 20
ROLES = ("admin", "user", "viewer")


def page_items(current, total):
    """Show six nearby page numbers, with the first and last page as anchors."""
    if total <= 8:
        return list(range(1, total + 1))
    start = max(1, min(current - 2, total - 5))
    end = start + 5
    pages = list(range(start, end + 1))
    if start > 1:
        pages = [1, *(["…"] if start > 2 else []), *pages]
    if end < total:
        pages.extend(["…"] if end < total - 1 else [])
        pages.append(total)
    return pages


def render_pagination(page, total_pages):
    st.markdown("""<style>
    .st-key-system_pagination button {min-width:2.25rem; height:2.25rem; border-radius:.6rem; padding:0;}
    .st-key-system_pagination button[kind="primary"] {background:#ff6b00; border-color:#ff6b00; color:white;}
    .st-key-system_pagination button[kind="primary"]:hover {background:#e65f00; border-color:#e65f00;}
    </style>""", unsafe_allow_html=True)
    items = page_items(page, total_pages)
    with st.container(key="system_pagination"):
        columns = st.columns([0.6] * (len(items) + 2) + [6], gap="small")
        if columns[0].button("‹", key="system_prev", help="上一頁", disabled=page == 1,
                             use_container_width=True):
            st.session_state["system_admin_page"] = page - 1
            st.rerun()
        for column, item in zip(columns[1:], items):
            if item == "…":
                column.markdown("<div style='text-align:center;padding-top:.3rem'>…</div>",
                                unsafe_allow_html=True)
            elif column.button(str(item), key=f"system_page_{item}",
                               type="primary" if item == page else "secondary",
                               use_container_width=True) and item != page:
                st.session_state["system_admin_page"] = item
                st.rerun()
        if columns[len(items) + 1].button("›", key="system_next", help="下一頁",
                                          disabled=page == total_pages, use_container_width=True):
            st.session_state["system_admin_page"] = page + 1
            st.rerun()


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
    render_pagination(page, total_pages)
