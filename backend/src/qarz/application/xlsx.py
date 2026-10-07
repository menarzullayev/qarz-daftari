"""A minimal writer of `.xlsx` workbooks, with the standard library only (specification: exports in `.xlsx`).

Rows are appended to any sheet in any order and spooled to temporary storage, so a ledger of any size is
never held in memory as a whole; `finish` then packs the sheets into the workbook.

Text is always written as an inline string, never as a formula: a name or a note that begins with `=`,
`+`, `-` or `@` is shown as typed and never calculated by the program that opens the file. Numbers are
written as numbers, so totals can be summed.
"""

import io
import re
import tempfile
import zipfile
from collections.abc import Sequence
from decimal import Decimal
from typing import IO
from xml.sax.saxutils import escape

from qarz.domain.files import XLSX

MIME = XLSX
# What a sheet can hold (the format's limit), the header row included.
MAX_ROWS = 1_048_576
MAX_NAME = 31
MAX_CELL = 32_767
_SPOOL_BYTES = 4 * 1024 * 1024
_COPY_BYTES = 1024 * 1024
# Characters XML 1.0 cannot carry at all.
_ILLEGAL = re.compile("[^\t\n\r\x20-퟿-�\U00010000-\U0010ffff]")
_BAD_IN_NAME = re.compile(r"[\[\]:*?/\\]")

Cell = str | int | Decimal | None

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    '<Override PartName="/xl/styles.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
    "{sheets}</Types>"
)
_SHEET_TYPE = (
    '<Override PartName="/xl/worksheets/sheet{n}.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
)
_ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="xl/workbook.xml"/></Relationships>'
)
_WORKBOOK = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
    "<sheets>{sheets}</sheets></workbook>"
)
_WORKBOOK_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    "{sheets}"
    '<Relationship Id="rId{styles}" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    "</Relationships>"
)
_SHEET_REL = (
    '<Relationship Id="rId{n}" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
    'Target="worksheets/sheet{n}.xml"/>'
)
# Two cell formats: 0 is plain, 1 is bold (header rows).
_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
    '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
    '<fills count="2"><fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill></fills>'
    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
    "</styleSheet>"
)
_SHEET_HEAD = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">{cols}<sheetData>'
)
_SHEET_TAIL = "</sheetData></worksheet>"


