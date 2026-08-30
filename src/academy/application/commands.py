"""The inputs to use cases.

A command is the request an actor makes, expressed in the application's own vocabulary and
carrying no trace of the protocol it arrived over. Translating a form post, a JSON body or an
``argv`` list into one of these is the inbound adapter's whole job.

Commands are **primitives and identifiers**, not domain objects. A router that had to build a
:class:`~academy.domain.grades.grade.Grade` to call a use case would be doing domain work in
the adapter layer, and would have to decide there what happens when someone types ``eleven``
-- three times, once per inbound adapter, differently each time. Parsing and validation belong
to the use case and the domain value objects, exactly once.

Every command carries its :class:`~academy.application.dtos.Actor`. Authorization is not an
ambient fact read from a thread-local or a request context: it is an argument, which is what
makes a use case callable from a CLI and a scheduler as easily as from HTTP.
"""

from __future__ import annotations

from dataclasses import dataclass

from academy.application.dtos import Actor
from academy.application.jobs import ImportKind


@dataclass(frozen=True, slots=True)
class RecordGradeCommand:
    """Record one grade for one student in one section (UC-22)."""

    actor: Actor
    section_id: str
    student_id: str
    grade: int


@dataclass(frozen=True, slots=True)
class ListSectionGradesCommand:
    """List a section's roster with each student's standing (UC-21)."""

    actor: Actor
    section_id: str


@dataclass(frozen=True, slots=True)
class ViewAcademicHistoryCommand:
    """Read one student's transcript (UC-26, UC-30).

    The same command serves a student reading their own record and a guardian reading a
    ward's. There is no separate "view my history" command, because the authorization check
    already distinguishes the two cases and a second command would be an invitation to
    forget it in one of them.
    """

    actor: Actor
    student_id: str


@dataclass(frozen=True, slots=True)
class ListMyWardsCommand:
    """List the students currently in the actor's care (UC-28).

    Carries only its actor, and that is the entire content of the request: the answer is
    *whose wards am I responsible for*, so the subject and the asker are the same person.

    It exists as a command rather than as a bare person id for exactly that reason. An id
    parameter would have to come from somewhere, and the somewhere an inbound adapter reaches
    for first is the request -- at which point anyone can enumerate anyone else's wards. An
    ``Actor`` can only come from authentication (ADR-0010), so the trust boundary is in the
    type rather than in a comment asking the adapter to be careful.
    """

    actor: Actor


@dataclass(frozen=True, slots=True)
class ImportSpreadsheetCommand:
    """Load a spreadsheet through one of the importers (UC-37 to UC-40).

    Attributes:
        actor: Who is importing.
        kind: Which importer to run.
        data: The complete uploaded file.
        content_type: The uploaded MIME type, used only to choose a
            :class:`~academy.application.ports.outbound.spreadsheet.SpreadsheetReader`
            adapter. It never reaches the importing rules.
        dry_run: Validate and report, then roll back, writing nothing. The most useful
            feature of the whole import surface: a registrar can find out what a file would
            do before it does it.
        context: Importer-specific parameters, such as the target section for a grade sheet.
    """

    actor: Actor
    kind: ImportKind
    data: bytes
    content_type: str = ''
    dry_run: bool = False
    context: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class SubmitImportCommand:
    """Submit an import, to run inline or in the background as size dictates (UC-41).

    Deliberately identical in shape to :class:`ImportSpreadsheetCommand`. The caller does not
    choose where the work runs -- that is a deployment concern decided by the file's size
    against a configured threshold (ADR-0009) -- so there is no ``background`` flag to get
    wrong.
    """

    actor: Actor
    kind: ImportKind
    data: bytes
    filename: str = ''
    content_type: str = ''
    dry_run: bool = False
    context: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class DownloadTemplateCommand:
    """Produce an import template (UC-36).

    Attributes:
        actor: Who is asking.
        kind: Which importer the template is for.
        file_format: ``'csv'`` or ``'xlsx'``; selects the
            :class:`~academy.application.ports.outbound.spreadsheet.SpreadsheetWriter`.
        context: Optional parameters. A grade-sheet template given a ``section_id`` comes
            back pre-filled with the enrolled students, which is what turns the round trip
            into an actual workflow rather than a blank form.
    """

    actor: Actor
    kind: ImportKind
    file_format: str = 'xlsx'
    context: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class RunImportJobCommand:
    """Execute one previously queued job (UC-42).

    Issued by the worker, not by a human. It names only the job: everything else -- the
    payload, the importer, the submitter -- was recorded when the job was accepted.
    """

    job_id: str


@dataclass(frozen=True, slots=True)
class ViewImportJobCommand:
    """Read a job's current state, for the polling progress fragment (UC-41)."""

    actor: Actor
    job_id: str


__all__ = [
    'DownloadTemplateCommand',
    'ImportKind',
    'ImportSpreadsheetCommand',
    'ListSectionGradesCommand',
    'RecordGradeCommand',
    'RunImportJobCommand',
    'SubmitImportCommand',
    'ViewAcademicHistoryCommand',
    'ViewImportJobCommand',
]
