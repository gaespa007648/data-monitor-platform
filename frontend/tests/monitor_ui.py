"""Verify monitor form, live view and anomaly workbook in Streamlit AppTest."""
from io import BytesIO

from openpyxl import load_workbook
from streamlit.testing.v1 import AppTest

from monitor_ui import alert_excel


source = '''
import streamlit as st
from monitor_ui import render_monitor_page

dataset = {"id": 1, "name": "iris", "can_write": True,
    "fields": [{"name": "value", "data_type": "decimal"},
               {"name": "label", "data_type": "string"}]}

def request(method, path, **kwargs):
    if method == "POST":
        st.session_state["submitted"] = kwargs["json"]
        return {"id": 1}
    if path == "/api/v1/datasets":
        return {"items": [dataset], "total": 1}
    if path.endswith("/fields"):
        return {"value": {"minimum": "1.5", "maximum": "8.5"},
                "label": {"known_count": 1}}
    if path == "/api/v1/monitors":
        return []
    raise AssertionError(path)

render_monitor_page(request, lambda error: st.error(str(error)), "http://api:8000", "token")
'''

app = AppTest.from_string(source).run()
assert not app.exception
app.selectbox[0].set_value(1).run()
assert not app.exception
assert [item.value for item in app.title] == ["即時監控"]
assert [item.value for item in app.header] == ["新增即時監控", "查看即時監控"]
app.multiselect[0].set_value(["value", "label"]).run()
assert not app.exception
assert app.checkbox[0].value is True
app.button[0].click().run()
assert not app.exception
payload = app.session_state["submitted"]
assert payload["dataset_id"] == 1 and payload["activate"] is False
assert payload["rules"] == [
    {"field": "value", "kind": "decimal", "minimum": "1.5", "maximum": "8.5"},
    {"field": "label", "kind": "string"}]

active_source = source.replace('''    if path == "/api/v1/monitors":
        return []''', '''    if path == "/api/v1/monitors":
        return [{"id": 3, "dataset_id": 1, "dataset_name": "iris", "name": "live",
                 "active": True, "chart_type": "line"}]
    if path == "/api/v1/datasets/1":
        return dataset
    if path == "/api/v1/monitors/3/snapshot":
        return {"points": [{"time": "2026-10-28T00:00:00+00:00", "count": 1}],
                "alert_total": 0, "alerts": []}''')
active = AppTest.from_string(active_source).run()
assert not active.exception
active.selectbox[1].set_value(3).run()
assert not active.exception
assert any("即時匯入資料數" == heading.value for heading in active.subheader)
workbook = load_workbook(BytesIO(alert_excel(
    {"fields": [{"name": "value", "data_type": "decimal"}]},
    [{"id": 4, "record_id": 7, "created_at": "2026-10-28T00:00:00+00:00",
      "reasons": [{"field": "value", "reason": "outside_range", "value": "12.5"}],
      "data": {"value": "12.5"}}])), read_only=True)
assert workbook.active["B2"].value == 7
assert workbook.active["G2"].value == 12.5
workbook.close()
print("Monitor Streamlit form passed")
