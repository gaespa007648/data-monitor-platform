"""Schema management and guided import pages. API remains the validation authority."""
import hashlib
import json
from uuid import uuid4

import httpx
import streamlit as st

TARGETS = ("title", "value", "category_id", "timestamp")
TYPES = {"title": ["string"], "value": ["integer", "decimal"],
         "category_id": ["integer"], "timestamp": ["datetime"]}


def rules_table(rules):
    st.dataframe([{"來源欄位": f["source_field"], "目標欄位": f["target_field"],
                   "data_type": f.get("data_type") or "未指定"} for f in rules["fields"]], use_container_width=True)
    st.caption("未指定額外型別的欄位仍須通過正式欄位驗證；四個目標欄位皆必填。")


def rules_editor(prefix, seed=None):
    seed = seed or {"file_format": "csv", "extra_policy": "reject", "trim_whitespace": True,
                    "fields": [{"source_field": k, "target_field": k, "data_type": None} for k in TARGETS]}
    kind = st.selectbox("檔案格式", ["csv", "json"], index=["csv", "json"].index(seed["file_format"]), key=prefix+"_format")
    extra = st.selectbox("額外欄位策略", ["reject", "ignore"], index=["reject", "ignore"].index(seed["extra_policy"]), key=prefix+"_extra")
    trim = st.checkbox("去除來源資料值首尾空白", value=seed["trim_whitespace"], key=prefix+"_trim")
    fields = []
    for target in TARGETS:
        initial = next(f for f in seed["fields"] if f["target_field"] == target)
        st.markdown(f"**{target}**")
        source = st.text_input(f"{target} 的來源欄名", value=initial["source_field"], max_chars=128, key=f"{prefix}_{target}_source")
        choices = ["未指定", *TYPES[target]]
        kind_choice = st.selectbox(f"{target} 的 data_type（選填）", choices,
            index=choices.index(initial.get("data_type") or "未指定"), key=f"{prefix}_{target}_type")
        field = {"source_field": source, "target_field": target, "data_type": None if kind_choice == "未指定" else kind_choice}
        if kind_choice in {"integer", "decimal"} and kind == "json":
            field["allow_numeric_string"] = st.checkbox(f"{target} 允許 JSON 數值字串",
                value=bool(initial.get("allow_numeric_string")), key=f"{prefix}_{target}_strings")
        if kind_choice == "string" and st.checkbox(f"{target} 設定字串長度上限", value=initial.get("max_length") is not None, key=prefix+target+"_limit"):
            field["max_length"] = st.number_input(f"{target} 最大字元數", 1, 200, value=initial.get("max_length") or 200, key=prefix+target+"_length")
        if kind_choice == "decimal" and st.checkbox(f"{target} 設定數值精度", value=initial.get("max_digits") is not None or initial.get("decimal_places") is not None, key=prefix+target+"_precision"):
            field["max_digits"] = st.number_input("最大總位數", 1, 18, value=initial.get("max_digits") or 18, key=prefix+"_digits")
            field["decimal_places"] = st.number_input("最大小數位數", 0, 6, value=initial.get("decimal_places") if initial.get("decimal_places") is not None else 6, key=prefix+"_places")
        fields.append(field)
    return {"file_format": kind, "extra_policy": extra, "trim_whitespace": trim, "fields": fields}


def choose_template(request, label="選擇範本", active_only=False, prefix="schema"):
    page = st.number_input("範本清單頁碼", min_value=1, step=1, key=prefix+"_page")
    templates = request("GET", "/api/v1/import-schemas", params={"page": page, "page_size": 100})
    if active_only:
        templates = [t for t in templates if t["is_active"]]
    if not templates:
        st.info("本頁沒有可用範本，請建立範本或切換頁碼。")
        return None
    by_id = {t["id"]: t for t in templates}
    selected = st.selectbox(label, list(by_id), format_func=lambda i: f"{by_id[i]['name']} · {by_id[i]['visibility']} · #{i}", key=prefix+"_selected")
    return by_id[selected]


