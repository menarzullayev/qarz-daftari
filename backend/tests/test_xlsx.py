"""The workbook writer, read back by an independent reader (specification: exports in `.xlsx`)."""

from decimal import Decimal

import pytest

from qarz.application.xlsx import MAX_CELL, MAX_ROWS, MIME, Workbook, clean_text, column_letters, sheet_name

from . import xlsx_reader


def test_what_is_written_is_what_is_read_back() -> None:
    book = Workbook()
    people = book.sheet("Mijozlar", ("Ism", "Telefon", "Qarz"), (28, 16, 14))
    people.append(("Ali Valiyev", "+998901234567", 50_000))
    people.append(("O'g'iloy <Qizi> & \"Co\"", None, 0))
    people.append((None, None, -1_250_000))
    people.append(("  leading and trailing  ", "", Decimal("2.500")))
    people.append(("много\nстрок\tи таб", "🙂 emoji", Decimal("0.001")))
    assert xlsx_reader.read(book.finish()) == {
        "Mijozlar": [
            ["Ism", "Telefon", "Qarz"],
            ["Ali Valiyev", "+998901234567", 50_000],
            ["O'g'iloy <Qizi> & \"Co\"", None, 0],
            [None, None, -1_250_000],
            ["  leading and trailing  ", "", Decimal("2.500")],
            ["много\nстрок\tи таб", "🙂 emoji", Decimal("0.001")],
        ]
    }
    assert people.data_rows == 5
    assert MIME == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_the_package_has_every_part_a_spreadsheet_program_looks_for() -> None:
    book = Workbook()
    book.sheet("A", ("x",)).append((1,))
    book.sheet("B").append(("y",))
    files = xlsx_reader.parts(book.finish())
    assert sorted(files) == [
        "[Content_Types].xml",
        "_rels/.rels",
        "xl/_rels/workbook.xml.rels",
        "xl/styles.xml",
        "xl/workbook.xml",
        "xl/worksheets/sheet1.xml",
        "xl/worksheets/sheet2.xml",
    ]
    assert b'Target="styles.xml"' in files["xl/_rels/workbook.xml.rels"]
    assert b"/xl/styles.xml" in files["[Content_Types].xml"]
    # The header row is bold; data rows are plain.
    assert b'<c r="A1" s="1" t="inlineStr">' in files["xl/worksheets/sheet1.xml"]
    assert b'<c r="A2"><v>1</v></c>' in files["xl/worksheets/sheet1.xml"]
    assert b'<col min="1" max="1"' not in files["xl/worksheets/sheet2.xml"], "no widths were asked for"


@pytest.mark.parametrize(
    "text",
    ["=1+1", '=HYPERLINK("http://evil.example","x")', "+998901234567", "-20000", "@SUM(A1)", "=cmd|' /C calc'!A0"],
)
def test_text_is_never_a_formula_whatever_it_begins_with(text: str) -> None:
    """A customer's name or a note is chosen by people; the program that opens the file must not run it."""
    book = Workbook()
    book.sheet("S").append((text, 5))
    content = book.finish()
    sheet = xlsx_reader.parts(content)["xl/worksheets/sheet1.xml"].decode()
    assert "<f>" not in sheet and "<f " not in sheet
    assert '<c r="A1" t="inlineStr">' in sheet
    assert xlsx_reader.read(content) == {"S": [[text, 5]]}


def test_characters_a_file_cannot_carry_are_left_out_and_a_cell_has_a_longest_text() -> None:
    assert clean_text("a\x00b\x01c\x0bd\x1fe￾f￿g") == "abcdefg"
    assert clean_text("tab\there\nnew\rline") == "tab\there\nnew\rline"
    assert clean_text("\ud800 lone surrogate") == " lone surrogate"
    assert len(clean_text("x" * (MAX_CELL + 1))) == MAX_CELL == 32_767
    book = Workbook()
    book.sheet("S").append(("bad\x00\x08 char", "]]> <![CDATA[ &amp; &#x0;"))
    assert xlsx_reader.read(book.finish()) == {"S": [["bad char", "]]> <![CDATA[ &amp; &#x0;"]]}


