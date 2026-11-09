"""Spreadsheet extraction across the three formats the app accepts.

`.xls` used to fall through silently: `SPREADSHEET_TYPES` only listed `xlsx`, and openpyxl
cannot read the legacy binary format, so such a document was indexed with no content at all.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api"))

from services.spreadsheet_extract import parse_sheets, rows_by_sheet  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_xls_rows_are_read_with_native_rendering():
    sheets = rows_by_sheet(str(FIXTURES / "sample.xls"), "xls")

    assert [name for name, _ in sheets]
    rows = sheets[0][1]
    assert rows[0] == ["项目", "数量", "单价", "合计"]
    assert rows[1] == ["服务器", "2", "12000", "24000"]
    # Integral numbers must not come back as "2.0".
    assert rows[2][1] == "4"
    # Empty cells stay empty rather than becoming the string "None".
    assert rows[2][3] == "14000"
    assert rows[3][2] == ""


def test_xls_dates_are_formatted_not_left_as_serial_numbers():
    rows = rows_by_sheet(str(FIXTURES / "sample.xls"), "xls")[0][1]

    dates = [cell for row in rows for cell in row if cell.count("-") == 2]
    assert "2026-12-01" in dates


def test_xls_parses_into_a_markdown_table():
    sheets = parse_sheets(str(FIXTURES / "sample.xls"), "xls")

    sheet_name, table = sheets[0]
    assert sheet_name
    assert table.splitlines()[0].startswith("| 项目 | 数量 |")
    assert "| 服务器 | 2 | 12000 | 24000 |" in table


def test_xls_is_a_processing_type():
    from domain.file_types import PROCESSING_TYPES, SPREADSHEET_TYPES

    assert "xls" in SPREADSHEET_TYPES
    assert "xls" in PROCESSING_TYPES


def test_csv_still_goes_through_the_csv_reader(tmp_path):
    path = tmp_path / "simple.csv"
    path.write_text("名称,数量\n网关,5\n", encoding="utf-8")

    sheets = rows_by_sheet(str(path), "csv")

    assert sheets == [("Sheet1", [["名称", "数量"], ["网关", "5"]])]


def test_missing_xlrd_is_reported_not_swallowed(monkeypatch):
    """A deployment without xlrd must fail loudly; silently indexing nothing is worse."""
    import services.spreadsheet_extract as module

    # `None` in sys.modules makes the next `import xlrd` raise ImportError.
    monkeypatch.setitem(sys.modules, "xlrd", None)
    with pytest.raises(ImportError):
        module.rows_by_sheet(str(FIXTURES / "sample.xls"), "xls")
