"""Importing customers with opening balances from a spreadsheet (DOM-017; BR-24; REQ-062, REQ-063).

Everything here is pure: bytes in, rows and problems out. A file is read with the standard library only,
and never trusted: what it expands to, how many cells it has and what its XML declares are all bounded
before anything is kept. Problems are reported as codes with a row number, never as free text, so no
content of the file is repeated in an error.
"""

import csv
import hashlib
import io
import json
import re
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from uuid import UUID
from xml.parsers import expat

from qarz.domain.files import MAX_FILE_BYTES
from qarz.domain.names import normalize_name, unify_apostrophes
from qarz.domain.phones import normalize_phone

MAX_ROWS = 2000
# What a file may expand to, and how much of it is looked at. A real file of 2 000 rows is far below these.
MAX_EXPANDED_BYTES = 16 * 1024 * 1024
MAX_CELLS = 200_000
MAX_COLUMNS = 64
MAX_CELL_CHARS = 2000
MAX_ARCHIVE_MEMBERS = 2000

MIN_AMOUNT = 100
MAX_AMOUNT = 100_000_000
NAME_MAX = 80
NOTE_MAX = 200
# An old debt may already be overdue: its promised date may lie up to a year back, and up to a year ahead.
PROMISE_PAST_DAYS = 365
PROMISE_FUTURE_DAYS = 365
# BR-24: a whole import can be undone for this long after it was applied.
UNDO_WINDOW = timedelta(hours=24)

XLSX, CSV = "xlsx", "csv"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSV_MIME = "text/csv"
MIMES = {XLSX: XLSX_MIME, CSV: CSV_MIME}

NAME, PHONE, AMOUNT, PROMISED, NOTE = "name", "phone", "amount", "promised_date", "note"
COLUMNS = (NAME, PHONE, AMOUNT, PROMISED, NOTE)
REQUIRED_COLUMNS = (NAME, AMOUNT)

# Header names a column is recognised by, in Uzbek (Latin and Cyrillic), Russian and English.
_HEADERS = {
    NAME: ("ism", "ismi", "mijoz", "mijoz ismi", "исм", "мижоз", "имя", "клиент", "имя клиента", "фио", "name"),
    PHONE: ("telefon", "telefon raqami", "tel", "телефон", "номер телефона", "phone"),
    AMOUNT: ("qarz", "qarzi", "qarz summasi", "summa", "қарз", "сумма", "долг", "сумма долга", "amount"),
    PROMISED: (
        "muddat",
        "to'lash muddati",
        "to'lash sanasi",
        "sana",
        "муддат",
        "срок",
        "срок оплаты",
        "дата",
        "дата оплаты",
        "date",
    ),
    NOTE: ("izoh", "изоҳ", "заметка", "примечание", "комментарий", "note"),
}
_COLUMN_OF = {header: column for column, headers in _HEADERS.items() for header in headers}
TEMPLATE_HEADERS = {
    "uz": ("Ism", "Telefon", "Qarz summasi", "To'lash muddati", "Izoh"),
    "ru": ("Имя", "Телефон", "Сумма долга", "Срок оплаты", "Примечание"),
}


class FileProblem(StrEnum):
    """Why a file cannot be read as an import at all. Nothing is kept of such a file."""

    EMPTY = "empty"
    TOO_LARGE = "too_large"
    TYPE = "not_a_spreadsheet"  # neither an .xlsx workbook nor text
    ENCODING = "encoding"  # text, but not UTF-8
    MALFORMED = "malformed"
    EXPANDS_TOO_MUCH = "expands_too_much"
    NO_HEADER = "no_header"
    MISSING_COLUMN = "missing_column"  # the name or the amount column is not there
    UNKNOWN_COLUMN = "unknown_column"
    DUPLICATE_COLUMN = "duplicate_column"
    NO_ROWS = "no_rows"
    TOO_MANY_ROWS = "too_many_rows"


