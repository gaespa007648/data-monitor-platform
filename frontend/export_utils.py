"""Create filtered Excel workbooks for Streamlit downloads."""
import io
import re
from datetime import datetime, timezone
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell


EXCEL_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def safe_filename_part(value):
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value)).strip(" .")
    return cleaned[:80] or "dataset"


def _text_cell(sheet, value):
    cell = WriteOnlyCell(sheet, value=str(value))
    cell.data_type = "s"  # A leading '=' must remain text, never an Excel formula.
    return cell


def _excel_cell(sheet, value, kind):
    if value is None:
        return None
    if kind == "datetime":
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        cell = WriteOnlyCell(sheet, value=stamp.astimezone(timezone.utc).replace(tzinfo=None))
        cell.number_format = "yyyy-mm-dd hh:mm:ss.000"
        return cell
    if kind == "boolean" or isinstance(value, bool):
        return bool(value)
    if kind == "integer" or isinstance(value, int):
        number = int(value)
        return number if len(str(abs(number))) <= 15 else _text_cell(sheet, number)
    if kind == "decimal" or isinstance(value, (float, Decimal)):
        number = Decimal(str(value))
        return float(number) if len(number.as_tuple().digits) <= 15 else _text_cell(sheet, value)
    return _text_cell(sheet, value)


def excel_bytes(dataset, rows):
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("Data")
    fields = dataset["fields"]
    sheet.append([_text_cell(sheet, field["name"]) for field in fields])
    for row in rows:
        sheet.append([_excel_cell(sheet, row.get(field["name"]), field["data_type"])
                      for field in fields])
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()
