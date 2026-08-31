"""What every spreadsheet adapter must do (ADR-0008, ADR-0014).

The port docstrings are the specification; these are the assertions, run against every format.
This is the suite the repository's second claim rests on: business rules live above the port,
so the two adapters must be indistinguishable to everything above them.

**Each reader is paired with its own writer.** That is not laziness about fixtures -- it is
forced by the port. "An empty file" is not a sequence of bytes both formats share: an empty CSV
is zero bytes and an empty workbook is a zip archive. The only way to say "a file this format
considers empty" once, for both, is to have the format produce it.

Promises that cannot be expressed through a writer -- a ragged row, a natively-typed cell --
are asserted per adapter in ``tests/adapters/test_spreadsheet_adapters.py``, with input built
by hand. They are still the port's promises; they just need format-specific bytes to provoke.
"""

from dataclasses import dataclass

import pytest

from academy.adapters.outbound.spreadsheet import (
    CsvSpreadsheetReader,
    CsvSpreadsheetWriter,
    XlsxSpreadsheetReader,
    XlsxSpreadsheetWriter,
)
from academy.application.errors import MalformedSpreadsheetError
from academy.application.ports.outbound.spreadsheet import SpreadsheetReader, SpreadsheetWriter


@dataclass(frozen=True, slots=True)
class Format:
    """One spreadsheet format, as the contract suite needs to exercise it."""

    reader: SpreadsheetReader
    writer: SpreadsheetWriter
    #: Bytes this format cannot possibly parse, for the malformed-input promise.
    garbage: bytes


# Add a third format here and every test below runs against it.
FORMATS = [
    pytest.param(
        Format(CsvSpreadsheetReader(), CsvSpreadsheetWriter(), b'\xff\xfe\x00\x00 not text'),
        id='csv',
    ),
    pytest.param(
        Format(XlsxSpreadsheetReader(), XlsxSpreadsheetWriter(), b'this is not a workbook'),
        id='xlsx',
    ),
]

HEADERS = ['student_email', 'grade', 'comment']
ROWS = [
    ['ada@academy.test', '8', 'good'],
    ['bob@academy.test', '4', ''],
]


@pytest.fixture(params=FORMATS)
def sheets(request: pytest.FixtureRequest) -> Format:
    """One format's reader and writer."""
    fixture: Format = request.param
    return fixture


@pytest.mark.unit
def test_a_written_sheet_reads_back_as_the_rows_that_went_in(sheets: Format) -> None:
    data = sheets.writer.write_sheet(HEADERS, ROWS)

    assert sheets.reader.read_rows(data) == [dict(zip(HEADERS, row, strict=True)) for row in ROWS]


@pytest.mark.unit
def test_every_value_comes_back_as_a_string(sheets: Format) -> None:
    # The promise the XLSX adapter has to work for and the CSV one gets free. A rule above
    # the port decides whether '8' is a valid grade; an adapter that helpfully returned the
    # int 8 would make that rule behave differently per format.
    data = sheets.writer.write_sheet(['n', 'when', 'flag'], [['8', '2026-08-30', 'TRUE']])

    row = sheets.reader.read_rows(data)[0]

    assert row == {'n': '8', 'when': '2026-08-30', 'flag': 'TRUE'}
    assert all(isinstance(value, str) for value in row.values())


@pytest.mark.unit
def test_a_file_with_only_a_header_has_no_rows(sheets: Format) -> None:
    data = sheets.writer.write_sheet(HEADERS, [])

    assert sheets.reader.read_rows(data) == []


@pytest.mark.unit
def test_an_empty_file_has_no_rows(sheets: Format) -> None:
    data = sheets.writer.write_sheet([], [])

    assert sheets.reader.read_rows(data) == []


@pytest.mark.unit
def test_unparseable_bytes_raise_the_one_error_every_adapter_shares(sheets: Format) -> None:
    # The whole reason these adapters exist: a use case catches one error, not the union of
    # everything `csv` and `openpyxl` might raise.
    with pytest.raises(MalformedSpreadsheetError):
        sheets.reader.read_rows(sheets.garbage)


@pytest.mark.unit
def test_a_row_that_does_not_match_the_header_is_refused(sheets: Format) -> None:
    # Silently padding or truncating would lose a column in a template, and a template is
    # what the registrar fills in and uploads back.
    with pytest.raises(ValueError, match='row 2'):
        sheets.writer.write_sheet(HEADERS, [ROWS[0], ['too', 'short']])


@pytest.mark.unit
def test_blank_rows_are_not_rows(sheets: Format) -> None:
    # Every spreadsheet a human has touched ends in blank lines -- a trailing newline in CSV,
    # a sheet whose used range Excel extended. Neither is a row someone meant to import.
    data = sheets.writer.write_sheet(HEADERS, [ROWS[0], ['', '', '']])

    assert sheets.reader.read_rows(data) == [dict(zip(HEADERS, ROWS[0], strict=True))]


@pytest.mark.unit
def test_a_column_with_no_name_is_not_a_column(sheets: Format) -> None:
    # Excel writes a trailing comma per empty column, and openpyxl pads rows to the sheet's
    # widest. Both produce a header ending in blanks, and a cell arriving under the key ''
    # is one no rule above the port can name. Each adapter trims for its own reason; this
    # asserts they end up in the same place.
    data = sheets.writer.write_sheet(['a', 'b', ''], [['1', '2', '3']])

    assert sheets.reader.read_rows(data) == [{'a': '1', 'b': '2'}]


@pytest.mark.unit
def test_both_adapters_satisfy_their_ports(sheets: Format) -> None:
    assert isinstance(sheets.reader, SpreadsheetReader)
    assert isinstance(sheets.writer, SpreadsheetWriter)
