"""Dataset analysis controls and calculations for Streamlit."""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from statistics import median

import pandas as pd
import streamlit as st


SUPPORTED = {"datetime", "integer", "decimal", "boolean", "string"}
NUMERIC = {"integer", "decimal"}
CATEGORICAL = {"boolean", "string"}


def load_records(request, dataset_id):
    """Read every page so statistics always use the whole dataset."""
    rows, page = [], 1
    while True:
        result = request("GET", f"/api/v1/datasets/{dataset_id}/records",
                         params={"page": page, "page_size": 20})
        rows.extend(record["data"] for record in result["items"])
        if len(rows) >= result["total"] or not result["items"]:
            return rows
        page += 1


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def apply_filters(rows, conditions):
    selected = []
    for row in rows:
        for name, (kind, value) in conditions.items():
            cell = row.get(name)
            if kind == "datetime":
                if cell is None or not value[0] <= timestamp(cell) <= value[1]:
                    break
            elif kind in NUMERIC:
                if cell is None or not value[0] <= Decimal(str(cell)) <= value[1]:
                    break
            elif cell not in value:
                break
        else:
            selected.append(row)
    return selected


def numeric_stats(rows, field):
    values = [Decimal(str(row[field])) for row in rows if row.get(field) is not None]
    if not values:
        return None
    return {"最大值": max(values), "最小值": min(values),
            "平均值": sum(values) / len(values), "中位數": median(values)}


def histogram(rows, field, bins=10):
    values = [Decimal(str(row[field])) for row in rows if row.get(field) is not None]
    if not values:
        return []
    low, high = min(values), max(values)
    if low == high:
        return [{"區間": str(low), "筆數": len(values)}]
    width = (high - low) / bins
    counts = [0] * bins
    for value in values:
        counts[min(bins - 1, int((value - low) / width))] += 1
    return [{"區間": f"{low + width * i:.4f}–{low + width * (i + 1):.4f}",
             "筆數": count} for i, count in enumerate(counts)]


def category_stats(rows, category, target):
    groups = defaultdict(list)
    for row in rows:
        if row.get(target) is not None:
            groups[_category_label(row.get(category))].append(Decimal(str(row[target])))
    total = sum(map(len, groups.values()))
    return [{"類別": name, "數量": len(values), "比例 (%)": len(values) * 100 / total,
             "平均值": float(sum(values) / len(values)), "總和": float(sum(values))}
            for name, values in sorted(groups.items())]


def trend_points(rows, time_field, target):
    groups = defaultdict(list)
    for row in rows:
        if row.get(time_field) is not None and row.get(target) is not None:
            groups[timestamp(row[time_field])].append(Decimal(str(row[target])))
    return [{"時間": when, "數值": float(sum(values) / len(values))}
            for when, values in sorted(groups.items())]


def _category_label(value):
    return "空值／未填" if value is None else str(value)


