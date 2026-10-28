"""Import Vega stocks and verify datetime, numeric, category and trend analysis.

Source: https://github.com/vega/vega-datasets/blob/main/data/stocks.csv
The source dates have no timezone; this test converts each date to UTC midnight.
"""
import csv
import io
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen
from uuid import uuid4

import httpx
from streamlit.testing.v1 import AppTest

sys.path.insert(0, str(Path.cwd()))
from analysis_ui import (apply_filters, boxplot_summary, category_stats, histogram,
                         load_records, numeric_stats, trend_points)

with urlopen("https://raw.githubusercontent.com/vega/vega-datasets/main/data/stocks.csv", timeout=20) as response:
    stocks = list(csv.DictReader(io.StringIO(response.read().decode("utf-8"))))
assert len(stocks) == 560

username = os.environ["TEST_UI_USERNAME"]
password = "StockAnalysisTest123!"
base = os.getenv("API_BASE_URL", "http://api:8000")
with httpx.Client(base_url=base, timeout=60) as client:
    response = client.post("/api/v1/users", json={"username": username, "password": password})
    response.raise_for_status()
    response = client.post("/api/v1/auth/tokens", json={"username": username, "password": password})
    response.raise_for_status()
    token = response.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    user = client.get("/api/v1/users/me").json()
    response = client.post("/api/v1/datasets", json={"name": "vega_stocks", "fields": [
        {"name": "symbol", "data_type": "string"},
        {"name": "date", "data_type": "datetime"},
        {"name": "price", "data_type": "decimal"},
    ]})
    response.raise_for_status()
    dataset_id = response.json()["id"]
    content = io.StringIO(newline="")
    writer = csv.writer(content)
    writer.writerow(["symbol", "date", "price"])
    for row in stocks:
        date = datetime.strptime(row["date"], "%b %d %Y").replace(tzinfo=timezone.utc)
        writer.writerow([row["symbol"], date.isoformat(), row["price"]])
    response = client.post(f"/api/v1/datasets/{dataset_id}/imports",
        params={"schema_version": 1, "skip_first_row": True},
        headers={"Idempotency-Key": uuid4().hex},
        files={"file": ("stocks.csv", content.getvalue().encode())})
    response.raise_for_status()
    assert response.json()["row_count"] == 560

    def request(method, path, **kwargs):
        response = client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()

    rows = load_records(request, dataset_id)
    assert len(rows) == 560
    assert rows[0]["date"].endswith("+00:00")
    low = datetime(2000, 1, 1, tzinfo=timezone.utc)
    high = datetime(2000, 3, 1, tzinfo=timezone.utc)
    filtered = apply_filters(rows, {"date": ("datetime", (low, high))})
    assert filtered and all(low <= datetime.fromisoformat(row["date"]) <= high for row in filtered)
    assert any(row["date"].startswith("2000-01-01") for row in filtered)
    numeric = apply_filters(rows, {"price": ("decimal", (Decimal("39.81"), Decimal("39.81")))})
    assert numeric and all(Decimal(str(row["price"])) == Decimal("39.81") for row in numeric)
    microsoft = apply_filters(rows, {"symbol": ("string", {"MSFT"})})
    assert microsoft and all(row["symbol"] == "MSFT" for row in microsoft)
    assert len(histogram(microsoft, "price")) == 10
    narrow_bins = histogram([{"value": "0.0001"}, {"value": "0.0002"}], "value")
    assert sum(bucket["筆數"] for bucket in narrow_bins) == 2
    assert all(all(len(part.split(".")[1]) <= 3 if "." in part else True
                   for part in bucket["區間"].split("–")) for bucket in narrow_bins)
    assert len(numeric_stats(microsoft, "price")) == 4
    simple = boxplot_summary([{"value": value} for value in [1, 2, 3, 4, 5]], "value")
    assert simple == {"最小值": 1, "第一四分位數": 2, "中位數": 3,
                      "第三四分位數": 4, "最大值": 5, "平均值": 3}
    assert boxplot_summary([{"value": 7}], "value")["中位數"] == 7
    assert sum(group["數量"] for group in category_stats(rows, "symbol", "price")) == 560
    points = trend_points(microsoft, "date", "price")
    assert points and points == sorted(points, key=lambda row: row["時間"])

    definitions = Path("app.py").read_text(encoding="utf-8").rsplit('if "token" not in st.session_state:', 1)[0]
    app = AppTest.from_string(definitions + "\nanalysis_page()", default_timeout=60)
    app.session_state["token"], app.session_state["user"] = token, user
    with patch("dataset_ui.render_table", return_value=None), patch("analysis_ui.st.altair_chart") as chart:
        app.run()
        next(element for element in app.selectbox if element.label == "選擇數據集").set_value(dataset_id).run()
        next(element for element in app.multiselect if element.label == "篩選欄位").set_value(["date"]).run()
        next(element for element in app.selectbox if element.label == "分析欄位").set_value("date").run()
        next(element for element in app.selectbox if element.label == "趨勢目標欄位").set_value("price").run()
    assert not app.exception, app.exception
    assert [element.value for element in app.subheader] == ["選擇數據集", "資料分析", "數據檢視"]
    assert len(app.date_input) == 2 and len(app.time_input) == 2
    assert chart.called
    with patch("dataset_ui.render_table", return_value=None):
        app.date_input[1].set_value(None).run()
    assert not app.exception, app.exception
    assert any("都必須填寫" in item.value for item in app.error)

    with (patch("dataset_ui.render_table", return_value=None),
          patch("analysis_ui.st.altair_chart") as charts):
        app.date_input[1].set_value(max(datetime.fromisoformat(row["date"]).date() for row in rows)).run()
        next(element for element in app.selectbox if element.label == "分析欄位").set_value("price").run()
    assert not app.exception, app.exception
    assert charts.call_count >= 2
    assert len(charts.call_args.args[0].to_dict()["layer"]) == 4
    assert {item.label for item in app.metric} == {"最小值", "最大值", "平均值", "中位數"}
    assert all(len(item.value.split(".")[1]) <= 3 if "." in item.value else True for item in app.metric)
    with patch("dataset_ui.render_table", return_value=None):
        app.run()
    assert not app.exception, app.exception
    with patch("dataset_ui.render_table", return_value=None), patch("analysis_ui.st.altair_chart") as charts:
        next(element for element in app.selectbox if element.label == "分析欄位").set_value("symbol").run()
        next(element for element in app.selectbox if element.label == "群組計算目標欄位").set_value("price").run()
    assert not app.exception, app.exception
    assert charts.called

    boolean_rows = [{"active": True, "price": "1"}, {"active": False, "price": "2"}]
    assert apply_filters(boolean_rows, {"active": ("boolean", {True})}) == boolean_rows[:1]

print("PASS: 560 stock rows imported; datetime filter, numeric/category statistics and trend")
