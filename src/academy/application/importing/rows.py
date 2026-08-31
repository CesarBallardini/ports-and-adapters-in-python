"""What every importer is, and the row handling they all share.

This module holds the half of importing that is *not* about any particular kind of data: how a
header is normalised, how a rejected row is recorded, and what shape an importer has. The rules
about people, subjects, enrollments or grades live in the importer for that kind.

The split is the point of the whole feature. An adapter turns bytes into rows of strings
(ADR-0008); everything that makes an import worth writing -- header normalisation, per-row
validation, partial success, duplicate detection -- happens here, above the port, once, so that
swapping CSV for XLSX cannot change a single rule (``docs/03-sequence-diagrams.md`` §7).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from academy.application.dtos import Actor, ImportResultDto
from academy.application.jobs import ImportContext
from academy.application.ports.outbound.spreadsheet import Row


@dataclass(frozen=True, slots=True)
class Template:
    """The empty file an importer hands out, ready to be filled in and uploaded back.

    Carries rows as well as headers because a useful template is rarely blank: a grade sheet
    for a section comes back listing the enrolled students, which is what turns the round trip
    into a workflow rather than a form (UC-36).
    """

    headers: list[str]
    rows: list[list[str]]


@runtime_checkable
class RowImporter(Protocol):
    """One kind of import: what its file looks like, and what its rows mean.

    Implementations own **all** the rules for their kind and none of the plumbing.
    :class:`~academy.application.importing.service.ImportService` decides where the work runs,
    opens the transaction and rolls back a dry run; an importer never sees a unit of work, a
    file, a byte or a content type.

    Deliberately *not* one class per file format. There is one importer per kind of data, and
    it is handed rows that both spreadsheet adapters produce identically.
    """

    async def template(self, context: ImportContext) -> Template:
        """Build the template for this kind.

        Args:
            context: Importer-specific parameters, such as the section a grade sheet is for.

        Raises:
            NotFoundError: If ``context`` names something that does not exist.
        """
        ...

    async def import_rows(self, actor: Actor, rows: Sequence[Row], context: ImportContext) -> ImportResultDto:
        """Apply every row, rejecting the ones that cannot be applied.

        A row that cannot be applied is a :class:`~academy.application.dtos.RowError` in the
        report, never an exception: one bad row in a hundred must not cost the other
        ninety-nine. Failures that prevent *any* row from being considered -- an unreadable
        file, a section that does not exist, an actor who may not import at all -- are
        exceptions, because there is no partial success to report.

        Args:
            actor: Who is importing. Authorization for the run as a whole is checked here.
            rows: The parsed rows, headers not yet normalised.
            context: Importer-specific parameters.

        Returns:
            The report: counts, and one entry per rejected row with its line number.

        Raises:
            AuthorizationError: If the actor may not run this import at all.
            NotFoundError: If ``context`` names something that does not exist.
        """
        ...


def normalise(row: Row) -> Row:
    """Normalise one row's header names: lower case, trimmed, spaces and dashes to underscores.

    ``"Student Email"``, ``"student_email"`` and ``"STUDENT-EMAIL "`` are the same column, and
    deciding that is emphatically the use case's job rather than an adapter's: two adapters
    that each normalised would be two chances to do it differently, and the file a registrar
    exports from their own spreadsheet has whichever spelling their locale chose.

    Values are left exactly as they arrived. Trimming a value is a per-column decision an
    importer makes when it knows what the column means -- an email tolerates it, a free-text
    comment might not.
    """
    return {_header(name): value for name, value in row.items()}


def _header(name: str) -> str:
    """Reduce one header cell to its canonical form."""
    return name.strip().lower().replace(' ', '_').replace('-', '_')


def numbered(rows: Sequence[Row]) -> list[tuple[int, Row]]:
    """Pair each row with the line number a person would see in their spreadsheet.

    Row 1 is the header, so the first data row is line 2. Reporting the index within the data
    would send a teacher to the wrong line of the file they are looking at, which is the whole
    value of reporting a line at all.
    """
    return [(number, normalise(row)) for number, row in enumerate(rows, start=2)]
