"""Spreadsheet parsing into rows, then into per-sheet markdown tables or plain text.

`.xls` (BIFF, the pre-2007 binary format) is not readable by openpyxl, so it goes through
xlrd. xlrd 2.x reads only that legacy format, which makes it a clean companion to openpyxl
rather than a replacement.
"""

import csv

import openpyxl

MAX_TABLE_ROWS = 100


def rows_by_sheet(path: str, ext: str) -> list[tuple[str, list[list[str]]]]:
    """Return (sheet_name, rows) per sheet, every cell already rendered as text."""
    ext = ext.lower()
    if ext == "csv":
        with open(path, newline="", encoding="utf-8", errors="replace") as f:
            return [("Sheet1", [list(row) for row in csv.reader(f)])]
    if ext == "xls":
        return _xls_rows(path)
    return _xlsx_rows(path)


def _xlsx_rows(path: str) -> list[tuple[str, list[list[str]]]]:
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        return [
            (
                sheet_name,
                [
                    [str(cell) if cell is not None else "" for cell in row]
                    for row in workbook[sheet_name].iter_rows(values_only=True)
                ],
            )
            for sheet_name in workbook.sheetnames
        ]
    finally:
        workbook.close()


def _xls_rows(path: str) -> list[tuple[str, list[list[str]]]]:
    import xlrd

    # on_demand keeps a large legacy workbook from being fully parsed up front; `sheet_by_name`
    # loads each sheet on first use.
    book = xlrd.open_workbook(path, on_demand=True)
    try:
        sheets: list[tuple[str, list[list[str]]]] = []
        for name in book.sheet_names():
            sheet = book.sheet_by_name(name)
            rows = [
                [_xls_cell(book, sheet, r, c) for c in range(sheet.ncols)]
                for r in range(sheet.nrows)
            ]
            sheets.append((name, rows))
        return sheets
    finally:
        book.release_resources()


def _xls_cell(book, sheet, row: int, column: int) -> str:
    """Render one xlrd cell as text, matching what Excel would show."""
    import xlrd

    cell = sheet.cell(row, column)
    kind = cell.ctype
    if kind in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
        return ""
    if kind == xlrd.XL_CELL_TEXT:
        return cell.value
    if kind == xlrd.XL_CELL_NUMBER:
        value = cell.value
        # Integral floats are almost always whole numbers, and "3.0" reads as noise.
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    if kind == xlrd.XL_CELL_DATE:
        try:
            stamp = xlrd.xldate_as_datetime(cell.value, book.datemode)
        except (ValueError, OverflowError):
            return str(cell.value)
        if (stamp.hour, stamp.minute, stamp.second) == (0, 0, 0):
            return stamp.strftime("%Y-%m-%d")
        return stamp.strftime("%Y-%m-%d %H:%M:%S")
    if kind == xlrd.XL_CELL_BOOLEAN:
        return "TRUE" if cell.value else "FALSE"
    # XL_CELL_ERROR and anything xlrd grows later.
    return str(cell.value)


def parse_sheets(path: str, ext: str) -> list[tuple[str, str]]:
    """Return (sheet_name, markdown_table) per sheet."""
    sheets = []
    for name, rows in rows_by_sheet(path, ext):
        if not rows:
            continue
        sheets.append((name, _rows_to_markdown(rows)))
    return sheets


def _rows_to_markdown(rows: list[list[str]], max_rows: int = MAX_TABLE_ROWS) -> str:
    if not rows:
        return "(empty)"
    header = "| " + " | ".join(rows[0]) + " |"
    sep = "| " + " | ".join("---" for _ in rows[0]) + " |"
    data = rows[1:max_rows + 1]
    body = "\n".join("| " + " | ".join(r) + " |" for r in data)
    truncated = f"\n\n*({len(rows) - 1 - max_rows} more rows truncated)*" if len(rows) - 1 > max_rows else ""
    return f"{header}\n{sep}\n{body}{truncated}"
