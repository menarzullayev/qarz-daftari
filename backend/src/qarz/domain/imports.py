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
from qarz.domain.money import RULES, Currency, parse_code, to_minor
from qarz.domain.names import normalize_name, unify_apostrophes
from qarz.domain.phones import normalize_phone
from qarz.domain.uz_cyrillic import to_cyrillic

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
# A step whose worker has been silent this long is taken by another; one started this often is given up.
STALE_AFTER = timedelta(minutes=15)
MAX_ATTEMPTS = 3

XLSX, CSV = "xlsx", "csv"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
FILE_GONE = "file_gone"  # the batch's file is no longer in the store: said like a file problem
CSV_MIME = "text/csv"
MIMES = {XLSX: XLSX_MIME, CSV: CSV_MIME}

NAME, PHONE, AMOUNT, PROMISED, NOTE = "name", "phone", "amount", "promised_date", "note"
COLUMNS = (NAME, PHONE, AMOUNT, PROMISED, NOTE)
REQUIRED_COLUMNS = (NAME, AMOUNT)
# The column a shop that works in dollars may add: "UZS" or "USD" for each row, so'm when the cell is
# empty. For every other shop it does not exist: such a title is an unknown column, as it always was.
CURRENCY = "currency"

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
_UZ_TEMPLATE = ("Ism", "Telefon", "Qarz summasi", "To'lash muddati", "Izoh")
# The titles of the published template in each language of the product (qarz.domain.languages), in the
# order of `COLUMNS`. Uzbek Cyrillic is made from the Uzbek titles, like every Uzbek Cyrillic text.
TEMPLATE_HEADERS = {
    "uz": _UZ_TEMPLATE,
    "uz-Cyrl": tuple(to_cyrillic(title) for title in _UZ_TEMPLATE),
    "ru": ("Имя", "Телефон", "Сумма долга", "Срок оплаты", "Примечание"),
    "tg": ("Ном", "Телефон", "Маблағи қарз", "Мӯҳлати пардохт", "Эзоҳ"),
    "kaa": ("Atı", "Telefon", "Qarız summası", "Tólew múddeti", "Túsindirme"),
    "en": ("Name", "Phone", "Debt amount", "Due date", "Note"),
}

# The title of the currency column, which only the template of a shop that works in dollars has, right
# after the amount it says the currency of.
_UZ_CURRENCY_TITLE = "Valyuta"
CURRENCY_TITLES = {
    "uz": _UZ_CURRENCY_TITLE,
    "uz-Cyrl": to_cyrillic(_UZ_CURRENCY_TITLE),
    "ru": "Валюта",
    "tg": "Асъор",
    "kaa": "Valyuta",
    "en": "Currency",
}


def template_headers(lang: str, *, dollars: bool = False) -> tuple[str, ...]:
    """The titles of the published template in a language; Uzbek for a language the product does not have.

    With `dollars` the currency column stands after the amount. Without, the titles are the five they
    always were.
    """
    titles = TEMPLATE_HEADERS.get(lang, TEMPLATE_HEADERS["uz"])
    if not dollars:
        return titles
    at = COLUMNS.index(AMOUNT) + 1
    return (*titles[:at], CURRENCY_TITLES.get(lang, CURRENCY_TITLES["uz"]), *titles[at:])


def header_form(title: str) -> str:
    """A column title as it is compared: lower case, one kind of apostrophe, no "*" or ":", single spaces."""
    return " ".join(unify_apostrophes(title).replace("*", " ").replace(":", " ").lower().split())


# A template is read back whatever language it was downloaded in: each of its titles names its column.
_COLUMN_OF = {header: column for column, headers in _HEADERS.items() for header in headers} | {
    header_form(title): column
    for titles in TEMPLATE_HEADERS.values()
    for column, title in zip(COLUMNS, titles, strict=True)
}

# The titles the currency column is recognised by, in a file of a shop that works in dollars.
_CURRENCY_HEADERS = frozenset({header_form(title) for title in CURRENCY_TITLES.values()})


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
    # Only in a file with a currency column, that is, of a shop that works in dollars:
    CURRENCY_UNKNOWN = "currency_unknown"  # neither UZS nor USD
    AMOUNT_TOO_PRECISE = "amount_too_precise"  # a dollar amount that is not a whole number of cents
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
    # `amount` is in this currency's minor unit: whole so'm, or cents.
    currency: Currency = Currency.UZS


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


def _header(table: Table, dollars: bool = False) -> dict[str, int]:
    if not table:
        raise _Refused(FileProblem.NO_HEADER)
    found: dict[str, int] = {}
    for index, cell in enumerate(table[0][1]):
        title = header_form(cell)
        if not title:
            continue  # a column without a title carries nothing that is imported
        column = _COLUMN_OF.get(title)
        if column is None and dollars and title in _CURRENCY_HEADERS:
            column = CURRENCY
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


_DOLLAR_AMOUNT = re.compile(r"[0-9]{1,15}(?:[.,][0-9]{1,6})?")


def parse_currency(raw: str) -> Currency | RowProblem:
    """The currency a cell names: "UZS" or "USD", in capitals or not; so'm when the cell is empty."""
    text = raw.strip().upper()
    if not text:
        return Currency.UZS
    return parse_code(text) or RowProblem.CURRENCY_UNKNOWN


