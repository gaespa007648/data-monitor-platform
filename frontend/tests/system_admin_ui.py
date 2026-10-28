"""Check admin-only view, role editor and page controls with Streamlit AppTest."""
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from system_admin_ui import page_items


assert page_items(1, 67) == [1, 2, 3, 4, 5, 6, "…", 67]
assert page_items(34, 67) == [1, "…", 32, 33, 34, 35, 36, 37, "…", 67]
assert page_items(67, 67) == [1, "…", 62, 63, 64, 65, 66, 67]
assert page_items(1, 2) == [1, 2]

pagination = AppTest.from_string('''
import streamlit as st
from system_admin_ui import render_pagination
render_pagination(st.session_state.setdefault("system_admin_page", 1), 67)
''').run()
assert not pagination.exception
assert [button.label for button in pagination.button] == ["‹", "1", "2", "3", "4", "5", "6", "67", "›"]
next(button for button in pagination.button if button.label == "67").click().run()
assert not pagination.exception and pagination.session_state["system_admin_page"] == 67
assert [button.label for button in pagination.button] == ["‹", "1", "62", "63", "64", "65", "66", "67", "›"]


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
assert [button.label for button in admin.button if button.label in {"‹", "1", "2", "›"}] == ["‹", "1", "2", "›"]
assert not admin.selectbox
next_button = next(button for button in admin.button if button.label == "›")
next_button.click().run()
assert not admin.exception
assert len(admin.dataframe[0].value) == 5
assert admin.session_state["system_admin_page"] == 2
previous_button = next(button for button in admin.button if button.label == "‹")
previous_button.click().run()
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