class RowProblem(StrEnum):
    NAME_MISSING = "name_missing"
    NAME_TOO_LONG = "name_too_long"
    PHONE_INVALID = "phone_invalid"
    AMOUNT_MISSING = "amount_missing"
    AMOUNT_INVALID = "amount_invalid"
    AMOUNT_NOT_WHOLE = "amount_not_whole"
    AMOUNT_TOO_SMALL = "amount_too_small"
    AMOUNT_TOO_LARGE = "amount_too_large"
    DATE_INVALID = "date_invalid"
    DATE_TOO_OLD = "date_too_old"
    DATE_TOO_FAR = "date_too_far"
    NOTE_TOO_LONG = "note_too_long"
    # Found when the rows are set against the shop's customers:
    AMBIGUOUS_CUSTOMER = "ambiguous_customer"  # more than one customer it could mean
    CUSTOMER_ARCHIVED = "customer_archived"


@dataclass(frozen=True)
class RowError:
    row: int  # as numbered in the spreadsheet: the header is row 1
    column: str
    code: RowProblem


@dataclass(frozen=True)
class ImportRow:
    row: int
    name: str
    name_norm: str
    phone: str | None
    amount: int
    promised: date | None  # None: the shop's default applies (BR-1)
    note: str | None


@dataclass(frozen=True)
class ParsedFile:
    kind: str  # XLSX or CSV
    rows: tuple[ImportRow, ...]  # the rows without a problem
    errors: tuple[RowError, ...]
    total: int  # data rows in the file, with or without a problem


class _Refused(Exception):
    def __init__(self, problem: FileProblem) -> None:
        super().__init__(problem.value)
        self.problem = problem


Table = list[tuple[int, list[str]]]


# --- reading a workbook --------------------------------------------------------------------------------

_CELL_REF = re.compile(r"([A-Za-z]{1,3})([0-9]{1,7})")
_SHEET_NAME = re.compile(r"xl/worksheets/sheet([0-9]{1,4})\.xml")
_SHARED_STRINGS = "xl/sharedStrings.xml"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _parser() -> expat.XMLParserType:
    parser = expat.ParserCreate()

    def no_doctype(*_: object) -> None:
        # A workbook has no document type. Refusing one refuses every entity trick with it.
        raise _Refused(FileProblem.MALFORMED)

    parser.StartDoctypeDeclHandler = no_doctype
    parser.EntityDeclHandler = no_doctype
    parser.buffer_text = True
    return parser


def _feed(parser: expat.XMLParserType, data: bytes) -> None:
    try:
        parser.Parse(data, True)
    except expat.ExpatError:
        raise _Refused(FileProblem.MALFORMED) from None


def _column_index(letters: str) -> int:
    index = 0
    for letter in letters.upper():
        index = index * 26 + (ord(letter) - ord("A") + 1)
    return index - 1


def _number_text(raw: str) -> str:
    """A numeric cell as a person would have typed it: 45000.0 and 4.5E4 are both "45000"."""
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return raw
    if not value.is_finite() or value.adjusted() > 20:
        return raw
    return str(int(value)) if value == value.to_integral_value() else format(value, "f")


def _shared_strings(data: bytes) -> list[str]:
    strings: list[str] = []
    parts: list[str] = []
    depth_skipped = 0
    in_text = False
    size = 0

    def start(tag: str, _: dict[str, str]) -> None:
        nonlocal depth_skipped, in_text
        name = _local(tag)
        if name == "si":
            parts.clear()
        elif name == "rPh" or depth_skipped:  # phonetic hints are not part of the text
            depth_skipped += 1
        elif name == "t":
            in_text = True

    def end(tag: str) -> None:
        nonlocal depth_skipped, in_text
        name = _local(tag)
        if depth_skipped:
            depth_skipped -= 1
        elif name == "t":
            in_text = False
        elif name == "si":
            strings.append("".join(parts))
            if len(strings) > MAX_CELLS:
                raise _Refused(FileProblem.EXPANDS_TOO_MUCH)

    def text(chunk: str) -> None:
        nonlocal size
        if in_text and not depth_skipped:
            size += len(chunk)
            if size > MAX_EXPANDED_BYTES:
                raise _Refused(FileProblem.EXPANDS_TOO_MUCH)
            parts.append(chunk)

    parser = _parser()
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = text
    _feed(parser, data)
    return strings


