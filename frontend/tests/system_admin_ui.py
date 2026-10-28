"""Check admin-only view, role editor and page controls with Streamlit AppTest."""
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


viewer_source = '''
import streamlit as st
from system_admin_ui import render_system_admin_page

def request(*args, **kwargs):
    raise AssertionError("Non-admin must not call user list API")

render_system_admin_page(request, lambda exc: st.error(str(exc)),
                         {"id": 2, "role": "viewer"})
'''
viewer = AppTest.from_string(viewer_source).run()
assert not viewer.exception
assert viewer.warning and "無權限查看" in viewer.warning[0].value
assert not viewer.dataframe

admin_source = '''
import streamlit as st
from system_admin_ui import render_system_admin_page

def request(method, path, **kwargs):
    if method == "PATCH":
        st.session_state["last_update"] = (path, kwargs["json"])
        return {"id": 1, "role": kwargs["json"]["role"]}
    page = kwargs["params"]["page"]
    items = [{"id": i, "username": f"member{i}", "role": "user", "is_active": True}
             for i in range((page - 1) * 20 + 1, min(page * 20, 25) + 1)]
    return {"items": items, "total": 25, "page": page, "page_size": 20}

render_system_admin_page(request, lambda exc: st.error(str(exc)),
                         {"id": 99, "role": "admin"})
'''
admin = AppTest.from_string(admin_source).run()
assert not admin.exception
assert admin.subheader[0].value == "系統管理"
assert admin.markdown[0].value == "### 使用者列表"
assert len(admin.dataframe[0].value) == 20
assert not admin.number_input
next_button = next(button for button in admin.button if button.label == "下一頁 ▶")
next_button.click().run()
assert not admin.exception
assert len(admin.dataframe[0].value) == 5
assert admin.session_state["system_admin_page"] == 2
admin.selectbox[0].set_value(1).run()
assert not admin.exception and admin.session_state["system_admin_page"] == 1

edited = [{"id": i, "username": f"member{i}", "role": "viewer" if i == 1 else "user",
           "is_active": True} for i in range(1, 21)]
with patch("system_admin_ui.st.data_editor", return_value=edited):
    admin.run()
    save = next(button for button in admin.button if button.label == "儲存角色變更")
    save.click().run()
assert not admin.exception
assert admin.session_state["last_update"] == ("/api/v1/users/1/role", {"role": "viewer"})
print("System management Streamlit controls passed")
