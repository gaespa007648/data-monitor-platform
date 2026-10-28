"""Shared numeric display precision for every Streamlit page."""
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, localcontext


DECIMAL_PLACES = 3
QUANTUM = Decimal(1).scaleb(-DECIMAL_PLACES)
CHART_NUMBER_FORMAT = f",.{DECIMAL_PLACES}~f"


def _quantize(value, rounding=ROUND_HALF_UP):
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("Non-finite number")
    with localcontext() as context:
        digits = number.as_tuple().digits
        exponent = number.as_tuple().exponent
        context.prec = max(28, len(digits) + max(exponent, 0) + DECIMAL_PLACES + 4)
        return number.quantize(QUANTUM, rounding=rounding)


def display_number(value):
    """Round for display only, dropping trailing zeroes without changing stored data."""
    try:
        rounded = _quantize(value)
    except (InvalidOperation, ValueError, TypeError):
        return str(value)
    if rounded == 0:
        rounded = abs(rounded)
    shown = format(rounded, "f")
    return shown.rstrip("0").rstrip(".") if "." in shown else shown


def chart_number(value):
    return float(_quantize(value))


def display_bounds(minimum, maximum):
    """Outward rounding keeps every value inside default inclusive filters."""
    return (display_number(_quantize(minimum, ROUND_FLOOR)),
            display_number(_quantize(maximum, ROUND_CEILING)))


def display_rows(rows, fields, value_types=None):
    decimal_names = {field["name"] for field in fields if field.get("data_type") == "decimal"}
    value_types = value_types or {}
    return [{name: display_number(value) if value is not None and (
                 name in decimal_names or value_types.get(name) == "decimal"
                 or isinstance(value, (float, Decimal))) else value
             for name, value in row.items()} for row in rows]