def _sheet_rows(data: bytes, strings: Sequence[str]) -> Table:
    rows: Table = []
    cells: dict[int, str] = {}
    row_number = 0
    column = -1
    cell_type = ""
    value: list[str] = []
    capture = False
    count = 0

    def start(tag: str, attributes: dict[str, str]) -> None:
        nonlocal row_number, column, cell_type, capture, count
        name = _local(tag)
        if name == "row":
            declared = attributes.get("r", "")
            row_number = int(declared) if declared.isdigit() and len(declared) <= 7 else row_number + 1
            cells.clear()
            column = -1
        elif name == "c":
            count += 1
            if count > MAX_CELLS:
                raise _Refused(FileProblem.EXPANDS_TOO_MUCH)
            reference = _CELL_REF.fullmatch(attributes.get("r", ""))
            column = _column_index(reference.group(1)) if reference else column + 1
            cell_type = attributes.get("t", "n")
            value.clear()
        elif name in ("v", "t"):
            capture = True

    def end(tag: str) -> None:
        nonlocal capture
        name = _local(tag)
        if name in ("v", "t"):
            capture = False
        elif name == "c":
            raw = "".join(value)
            if cell_type == "s":
                shown = strings[int(raw)] if raw.isdigit() and int(raw) < len(strings) else ""
            elif cell_type == "n":
                shown = _number_text(raw.strip())
            else:
                shown = raw
            if 0 <= column < MAX_COLUMNS and shown.strip():
                cells[column] = shown[:MAX_CELL_CHARS]
        elif name == "row" and cells:
            width = max(cells) + 1
            rows.append((row_number, [cells.get(index, "") for index in range(width)]))
            if len(rows) > MAX_ROWS + 1:
                raise _Refused(FileProblem.TOO_MANY_ROWS)

    def text(chunk: str) -> None:
        if capture:
            value.append(chunk)

    parser = _parser()
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = text
    _feed(parser, data)
    return rows


def _member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    if info.flag_bits & 0x1:  # encrypted
        raise _Refused(FileProblem.MALFORMED)
    # The declared size is not believed: at most the limit and one byte more is ever expanded.
    with archive.open(info) as member:
        data = member.read(MAX_EXPANDED_BYTES + 1)
    if len(data) > MAX_EXPANDED_BYTES:
        raise _Refused(FileProblem.EXPANDS_TOO_MUCH)
    return data


def read_xlsx(data: bytes) -> Table:
    """The non-empty rows of a workbook's first worksheet, each with its spreadsheet row number.

    The first worksheet is the part `xl/worksheets/sheetN.xml` with the lowest N. Numbers come back as
    they would be typed; dates that the workbook stores as numbers come back as those numbers.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise _Refused(FileProblem.EXPANDS_TOO_MUCH)
            sheets = sorted(
                (int(match.group(1)), info) for info in members if (match := _SHEET_NAME.fullmatch(info.filename))
            )
            if not sheets:
                raise _Refused(FileProblem.MALFORMED)
            shared = next((info for info in members if info.filename == _SHARED_STRINGS), None)
            strings = [] if shared is None else _shared_strings(_member(archive, shared))
            return _sheet_rows(_member(archive, sheets[0][1]), strings)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, EOFError, OSError, RuntimeError):
        # A damaged archive, one that needs a password, or a compression method that is not available.
        raise _Refused(FileProblem.MALFORMED) from None


def read_csv(data: bytes) -> Table:
    """The non-empty records of a UTF-8 text file. The delimiter is the comma, semicolon or tab its
    first line uses most."""
    if b"\x00" in data:
        raise _Refused(FileProblem.TYPE)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise _Refused(FileProblem.ENCODING) from None
    first = next((line for line in text.splitlines() if line.strip()), "")
    delimiter = max(",;\t", key=first.count)
    rows: Table = []
    try:
        for number, record in enumerate(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter), start=1):
            cells = [cell[:MAX_CELL_CHARS] for cell in record[:MAX_COLUMNS]]
            if any(cell.strip() for cell in cells):
                rows.append((number, cells))
                if len(rows) > MAX_ROWS + 1:
                    raise _Refused(FileProblem.TOO_MANY_ROWS)
    except csv.Error:
        raise _Refused(FileProblem.MALFORMED) from None
    return rows


# --- from cells to rows --------------------------------------------------------------------------------

_SPACES = str.maketrans("", "", "    ")
_DATE_FORMS = (
    re.compile(r"(?P<d>[0-9]{1,2})[./-](?P<m>[0-9]{1,2})[./-](?P<y>[0-9]{4})"),
    re.compile(r"(?P<y>[0-9]{4})-(?P<m>[0-9]{1,2})-(?P<d>[0-9]{1,2})(?:[T ]00:00:00)?"),
)
_SERIAL_EPOCH = date(1899, 12, 30)
# Day numbers of the years 1954 to 2119: what a date cell of a workbook holds.
_SERIAL_MIN, _SERIAL_MAX = 20_000, 80_000


def _header(table: Table) -> dict[str, int]:
    if not table:
        raise _Refused(FileProblem.NO_HEADER)
    found: dict[str, int] = {}
    for index, cell in enumerate(table[0][1]):
        title = " ".join(unify_apostrophes(cell).replace("*", " ").replace(":", " ").lower().split())
        if not title:
            continue  # a column without a title carries nothing that is imported
        column = _COLUMN_OF.get(title)
        if column is None:
            raise _Refused(FileProblem.UNKNOWN_COLUMN)
        if column in found:
            raise _Refused(FileProblem.DUPLICATE_COLUMN)
        found[column] = index
    if any(column not in found for column in REQUIRED_COLUMNS):
        raise _Refused(FileProblem.MISSING_COLUMN)
    return found


def parse_amount(raw: str) -> int | RowProblem:
    """A whole amount of so'm between the smallest and the largest an entry may carry."""
    text = raw.translate(_SPACES)
    if not text:
        return RowProblem.AMOUNT_MISSING
    # Only the digits 0 to 9: other scripts have digits too, and nobody checking the file would read them.
    if not re.fullmatch(r"[+-]?[0-9]{1,15}(?:\.[0-9]{1,6})?", text):
        return RowProblem.AMOUNT_INVALID
    value = Decimal(text)
    if value != value.to_integral_value():
        return RowProblem.AMOUNT_NOT_WHOLE
    if value < MIN_AMOUNT:
        return RowProblem.AMOUNT_TOO_SMALL
    if value > MAX_AMOUNT:
        return RowProblem.AMOUNT_TOO_LARGE
    return int(value)


