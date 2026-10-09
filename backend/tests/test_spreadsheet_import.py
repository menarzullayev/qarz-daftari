"""The pure rules of the import (REQ-062, REQ-063; BR-24): reading a file that is not trusted, turning cells
into rows with coded problems, setting rows against a shop's customers, and the undo window."""

import hashlib
import io
import json
import uuid
import zipfile
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from qarz.application import ledger_service
from qarz.application.imports import template
from qarz.application.xlsx import Workbook
from qarz.domain import imports, money
from qarz.domain.files import MAX_FILE_BYTES
from qarz.domain.imports import (
    Candidate,
    FileProblem,
    ImportRow,
    ParsedFile,
    RowProblem,
    may_undo,
    parse,
    parse_amount,
    parse_date,
    plan,
    plan_token,
    read_xlsx,
)
from qarz.domain.languages import LANGUAGES
from qarz.domain.money import Currency
from qarz.domain.names import normalize_name

TODAY = date(2026, 10, 7)
HEAD = ["Ism", "Telefon", "Qarz summasi", "To'lash muddati", "Izoh"]
MAIN = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'


def write_xlsx(rows: list[list[Any]]) -> bytes:
    """A one-sheet workbook made by the service's own writer; an empty text is an empty cell."""
    made = Workbook()
    sheet_ = made.sheet("Import")
    for cells in rows:
        sheet_.append([None if cell == "" else cell for cell in cells])
    return made.finish()


def book(*rows: list[str]) -> bytes:
    return write_xlsx([HEAD, *rows])


def text(*lines: str) -> bytes:
    return "\n".join(lines).encode("utf-8")


def good(data: bytes) -> ParsedFile:
    parsed = parse(data, TODAY)
    assert isinstance(parsed, ParsedFile), parsed
    return parsed


def codes(data: bytes) -> list[tuple[int, str, str]]:
    return [(error.row, error.column, error.code.value) for error in good(data).errors]


