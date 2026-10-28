"""Shared display rounding must leave source values and filter coverage intact."""
from decimal import Decimal
from pathlib import Path
import sys

sys.path.insert(0, str(Path.cwd()))
from display_format import chart_number, display_bounds, display_number, display_rows

source = Decimal("5.843333333333333333333")
assert display_number(source) == "5.843"
assert source == Decimal("5.843333333333333333333")
assert display_number("7.90000") == "7.9"
assert display_number("-1.2345") == "-1.235"
assert display_number("0.0004") == "0"
assert display_number("99999999999999999999999999999999999999") == "99999999999999999999999999999999999999"
assert chart_number(source) == 5.843
assert display_bounds("1.0001", "1.9999") == ("1", "2")
assert display_bounds("-1.0001", "-0.0001") == ("-1.001", "0")
rows = [{"price": "5.843333333", "count": 4, "title": "5.843333333"}]
fields = [{"name": "price", "data_type": "decimal"},
          {"name": "count", "data_type": "integer"},
          {"name": "title", "data_type": "string"}]
assert display_rows(rows, fields) == [{"price": "5.843", "count": 4, "title": "5.843333333"}]
assert rows[0]["price"] == "5.843333333"
assert display_rows([{"dynamic": "2.34567"}], [], {"dynamic": "decimal"}) == [{"dynamic": "2.346"}]
assert display_rows([{"dynamic": 2.34567}], []) == [{"dynamic": "2.346"}]
print("PASS: shared three-decimal display precision and outward filter bounds")