def parse_date(raw: str, today: date) -> date | RowProblem | None:
    """A promised date written as 25.10.2026, 25/10/2026 or 2026-10-25, or held as a workbook day number.

    None when the cell is empty: the shop's default then applies.
    """
    text = raw.strip()
    if not text:
        return None
    found: date | None = None
    if text.isascii() and text.isdigit() and _SERIAL_MIN <= int(text) <= _SERIAL_MAX:
        found = _SERIAL_EPOCH + timedelta(days=int(text))
    else:
        for form in _DATE_FORMS:
            match = form.fullmatch(text)
            if match is not None:
                try:
                    found = date(int(match["y"]), int(match["m"]), int(match["d"]))
                except ValueError:
                    return RowProblem.DATE_INVALID
                break
    if found is None:
        return RowProblem.DATE_INVALID
    if found < today - timedelta(days=PROMISE_PAST_DAYS):
        return RowProblem.DATE_TOO_OLD
    if found > today + timedelta(days=PROMISE_FUTURE_DAYS):
        return RowProblem.DATE_TOO_FAR
    return found


def _row(number: int, cells: Sequence[str], header: dict[str, int], today: date) -> ImportRow | list[RowError]:
    def cell(column: str) -> str:
        index = header.get(column)
        return cells[index] if index is not None and index < len(cells) else ""

    errors: list[RowError] = []
    name = " ".join(cell(NAME).split())
    if not name:
        errors.append(RowError(number, NAME, RowProblem.NAME_MISSING))
    elif len(name) > NAME_MAX:
        errors.append(RowError(number, NAME, RowProblem.NAME_TOO_LONG))

    phone: str | None = None
    if cell(PHONE).strip():
        phone = normalize_phone(cell(PHONE))
        if phone is None:
            errors.append(RowError(number, PHONE, RowProblem.PHONE_INVALID))

    amount = parse_amount(cell(AMOUNT))
    if isinstance(amount, RowProblem):
        errors.append(RowError(number, AMOUNT, amount))

    promised = parse_date(cell(PROMISED), today)
    if isinstance(promised, RowProblem):
        errors.append(RowError(number, PROMISED, promised))

    note = " ".join(cell(NOTE).split()) or None
    if note is not None and len(note) > NOTE_MAX:
        errors.append(RowError(number, NOTE, RowProblem.NOTE_TOO_LONG))

    if errors or isinstance(amount, RowProblem) or isinstance(promised, RowProblem):
        return errors
    return ImportRow(number, name, normalize_name(name), phone, amount, promised, note)