def parse_amount_in(currency: Currency, raw: str) -> int | RowProblem:
    """The amount of one entry in the currency's minor unit, within the currency's own range (`RULES`).

    So'm is read as it always was (`parse_amount`). Dollars are read by `money.to_minor`: "12.5" and
    "12,50" are 1250 cents, and what is not a whole number of cents is refused, never rounded.
    """
    if currency is Currency.UZS:
        return parse_amount(raw)
    text = raw.translate(_SPACES)
    if not text:
        return RowProblem.AMOUNT_MISSING
    if not _DOLLAR_AMOUNT.fullmatch(text):
        return RowProblem.AMOUNT_INVALID
    value = to_minor(currency, text)
    if value is None:
        return RowProblem.AMOUNT_TOO_PRECISE
    if value < RULES[currency].min_entry:
        return RowProblem.AMOUNT_TOO_SMALL
    if value > RULES[currency].max_entry:
        return RowProblem.AMOUNT_TOO_LARGE
    return value


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

    # Without a currency column every row is so'm. An amount is not judged in a currency nobody knows.
    currency = parse_currency(cell(CURRENCY))
    amount: int | RowProblem | None = None
    if isinstance(currency, RowProblem):
        errors.append(RowError(number, CURRENCY, currency))
    else:
        amount = parse_amount_in(currency, cell(AMOUNT))
        if isinstance(amount, RowProblem):
            errors.append(RowError(number, AMOUNT, amount))

    promised = parse_date(cell(PROMISED), today)
    if isinstance(promised, RowProblem):
        errors.append(RowError(number, PROMISED, promised))

    note = " ".join(cell(NOTE).split()) or None
    if note is not None and len(note) > NOTE_MAX:
        errors.append(RowError(number, NOTE, RowProblem.NOTE_TOO_LONG))

    if errors or isinstance(currency, RowProblem) or not isinstance(amount, int) or isinstance(promised, RowProblem):
        return errors
    return ImportRow(number, name, normalize_name(name), phone, amount, promised, note, currency)


def sniff(data: bytes) -> str | FileProblem:
    """What a file is by its own bytes, XLSX or CSV, or why it is refused without being read.

    This is all that is decided when a file is uploaded; everything else needs the sheet to be read.
    """
    if not data:
        return FileProblem.EMPTY
    if len(data) > MAX_FILE_BYTES:
        return FileProblem.TOO_LARGE
    if data[:4] == b"PK\x03\x04":
        return XLSX
    return FileProblem.TYPE if b"\x00" in data else CSV


def parse(data: bytes, today: date, *, dollars: bool = False) -> ParsedFile | FileProblem:
    """Read an uploaded file. `today` is the Tashkent calendar date, against which promised dates are bound.

    `dollars` says that the shop works in dollars: only then is a currency column read, and may a row be
    in dollars. Without it such a column is an unknown column and every row is so'm, as before dollars.

    A file that cannot be read as an import at all gives a `FileProblem`. Otherwise every data row is
    either among the rows or has at least one error, so nothing is dropped without being reported.
    """
    kind = sniff(data)
    if isinstance(kind, FileProblem):
        return kind
    try:
        table = read_xlsx(data) if kind == XLSX else read_csv(data)
        header = _header(table, dollars)
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

    Rows are taken in file order, and each is set against the customers of the shop and the new customers
    of the rows before it, all at once: a row that could mean more than one of them, or means an archived
    customer, is an error.
    """
    planned: list[PlannedRow] = []
    errors: list[RowError] = []
    pool = [(customer.name_norm, customer.phone) for customer in customers]
    new_rows: list[int] = []  # the row that opened each new customer, in the order they follow `customers`
    for row in rows:
        found = _match(row, pool)
        if isinstance(found, RowProblem):
            errors.append(RowError(row.row, NAME, found))
        elif found is None:
            pool.append((row.name_norm, row.phone))
            new_rows.append(row.row)
            planned.append(PlannedRow(row, CREATE))
        elif found[0] >= len(customers):
            first_row = new_rows[found[0] - len(customers)]
            planned.append(PlannedRow(row, SAME_AS_ROW, matched_by=found[1], first_row=first_row))
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
            # So'm is the absence of a currency, here as everywhere: a plan of so'm rows keeps the
            # fingerprint it had before dollars.
            *([] if item.row.currency is Currency.UZS else [item.row.currency.value]),
        ]
        for item in planned
    ]
    canonical = json.dumps(facts, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


# --- undo ------------------------------------------------------------------------------------------------


class UndoRefusal(StrEnum):
    NOT_APPLIED = "not_applied"
    TOO_LATE = "too_late"


def stale_before(now: datetime) -> datetime:
    return now - STALE_AFTER


def gives_up(attempts: int) -> bool:
    """`attempts` counts the start just made. The third start is still made; a fourth is not."""
    return attempts > MAX_ATTEMPTS


def may_undo(status: str, applied_at: datetime | None, now: datetime) -> UndoRefusal | None:
    """BR-24: only an applied import, and for 24 hours. The last moment of the twenty-fourth hour is in time."""
    if status != "applied" or applied_at is None:
        return UndoRefusal.NOT_APPLIED
    if now - applied_at > UNDO_WINDOW:
        return UndoRefusal.TOO_LATE
    return None
