"""Spreadsheet adapters: the same table, in two file formats.

Two implementations of one port, which is the arrangement ADR-0008 exists to justify. The
acceptance suite runs the same import scenarios through both and requires identical outcomes,
so anything either adapter does beyond turning bytes into rows of strings shows up as a
divergence rather than as a feature.

The division of labour is the point:

* an adapter decodes, parses and renders -- and nothing else;
* header normalisation, validation, deduplication and every other rule lives above the port,
  where one implementation serves both formats.

``_require_rectangular`` and ``_row`` are deliberately duplicated in the two modules rather
than shared. They are three lines each, and a shared helper would be the first place a
format-specific exception got added -- at which point the two adapters would differ in the
one behaviour the contract suite exists to hold identical.
"""

from academy.adapters.outbound.spreadsheet.csv_sheets import CsvSpreadsheetReader, CsvSpreadsheetWriter
from academy.adapters.outbound.spreadsheet.xlsx_sheets import XlsxSpreadsheetReader, XlsxSpreadsheetWriter

__all__ = [
    'CsvSpreadsheetReader',
    'CsvSpreadsheetWriter',
    'XlsxSpreadsheetReader',
    'XlsxSpreadsheetWriter',
]
