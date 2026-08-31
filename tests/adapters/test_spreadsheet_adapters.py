"""Per-format tests for the spreadsheet adapters, and for local file storage.

`unit` tier. These are the port's promises that a writer cannot provoke -- a ragged row, a
natively-typed cell, a byte-order mark -- so they need input built by hand, per format. The
promises both adapters share live in ``tests/contract/test_spreadsheet_contract.py``.

The two halves matter together. The contract suite says the adapters agree; these say what
each one had to do to make that true, which is where the divergence would reappear if someone
"simplified" either.
"""

import datetime as dt
from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import Workbook

from academy.adapters.outbound.spreadsheet import (
    CsvSpreadsheetReader,
    CsvSpreadsheetWriter,
    XlsxSpreadsheetReader,
)
from academy.adapters.outbound.storage import LocalFileStorage
from academy.application.errors import MalformedSpreadsheetError


def _workbook(*sheets: tuple[str, list[list[object]]]) -> bytes:
    """Build a workbook by hand, with cells of whatever type each value is."""
    workbook = Workbook()
    workbook.remove(workbook.active)  # type: ignore[arg-type]
    for title, rows in sheets:
        sheet = workbook.create_sheet(title)
        for row in rows:
            sheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


# --------------------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------------------


@pytest.mark.unit
def test_a_byte_order_mark_is_not_part_of_the_first_header() -> None:
    # What Excel writes when it saves CSV. Without `utf-8-sig` the first column is named
    # '﻿student_email', which matches nothing and looks identical in every error message.
    rows = CsvSpreadsheetReader().read_rows('﻿student_email\r\nada@academy.test\r\n'.encode())

    assert rows == [{'student_email': 'ada@academy.test'}]


@pytest.mark.unit
def test_bytes_that_are_not_text_are_refused_as_malformed() -> None:
    # An .xlsx renamed to .csv arrives exactly like this.
    with pytest.raises(MalformedSpreadsheetError, match='not valid UTF-8'):
        CsvSpreadsheetReader().read_rows(b'PK\x03\x04\xff\xfe\x00\x00')


@pytest.mark.unit
def test_a_row_wider_than_its_header_loses_the_unnamed_cells() -> None:
    rows = CsvSpreadsheetReader().read_rows(b'a,b\r\n1,2,3\r\n')

    assert rows == [{'a': '1', 'b': '2'}]


@pytest.mark.unit
def test_a_row_shorter_than_its_header_is_padded_with_blanks() -> None:
    # A trailing column the author left empty, which is a data question for the importer
    # above the port -- not a parse failure.
    rows = CsvSpreadsheetReader().read_rows(b'a,b,c\r\n1,2\r\n')

    assert rows == [{'a': '1', 'b': '2', 'c': ''}]


@pytest.mark.unit
def test_a_value_containing_the_delimiter_survives_the_round_trip() -> None:
    tricky = [['Lovelace, Ada', 'said "hi"', 'two\nlines']]

    data = CsvSpreadsheetWriter().write_sheet(['name', 'quote', 'note'], tricky)

    assert CsvSpreadsheetReader().read_rows(data) == [
        {'name': 'Lovelace, Ada', 'quote': 'said "hi"', 'note': 'two\nlines'}
    ]


@pytest.mark.unit
def test_the_writer_ends_lines_the_way_the_format_specifies() -> None:
    # RFC 4180, and what Excel expects. Asserted on the bytes because the failure mode --
    # `\r\r\n` from a platform-translated stream -- reads back correctly and looks wrong only
    # to the person opening the file.
    data = CsvSpreadsheetWriter().write_sheet(['a'], [['1']])

    assert data == b'a\r\n1\r\n'


# --------------------------------------------------------------------------------------
# XLSX
# --------------------------------------------------------------------------------------


@pytest.mark.unit
def test_a_typed_workbook_gives_up_its_types() -> None:
    # The adapter's real work: a workbook knows 8 is a number and 2026-08-30 is a date, and
    # has to hand them over as the strings the CSV adapter would have produced. A rule above
    # the port decides what they mean -- once, not once per format.
    data = _workbook(
        (
            'Sheet1',
            [
                ['n', 'float', 'fraction', 'when', 'flag', 'blank'],
                [8, 8.0, 8.5, dt.date(2026, 8, 30), True, None],
            ],
        )
    )

    assert XlsxSpreadsheetReader().read_rows(data) == [
        {'n': '8', 'float': '8', 'fraction': '8.5', 'when': '2026-08-30', 'flag': 'TRUE', 'blank': ''}
    ]


@pytest.mark.unit
def test_a_datetime_cell_keeps_its_time() -> None:
    data = _workbook(('Sheet1', [['when'], [dt.datetime(2026, 8, 30, 9, 30)]]))

    assert XlsxSpreadsheetReader().read_rows(data) == [{'when': '2026-08-30T09:30:00'}]


@pytest.mark.unit
def test_only_the_first_worksheet_is_read() -> None:
    # The port promises one table per file. A workbook with several is a file whose author
    # meant something this system has no way to ask about, so it reads the one it can.
    data = _workbook(
        ('First', [['a'], ['1']]),
        ('Second', [['a'], ['2']]),
    )

    assert XlsxSpreadsheetReader().read_rows(data) == [{'a': '1'}]


@pytest.mark.unit
def test_openpyxl_padding_does_not_invent_a_column() -> None:
    # openpyxl pads every row to the sheet's widest, so a data row with an extra cell comes
    # back having extended the *header* with an unnamed column -- and that cell would survive
    # under the key ''. CSV drops it, the port says drop it, so this adapter trims.
    data = _workbook(('Sheet1', [['a', 'b'], ['1', '2', '3']]))

    assert XlsxSpreadsheetReader().read_rows(data) == [{'a': '1', 'b': '2'}]


@pytest.mark.unit
def test_a_workbook_that_is_not_a_workbook_is_refused_as_malformed() -> None:
    with pytest.raises(MalformedSpreadsheetError, match='not a readable workbook'):
        XlsxSpreadsheetReader().read_rows(b'PK\x03\x04 truncated zip')


# --------------------------------------------------------------------------------------
# Local file storage
# --------------------------------------------------------------------------------------


@pytest.mark.unit
async def test_a_key_cannot_write_outside_the_directory_it_was_given(tmp_path: Path) -> None:
    # The contract suite says a key that looks like a path still behaves as a key; it cannot
    # say *where the bytes landed*, because that is a fact about this adapter alone. This is
    # the assertion that makes "keys are opaque" structural rather than a promise: a key of
    # '../escape' is a perfectly good dictionary key and a catastrophic filename.
    root = tmp_path / 'blobs'
    outside = tmp_path / 'outside'
    outside.mkdir()
    storage = LocalFileStorage(root)

    await storage.put('../outside/escaped', b'payload')

    assert list(outside.iterdir()) == []
    assert [path.parent for path in root.iterdir()] == [root]
    assert await storage.get('../outside/escaped') == b'payload'


@pytest.mark.unit
async def test_two_keys_that_differ_do_not_collide_however_they_are_spelled(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path)

    await storage.put('imports/job-1', b'one')
    await storage.put('imports/job-2', b'two')

    assert await storage.get('imports/job-1') == b'one'
    assert await storage.get('imports/job-2') == b'two'
