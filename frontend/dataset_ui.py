import hashlib
import json
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import streamlit as st


def field_editor(key, append=False):
    rows = st.data_editor([{"name": "", "remark": "", "data_type": "未指定", "required": not append, "nullable": append}],
        num_rows="dynamic", hide_index=True, use_container_width=True, key=key,
        column_config={"name": st.column_config.TextColumn("欄位名稱", required=True, max_chars=64),
            "remark": st.column_config.TextColumn("備註", max_chars=500),
            "data_type": st.column_config.SelectboxColumn("data_type（選填）",
                options=["未指定", "string", "integer", "decimal", "datetime", "boolean"]),
            "required": st.column_config.CheckboxColumn("必填欄位", default=not append),
            "nullable": st.column_config.CheckboxColumn("允許 null", default=append)})
    if hasattr(rows, "to_dict"):
        rows = rows.to_dict("records")
    fields = []
    for row in rows:
        name = row.get("name") or ""
        kind = row.get("data_type")
        if not name and kind in {None, "未指定"}:
            continue
        fields.append({"name": name, "remark": row.get("remark") or "",
                       "data_type": None if kind in {None, "未指定"} else kind,
                       "required": row.get("required") if type(row.get("required")) is bool else not append,
                       "nullable": row.get("nullable") if type(row.get("nullable")) is bool else append})
    return fields


def choose_dataset(request, prefix, writable=False, show_page=True):
    page = st.number_input("數據集清單頁碼", min_value=1, step=1, key=prefix+"_datasets_page") if show_page else 1
    result = request("GET", "/api/v1/datasets", params={"page": page, "page_size": 100})
    rows = result["items"]
    if not show_page:
        while len(rows) < result["total"]:
            next_page = len(rows) // 100 + 1
            batch = request("GET", "/api/v1/datasets",
                params={"page": next_page, "page_size": 100})["items"]
            if not batch:
                break
            rows.extend(batch)
    rows = [row for row in rows if not writable or row["can_write"]]
    if not rows:
        st.info("沒有可用數據集，請先建立數據集。" if not show_page else
                "本頁沒有可用數據集，請先建立數據集或切換清單頁碼。")
        return None
    options = {row["id"]: row for row in rows}
    selected = st.selectbox("選擇數據集", list(options), key=prefix+"_dataset",
        format_func=lambda i: f"{options[i]['name']} · #{i}")
    return options[selected]


def show_schema(dataset):
    st.caption(f"{dataset['name']} · Schema v{dataset['schema_version']}；既有欄位不可修改或刪除。")
    st.dataframe([{**field, "data_type": field["data_type"] or "未指定"} for field in dataset["fields"]],
                 use_container_width=True, hide_index=True)


def render_dataset_page(request, show_error):
    st.subheader("建立數據集 Dataset")
    st.caption("每個數據集可自訂 1–100 個欄位；建立後只能新增欄位。")
    user = st.session_state["user"]
    modes = ["建立數據集", "管理數據集"] if user["role"] in {"admin", "user"} else ["管理數據集"]
    mode = st.radio("數據集操作", modes)
    try:
        if mode == "建立數據集":
            name = st.text_input("數據集名稱", max_chars=100)
            description = st.text_input("說明", max_chars=500)
            st.info("可在欄位表新增或刪除尚未儲存的欄位；data_type 可選未指定。提交前請確認欄位已完成編輯。")
            fields = field_editor("new_dataset_fields")
            if st.button("建立數據集"):
                result = request("POST", "/api/v1/datasets", json={"name": name, "description": description, "fields": fields})
                st.success(f"數據集 {result['name']} 已建立（#{result['id']}），可前往建立資料或批次匯入。")
            return
        dataset = choose_dataset(request, "manage_dataset")
        if dataset is None:
            return
        show_schema(dataset)
        if not dataset["can_write"]:
            st.info("僅建立者或 Admin 可新增欄位，目前僅供檢視。")
            return
        st.markdown("**新增欄位**")
        st.caption("只能新增未使用的名稱，不能更改原欄位。新欄位不回填舊資料；必填設定僅約束之後新增的資料。")
        fields = field_editor(f"append_{dataset['id']}_{dataset['schema_version']}", append=True)
        if st.button("新增欄位"):
            result = request("POST", f"/api/v1/datasets/{dataset['id']}/fields",
                json={"schema_version": dataset["schema_version"], "fields": fields})
            st.success(f"已新增欄位，Schema 更新為 v{result['schema_version']}。")
            show_schema(result)
    except httpx.HTTPError as exc:
        show_error(exc)


