"""Streamlit controls for persisted dataset monitors and WebSocket simulation."""
import json
import queue
import threading
from io import BytesIO

import httpx
import pandas as pd
import streamlit as st
from websockets.sync.client import connect

from dataset_ui import choose_dataset
from display_format import display_number, display_rows
from export_utils import EXCEL_MIME, excel_bytes, safe_filename_part


def alert_rows(alerts, dataset, formatted=True):
    rows = []
    field_types = {field["name"]: field.get("data_type") for field in dataset["fields"]}
    for alert in alerts:
        reasons = alert["reasons"]
        rows.append({"異常 ID": alert["id"], "資料 ID": alert["record_id"],
                     "告警時間 (UTC)": alert["created_at"],
                     "異常欄位": ", ".join(reason["field"] for reason in reasons),
                     "異常原因": ", ".join(reason["reason"] for reason in reasons),
                     "異常值": ", ".join(display_number(reason["value"])
                         if formatted and field_types.get(reason["field"]) == "decimal"
                         else str(reason["value"]) for reason in reasons),
                     **alert["data"]})
    return display_rows(rows, dataset["fields"]) if formatted else rows


def alert_excel(dataset, alerts):
    names = ["異常 ID", "資料 ID", "告警時間 (UTC)", "異常欄位", "異常原因", "異常值"]
    fields = [{"name": name, "data_type": "string"} for name in names]
    fields += dataset["fields"]
    return excel_bytes({"fields": fields}, alert_rows(alerts, dataset, formatted=False))


def load_alerts(request, monitor_id):
    rows = []
    while True:
        page = request("GET", f"/api/v1/monitors/{monitor_id}/alerts",
                       params={"limit": 500, "offset": len(rows)})
        rows.extend(page)
        if len(page) < 500:
            return rows


def render_new_monitor(request, show_error):
    st.subheader("新增即時監控")
    try:
        dataset = choose_dataset(request, "monitor_create", writable=True, show_page=False,
                                 require_selection=True)
        if dataset is None:
            return
        field_stats = request("GET", f"/api/v1/monitors/datasets/{dataset['id']}/fields")
    except httpx.HTTPError as exc:
        show_error(exc)
        return
    with st.container(border=True):
        name = st.text_input("監控名稱", value=f"{dataset['name']} 即時監控", max_chars=100)
        st.markdown("**即時圖表設定**")
        chart_type = st.selectbox("圖表類型", ["折線圖", "柱狀圖"])
        window = st.number_input("追蹤時間 (秒)", min_value=5, max_value=86400, value=60)
        interval = st.number_input("記錄頻率 (秒)", min_value=1, max_value=3600, value=1)
        st.markdown("**異常告警**")
        options = [field["name"] for field in dataset["fields"]
                   if field.get("data_type") in {"integer", "decimal", "string"}]
        selected = st.multiselect("追蹤異常的欄位", options)
        rules = []
        for field in dataset["fields"]:
            if field["name"] not in selected:
                continue
            field_name, kind = field["name"], field["data_type"]
            if kind == "string":
                st.caption(f"{field_name}：目前未出現過的新字串視為異常。")
                rules.append({"field": field_name, "kind": kind})
                continue
            stats = field_stats[field_name]
            available = stats["minimum"] is not None
            automatic = st.checkbox(f"{field_name}：以目前最小值／最大值為正常範圍",
                value=available, disabled=not available, key=f"monitor_auto_{dataset['id']}_{field_name}")
            if automatic:
                minimum, maximum = stats["minimum"], stats["maximum"]
                st.caption(f"正常範圍：{display_number(minimum)} ～ {display_number(maximum)}（包含邊界）")
            else:
                left, right = st.columns(2)
                minimum = left.text_input(f"{field_name} 最小值", key=f"monitor_min_{dataset['id']}_{field_name}")
                maximum = right.text_input(f"{field_name} 最大值", key=f"monitor_max_{dataset['id']}_{field_name}")
            rules.append({"field": field_name, "kind": kind,
                          "minimum": minimum, "maximum": maximum})
        left, right = st.columns(2)
        save = left.button("儲存")
        activate = right.button("建立並啟用")
    if save or activate:
        if interval > window or (window + interval - 1) // interval > 1000:
            st.error("記錄頻率不可大於追蹤時間，且圖表最多 1,000 個時間區間。")
            return
        try:
            request("POST", "/api/v1/monitors", json={"dataset_id": dataset["id"],
                "name": name, "chart_type": "line" if chart_type == "折線圖" else "bar",
                "window_seconds": int(window), "interval_seconds": int(interval),
                "rules": rules, "activate": activate})
            st.success("監控設定已建立並啟用。" if activate else "監控設定已儲存，尚未啟用。")
        except httpx.HTTPError as exc:
            show_error(exc)


