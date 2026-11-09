"""Shared file-type classification (watcher, processors, upload)."""

PDF_TYPES = frozenset({"pdf"})
OFFICE_TYPES = frozenset({"pptx", "ppt", "docx", "doc"})
# `xls` is the pre-2007 binary format: openpyxl cannot read it, so extraction goes through
# xlrd (services/spreadsheet_extract.py). Preview does not depend on this — the ZetaOffice
# viewer opens the original file either way.
SPREADSHEET_TYPES = frozenset({"xlsx", "xls"})
IMAGE_TYPES = frozenset({"png", "jpg", "jpeg", "webp", "gif"})
HTML_TYPES = frozenset({"html", "htm"})

SIMPLE_TEXT_TYPES = frozenset({
    "md", "txt", "csv", "svg", "json", "xml",
    "yaml", "yml", "toml", "ini", "cfg", "rst", "tex", "latex",
})

# HTML isn't simple text: it goes through the webmd parser.
EXTRACTION_TYPES = PDF_TYPES | OFFICE_TYPES | SPREADSHEET_TYPES | HTML_TYPES

# Images need no extraction, but the processor still finalizes their status.
PROCESSING_TYPES = EXTRACTION_TYPES | IMAGE_TYPES
