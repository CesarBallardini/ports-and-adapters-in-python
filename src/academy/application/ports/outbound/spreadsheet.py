"""Ports for reading and writing tabular files.

**Both are sync** (ADR-0005): the bytes are already in memory, and parsing them is CPU work.
Wrapping that in ``async def`` would suggest it yields to the event loop, which it does not.
A file large enough for that to matter belongs on the job queue instead (ADR-0009), which is
a different answer to a different problem.

These two ports carry most of the weight of the repository's argument. They are deliberately
tiny -- one method each -- because every interesting decision about an import belongs *above*
them, in the use case. The adapter's entire job is the format.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

# One parsed row: a header cell's name to that cell's value, both always strings. Named because
# it is the vocabulary the importers above the port are written in, and because `list[dict[str,
# str]]` says nothing about which string is which.
#
# Comments rather than attribute docstrings: the check-docstring-first hook reads a string
# literal after a module-level assignment as a second module docstring.
type Row = dict[str, str]


@runtime_checkable
class SpreadsheetReader(Protocol):
    """Turns the bytes of a tabular file into rows of strings.

    Implemented twice, over stdlib ``csv`` and over ``openpyxl`` (ADR-0008). The acceptance
    suite runs the same scenarios against both and requires identical outcomes, so anything
    that would make them differ is a bug in whichever adapter is doing more than its job.
    """

    def read_rows(self, data: bytes) -> list[Row]:
        """Parse ``data`` into one dictionary per data row.

        The first row is the header. Keys are header cells exactly as they appear in the
        file; **normalising them is the use case's job**, not the adapter's, so that both
        adapters present the same raw material and one shared rule decides what
        ``"Student Email"``, ``"student_email"`` and ``"STUDENT EMAIL "`` mean.

        Every value is a ``str``. Adapters must not coerce types, even when the format knows
        them: an ``.xlsx`` cell containing 8 must arrive as ``'8'``, exactly as the CSV
        adapter would deliver it. Deciding that ``'8'`` is a valid grade and ``'eight'`` is
        not is a domain rule, and a type-aware adapter would apply it differently from a
        type-blind one -- which is precisely the divergence the port exists to prevent.

        Args:
            data: The complete file contents.

        Returns:
            One dictionary per row after the header, in file order. Rows shorter than the
            header are padded with empty strings; cells beyond the header are dropped. An
            empty file, or one containing only a header, yields an empty list.

        Raises:
            MalformedSpreadsheetError: If the bytes cannot be parsed as this format at all.
                Adapters must normalise **every** library-specific failure into this single
                error -- ``openpyxl`` alone can raise ``InvalidFileException``, ``BadZipFile``,
                ``KeyError``, ``OSError`` and ``ValueError`` for the same "this is not a
                workbook" condition. A use case that had to catch all of those would be
                coupled to the parsing library it is supposed to be insulated from.
        """
        ...


@runtime_checkable
class SpreadsheetWriter(Protocol):
    """Turns rows of strings into the bytes of a tabular file.

    Used for import templates and for exports. The same two adapters implement it, so a
    template downloaded as ``.xlsx`` and one downloaded as ``.csv`` describe the same columns.
    """

    def write_sheet(
        self,
        headers: list[str],
        rows: list[list[str]],
        *,
        sheet_name: str = 'Sheet1',
    ) -> bytes:
        """Render a header row and data rows as a file.

        Args:
            headers: Column headings, in order.
            rows: Data rows. Each must have the same length as ``headers``.
            sheet_name: Worksheet name. Formats without a concept of sheets ignore it.

        Returns:
            The complete file contents, ready to be served as a download.

        Raises:
            ValueError: If any row's length differs from ``headers``. Adapters must check
                this rather than silently truncating or padding: a mismatch means the caller
                built the rows wrongly, and a file that quietly loses a column is far more
                expensive to discover later than a failed request now.
        """
        ...