def render_manual_page(request, show_error):
    st.subheader("建立資料")
    try:
        dataset = choose_dataset(request, "manual_dataset", writable=True, show_page=False)
        if dataset is None:
            return
        show_schema(dataset)
        data = {}
        prefix = f"manual_{dataset['id']}_{dataset['schema_version']}_"
        for field in dataset["fields"]:
            name, kind = field["name"], field["data_type"]
            if not field["required"] and not st.checkbox(f"填寫 {name}", value=False, key=prefix+name+"_include"):
                continue
            if field["nullable"] and st.checkbox(f"{name} 使用 null", key=prefix+name+"_null"):
                data[name] = None
                continue
            if kind == "boolean":
                data[name] = st.selectbox(name, ["true", "false"], key=prefix+name) == "true"
            else:
                default = datetime.now(ZoneInfo("Asia/Taipei")).isoformat() if kind == "datetime" else ""
                data[name] = st.text_input(name, value=default, key=prefix+name, max_chars=4096,
                    help=f"data_type：{kind or '未指定（手動輸入保存為文字）'}")
        if st.button("建立"):
            result = request("POST", f"/api/v1/datasets/{dataset['id']}/records",
                json={"schema_version": dataset["schema_version"], "data": data})
            st.success(f"資料已建立，記錄 #{result['id']}。")
    except httpx.HTTPError as exc:
        show_error(exc)


def render_import_page(request, show_error):
    st.subheader("批次匯入")
    st.caption("先選數據集，CSV／JSON 需符合該數據集的目前 Schema。每檔最多 2 MiB／1000 筆。")
    try:
        dataset = choose_dataset(request, "import_dataset", writable=True, show_page=False)
        if dataset is None:
            return
        show_schema(dataset)
        kind = st.selectbox("檔案格式", ["csv", "json"])
        file = st.file_uploader("選擇檔案", type=[kind])
        skip_first_row = st.checkbox("排除第一列欄名", value=True,
            help="勾選時第一列作為欄名，不匯入資料；取消勾選時依數據集欄位順序匯入每一列。") if kind == "csv" else False
        if file is None:
            st.session_state.pop("dataset_import_digest", None)
            return
        content = file.getvalue()
        if len(content) > 2 * 1024 * 1024:
            st.error("檔案不可超過 2 MiB")
            return
        st.code(content.decode("utf-8-sig", errors="replace")[:2000], language="text")
        digest = hashlib.sha256(content + str(dataset["id"]).encode() + dataset["rules_hash"].encode()
            + kind.encode() + str(skip_first_row).encode()).hexdigest()
        if st.session_state.get("dataset_import_digest") != digest:
            st.session_state.update(dataset_import_digest=digest, dataset_import_key=uuid4().hex,
                                    dataset_import_valid=False, dataset_import_done=False)
            st.session_state.pop("dataset_preview", None)
        params = {"schema_version": dataset["schema_version"]}
        if kind == "csv":
            params["skip_first_row"] = skip_first_row
        headers = {"Idempotency-Key": st.session_state["dataset_import_key"]}
        if st.button("預檢檔案"):
            st.session_state["dataset_import_valid"] = False
            result = request("POST", f"/api/v1/datasets/{dataset['id']}/imports", params={**params, "preview": True},
                headers=headers, files={"file": (file.name, content)})
            st.session_state.update(dataset_import_valid=True, dataset_preview=result)
        if st.session_state.get("dataset_import_valid"):
            result = st.session_state["dataset_preview"]
            st.success(f"預檢成功，共 {result['row_count']} 筆。")
            st.dataframe(result["normalized_sample"], use_container_width=True)
        if st.button("確認匯入", disabled=not st.session_state.get("dataset_import_valid", False) or st.session_state.get("dataset_import_done", False)):
            result = request("POST", f"/api/v1/datasets/{dataset['id']}/imports", params=params,
                headers=headers, files={"file": (file.name, content)})
            st.session_state["dataset_import_done"] = True
            st.success(f"已匯入 {result['row_count']} 筆，批次 #{result['batch_id']}，可至資料分析查看。")
    except httpx.HTTPError as exc:
        st.session_state["dataset_import_valid"] = False
        show_error(exc)


def render_analysis_page(request, show_error):
    st.subheader("資料分析")
    st.caption("目前提供數據集資料的分頁檢視，每頁最多 20 筆，不提供篩選條件。")
    try:
        dataset = choose_dataset(request, "analysis_dataset")
        if dataset is None:
            return
        page = st.number_input("資料頁碼", min_value=1, step=1, key=f"analysis_page_{dataset['id']}")
        result = request("GET", f"/api/v1/datasets/{dataset['id']}/records", params={"page": page, "page_size": 20})
        fields = [field["name"] for field in result["dataset"]["fields"]]
        rows = [{"__record_id": row["id"], "__schema_version": row["schema_version"],
                 **{name: row["data"].get(name) for name in fields}} for row in result["items"]]
        st.caption(f"共 {result['total']} 筆，第 {page} 頁／共 {max(1, (result['total'] + 19) // 20)} 頁。新增欄位未回填舊資料，空白可能代表缺值或 null。")
        st.dataframe(rows, use_container_width=True, hide_index=True)
    except httpx.HTTPError as exc:
        show_error(exc)