def choose_version(request, template, published_only=False, prefix="version"):
    page = st.number_input("版本清單頁碼", min_value=1, step=1, key=f"{prefix}_{template['id']}_page")
    versions = request("GET", f"/api/v1/import-schemas/{template['id']}/versions", params={"page": page, "page_size": 100})
    if published_only:
        versions = [v for v in versions if v["status"] == "published" and v["is_active"]]
    if not versions:
        st.info("本頁沒有可用版本；請先儲存並發布版本，或切換頁碼。")
        return None
    by_id = {v["id"]: v for v in versions}
    selected = st.selectbox("選擇版本", list(by_id), key=f"{prefix}_{template['id']}_selected",
        format_func=lambda i: f"v{by_id[i]['number']} · {by_id[i]['rules']['file_format']} · {by_id[i]['status']} · {'啟用' if by_id[i]['is_active'] else '停用'}")
    return by_id[selected]


def render_schema_page(request, show_error):
    st.subheader("匯入 Schema 範本")
    user = st.session_state["user"]
    if "schema_notice" in st.session_state:
        st.success(st.session_state.pop("schema_notice"))
    mode = st.radio("範本操作", ["建立範本", "管理範本"] if user["role"] in {"admin", "user"} else ["管理範本"])
    try:
        if mode == "建立範本":
            seed = None
            seed_id = "blank"
            if st.checkbox("從既有版本複製規則"):
                template = choose_template(request, prefix="clone")
                if template is None:
                    return
                version = choose_version(request, template, prefix="cloneversion")
                if version is None:
                    return
                seed, seed_id = version["rules"], str(version["id"])
            name = st.text_input("範本名稱", max_chars=100)
            description = st.text_input("範本說明", max_chars=500)
            visibility = st.selectbox("可見範圍", ["private", "shared"] if user["role"] == "admin" else ["private"])
            rules = rules_editor("new_"+seed_id, seed)
            if st.button("儲存範本草稿"):
                result = request("POST", "/api/v1/import-schemas", json={"name": name, "description": description, "visibility": visibility, "rules": rules})
                st.success(f"已儲存範本 #{result['id']}，請切換「管理範本」選取後發布草稿。")
            return
        template = choose_template(request, prefix="manage")
        if template is None:
            return
        can_edit = user["role"] == "admin" or (user["role"] == "user" and template["owner_id"] == user["id"] and template["visibility"] == "private")
        st.caption(f"範本狀態：{'啟用' if template['is_active'] else '停用'}；已發布規則不能覆寫。")
        if can_edit:
            with st.expander("範本名稱、說明與狀態"):
                with st.form(f"metadata_{template['id']}"):
                    name = st.text_input("修改名稱", value=template["name"], max_chars=100)
                    description = st.text_input("修改說明", value=template["description"], max_chars=500)
                    active = st.checkbox("範本啟用", value=template["is_active"])
                    save = st.form_submit_button("儲存範本資訊")
                if save:
                    request("PATCH", f"/api/v1/import-schemas/{template['id']}", json={"name": name, "description": description, "is_active": active})
                    st.rerun()
        version = choose_version(request, template, prefix="manageversion")
        if version is None:
            return
        rules_table(version["rules"])
        st.json(version["rules"], expanded=False)
        if not can_edit:
            st.info("此範本僅可檢視；User 可在建立範本時複製為私有範本。")
            return
        path = f"/api/v1/import-schemas/{template['id']}/versions/{version['id']}"
        if version["status"] == "draft" and st.button("發布草稿", disabled=not template["is_active"] or not version["is_active"]):
            request("POST", path+"/publication")
            st.session_state["schema_notice"] = "版本已發布，可於批次匯入選取。"
            st.rerun()
        if st.button("停用版本" if version["is_active"] else "啟用版本"):
            request("PATCH", path+"/activation", json={"is_active": not version["is_active"]})
            st.rerun()
        if template["builtin_key"]:
            st.info("內建範本規則不可修改；請複製後自訂。")
            return
        options = ["檢視", "另建新版本"] + (["編輯草稿"] if version["status"] == "draft" else [])
        action = st.radio("版本操作", options, key=f"action_{version['id']}")
        if action != "檢視":
            rules = rules_editor(f"edit_{version['id']}_{action}", version["rules"])
            if st.button("儲存版本草稿"):
                request("PUT" if action == "編輯草稿" else "POST",
                        path if action == "編輯草稿" else f"/api/v1/import-schemas/{template['id']}/versions", json=rules)
                st.session_state["schema_notice"] = "草稿已儲存，請選取對應版本並發布。"
                st.rerun()
        if version["status"] == "draft":
            confirmed = st.checkbox("確認刪除未使用草稿", key=f"delete_draft_{version['id']}")
            if st.button("刪除草稿", disabled=not confirmed):
                request("DELETE", path)
                st.rerun()
    except httpx.HTTPError as exc:
        show_error(exc)