def parse(data: bytes, today: date) -> ParsedFile | FileProblem:
    """Read an uploaded file. `today` is the Tashkent calendar date, against which promised dates are bound.

    A file that cannot be read as an import at all gives a `FileProblem`. Otherwise every data row is
    either among the rows or has at least one error, so nothing is dropped without being reported.
    """
    if not data:
        return FileProblem.EMPTY
    if len(data) > MAX_FILE_BYTES:
        return FileProblem.TOO_LARGE
    try:
        kind = XLSX if data[:4] == b"PK\x03\x04" else CSV
        table = read_xlsx(data) if kind == XLSX else read_csv(data)
        header = _header(table)
    except _Refused as refused:
        return refused.problem
    body = table[1:]
    if not body:
        return FileProblem.NO_ROWS
    rows: list[ImportRow] = []
    errors: list[RowError] = []
    for number, cells in body:
        outcome = _row(number, cells, header, today)
        if isinstance(outcome, ImportRow):
            rows.append(outcome)
        else:
            errors.extend(outcome)
    return ParsedFile(kind, tuple(rows), tuple(errors), len(body))


# --- setting the rows against the shop's customers -----------------------------------------------------

CREATE, EXISTING, SAME_AS_ROW = "create", "existing", "same_as_row"
BY_PHONE, BY_NAME = "phone", "name"


@dataclass(frozen=True)
class Candidate:
    """A customer of the shop that a row could mean."""

    customer_id: UUID
    name_norm: str
    phone: str | None
    archived: bool


@dataclass(frozen=True)
class PlannedRow:
    """What applying the import would do with one row. Nothing is merged that is not said here."""

    row: ImportRow
    action: str  # CREATE: a new customer; EXISTING: added to a customer of the shop; SAME_AS_ROW: see `first_row`
    customer_id: UUID | None = None  # for EXISTING
    matched_by: str | None = None  # BY_PHONE or BY_NAME; None for CREATE
    first_row: int | None = None  # for SAME_AS_ROW: the earlier row whose new customer this row is added to


def _match(row: ImportRow, pool: Sequence[tuple[str, str | None]]) -> tuple[int, str] | RowProblem | None:
    """Index in the pool (of normalized names and phones) of whom the row means, and by what.

    The phone decides before the name.

    Two different phone numbers are two people, whatever their names; a name alone matches only when
    at most one side has a phone.
    """
    if row.phone is not None:
        by_phone = [index for index, (_, phone) in enumerate(pool) if phone == row.phone]
        if len(by_phone) > 1:
            return RowProblem.AMBIGUOUS_CUSTOMER
        if by_phone:
            return by_phone[0], BY_PHONE
    by_name = [
        index
        for index, (name_norm, phone) in enumerate(pool)
        if name_norm == row.name_norm and (phone is None or row.phone is None)
    ]
    if len(by_name) > 1:
        return RowProblem.AMBIGUOUS_CUSTOMER
    return (by_name[0], BY_NAME) if by_name else None


def plan(rows: Sequence[ImportRow], customers: Sequence[Candidate]) -> tuple[list[PlannedRow], list[RowError]]:
    """Decide for every row whether it opens a new customer or goes to one that exists or to an earlier row's.

    Rows are taken in file order. A customer of the shop is looked for first; only then an earlier row
    of the same file. A row that could mean more than one customer, or means an archived one, is an error.
    """
    planned: list[PlannedRow] = []
    errors: list[RowError] = []
    known = [(customer.name_norm, customer.phone) for customer in customers]
    new: list[tuple[str, str | None]] = []
    new_rows: list[int] = []
    for row in rows:
        found = _match(row, known)
        if found is None:
            again = _match(row, new)
            if isinstance(again, RowProblem):
                errors.append(RowError(row.row, NAME, again))
            elif again is None:
                new.append((row.name_norm, row.phone))
                new_rows.append(row.row)
                planned.append(PlannedRow(row, CREATE))
            else:
                planned.append(PlannedRow(row, SAME_AS_ROW, matched_by=again[1], first_row=new_rows[again[0]]))
        elif isinstance(found, RowProblem):
            errors.append(RowError(row.row, NAME, found))
        elif customers[found[0]].archived:
            errors.append(RowError(row.row, NAME, RowProblem.CUSTOMER_ARCHIVED))
        else:
            planned.append(PlannedRow(row, EXISTING, customers[found[0]].customer_id, found[1]))
    return planned, errors