def archive(sheet: str | bytes, shared: str | None = None, name: str = "xl/worksheets/sheet1.xml") -> bytes:
    """A workbook made by hand, to say exactly what its parts contain."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as made:
        made.writestr(name, sheet)
        if shared is not None:
            made.writestr("xl/sharedStrings.xml", shared)
    return out.getvalue()


def sheet(body: str) -> str:
    return f"<worksheet {MAIN}><sheetData>{body}</sheetData></worksheet>"


def inline(ref: str, value: str) -> str:
    return f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>'


HEAD_ROW = '<row r="1">' + inline("A1", "Ism") + inline("B1", "Qarz summasi") + inline("C1", "Telefon") + "</row>"


# --- the template and the writer ------------------------------------------------------------------------


def test_the_template_has_the_five_columns_and_no_rows() -> None:
    assert read_xlsx(template("uz")) == [(1, HEAD)]
    assert read_xlsx(template("ru")) == [(1, ["Имя", "Телефон", "Сумма долга", "Срок оплаты", "Примечание"])]
    assert read_xlsx(template("kk")) == read_xlsx(template("uz")), "an unknown language gets the Uzbek template"
    assert read_xlsx(template("en")) == [(1, ["Name", "Phone", "Debt amount", "Due date", "Note"])]
    assert read_xlsx(template("uz-Cyrl")) == [(1, ["Исм", "Телефон", "Қарз суммаси", "Тўлаш муддати", "Изоҳ"])]
    for lang in LANGUAGES:
        assert len(imports.TEMPLATE_HEADERS[lang]) == 5, lang
        assert parse(template(lang), TODAY) is FileProblem.NO_ROWS, f"the {lang} titles are recognised too"
    # A title is compared without its case, its "*" and its extra spaces; a title nobody published is unknown.
    assert imports.header_form("  Qarz   SUMMASI* ") == "qarz summasi"
    assert imports.header_form("Qarz summasi") in imports._COLUMN_OF
    assert imports.header_form("Qarz summas1") not in imports._COLUMN_OF
    assert parse(template("uz"), TODAY) is FileProblem.NO_ROWS
    assert parse(template("ru"), TODAY) is FileProblem.NO_ROWS, "the Russian titles are recognised too"
    with zipfile.ZipFile(io.BytesIO(template("uz"))) as made:
        assert made.testzip() is None
        assert [name for name in made.namelist() if name.startswith("xl/worksheets/")] == ["xl/worksheets/sheet1.xml"]


def test_what_the_shared_workbook_writer_writes_is_read_back() -> None:
    """The template and the exports are written by `qarz.application.xlsx`; this reader must read them."""
    rows = [['Ali & <Vali> "aka"', "", "45000"], ["", "", ""], ["Ғани", "+998901234567", 1200000]]
    assert read_xlsx(write_xlsx(rows)) == [
        (1, ['Ali & <Vali> "aka"', "", "45000"]),
        (3, ["Ғани", "+998901234567", "1200000"]),
    ]


def test_a_filled_template_becomes_rows() -> None:
    parsed = good(
        book(
            ["  Ali   Karimov ", "90 123-45-67", "45 000", "25.10.2026", " non,  sut "],
            ["Ғани", "", "1200000", "", ""],
        )
    )
    assert parsed.kind == "xlsx"
    assert parsed.total == 2
    assert parsed.errors == ()
    assert parsed.rows == (
        ImportRow(2, "Ali Karimov", "ali karimov", "+998901234567", 45000, date(2026, 10, 25), "non, sut"),
        ImportRow(3, "Ғани", "g'ani", None, 1200000, None, None),
    )
    assert parsed.rows[1].name_norm == normalize_name("G'ani")


# --- the header ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "header",
    [
        "Ism,Telefon,Qarz summasi,To'lash muddati,Izoh",
        "ism;telefon;qarz;muddat;izoh",
        "Имя,Телефон,Сумма долга,Срок оплаты,Примечание",
        "ИМЯ,телефон,Долг,Срок,Заметка",
        "Mijoz,Tel,Summa,To’lash muddati,Izoh",
        " Ism * , Telefon: ,Qarz summasi*,Sana,Izoh",
        "Исм,Телефон,Қарз,Муддат,Изоҳ",
        "name,phone,amount,date,note",
    ],
)
def test_the_titles_are_recognised_in_uzbek_russian_and_english(header: str) -> None:
    delimiter = ";" if ";" in header else ","
    line = delimiter.join(["Ali", "901234567", "45000", "25.10.2026", "non"])
    assert good(text(header, line)).rows == (
        ImportRow(2, "Ali", "ali", "+998901234567", 45000, date(2026, 10, 25), "non"),
    )


def test_the_columns_may_come_in_any_order_and_only_name_and_amount_are_needed() -> None:
    assert good(text("Qarz,Ism", "45000,Ali")).rows == (ImportRow(2, "Ali", "ali", None, 45000, None, None),)
    assert good(text("Ism,,Qarz", "Ali,ignored,45000")).rows[0].amount == 45000, "an untitled column is not read"


@pytest.mark.parametrize(
    ("file", "problem"),
    [
        (text("Ism,Telefon", "Ali,901234567"), FileProblem.MISSING_COLUMN),
        (text("Qarz,Telefon", "45000,901234567"), FileProblem.MISSING_COLUMN),
        (text("Ism,Qarz,Manzil", "Ali,45000,Chilonzor"), FileProblem.UNKNOWN_COLUMN),
        (text("Ism,Qarz,Summa", "Ali,45000,45000"), FileProblem.DUPLICATE_COLUMN),
        (text("Ism,Mijoz,Qarz", "Ali,Ali,45000"), FileProblem.DUPLICATE_COLUMN),
        (text("Ism,Qarz"), FileProblem.NO_ROWS),
        (text("Ism,Qarz", "", "  ,  "), FileProblem.NO_ROWS),
        (text("", "  "), FileProblem.NO_HEADER),
    ],
)
def test_a_file_without_a_usable_header_is_refused(file: bytes, problem: FileProblem) -> None:
    assert parse(file, TODAY) is problem


# --- cells ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "outcome"),
    [
        ("45000", 45000),
        ("45 000", 45000),
        ("45 000", 45000),
        ("45000.00", 45000),
        ("+45000", 45000),
        ("100", 100),
        ("100000000", 100_000_000),
        ("99", RowProblem.AMOUNT_TOO_SMALL),
        ("0", RowProblem.AMOUNT_TOO_SMALL),
        ("-45000", RowProblem.AMOUNT_TOO_SMALL),
        ("100000001", RowProblem.AMOUNT_TOO_LARGE),
        ("45000.5", RowProblem.AMOUNT_NOT_WHOLE),
        ("45000.000001", RowProblem.AMOUNT_NOT_WHOLE),
        ("", RowProblem.AMOUNT_MISSING),
        ("   ", RowProblem.AMOUNT_MISSING),
        ("45,000", RowProblem.AMOUNT_INVALID),
        ("45k", RowProblem.AMOUNT_INVALID),
        ("45000 so'm", RowProblem.AMOUNT_INVALID),
        ("4.5E4", RowProblem.AMOUNT_INVALID),
        ("١٢٣٤٥", RowProblem.AMOUNT_INVALID),
        ("1" * 16, RowProblem.AMOUNT_INVALID),
    ],
)
def test_an_amount_is_a_whole_number_of_sum_within_the_ledgers_bounds(raw: str, outcome: Any) -> None:
    assert parse_amount(raw) == outcome


def test_the_bounds_are_those_of_every_ledger_entry() -> None:
    assert (imports.MIN_AMOUNT, imports.MAX_AMOUNT) == (ledger_service.MIN_AMOUNT, ledger_service.MAX_AMOUNT)
    assert imports.MAX_ROWS == 2000


@pytest.mark.parametrize(
    ("raw", "outcome"),
    [
        ("", None),
        ("  ", None),
        ("25.10.2026", date(2026, 10, 25)),
        ("5.1.2027", date(2027, 1, 5)),
        ("25/10/2026", date(2026, 10, 25)),
        ("25-10-2026", date(2026, 10, 25)),
        ("2026-10-25", date(2026, 10, 25)),
        ("2026-10-25T00:00:00", date(2026, 10, 25)),
        ("46320", date(2026, 10, 25)),  # what a date cell of a workbook holds
        ("31.02.2026", RowProblem.DATE_INVALID),
        ("25.13.2026", RowProblem.DATE_INVALID),
        ("25.10", RowProblem.DATE_INVALID),
        ("25.10.26", RowProblem.DATE_INVALID),
        ("ertaga", RowProblem.DATE_INVALID),
        ("\u0662\u0665.\u0661\u0660.\u0662\u0660\u0662\u0666", RowProblem.DATE_INVALID),
        ("\u0664\u0666\u0663\u0662\u0660", RowProblem.DATE_INVALID),
        ("19999", RowProblem.DATE_INVALID),
        ("80001", RowProblem.DATE_INVALID),
        ("2026-10-25T10:30:00", RowProblem.DATE_INVALID),
    ],
)
def test_a_promised_date_is_read_in_the_forms_people_type(raw: str, outcome: Any) -> None:
    assert parse_date(raw, TODAY) == outcome


def test_a_promised_date_may_lie_a_year_back_and_a_year_ahead() -> None:
    def typed(day: date) -> str:
        return day.strftime("%d.%m.%Y")

    back, ahead = TODAY - timedelta(days=365), TODAY + timedelta(days=365)
    assert parse_date(typed(back), TODAY) == back, "an old debt may be overdue already"
    assert parse_date(typed(back - timedelta(days=1)), TODAY) is RowProblem.DATE_TOO_OLD
    assert parse_date(typed(ahead), TODAY) == ahead
    assert parse_date(typed(ahead + timedelta(days=1)), TODAY) is RowProblem.DATE_TOO_FAR
    assert parse_date(typed(TODAY), TODAY) == TODAY


def test_every_problem_of_a_row_is_reported_with_its_row_number_and_a_code() -> None:
    file = text(
        "Ism,Telefon,Qarz,Muddat,Izoh",
        "Ali,901234567,45000,25.10.2026,non",
        "",
        ",12345,abc,31.02.2026," + "x" * 201,
        "x" * 81 + ",,50,,",
        "Vali,,,,",
        "x" * 80 + ",,100,," + "y" * 200,
    )
    parsed = good(file)
    assert parsed.total == 5
    assert [row.row for row in parsed.rows] == [2, 7], "the blank line is not a row, but it keeps its number"
    assert (len(parsed.rows[1].name), len(parsed.rows[1].note or "")) == (80, 200)
    assert codes(file) == [
        (4, "name", "name_missing"),
        (4, "phone", "phone_invalid"),
        (4, "amount", "amount_invalid"),
        (4, "promised_date", "date_invalid"),
        (4, "note", "note_too_long"),
        (5, "name", "name_too_long"),
        (5, "amount", "amount_too_small"),
        (6, "amount", "amount_missing"),
    ]


def test_no_row_is_dropped_without_being_reported() -> None:
    parsed = good(text("Ism,Qarz", "Ali,45000", "Vali,abc", ",1000", "G'ani,2000"))
    assert parsed.total == len(parsed.rows) + len({error.row for error in parsed.errors}) == 4


# --- at most 2 000 rows ---------------------------------------------------------------------------------


def test_a_file_has_at_most_two_thousand_rows() -> None:
    lines = [f"Mijoz {number},{1000 + number}" for number in range(2001)]
    assert len(good(text("Ism,Qarz", *lines[:2000])).rows) == 2000
    assert parse(text("Ism,Qarz", *lines), TODAY) is FileProblem.TOO_MANY_ROWS
    rows = [[f"Mijoz {number}", "", str(1000 + number)] for number in range(2001)]
    assert len(good(book(*rows[:2000])).rows) == 2000
    assert parse(book(*rows), TODAY) is FileProblem.TOO_MANY_ROWS


# --- text files -------------------------------------------------------------------------------------------


def test_a_text_file_may_use_commas_semicolons_or_tabs_and_start_with_a_mark() -> None:
    expected = (ImportRow(2, "Karimov, Ali", "karimov, ali", None, 45000, None, "non; sut"),)
    assert good(text("Ism,Qarz,Izoh", '"Karimov, Ali",45000,non; sut')).rows == expected
    assert good(text("Ism;Qarz;Izoh", 'Karimov, Ali;45000;"non; sut"')).rows == expected
    assert good(text("Ism\tQarz\tIzoh", "Karimov, Ali\t45000\tnon; sut")).rows == expected
    assert good(b"\xef\xbb\xbf" + text("Ism,Qarz,Izoh", '"Karimov, Ali",45000,non; sut')).rows == expected
    assert good(text("Ism,Qarz", "Ali,45000").replace(b"\n", b"\r\n")).rows[0].amount == 45000


@pytest.mark.parametrize(
    ("file", "problem"),
    [
        (b"", FileProblem.EMPTY),
        (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", FileProblem.TYPE),
        (b"Ism,Qarz\nAli\x00,45000\n", FileProblem.TYPE),
        ("Имя;Долг\nАли;45000\n".encode("cp1251"), FileProblem.ENCODING),
        (b"\xff\xfeI\x00s\x00m\x00", FileProblem.TYPE),
        (b"%PDF-1.4 not a table", FileProblem.UNKNOWN_COLUMN),
    ],
)
def test_what_is_not_a_spreadsheet_is_refused(file: bytes, problem: FileProblem) -> None:
    assert parse(file, TODAY) is problem


def test_a_file_of_exactly_the_largest_size_is_read() -> None:
    line = b"Ali,45000\n"
    file = b"Ism,Qarz\n" + line + b"\n" * (MAX_FILE_BYTES - len(b"Ism,Qarz\n") - len(line))
    assert len(file) == MAX_FILE_BYTES
    assert len(good(file).rows) == 1
    assert parse(file + b"\n", TODAY) is FileProblem.TOO_LARGE


# --- workbooks that are not what they seem --------------------------------------------------------------


def test_a_workbook_is_read_through_its_shared_strings_numbers_and_inline_text() -> None:
    shared = (
        f"<sst {MAIN}><si><t>Ism</t></si><si><t>Qarz summasi</t></si><si><t>Telefon</t></si>"
        "<si><r><t>Ali </t></r><r><t>Karimov</t></r><rPh><t>NOT THIS</t></rPh></si></sst>"
    )
    body = (
        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c></row>'
        '<row r="2"><c r="A2" t="s"><v>3</v></c><c r="B2"><v>45000.0</v></c><c r="C2"><v>998901234567</v></c></row>'
        '<row r="3"><c r="A3" t="str"><f>A2</f><v>Vali</v></c><c r="B3" t="n"><v>4.5E4</v></c></row>'
        '<row r="5">' + inline("A5", "G'ani") + '<c r="B5"><v>1200.5</v></c><c r="C5" t="s"><v>99</v></c></row>'
    )
    file = archive(sheet(body), shared)
    assert good(file).rows == (
        ImportRow(2, "Ali Karimov", "ali karimov", "+998901234567", 45000, None, None),
        ImportRow(3, "Vali", "vali", None, 45000, None, None),
    )
    assert codes(file) == [(5, "amount", "amount_not_whole")]


def test_cells_without_a_reference_follow_one_another_and_far_columns_are_not_read() -> None:
    body = (
        '<row><c t="inlineStr"><is><t>Ism</t></is></c><c t="inlineStr"><is><t>Qarz</t></is></c></row>'
        '<row><c t="inlineStr"><is><t>Ali</t></is></c><c><v>45000</v></c>' + inline("ZZ2", "far away") + "</row>"
    )
    assert read_xlsx(archive(sheet(body))) == [(1, ["Ism", "Qarz"]), (2, ["Ali", "45000"])]


def test_the_first_worksheet_is_the_one_with_the_lowest_number() -> None:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as made:
        made.writestr("xl/worksheets/sheet10.xml", sheet('<row r="1">' + inline("A1", "tenth") + "</row>"))
        made.writestr("xl/worksheets/sheet2.xml", sheet('<row r="1">' + inline("A1", "second") + "</row>"))
        made.writestr("xl/worksheets/other.xml", sheet('<row r="1">' + inline("A1", "other") + "</row>"))
    assert read_xlsx(out.getvalue()) == [(1, ["second"])]


def test_a_number_too_long_to_be_one_does_not_stall_the_reader() -> None:
    body = HEAD_ROW + '<row r="2">' + inline("A2", "Ali") + '<c r="B2"><v>1E+999999999</v></c></row>'
    assert codes(archive(sheet(body))) == [(2, "amount", "amount_invalid")]


@pytest.mark.parametrize(
    "part",
    [
        '<!DOCTYPE worksheet [<!ENTITY a "aaaaaaaaaa">]>'
        + sheet('<row r="1"><c t="inlineStr"><is><t>&a;</t></is></c></row>'),
        '<!DOCTYPE worksheet SYSTEM "http://example.invalid/x.dtd">' + sheet(""),
        "<!DOCTYPE worksheet>" + sheet(""),
        sheet("<row><c></row>"),
        "not xml at all",
        "",
    ],
)
def test_a_document_type_or_broken_xml_is_refused(part: str) -> None:
    assert parse(archive(part), TODAY) is FileProblem.MALFORMED
    good_sheet = sheet(HEAD_ROW + '<row r="2">' + inline("A2", "Ali") + '<c r="B2"><v>45000</v></c></row>')
    assert parse(archive(good_sheet, shared=part or "<"), TODAY) is FileProblem.MALFORMED


def test_a_document_type_is_refused_in_any_encoding() -> None:
    hidden = ('<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE worksheet [<!ENTITY a "b">]>' + sheet("")).encode(
        "utf-16"
    )
    assert b"DOCTYPE" not in hidden
    assert parse(archive(hidden), TODAY) is FileProblem.MALFORMED


def test_an_entity_bomb_never_expands() -> None:
    bomb = (
        '<!DOCTYPE b [<!ENTITY a "' + "x" * 1000 + '"><!ENTITY b "' + "&a;" * 1000 + '"><!ENTITY c "' + "&b;" * 1000
        + '">]>' + sheet('<row r="1"><c t="inlineStr"><is><t>&c;</t></is></c></row>')
    )  # fmt: skip
    assert parse(archive(bomb), TODAY) is FileProblem.MALFORMED


def test_a_part_that_expands_beyond_the_limit_is_given_up_on() -> None:
    padding = " " * (imports.MAX_EXPANDED_BYTES + 1)
    bomb = archive(sheet(HEAD_ROW) + padding)
    assert len(bomb) < 100_000, "a few kilobytes that expand to sixteen megabytes"
    assert parse(bomb, TODAY) is FileProblem.EXPANDS_TOO_MUCH
    assert (
        parse(archive(sheet(HEAD_ROW), shared=f"<sst {MAIN}></sst>" + padding), TODAY) is FileProblem.EXPANDS_TOO_MUCH
    )
    within = archive(
        sheet(HEAD_ROW + '<row r="2">' + inline("A2", "Ali") + '<c r="B2"><v>500</v></c></row>') + " " * 9000
    )
    assert len(good(within).rows) == 1


def test_too_many_cells_or_archive_members_are_given_up_on() -> None:
    cells = "<c><v>1</v></c>" * (imports.MAX_CELLS + 1)
    assert parse(archive(sheet(f"<row>{cells}</row>")), TODAY) is FileProblem.EXPANDS_TOO_MUCH
    strings = "<si><t>a</t></si>" * (imports.MAX_CELLS + 1)
    assert parse(archive(sheet(HEAD_ROW), shared=f"<sst {MAIN}>{strings}</sst>"), TODAY) is FileProblem.EXPANDS_TOO_MUCH
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as made:
        made.writestr("xl/worksheets/sheet1.xml", sheet(HEAD_ROW))
        for number in range(imports.MAX_ARCHIVE_MEMBERS):
            made.writestr(f"junk/{number}", b"")
    assert parse(out.getvalue(), TODAY) is FileProblem.EXPANDS_TOO_MUCH


def test_a_damaged_empty_or_protected_archive_is_refused() -> None:
    whole = book(["Ali", "", "45000"])
    assert parse(whole[: len(whole) // 2], TODAY) is FileProblem.MALFORMED
    assert parse(b"PK\x03\x04" + b"\x00" * 200, TODAY) is FileProblem.MALFORMED
    assert parse(archive(sheet(HEAD_ROW), name="xl/other.xml"), TODAY) is FileProblem.MALFORMED
    protected = bytearray(archive(sheet(HEAD_ROW)))
    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        at = protected.index(signature) + offset
        protected[at] |= 0x01  # the "encrypted" bit of the member, in its local and its central header
    assert parse(bytes(protected), TODAY) is FileProblem.MALFORMED


def test_a_long_cell_is_cut_not_kept() -> None:
    body = HEAD_ROW + '<row r="2">' + inline("A2", "x" * 5000) + '<c r="B2"><v>500</v></c></row>'
    assert codes(archive(sheet(body))) == [(2, "name", "name_too_long")]
    assert len(read_xlsx(archive(sheet(body)))[1][1][0]) == imports.MAX_CELL_CHARS


# --- rows against the shop's customers ------------------------------------------------------------------


def row(number: int, name: str, phone: str | None = None, amount: int = 1000) -> ImportRow:
    return ImportRow(number, name, normalize_name(name), phone, amount, None, None)


def known(name: str, phone: str | None = None, archived: bool = False) -> Candidate:
    return Candidate(uuid.uuid5(uuid.NAMESPACE_URL, f"{name}|{phone}"), normalize_name(name), phone, archived)


def decided(rows: list[ImportRow], customers: list[Candidate]) -> list[tuple[Any, ...]]:
    planned, errors = plan(rows, customers)
    done = [(item.row.row, item.action, item.customer_id, item.matched_by, item.first_row) for item in planned]
    return sorted(done + [(error.row, error.code.value, None, None, None) for error in errors])


P1, P2, P3 = "+998901111111", "+998902222222", "+998903333333"


def test_a_row_that_means_nobody_known_opens_a_new_customer() -> None:
    assert decided([row(2, "Ali"), row(3, "Vali", P1)], [known("Sobir", P2)]) == [
        (2, "create", None, None, None),
        (3, "create", None, None, None),
    ]


def test_a_row_is_matched_to_a_customer_by_phone_before_name() -> None:
    by_phone, by_name = known("Aliyev Ali", P1), known("Ali")
    assert decided([row(2, "Ali", P1)], [by_name, by_phone]) == [(2, "existing", by_phone.customer_id, "phone", None)]
    assert decided([row(2, "Ali")], [by_name, by_phone]) == [(2, "existing", by_name.customer_id, "name", None)]
    assert decided([row(2, "АЛИ", P2)], [by_name]) == [(2, "existing", by_name.customer_id, "name", None)]
    with_phone = known("Vali", P3)
    assert decided([row(2, "Vali")], [with_phone]) == [(2, "existing", with_phone.customer_id, "name", None)]


def test_the_same_name_with_two_different_phones_is_two_people() -> None:
    assert decided([row(2, "Ali", P2)], [known("Ali", P1)]) == [(2, "create", None, None, None)]
    assert decided([row(2, "Ali", P1), row(3, "Ali", P2)], []) == [
        (2, "create", None, None, None),
        (3, "create", None, None, None),
    ]


def test_a_row_that_could_mean_two_customers_is_an_error_not_a_guess() -> None:
    assert decided([row(2, "Ali")], [known("Ali"), known("Ali", P1)]) == [(2, "ambiguous_customer", None, None, None)]
    assert decided([row(2, "Vali", P1)], [known("Ali", P1), known("Sobir", P1)]) == [
        (2, "ambiguous_customer", None, None, None)
    ]
    # Among the rows of the file as well: two new people called Ali, and a third row that names neither phone.
    assert decided([row(2, "Ali", P1), row(3, "Ali", P2), row(4, "Ali")], []) == [
        (2, "create", None, None, None),
        (3, "create", None, None, None),
        (4, "ambiguous_customer", None, None, None),
    ]


def test_a_row_for_an_archived_customer_is_an_error() -> None:
    assert decided([row(2, "Sobir")], [known("Sobir", archived=True)]) == [(2, "customer_archived", None, None, None)]
    assert decided([row(2, "Boshqa", P1)], [known("Sobir", P1, archived=True)]) == [
        (2, "customer_archived", None, None, None)
    ]


def test_rows_of_one_new_customer_are_kept_together_and_said_to_be() -> None:
    assert decided(
        [row(2, "Ali"), row(3, "Vali", P1), row(4, "ali"), row(5, "Boshqa ism", P1), row(6, "Vali")], []
    ) == [
        (2, "create", None, None, None),
        (3, "create", None, None, None),
        (4, "same_as_row", None, "name", 2),
        (5, "same_as_row", None, "phone", 3),
        (6, "same_as_row", None, "name", 3),
    ]


def test_a_row_is_set_against_the_shops_customers_and_the_earlier_rows_together() -> None:
    ali = known("Ali", P1)
    # Row 2 has another phone, so it is a new Ali; row 3 has the phone of the shop's Ali; row 4 names no
    # phone at all, and could be either of the two: it is not guessed.
    assert decided([row(2, "Ali", P2), row(3, "Ali", P1), row(4, "Ali")], [ali]) == [
        (2, "create", None, None, None),
        (3, "existing", ali.customer_id, "phone", None),
        (4, "ambiguous_customer", None, None, None),
    ]
    # A phone that a customer of the shop and an earlier row both carry cannot happen: the row was matched.
    assert decided([row(2, "Vali", P1), row(3, "Boshqa", P1)], [ali]) == [
        (2, "existing", ali.customer_id, "phone", None),
        (3, "existing", ali.customer_id, "phone", None),
    ]
    plain = known("Vali")
    assert decided([row(2, "Vali"), row(3, "Vali")], [plain]) == [
        (2, "existing", plain.customer_id, "name", None),
        (3, "existing", plain.customer_id, "name", None),
    ]


def test_the_plan_token_names_exactly_what_would_be_done() -> None:
    ali = known("Ali")
    rows = [row(2, "Ali"), row(3, "Vali", P1, 5000)]
    token = plan_token(plan(rows, [ali])[0])
    assert token == plan_token(plan(rows, [ali])[0])
    assert len(token) == 32
    different = [
        plan(rows, [])[0],  # Ali is no longer matched
        plan(rows, [known("Ali", P3)])[0],  # matched to another customer
        plan([row(2, "Ali"), row(3, "Vali", P1, 5001)], [ali])[0],
        plan([row(2, "Ali"), row(3, "Vali", P2, 5000)], [ali])[0],
        plan([row(2, "Ali"), row(4, "Vali", P1, 5000)], [ali])[0],
        plan([row(2, "Ali")], [ali])[0],
        plan([*rows, row(4, "Vali")], [ali])[0],
    ]
    tokens = {plan_token(item) for item in different}
    assert len(tokens) == len(different)
    assert token not in tokens
    dated = [ImportRow(2, "Ali", "ali", None, 1000, TODAY, None)]
    noted = [ImportRow(2, "Ali", "ali", None, 1000, None, "izoh")]
    assert len({plan_token(plan(each, [])[0]) for each in ([row(2, "Ali")], dated, noted)}) == 3


# --- undo ---------------------------------------------------------------------------------------------------


def test_an_import_can_be_undone_for_twenty_four_hours() -> None:
    now = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
    assert timedelta(hours=24) == imports.UNDO_WINDOW
    assert may_undo("applied", now, now) is None
    assert may_undo("applied", now - timedelta(hours=24), now) is None, "the last moment is still in time"
    assert may_undo("applied", now - timedelta(hours=24, seconds=1), now) is imports.UndoRefusal.TOO_LATE
    for status in ("uploaded", "validated", "undone", "discarded"):
        assert may_undo(status, now, now) is imports.UndoRefusal.NOT_APPLIED
    assert may_undo("applied", None, now) is imports.UndoRefusal.NOT_APPLIED


# --- what is decided at upload, and the worker's patience ------------------------------------------------


def test_only_what_the_bytes_say_without_reading_the_sheet_is_decided_at_upload() -> None:
    assert imports.sniff(b"") is FileProblem.EMPTY
    assert imports.sniff(b"x" * (MAX_FILE_BYTES + 1)) is FileProblem.TOO_LARGE
    assert imports.sniff(b"x" * MAX_FILE_BYTES) == "csv"
    assert imports.sniff(b"PK\x03\x04 anything, even a broken archive") == "xlsx"
    assert imports.sniff(b"\x89PNG\r\n\x1a\n\x00\x00") is FileProblem.TYPE
    # Whether text is UTF-8, has a header or has rows needs the file to be read: that is the worker's.
    assert imports.sniff("Имя;Долг".encode("cp1251")) == "csv"
    assert imports.sniff(b"no header at all") == "csv"
    assert parse(b"PK\x03\x04 anything, even a broken archive", TODAY) is FileProblem.MALFORMED


def test_a_silent_worker_is_replaced_after_fifteen_minutes_and_a_step_is_started_three_times_at_most() -> None:
    now = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
    assert imports.stale_before(now) == now - timedelta(minutes=15)
    assert [imports.gives_up(attempts) for attempts in (1, 2, 3, 4)] == [False, False, False, True]


# --- the currency column, for a shop that works in dollars ---------------------------------------------

USD = Currency.USD
HEAD_USD = "Ism,Qarz summasi,Valyuta"


def in_dollars(data: bytes) -> ParsedFile:
    parsed = parse(data, TODAY, dollars=True)
    assert isinstance(parsed, ParsedFile), parsed
    return parsed


def test_without_dollars_the_template_is_the_file_it_always_was() -> None:
    """Byte for byte: the five titles and widths written by hand, as before the currency column existed."""
    for lang in LANGUAGES:
        before = Workbook()
        before.sheet("Import", imports.TEMPLATE_HEADERS[lang], (28, 20, 18, 18, 36))
        assert template(lang) == template(lang, dollars=False) == before.finish(), lang
        assert imports.template_headers(lang) == imports.TEMPLATE_HEADERS[lang]
        assert template(lang, dollars=True) != template(lang), "the counterpart: with dollars it is another file"


def test_with_dollars_the_template_has_the_currency_after_the_amount_in_every_language() -> None:
    assert read_xlsx(template("uz", dollars=True)) == [
        (1, ["Ism", "Telefon", "Qarz summasi", "Valyuta", "To'lash muddati", "Izoh"])
    ]
    assert imports.CURRENCY_TITLES == {
        "uz": "Valyuta",
        "uz-Cyrl": "Валюта",
        "ru": "Валюта",
        "tg": "Асъор",
        "kaa": "Valyuta",
        "en": "Currency",
    }
    assert set(imports.CURRENCY_TITLES) == set(LANGUAGES)
    assert read_xlsx(template("kk", dollars=True)) == read_xlsx(template("uz", dollars=True))
    for lang in LANGUAGES:
        titles = read_xlsx(template(lang, dollars=True))[0][1]
        assert len(titles) == 6 and titles[3] == imports.CURRENCY_TITLES[lang], lang
        assert [title for at, title in enumerate(titles) if at != 3] == list(imports.TEMPLATE_HEADERS[lang])
        # Read back: recognised by a shop that works in dollars, an unknown column for every other shop.
        assert parse(template(lang, dollars=True), TODAY, dollars=True) is FileProblem.NO_ROWS, lang
        filled = write_xlsx([titles, ["Ali", "", "12.5", "USD", "", ""]])
        assert in_dollars(filled).rows == (ImportRow(2, "Ali", "ali", None, 1250, None, None, USD),), lang
        assert parse(filled, TODAY) is FileProblem.UNKNOWN_COLUMN, lang


@pytest.mark.parametrize("title", ["Valyuta", "валюта", "Асъор", " CURRENCY* ", "Валюта:"])
def test_a_currency_column_is_an_unknown_column_unless_the_shop_works_in_dollars(title: str) -> None:
    file = text(f"Ism,Qarz,{title}", "Ali,45000,UZS")
    assert parse(file, TODAY) is FileProblem.UNKNOWN_COLUMN
    assert parse(file, TODAY, dollars=False) is FileProblem.UNKNOWN_COLUMN
    assert in_dollars(file).rows == (ImportRow(2, "Ali", "ali", None, 45000, None, None),)
    # Still only the published titles: with dollars too a title nobody published is unknown.
    assert parse(text("Ism,Qarz,Pul birligi", "Ali,45000,UZS"), TODAY, dollars=True) is FileProblem.UNKNOWN_COLUMN
    assert (
        parse(text("Ism,Qarz,Valyuta,Currency", "Ali,1,USD,USD"), TODAY, dollars=True) is FileProblem.DUPLICATE_COLUMN
    )


def test_a_file_without_the_column_or_with_an_empty_cell_is_sum_also_for_a_dollar_shop() -> None:
    plain_file = text("Ism,Telefon,Qarz summasi,To'lash muddati,Izoh", "Ali,901234567,45000,25.10.2026,non")
    assert in_dollars(plain_file) == good(plain_file), "the file of a so'm shop reads the same in a dollar shop"
    mixed = in_dollars(text(HEAD_USD, "Ali,45000,", "Vali,45000,  ", "Gani,45000,uzs", "Soli,12.50,usd", "Xon,7, USD "))
    assert [(row.name, row.amount, row.currency.value) for row in mixed.rows] == [
        ("Ali", 45000, "UZS"),
        ("Vali", 45000, "UZS"),
        ("Gani", 45000, "UZS"),
        ("Soli", 1250, "USD"),
        ("Xon", 700, "USD"),
    ]
    assert mixed.errors == ()
    assert ImportRow(2, "Ali", "ali", None, 1, None, None).currency is Currency.UZS


@pytest.mark.parametrize("cell", ["EUR", "$", "dollar", "so'm", "US", "USDT", "1"])
def test_a_currency_that_is_neither_of_the_two_is_a_problem_of_its_row(cell: str) -> None:
    parsed = in_dollars(text(HEAD_USD, f"Ali,45000,{cell}", "Vali,45000,UZS"))
    assert [(error.row, error.column, error.code) for error in parsed.errors] == [
        (2, "currency", RowProblem.CURRENCY_UNKNOWN)
    ], "and nothing is said about an amount whose currency nobody knows"
    assert [row.name for row in parsed.rows] == ["Vali"]
    assert parsed.total == 2


@pytest.mark.parametrize(
    ("raw", "outcome"),
    [
        ("12.50", 1250),
        ("12.5", 1250),
        ("12,5", 1250),
        ("12", 1200),
        ("1 250.50", 125050),
        ("0.01", 1),
        ("10000", 1_000_000),
        ("10000.00", 1_000_000),
        ("0", RowProblem.AMOUNT_TOO_SMALL),
        ("0.00", RowProblem.AMOUNT_TOO_SMALL),
        ("10000.01", RowProblem.AMOUNT_TOO_LARGE),
        ("45000", RowProblem.AMOUNT_TOO_LARGE),
        ("12.505", RowProblem.AMOUNT_TOO_PRECISE),
        ("0.001", RowProblem.AMOUNT_TOO_PRECISE),
        ("12.500", RowProblem.AMOUNT_TOO_PRECISE),
        ("", RowProblem.AMOUNT_MISSING),
        ("  ", RowProblem.AMOUNT_MISSING),
        ("-5", RowProblem.AMOUNT_INVALID),
        ("+5", RowProblem.AMOUNT_INVALID),
        ("12.", RowProblem.AMOUNT_INVALID),
        ("12$", RowProblem.AMOUNT_INVALID),
        ("$12", RowProblem.AMOUNT_INVALID),
        ("1,250.50", RowProblem.AMOUNT_INVALID),
        ("1.2E1", RowProblem.AMOUNT_INVALID),
        ("١٢", RowProblem.AMOUNT_INVALID),
    ],
)
def test_a_dollar_amount_is_whole_cents_within_the_dollar_bounds_and_never_rounded(raw: str, outcome: Any) -> None:
    assert imports.parse_amount_in(USD, raw) == outcome
    if isinstance(outcome, int):
        assert money.valid_entry_amount(USD, outcome)


def test_each_currency_has_its_own_bounds_and_its_own_reading() -> None:
    """What is a fine amount of so'm is too large in dollars, and the other way round."""
    limits = money.RULES[USD]
    assert imports.parse_amount_in(USD, money.plain(USD, limits.min_entry)) == limits.min_entry
    assert imports.parse_amount_in(USD, money.plain(USD, limits.max_entry)) == limits.max_entry
    assert imports.parse_amount_in(USD, money.plain(USD, limits.max_entry + 1)) is RowProblem.AMOUNT_TOO_LARGE
    for raw in ("45000", "45000.0", "99", "12.5", "+45000", ""):
        assert imports.parse_amount_in(Currency.UZS, raw) == parse_amount(raw), "so'm is read as it always was"
    parsed = in_dollars(text(HEAD_USD, "Ali,12.5,UZS", "Vali,12.5,USD", "Gani,45000,USD", "Soli,45000,", "Xon,50,"))
    assert [(error.row, error.column, error.code) for error in parsed.errors] == [
        (2, "amount", RowProblem.AMOUNT_NOT_WHOLE),
        (4, "amount", RowProblem.AMOUNT_TOO_LARGE),
        (6, "amount", RowProblem.AMOUNT_TOO_SMALL),
    ]
    assert [(row.row, row.amount, row.currency) for row in parsed.rows] == [(3, 1250, USD), (5, 45000, Currency.UZS)]


