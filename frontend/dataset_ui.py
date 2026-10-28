import csv
import hashlib
import io
import json
from datetime import datetime
from itertools import islice
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import streamlit as st

from analysis_table import render_table
from analysis_ui import render_analysis
from display_format import display_rows
from ui_heading import page_title, section


def preview_rows(content, kind, skip_first_row, fields):
    """Show at most five uploaded records; validation still uses the full file."""
    try:
        source = content.decode("utf-8-sig")
        if kind == "json":
            rows = json.loads(source)
            return rows[:5] if isinstance(rows, list) else None
        reader = csv.reader(io.StringIO(source, newline=""), strict=True)
        headers = next(reader, []) if skip_first_row else [field["name"] for field in fields]
        return [dict(zip(headers, cells)) for cells in islice(reader, 5)]
    except (UnicodeDecodeError, ValueError, csv.Error):
        return None


def field_editor(key, append=False):
    rows = st.data_editor([{"name": "", "remark": "", "data_type": "未指定", "required": not append,
                            "nullable": append, "unique": False}],
        num_rows="dynamic", hide_index=True, use_container_width=True, key=key,
        column_config={"name": st.column_config.TextColumn("欄位名稱", required=True, max_chars=64),
            "remark": st.column_config.TextColumn("備註", max_chars=500),
            "data_type": st.column_config.SelectboxColumn("data_type（選填）",
                options=["未指定", "string", "integer", "decimal", "datetime", "boolean"]),
            "required": st.column_config.CheckboxColumn("必填欄位", default=not append),
            "nullable": st.column_config.CheckboxColumn("允許 null", default=append),
            "unique": st.column_config.CheckboxColumn("唯一值 unique", default=False)})
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
                       "nullable": row.get("nullable") if type(row.get("nullable")) is bool else append,
                       "unique": row.get("unique") is True})
    return fields


def choose_dataset(request, prefix, writable=False, show_page=True, require_selection=False):
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
        index=None if require_selection else 0, placeholder="請選擇數據集",
        format_func=lambda i: f"{options[i]['name']} · #{i}")
    return options[selected] if selected is not None else None


def show_schema(dataset):
    st.caption(f"{dataset['name']} · Schema v{dataset['schema_version']}；既有欄位不可修改或刪除。")
    st.dataframe([{**field, "data_type": field["data_type"] or "未指定"} for field in dataset["fields"]],
                 use_container_width=True, hide_index=True)


def render_dataset_page(request, show_error, navigate_to_manage=None):
    page_title("建立數據集")
    st.caption("每個數據集可自訂 1–100 個欄位；建立後只能新增欄位。")
    user = st.session_state["user"]
    mode = st.radio("數據集操作", ["建立數據集", "編輯數據集"],
                    index=0 if user["role"] in {"admin", "user"} else 1)
    try:
        if mode == "建立數據集":
            if user["role"] not in {"admin", "user"}:
                st.warning("無權限建立數據集。")
                return
            name = st.text_input("數據集名稱", max_chars=100)
            description = st.text_input("說明", max_chars=500)
            st.info("可在欄位表新增或刪除尚未儲存的欄位；data_type 可選未指定。提交前請確認欄位已完成編輯。")
            fields = field_editor("new_dataset_fields")
            if st.button("建立數據集"):
                st.session_state.pop("dataset_created", None)
                result = request("POST", "/api/v1/datasets", json={"name": name, "description": description, "fields": fields})
                st.session_state["dataset_created"] = {"id": result["id"], "name": result["name"]}
            created = st.session_state.get("dataset_created")
            if created:
                st.success(f"數據集「{created['name']}」建置完成（#{created['id']}）。")
                if st.button("前往管理數據集查看", disabled=navigate_to_manage is None):
                    navigate_to_manage(created["id"])
            return
        st.session_state.pop("dataset_created", None)
        dataset = choose_dataset(request, "manage_dataset")
        if dataset is None:
            return
        show_schema(dataset)
        if not dataset["can_write"]:
            st.info("僅建立者或 Admin 可新增欄位，目前僅供檢視。")
            return
        section("新增欄位")
        st.caption("只能新增未使用的名稱，不能更改原欄位。新欄位不回填舊資料；必填設定僅約束之後新增的資料。")
        fields = field_editor(f"append_{dataset['id']}_{dataset['schema_version']}", append=True)
        if st.button("新增欄位"):
            result = request("POST", f"/api/v1/datasets/{dataset['id']}/fields",
                json={"schema_version": dataset["schema_version"], "fields": fields})
            st.success(f"已新增欄位，Schema 更新為 v{result['schema_version']}。")
            show_schema(result)
    except httpx.HTTPError as exc:
        show_error(exc)


def render_manual_entry(dataset, request, show_error):
    try:
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
            st.session_state[f"manage_created_{dataset['id']}"] = result["id"]
            st.rerun()
    except httpx.HTTPError as exc:
        show_error(exc)