def plan_token(planned: Sequence[PlannedRow]) -> str:
    """A short fingerprint of a plan. Applying must name the plan that was shown: if a customer was added
    or renamed since, the fingerprint differs and nothing is applied on a preview nobody saw."""
    facts = [
        [
            item.row.row,
            item.row.name,
            item.row.phone,
            item.row.amount,
            None if item.row.promised is None else item.row.promised.isoformat(),
            item.row.note,
            item.action,
            None if item.customer_id is None else str(item.customer_id),
            item.first_row,
        ]
        for item in planned
    ]
    canonical = json.dumps(facts, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


# --- undo ------------------------------------------------------------------------------------------------


class UndoRefusal(StrEnum):
    NOT_APPLIED = "not_applied"
    TOO_LATE = "too_late"


def may_undo(status: str, applied_at: datetime | None, now: datetime) -> UndoRefusal | None:
    """BR-24: only an applied import, and for 24 hours. The last moment of the twenty-fourth hour is in time."""
    if status != "applied" or applied_at is None:
        return UndoRefusal.NOT_APPLIED
    if now - applied_at > UNDO_WINDOW:
        return UndoRefusal.TOO_LATE
    return None


# --- writing a workbook: the published template ----------------------------------------------------------

_ZIP_TIME = (2026, 1, 1, 0, 0, 0)
_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    '<Override PartName="/xl/worksheets/sheet1.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
    '<Override PartName="/xl/styles.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
    "</Types>"
)
_ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="xl/workbook.xml"/>'
    "</Relationships>"
)
_WORKBOOK = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
    '<sheets><sheet name="Import" sheetId="1" r:id="rId1"/></sheets>'
    "</workbook>"
)
_WORKBOOK_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
    'Target="worksheets/sheet1.xml"/>'
    '<Relationship Id="rId2" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
    'Target="styles.xml"/>'
    "</Relationships>"
)
# Two cell formats: 0 is the general one; 1 is text ("@"), so a phone number keeps its plus sign and a
# date stays as it was typed.
_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
    '<fills count="2"><fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill></fills>'
    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="49" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>'
    "</styleSheet>"
)
_XML_ESCAPES = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"})


def _letters(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(ord("A") + rest) + letters
    return letters


def write_xlsx(rows: Sequence[Sequence[str]], text_columns: Sequence[int] = ()) -> bytes:
    """A one-sheet workbook of text cells. The same bytes for the same rows.

    `text_columns` are formatted as text for their whole length, so what is typed there is kept as typed.
    """
    width = max((len(row) for row in rows), default=0)
    columns = "".join(
        f'<col min="{index + 1}" max="{index + 1}" width="22" customWidth="1"'
        + (' style="1"' if index in text_columns else "")
        + "/>"
        for index in range(width)
    )
    body = "".join(
        f'<row r="{number}">'
        + "".join(
            f'<c r="{_letters(index)}{number}" t="inlineStr"'
            + (' s="1"' if index in text_columns else "")
            + f'><is><t xml:space="preserve">{cell.translate(_XML_ESCAPES)}</t></is></c>'
            for index, cell in enumerate(row)
            if cell
        )
        + "</row>"
        for number, row in enumerate(rows, start=1)
    )
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        + (f"<cols>{columns}</cols>" if columns else "")
        + f"<sheetData>{body}</sheetData></worksheet>"
    )
    parts = {
        "[Content_Types].xml": _CONTENT_TYPES,
        "_rels/.rels": _ROOT_RELS,
        "xl/workbook.xml": _WORKBOOK,
        "xl/_rels/workbook.xml.rels": _WORKBOOK_RELS,
        "xl/styles.xml": _STYLES,
        "xl/worksheets/sheet1.xml": sheet,
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(zipfile.ZipInfo(name, _ZIP_TIME), content.encode("utf-8"), zipfile.ZIP_DEFLATED)
    return out.getvalue()


def template(lang: str) -> bytes:
    """The published template (REQ-062): the five column titles, in the caller's language."""
    headers = TEMPLATE_HEADERS.get(lang, TEMPLATE_HEADERS["uz"])
    return write_xlsx([list(headers)], text_columns=(COLUMNS.index(PHONE), COLUMNS.index(PROMISED)))