def _live_content(request, show_error, monitor, dataset):
    try:
        snapshot = request("GET", f"/api/v1/monitors/{monitor['id']}/snapshot")
    except httpx.HTTPError as exc:
        show_error(exc)
        return
    st.markdown("#### 即時匯入資料數")
    points = snapshot["points"]
    if points:
        frame = pd.DataFrame(points)
        frame["time"] = pd.to_datetime(frame["time"], utc=True)
        frame = frame.set_index("time")
        if monitor["chart_type"] == "line":
            st.line_chart(frame, y="count", x_label="時間 (UTC)", y_label="新增筆數")
        else:
            st.bar_chart(frame, y="count", x_label="時間 (UTC)", y_label="新增筆數")
    st.markdown("#### 異常資料")
    st.caption(f"共 {snapshot['alert_total']} 筆，顯示最新 10 筆。")
    if snapshot["alerts"]:
        st.dataframe(alert_rows(snapshot["alerts"], dataset), hide_index=True,
                     use_container_width=True)
    else:
        st.info("目前沒有異常資料。")
    if snapshot["alert_total"]:
        try:
            cache_key = f"monitor_alert_excel_{monitor['id']}"
            cached = st.session_state.get(cache_key)
            if cached is None or cached[0] != snapshot["alert_total"]:
                all_alerts = load_alerts(request, monitor["id"])
                cached = (snapshot["alert_total"], alert_excel(dataset, all_alerts))
                st.session_state[cache_key] = cached
            st.download_button("下載所有異常資料 Excel", data=cached[1],
                file_name=f"{safe_filename_part(dataset['name'])}-{safe_filename_part(monitor['name'])}-alerts.xlsx",
                mime=EXCEL_MIME, key=f"monitor_alert_download_{monitor['id']}")
        except httpx.HTTPError as exc:
            show_error(exc)
    else:
        st.download_button("下載所有異常資料 Excel", data=b"", disabled=True,
                           key=f"monitor_alert_download_{monitor['id']}")


@st.fragment(run_every="1s")
def live_monitor(request, show_error, monitor_id, dataset_id):
    try:
        monitor = next(item for item in request("GET", "/api/v1/monitors") if item["id"] == monitor_id)
        dataset = request("GET", f"/api/v1/datasets/{dataset_id}")
    except (httpx.HTTPError, StopIteration) as exc:
        show_error(exc)
        return
    if monitor["active"]:
        _live_content(request, show_error, monitor, dataset)
    else:
        st.info("監控已停止，重新啟用後會繼續更新。")


def generator_worker(url, token, dataset_id, stop_event, messages):
    try:
        with connect(url, open_timeout=10, close_timeout=3) as socket:
            socket.send(json.dumps({"token": token, "dataset_id": dataset_id}))
            while not stop_event.is_set():
                message = json.loads(socket.recv(timeout=10))
                if message.get("status") == "error":
                    messages.put(message["message"])
                    return
                if message.get("status") == "created":
                    messages.put(f"已新增資料 #{message['record']['id']}")
            socket.send(json.dumps({"action": "stop"}))
    except Exception as exc:
        if not stop_event.is_set():
            messages.put(f"模擬生成器連線中斷：{type(exc).__name__}")


def stop_generator():
    control = st.session_state.get("monitor_generator")
    if control:
        control["stop"].set()


def render_generator(dataset, api_base_url, token):
    st.subheader("模擬生成器")
    with st.expander("模擬資料生成器"):
        st.caption("每秒依數據集 Schema 隨機產生一筆資料。僅限具有該數據集寫入權的使用者。")
        control = st.session_state.get("monitor_generator")
        running = bool(control and control["thread"].is_alive() and not control["stop"].is_set())
        if control and control["dataset_id"] != dataset["id"] and running:
            st.warning("另一個數據集的模擬生成器正在執行，請先停止。")
        start = st.button("開始", disabled=not dataset["can_write"] or running)
        stop = st.button("停止", disabled=not running)
        if start:
            control = {"dataset_id": dataset["id"], "stop": threading.Event(),
                       "messages": queue.Queue()}
            url = api_base_url.replace("http://", "ws://", 1).replace("https://", "wss://", 1)
            control["thread"] = threading.Thread(target=generator_worker,
                args=(f"{url}/api/v1/generator/ws", token, dataset["id"],
                      control["stop"], control["messages"]), daemon=True)
            st.session_state["monitor_generator"] = control
            control["thread"].start()
            st.rerun()
        if stop:
            stop_generator()
            st.rerun()
        if control:
            latest = None
            while not control["messages"].empty():
                latest = control["messages"].get_nowait()
            if latest:
                st.caption(latest)
            st.caption("執行中" if running else "已停止")


def render_monitor_page(request, show_error, api_base_url, token):
    render_new_monitor(request, show_error)
    st.divider()
    st.subheader("查看即時監控")
    try:
        monitors = request("GET", "/api/v1/monitors")
        if not monitors:
            st.info("目前沒有即時監控設定。")
            return
        choices = {row["id"]: row for row in monitors}
        selected = st.selectbox("選擇即時監控設定", list(choices), index=None,
            format_func=lambda value: f"{choices[value]['name']} · {choices[value]['dataset_name']}")
        if selected is None:
            return
        monitor = choices[selected]
        dataset = request("GET", f"/api/v1/datasets/{monitor['dataset_id']}")
        st.write(f"狀態：{'已啟用' if monitor['active'] else '未啟用'}")
        if dataset["can_write"]:
            action = "停止監控" if monitor["active"] else "啟動監控"
            if st.button(action):
                request("POST", f"/api/v1/monitors/{selected}/{'stop' if monitor['active'] else 'start'}")
                st.rerun()
        if monitor["active"]:
            live_monitor(request, show_error, monitor["id"], dataset["id"])
        render_generator(dataset, api_base_url, token)
    except httpx.HTTPError as exc:
        show_error(exc)