def render_filters(dataset, rows):
    fields = {field["name"]: field for field in dataset["fields"] if field["data_type"] in SUPPORTED}
    selected = st.multiselect("篩選欄位", list(fields), key=f"analysis_filters_{dataset['id']}")
    conditions = {}
    for name in selected:
        kind = fields[name]["data_type"]
        values = [row.get(name) for row in rows if row.get(name) is not None]
        st.markdown(f"**{name}**（{kind}）")
        prefix = f"analysis_filter_{dataset['id']}_{name}_"
        if not values and kind not in CATEGORICAL:
            st.info("此欄位沒有可篩選的非空值。")
            conditions[name] = (kind, set())
            continue
        if kind == "datetime":
            points = [timestamp(value) for value in values]
            start, end = min(points), max(points)
            left, right = st.columns(2)
            with left:
                start_date = st.date_input(f"{name} 起始日期", value=start.date(), key=prefix+"start_date")
                start_time = st.time_input(f"{name} 起始時間", value=start.time(), key=prefix+"start_time")
            with right:
                end_date = st.date_input(f"{name} 終止日期", value=end.date(), key=prefix+"end_date")
                end_time = st.time_input(f"{name} 終止時間", value=end.time(), key=prefix+"end_time")
            if None in (start_date, start_time, end_date, end_time):
                st.error("起始日期與時間、終止日期與時間都必須填寫。")
                return None
            start_value = datetime.combine(start_date, start_time, timezone.utc)
            end_value = datetime.combine(end_date, end_time, timezone.utc)
            if start_value > end_value:
                st.error("起始時間不可晚於終止時間。")
                return None
            conditions[name] = (kind, (start_value, end_value))
        elif kind in NUMERIC:
            minimum, maximum = min(map(lambda v: Decimal(str(v)), values)), max(map(lambda v: Decimal(str(v)), values))
            left, right = st.columns(2)
            with left:
                lower = st.text_input(f"{name} 最小值", value=str(minimum), key=prefix+"min")
            with right:
                upper = st.text_input(f"{name} 最大值", value=str(maximum), key=prefix+"max")
            try:
                lower, upper = Decimal(lower), Decimal(upper)
                if not lower.is_finite() or not upper.is_finite() or lower > upper:
                    raise ValueError
            except (ValueError, ArithmeticError, InvalidOperation):
                st.error(f"{name} 需填寫有效的最小值與最大值，且最小值不可大於最大值。")
                return None
            conditions[name] = (kind, (lower, upper))
        else:
            choices = sorted(set(values), key=str)
            if any(row.get(name) is None for row in rows):
                choices.append(None)
            checked = set()
            for index, choice in enumerate(choices):
                if st.checkbox(f"{name}：{_category_label(choice)}", value=True,
                               key=prefix+str(index)):
                    checked.add(choice)
            conditions[name] = (kind, checked)
    return conditions


def render_statistics(dataset, rows):
    fields = {field["name"]: field["data_type"] for field in dataset["fields"]
              if field["data_type"] in SUPPORTED}
    selected = st.selectbox("分析欄位", list(fields), index=None,
                            placeholder="選擇分析欄位", key=f"analysis_target_{dataset['id']}")
    if selected is None:
        return
    kind = fields[selected]
    numeric_fields = [name for name, dtype in fields.items() if dtype in NUMERIC]
    if kind in NUMERIC:
        stats = numeric_stats(rows, selected)
        if stats is None:
            st.info("篩選後沒有可分析的數值。")
            return
        st.markdown("**數值分布（Histogram）**")
        st.bar_chart(pd.DataFrame(histogram(rows, selected)).set_index("區間"))
        st.markdown("**最大值、最小值、平均值與中位數**")
        st.bar_chart(pd.DataFrame({"統計值": {name: float(value) for name, value in stats.items()}}))
    elif kind in CATEGORICAL:
        target = st.selectbox("群組計算目標欄位", numeric_fields, index=None,
                              placeholder="選擇數值欄位", key=f"analysis_group_target_{dataset['id']}")
        if target is None:
            return
        aggregate = category_stats(rows, selected, target)
        if not aggregate:
            st.info("篩選後沒有可聚合的資料。")
            return
        measure = st.selectbox("聚合方式", ["數量", "比例 (%)", "平均值", "總和"],
                               key=f"analysis_group_measure_{dataset['id']}")
        st.bar_chart(pd.DataFrame(aggregate).set_index("類別")[[measure]])
        st.dataframe(aggregate, hide_index=True, use_container_width=True)
    else:
        target = st.selectbox("趨勢目標欄位", numeric_fields, index=None,
                              placeholder="選擇數值欄位", key=f"analysis_trend_target_{dataset['id']}")
        if target is None:
            return
        points = trend_points(rows, selected, target)
        if not points:
            st.info("篩選後沒有可繪製的時間資料。")
            return
        st.line_chart(pd.DataFrame(points).set_index("時間"))


def render_analysis(dataset, request):
    rows = load_records(request, dataset["id"])
    st.markdown("### 篩選器")
    conditions = render_filters(dataset, rows)
    if conditions is None:
        return
    filtered = apply_filters(rows, conditions)
    st.caption(f"分析範圍：{len(filtered)}／{len(rows)} 筆。篩選條件僅用於統計圖表；下方資料表維持完整數據集。")
    st.markdown("### 統計分析")
    render_statistics(dataset, filtered)