def render_batch_import(dataset, request, show_error):
    st.caption("CSV／JSON 需符合目前數據集的 Schema。每檔最多 2 MiB／1000 筆。")
    try:
        kind = st.selectbox("檔案格式", ["csv", "json"], key=f"dataset_import_kind_{dataset['id']}")
        if kind == "json":
            st.info("JSON 頂層須為 1–1000 筆資料的物件陣列；key 須與 Schema 欄名完全一致。"
                    "integer／decimal 用 JSON 數字，boolean 用 true／false，datetime 用含時區的 ISO 8601 字串。"
                    "選填欄位可省略；只有允許 null 的欄位可填 null。不可有多餘欄位、重複 key 或巢狀物件／陣列。")
            examples = {"integer": 1, "decimal": 12.5, "boolean": True,
                        "datetime": "2026-10-28T10:00:00+08:00", "string": "範例文字"}
            st.code(json.dumps([{field["name"]: examples.get(field["data_type"], "範例文字")
                                 for field in dataset["fields"]}], ensure_ascii=False, indent=2),
                    language="json")
            st.caption("以上僅示範檔案結構；請依實際資料填值，unique 欄位不可與既有資料重複。上傳後會自動預檢全檔。")
        file = st.file_uploader("選擇檔案", type=[kind],
                                key=f"dataset_import_file_{dataset['id']}_{dataset['schema_version']}_{kind}")
        skip_first_row = st.checkbox("排除第一列欄名", value=True,
            help="勾選時第一列作為欄名，不匯入資料；取消勾選時依數據集欄位順序匯入每一列。") if kind == "csv" else False
        if file is None:
            st.session_state.pop("dataset_import_digest", None)
            st.session_state["dataset_import_valid"] = False
            return
        content = file.getvalue()
        if len(content) > 2 * 1024 * 1024:
            st.error("檔案不可超過 2 MiB")
            return
        st.caption("檔案預覽：最多 5 筆資料；預檢會檢查整份檔案。")
        sample = preview_rows(content, kind, skip_first_row, dataset["fields"])
        if sample is None:
            st.info("檔案無法預覽；請查看下方預檢結果。")
        else:
            st.dataframe(display_rows(sample, dataset["fields"]), use_container_width=True, hide_index=True)
        digest = hashlib.sha256(content + str(dataset["id"]).encode() + dataset["rules_hash"].encode()
            + kind.encode() + str(skip_first_row).encode()).hexdigest()
        if st.session_state.get("dataset_import_digest") != digest:
            st.session_state.update(dataset_import_digest=digest, dataset_import_key=uuid4().hex,
                                    dataset_import_valid=False, dataset_import_done=False,
                                    dataset_import_checked=False, dataset_import_error=None)
            st.session_state.pop("dataset_preview", None)
        params = {"schema_version": dataset["schema_version"]}
        if kind == "csv":
            params["skip_first_row"] = skip_first_row
        headers = {"Idempotency-Key": st.session_state["dataset_import_key"]}
        retry = st.button("重新預檢檔案" if kind == "json" else "預檢檔案")
        if retry or (kind == "json" and not st.session_state["dataset_import_checked"]):
            st.session_state["dataset_import_valid"] = False
            st.session_state["dataset_import_checked"] = True
            st.session_state["dataset_import_error"] = None
            try:
                result = request("POST", f"/api/v1/datasets/{dataset['id']}/imports", params={**params, "preview": True},
                    headers=headers, files={"file": (file.name, content)})
                st.session_state.update(dataset_import_valid=True, dataset_preview=result)
            except httpx.HTTPError as exc:
                if kind == "json":
                    if isinstance(exc, httpx.HTTPStatusError):
                        try:
                            detail = exc.response.json().get("detail", "檔案格式或欄位不符合 Schema")
                        except ValueError:
                            detail = "檔案格式或欄位不符合 Schema"
                        st.session_state["dataset_import_error"] = f"JSON 預檢失敗（HTTP {exc.response.status_code}）：{json.dumps(detail, ensure_ascii=False) if not isinstance(detail, str) else detail}"
                    else:
                        st.session_state["dataset_import_error"] = "JSON 預檢失敗：無法連接後端服務。"
                else:
                    raise
        if st.session_state.get("dataset_import_error"):
            st.error(st.session_state["dataset_import_error"])
        if st.session_state.get("dataset_import_valid"):
            result = st.session_state["dataset_preview"]
            st.success(f"預檢成功，共 {result['row_count']} 筆。")
            st.dataframe(display_rows(result["normalized_sample"], dataset["fields"]), use_container_width=True)
        if st.button("確認匯入", disabled=not st.session_state.get("dataset_import_valid", False) or st.session_state.get("dataset_import_done", False)):
            result = request("POST", f"/api/v1/datasets/{dataset['id']}/imports", params=params,
                headers=headers, files={"file": (file.name, content)})
            st.session_state["dataset_import_done"] = True
            st.session_state[f"manage_imported_{dataset['id']}"] = result
            st.rerun()
    except httpx.HTTPError as exc:
        st.session_state["dataset_import_valid"] = False
        show_error(exc)