def render_import_page(request, show_error):
    st.subheader("批次匯入 CSV／JSON")
    st.caption("選擇已發布 schema → 產生或準備資料 → 預檢全檔 → 確認匯入。上限 2 MiB／1000 筆。")
    try:
        template = choose_template(request, active_only=True, prefix="importschema")
        if template is None:
            return
        version = choose_version(request, template, published_only=True, prefix="importversion")
        if version is None:
            return
        rules_table(version["rules"])
        st.caption(f"範本 #{template['id']}，版本 v{version['number']}，格式 {version['rules']['file_format'].upper()}；額外欄位策略 {version['rules']['extra_policy']}")
        st.dataframe(request("GET", "/api/v1/categories"), use_container_width=True)
        sample_key = f"sample_{version['id']}_{version['rules_hash']}"
        if st.button("產生範本資料"):
            st.session_state[sample_key] = request("GET", f"/api/v1/import-schemas/{template['id']}/versions/{version['id']}/sample", raw=True)
        if sample_key in st.session_state:
            kind = version["rules"]["file_format"]
            st.download_button("下載範本資料", data=st.session_state[sample_key], file_name=f"schema-{version['id']}-sample.{kind}",
                mime="text/csv" if kind == "csv" else "application/json")
        file = st.file_uploader("選擇檔案", type=[version["rules"]["file_format"]])
        if file is None:
            st.session_state.pop("import_digest", None)
            return
        content = file.getvalue()
        if len(content) > 2 * 1024 * 1024:
            st.error("檔案不可超過 2 MiB")
            return
        st.code(content.decode("utf-8-sig", errors="replace")[:2000], language="text")
        st.caption("原始檔案預覽最多 2000 字；後端預檢會檢查全部資料列。")
        digest = hashlib.sha256(content + str(version["id"]).encode() + version["rules_hash"].encode() + version["rules"]["file_format"].encode()).hexdigest()
        if st.session_state.get("import_digest") != digest:
            st.session_state.update(import_digest=digest, import_key=uuid4().hex, import_valid=False, import_done=False)
            st.session_state.pop("import_preview", None)
        params = {"schema_version_id": version["id"]}
        if st.button("預檢檔案"):
            st.session_state["import_valid"] = False
            result = request("POST", "/api/v1/record-imports", params={**params, "preview": True},
                headers={"Idempotency-Key": st.session_state["import_key"]}, files={"file": (file.name, content)})
            st.session_state.update(import_valid=True, import_preview=result)
        if st.session_state.get("import_valid"):
            result = st.session_state["import_preview"]
            st.success(f"預檢成功，共 {result['row_count']} 筆，schema version #{result['schema_version_id']}")
            st.dataframe(result["normalized_sample"], use_container_width=True)
            if result["ignored_fields"]:
                st.warning("不會儲存的額外欄位：" + ", ".join(result["ignored_fields"]))
        if st.button("確認匯入", disabled=not st.session_state.get("import_valid") or st.session_state.get("import_done", False)):
            result = request("POST", "/api/v1/record-imports", params=params,
                headers={"Idempotency-Key": st.session_state["import_key"]}, files={"file": (file.name, content)})
            st.session_state["import_done"] = True
            st.success(f"匯入完成：批次 {result['batch_id']}，{result['row_count']} 筆；已保存規則快照。")
    except httpx.HTTPError as exc:
        st.session_state["import_valid"] = False
        show_error(exc)