def test_a_number_is_a_number_and_a_boolean_is_refused() -> None:
    book = Workbook()
    sheet = book.sheet("S")
    sheet.append((10**15, Decimal("1E+3"), Decimal("12.345")))
    with pytest.raises(TypeError):
        sheet.append((True,))
    content = book.finish()
    assert xlsx_reader.read(content)["S"][0] == [10**15, 1000, Decimal("12.345")]
    sheet = xlsx_reader.parts(content)["xl/worksheets/sheet1.xml"]
    assert b"<v>1000000000000000</v>" in sheet and b"<v>1000</v>" in sheet, "never in scientific notation"
    assert b"E+" not in sheet


def test_sheets_keep_the_order_they_were_added_in_whatever_order_rows_arrive() -> None:
    book = Workbook()
    first, second, third = book.sheet("First", ("a",)), book.sheet("Second", ("b",)), book.sheet("Third", ("c",))
    third.append((3,))
    first.append((1,))
    second.append((2,))
    first.append((11,))
    assert list(xlsx_reader.read(book.finish()).items()) == [
        ("First", [["a"], [1], [11]]),
        ("Second", [["b"], [2]]),
        ("Third", [["c"], [3]]),
    ]


def test_a_table_longer_than_a_worksheet_continues_on_the_next_with_its_header() -> None:
    book = Workbook(max_rows=4)  # the header and three rows
    before = book.sheet("Before", ("b",))
    ledger = book.sheet("Daftar", ("n", "text"))
    after = book.sheet("After", ("a",))
    for n in range(1, 8):
        ledger.append((n, f"row {n}"))
    before.append((0,))
    after.append((9,))
    read = xlsx_reader.read(book.finish())
    assert list(read) == ["Before", "Daftar", "Daftar (2)", "Daftar (3)", "After"]
    assert read["Daftar"] == [["n", "text"], [1, "row 1"], [2, "row 2"], [3, "row 3"]]
    assert read["Daftar (2)"] == [["n", "text"], [4, "row 4"], [5, "row 5"], [6, "row 6"]]
    assert read["Daftar (3)"] == [["n", "text"], [7, "row 7"]]
    assert ledger.data_rows == 7
    # Exactly full is not yet a reason for another worksheet.
    exact = Workbook(max_rows=4)
    table = exact.sheet("T", ("h",))
    for n in range(3):
        table.append((n,))
    assert list(xlsx_reader.read(exact.finish())) == ["T"]
    assert MAX_ROWS == 1_048_576
    with pytest.raises(ValueError, match="at least"):
        Workbook(max_rows=1)


def test_sheet_names_are_made_acceptable() -> None:
    assert sheet_name("Muddatlar tarixi") == "Muddatlar tarixi"
    assert sheet_name("a[b]c:d*e?f/g\\h") == "a b c d e f g h"
    assert sheet_name("x" * 40) == "x" * 31
    assert sheet_name("x" * 40, 12) == "x" * 26 + " (12)"
    assert len(sheet_name("x" * 40, 12)) == 31
    assert sheet_name("") == "Sheet" and sheet_name(" /: ") == "Sheet"
    book = Workbook()
    book.sheet('Quote " & <tag>').append(("v",))
    assert list(xlsx_reader.read(book.finish())) == ['Quote " & <tag>']


def test_two_sheets_cannot_share_a_name_and_a_workbook_needs_a_sheet() -> None:
    book = Workbook()
    book.sheet("Daftar")
    book.sheet("daftar")
    with pytest.raises(ValueError, match="share a name"):
        book.finish()
    with pytest.raises(ValueError, match="at least one sheet"):
        Workbook().finish()


def test_columns_are_lettered_as_spreadsheets_letter_them() -> None:
    assert [column_letters(n) for n in (0, 1, 25, 26, 27, 51, 52, 701, 702, 16_383)] == [
        "A",
        "B",
        "Z",
        "AA",
        "AB",
        "AZ",
        "BA",
        "ZZ",
        "AAA",
        "XFD",
    ]


def test_a_large_table_is_spooled_and_packed_small() -> None:
    book = Workbook()
    sheet = book.sheet("Daftar", ("n", "name", "amount"))
    for n in range(120_000):
        sheet.append((n, f"Mijoz {n % 500}", 45_000 + n))
    content = book.finish()
    assert len(content) < 3_000_000, "compressed: far smaller than the rows as text"
    rows = xlsx_reader.read(content)["Daftar"]
    assert len(rows) == 120_001 and rows[-1] == [119_999, "Mijoz 499", 164_999]
    book.close()  # closing again after finishing is harmless
