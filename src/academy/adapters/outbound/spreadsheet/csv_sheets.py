"""The CSV spreadsheet adapter: reader and writer over the standard library's ``csv``.

Named for the format rather than for the operation, because reading and writing CSV are one
decision -- the dialect, the encoding, what an empty cell means -- and splitting them across
two modules would let those answers drift.

The reader's entire job is to turn bytes into rows of strings. It normalises no headers,
validates no values and knows no rules; those belong above the port (ADR-0008), where one
implementation serves both formats.
"""

from __future__ import annotations

import csv
from io import StringIO

from academy.application.errors import MalformedSpreadsheetError

# The encoding a spreadsheet exported from Excel or Google Sheets actually arrives in.
# ``utf-8-sig`` reads plain UTF-8 too, and strips the byte-order mark that Excel writes and
# that would otherwise become part of the first header's name -- turning `student_email` into
# `﻿student_email`, which matches nothing and is invisible in every error message.
#
# Comments rather than attribute docstrings: the check-docstring-first hook reads a string
# literal after a module-level assignment as a second module docstring.
ENCODING = 'utf-8-sig'


class CsvSpreadsheetReader:
    """Parses CSV bytes into rows of strings.

    Satisfies :class:`~academy.application.ports.outbound.spreadsheet.SpreadsheetReader`.
    """

    def read_rows(self, data: bytes) -> list[dict[str, str]]:
        """Parse ``data`` into one dictionary per data row.

        Returns:
            One dictionary per row after the header, in file order, padded and truncated to
            the header's width. An empty file, or one containing only a header, yields an
            empty list.

        Raises:
            MalformedSpreadsheetError: If the bytes are not decodable text, or the CSV module
                refuses them -- a field longer than its limit, or a NUL byte, both of which
                mean this is not a CSV file whatever its extension says.
        """
        text = self._decode(data)
        if not text.strip():
            return []

        try:
            rows = list(csv.reader(StringIO(text, newline='')))
        except csv.Error as error:
            raise MalformedSpreadsheetError(f'not a readable CSV file: {error}') from error

        if not rows:
            return []

        header, *body = rows
        return [_row(_named(header), cells) for cells in body if any(cell.strip() for cell in cells)]

    @staticmethod
    def _decode(data: bytes) -> str:
        """Decode the upload, or say plainly that it is not text.

        Raises:
            MalformedSpreadsheetError: If the bytes are not valid UTF-8. An ``.xlsx`` file
                renamed to ``.csv`` lands here, which is exactly the mistake this message
                needs to describe.
        """
        try:
            return data.decode(ENCODING)
        except UnicodeDecodeError as error:
            raise MalformedSpreadsheetError('not a text file: the bytes are not valid UTF-8') from error


class CsvSpreadsheetWriter:
    """Renders rows of strings as CSV bytes.

    Satisfies :class:`~academy.application.ports.outbound.spreadsheet.SpreadsheetWriter`.
    """

    def write_sheet(
        self,
        headers: list[str],
        rows: list[list[str]],
        *,
        sheet_name: str = 'Sheet1',
    ) -> bytes:
        """Render a header row and data rows as a CSV file.

        ``sheet_name`` is ignored: CSV has no concept of a sheet, which the port anticipates.
        Ignoring it beats raising, because the caller asking for a template should not have to
        know which format it will come back in.

        Raises:
            ValueError: If any row's length differs from ``headers``.
        """
        _require_rectangular(headers, rows)

        buffer = StringIO(newline='')
        # `\r\n` is what RFC 4180 specifies and what Excel expects; the default would emit
        # `\r\r\n` on Windows, because `csv` writes `\r\n` into a stream that then translates
        # the `\n`. Writing to a StringIO with newline='' and saying so explicitly is the
        # combination that produces the same bytes on every platform.
        writer = csv.writer(buffer, lineterminator='\r\n')
        writer.writerow(headers)
        writer.writerows(rows)
        return buffer.getvalue().encode('utf-8')


def _named(header: list[str]) -> list[str]:
    """Drop the trailing columns that have no name.

    Excel writes a trailing comma per empty column, so `a,b,,` is a two-column file with two
    unnamed ones -- and a cell arriving under the key `''` is not a column any rule above the
    port can name. The XLSX adapter needs the same trim for a different reason; both end up
    agreeing about the ragged file a human actually produces.
    """
    while header and not header[-1].strip():
        header = header[:-1]
    return header


def _row(header: list[str], cells: list[str]) -> dict[str, str]:
    """Pair one row's cells with the header, padding short rows and dropping extra cells.

    Both halves are the port's promise rather than convenience: a short row is a row whose
    trailing columns were left blank, which is a data question for the importer above, and a
    cell beyond the header has no name to be known by.
    """
    padded = cells[: len(header)] + [''] * (len(header) - len(cells))
    return dict(zip(header, padded, strict=True))


def _require_rectangular(headers: list[str], rows: list[list[str]]) -> None:
    """Refuse rows that do not match the header's width.

    Raises:
        ValueError: Naming the offending row, because "a row is the wrong width" without
            saying which one sends the caller looking through the whole file.
    """
    for number, row in enumerate(rows, start=1):
        if len(row) != len(headers):
            raise ValueError(f'row {number} has {len(row)} cells, but there are {len(headers)} headers')