def test_a_dollar_amount_typed_as_a_number_cell_is_read_and_one_with_three_decimals_is_not() -> None:
    def line(number: int, name: str, value: str) -> str:
        cells = inline(f"A{number}", name) + f'<c r="B{number}"><v>{value}</v></c>' + inline(f"C{number}", "USD")
        return f'<row r="{number}">{cells}</row>'

    head = '<row r="1">' + inline("A1", "Ism") + inline("B1", "Qarz summasi") + inline("C1", "Valyuta") + "</row>"
    body = head + line(2, "Ali", "12.5") + line(3, "Vali", "100.0") + line(4, "Gani", "1.005")
    parsed = in_dollars(archive(sheet(body)))
    assert [(row.name, row.amount, row.currency) for row in parsed.rows] == [("Ali", 1250, USD), ("Vali", 10000, USD)]
    assert [(error.row, error.code) for error in parsed.errors] == [(4, RowProblem.AMOUNT_TOO_PRECISE)]


def test_one_customer_may_have_a_row_in_each_currency_and_gets_two_entries() -> None:
    rows = in_dollars(text(HEAD_USD, "Ali,45000,", "Ali,12.50,USD", "Vali,7,USD")).rows
    planned, errors = plan(rows, [])
    assert errors == []
    assert [(item.row.row, item.action, item.first_row, item.row.currency.value) for item in planned] == [
        (2, imports.CREATE, None, "UZS"),
        (3, imports.SAME_AS_ROW, 2, "USD"),
        (4, imports.CREATE, None, "USD"),
    ]


def test_the_plan_token_tells_the_currencies_apart_and_keeps_the_token_of_a_sum_plan() -> None:
    def token(currency: Currency) -> str:
        return plan_token(plan([ImportRow(2, "Ali", "ali", None, 1000, None, None, currency)], [])[0])

    assert token(USD) != token(Currency.UZS), "1 000 so'm and 10.00 $ are not one plan"
    assert token(Currency.UZS) == plan_token(plan([ImportRow(2, "Ali", "ali", None, 1000, None, None)], [])[0])
    # The fingerprint of a so'm plan is what it was before dollars: written out here by hand.
    facts = [[2, "Ali", None, 1000, None, None, "create", None]]
    canonical = json.dumps(facts, ensure_ascii=False, separators=(",", ":"))
    assert token(Currency.UZS) == hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