def column_letters(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def clean_text(value: str) -> str:
    """Text as a cell can hold it: no character XML cannot carry, and no longer than a cell may be."""
    return _ILLEGAL.sub("", value)[:MAX_CELL]


def sheet_name(wanted: str, part: int = 1) -> str:
    """A name a workbook accepts: at most 31 characters and none of `[]:*?/\\`; later parts are numbered."""
    suffix = "" if part == 1 else f" ({part})"
    base = _BAD_IN_NAME.sub(" ", clean_text(wanted)).strip() or "Sheet"
    return base[: MAX_NAME - len(suffix)] + suffix


def _cell(reference: str, value: Cell, style: int) -> str:
    if value is None:
        return ""
    look = f' s="{style}"' if style else ""
    if isinstance(value, bool):
        raise TypeError("a cell holds text or a number; say yes or no in words")
    if isinstance(value, int):
        return f'<c r="{reference}"{look}><v>{value}</v></c>'
    if isinstance(value, Decimal):
        return f'<c r="{reference}"{look}><v>{value:f}</v></c>'
    text = escape(clean_text(value))
    return f'<c r="{reference}"{look} t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


class _Part:
    """One worksheet of the file: at most `max_rows` rows, spooled as they come."""

    def __init__(self, name: str, widths: Sequence[int]) -> None:
        self.name = name
        self.widths = tuple(widths)
        self.rows = 0
        self.spool: IO[bytes] = tempfile.SpooledTemporaryFile(max_size=_SPOOL_BYTES)  # noqa: SIM115

    def append(self, values: Sequence[Cell], style: int) -> None:
        self.rows += 1
        cells = "".join(
            _cell(f"{column_letters(index)}{self.rows}", value, style) for index, value in enumerate(values)
        )
        self.spool.write(f'<row r="{self.rows}">{cells}</row>'.encode())

    def head(self) -> str:
        cols = "".join(
            f'<col min="{index}" max="{index}" width="{width}" customWidth="1"/>'
            for index, width in enumerate(self.widths, start=1)
        )
        return _SHEET_HEAD.format(cols=f"<cols>{cols}</cols>" if cols else "")


class Sheet:
    """A table with a header row. When it outgrows one worksheet it continues on the next, header repeated."""

    def __init__(self, name: str, header: Sequence[str], widths: Sequence[int], max_rows: int) -> None:
        self._name = name
        self._header = tuple(header)
        self._widths = tuple(widths)
        self._max_rows = max_rows
        self.parts: list[_Part] = []
        self.data_rows = 0
        self._start()

    def _start(self) -> None:
        part = _Part(sheet_name(self._name, len(self.parts) + 1), self._widths)
        self.parts.append(part)
        if self._header:
            part.append(self._header, 1)

    def append(self, values: Sequence[Cell], *, bold: bool = False) -> None:
        if self.parts[-1].rows >= self._max_rows:
            self._start()
        self.parts[-1].append(values, 1 if bold else 0)
        self.data_rows += 1


class Workbook:
    """Sheets appear in the file in the order they were added, whatever order their rows arrive in."""

    def __init__(self, *, max_rows: int = MAX_ROWS) -> None:
        if max_rows < 2:
            raise ValueError("a worksheet holds at least its header and one row")
        self._max_rows = max_rows
        self._sheets: list[Sheet] = []

    def sheet(self, name: str, header: Sequence[str] = (), widths: Sequence[int] = ()) -> Sheet:
        sheet = Sheet(name, header, widths, self._max_rows)
        self._sheets.append(sheet)
        return sheet

    def finish(self) -> bytes:
        """The workbook as bytes. The writer cannot be used afterwards."""
        parts = [part for sheet in self._sheets for part in sheet.parts]
        if not parts:
            raise ValueError("a workbook needs at least one sheet")
        if len({part.name.lower() for part in parts}) != len(parts):
            raise ValueError("two sheets of a workbook cannot share a name")
        numbers = range(1, len(parts) + 1)
        packed = io.BytesIO()
        try:
            with zipfile.ZipFile(packed, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(
                    "[Content_Types].xml",
                    _CONTENT_TYPES.format(sheets="".join(_SHEET_TYPE.format(n=n) for n in numbers)),
                )
                archive.writestr("_rels/.rels", _ROOT_RELS)
                archive.writestr(
                    "xl/workbook.xml",
                    _WORKBOOK.format(
                        sheets="".join(
                            f'<sheet name="{escape(part.name, {chr(34): "&quot;"})}" sheetId="{n}" r:id="rId{n}"/>'
                            for n, part in zip(numbers, parts, strict=True)
                        )
                    ),
                )
                archive.writestr(
                    "xl/_rels/workbook.xml.rels",
                    _WORKBOOK_RELS.format(
                        sheets="".join(_SHEET_REL.format(n=n) for n in numbers), styles=len(parts) + 1
                    ),
                )
                archive.writestr("xl/styles.xml", _STYLES)
                for n, part in zip(numbers, parts, strict=True):
                    with archive.open(f"xl/worksheets/sheet{n}.xml", "w") as target:
                        target.write(part.head().encode())
                        part.spool.seek(0)
                        while chunk := part.spool.read(_COPY_BYTES):
                            target.write(chunk)
                        target.write(_SHEET_TAIL.encode())
        finally:
            self.close()
        return packed.getvalue()

    def close(self) -> None:
        """Give back the temporary storage. Safe to call more than once."""
        for sheet in self._sheets:
            for part in sheet.parts:
                part.spool.close()