def render_manage_records_page(request, show_error):
    page_title("管理數據集")
    try:
        dataset = choose_dataset(request, "manage_records", show_page=False, require_selection=True)
        if dataset is None:
            return
        created_id = st.session_state.pop(f"manage_created_{dataset['id']}", None)
        if created_id is not None:
            st.success(f"資料已建立，記錄 #{created_id}。")
        imported = st.session_state.pop(f"manage_imported_{dataset['id']}", None)
        if imported is not None:
            st.success(f"已匯入 {imported['row_count']} 筆，批次 #{imported['batch_id']}。")
        section("數據檢視")
        st.caption("點欄名切換升冪／降冪；移到資料列上可刪除有權管理的資料。每頁最多 10 筆。")
        sort_key = f"manage_sort_{dataset['id']}"
        sort = st.session_state.get(sort_key, {"column": None, "direction": "asc"})
        page_key = f"manage_page_{dataset['id']}_{sort['column']}_{sort['direction']}"
        page = int(st.session_state.get(page_key, 1))
        params = {"page": page, "page_size": 10}
        if sort["column"]:
            params.update(sort_by=sort["column"], sort_dir=sort["direction"])
        result = request("GET", f"/api/v1/datasets/{dataset['id']}/records", params=params)
        total_pages = max(1, (result["total"] + 9) // 10)
        if page > total_pages:
            page = total_pages
            st.session_state[page_key] = page
            params["page"] = page
            result = request("GET", f"/api/v1/datasets/{dataset['id']}/records", params=params)
        columns = ["__record_id", "__schema_version",
                   *[field["name"] for field in result["dataset"]["fields"]]]
        rows = [{"record_id": row["id"],
                 "can_delete": dataset["can_write"] and (
                     st.session_state["user"]["role"] == "admin"
                     or row["owner_id"] == st.session_state["user"]["id"]),
                 "data": {"__record_id": row["id"], "__schema_version": row["schema_version"],
                          **display_rows([row["data"]], result["dataset"]["fields"], row["value_types"])[0]}}
                for row in result["items"]]
        if st.session_state.pop(f"manage_deleted_{dataset['id']}", False):
            st.success("資料列已刪除。")
        event = render_table(dataset["id"], columns, rows, sort["column"], sort["direction"])
        st.number_input("頁碼", min_value=1, max_value=total_pages, step=1, key=page_key)
        st.caption(f"共 {result['total']} 筆，第 {page} 頁／共 {total_pages} 頁；每頁最多 10 筆。新增欄位未回填舊資料，空白可能代表缺值或 null。")
        section("匯入資料")
        if not dataset["can_write"]:
            st.info("僅數據集建立者或 Admin 可匯入資料，目前僅供檢視。")
        else:
            method = st.radio("匯入方式", ["手動新增", "批次匯入"],
                              key=f"manage_import_mode_{dataset['id']}")
            show_schema(dataset)
            if method == "手動新增":
                render_manual_entry(dataset, request, show_error)
            else:
                render_batch_import(dataset, request, show_error)
        if isinstance(event, str):
            try:
                event = json.loads(event)
            except ValueError:
                event = None
        if not isinstance(event, dict) or event.get("dataset_id") != dataset["id"]:
            return
        event_id = event.get("event_id")
        last_key = f"manage_event_{dataset['id']}"
        if not isinstance(event_id, str) or event_id == st.session_state.get(last_key):
            return
        st.session_state[last_key] = event_id
        if event.get("action") == "sort" and event.get("column") in columns:
            column = event["column"]
            direction = "desc" if sort["column"] == column and sort["direction"] == "asc" else "asc"
            st.session_state[sort_key] = {"column": column, "direction": direction}
            st.rerun()
        if event.get("action") == "delete" and type(event.get("record_id")) is int:
            record_id = event["record_id"]
            if any(row["record_id"] == record_id and row["can_delete"] for row in rows):
                request("DELETE", f"/api/v1/datasets/{dataset['id']}/records/{record_id}")
                st.session_state[f"manage_deleted_{dataset['id']}"] = True
                st.rerun()
    except httpx.HTTPError as exc:
        show_error(exc)


def render_analysis_page(request, show_error):
    page_title("資料分析")
    section("選擇數據集")
    try:
        dataset = choose_dataset(request, "analysis_dataset", show_page=False, require_selection=True)
        if dataset is not None:
            section("資料分析")
            render_analysis(dataset, request)
    except httpx.HTTPError as exc:
        show_error(exc)
