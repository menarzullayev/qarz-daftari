"""Reads a workbook back, with the standard library and independently of the writer, for the tests.

It follows the package's own relationships (content types, workbook, sheet parts) the way a spreadsheet
program does, so a workbook the writer wires wrongly cannot be read here either.
"""

import io
import re
import zipfile
from decimal import Decimal
from typing import Any
from xml.etree import ElementTree  # only ever given workbooks this suite has just written

MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PACKAGE = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_REFERENCE = re.compile(r"([A-Z]+)([0-9]+)")


def _column(letters: str) -> int:
    index = 0
    for letter in letters:
        index = index * 26 + ord(letter) - 64
    return index - 1


def _value(cell: ElementTree.Element) -> Any:
    if cell.get("t") == "inlineStr":
        return "".join(part.text or "" for part in cell.iter(f"{MAIN}t"))
    raw = cell.findtext(f"{MAIN}v")
    assert raw is not None
    assert cell.find(f"{MAIN}f") is None, "the writer never writes a formula"
    return int(raw) if re.fullmatch(r"-?[0-9]+", raw) else Decimal(raw)


def parts(content: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        assert archive.testzip() is None
        return {name: archive.read(name) for name in archive.namelist()}


def read(content: bytes) -> dict[str, list[list[Any]]]:
    """Sheet name -> rows -> cell values (None for an empty cell), in the workbook's own sheet order."""
    files = parts(content)
    for name, data in files.items():
        ElementTree.fromstring(data)  # every part is well-formed XML  # noqa: S314
        assert name == "[Content_Types].xml" or name.startswith(("_rels/", "xl/"))
    root = ElementTree.fromstring(files["_rels/.rels"])  # noqa: S314
    (workbook_path,) = [rel.get("Target") for rel in root.iter(f"{PACKAGE}Relationship")]
    assert workbook_path == "xl/workbook.xml"
    targets = {
        rel.get("Id"): rel.get("Target")
        for rel in ElementTree.fromstring(files["xl/_rels/workbook.xml.rels"]).iter(f"{PACKAGE}Relationship")  # noqa: S314
    }
    declared = files["[Content_Types].xml"].decode()
    sheets: dict[str, list[list[Any]]] = {}
    for sheet in ElementTree.fromstring(files["xl/workbook.xml"]).iter(f"{MAIN}sheet"):  # noqa: S314
        name, target = sheet.get("name"), targets[sheet.get(f"{REL}id")]
        assert name is not None and name not in sheets and target is not None
        assert f'PartName="/xl/{target}"' in declared, "every sheet part has its content type"
        rows: list[list[Any]] = []
        for number, row in enumerate(ElementTree.fromstring(files[f"xl/{target}"]).iter(f"{MAIN}row"), start=1):  # noqa: S314
            assert row.get("r") == str(number), "rows are numbered from one without gaps"
            values: list[Any] = []
            for cell in row.iter(f"{MAIN}c"):
                match = _REFERENCE.fullmatch(cell.get("r") or "")
                assert match is not None and match.group(2) == str(number)
                column = _column(match.group(1))
                assert column >= len(values), "cells of a row go left to right"
                values.extend([None] * (column - len(values)))
                values.append(_value(cell))
            rows.append(values)
        sheets[name] = rows
    return sheets
