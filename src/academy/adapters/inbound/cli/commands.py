"""One handler per subcommand: argv in, a command object out, a port called, an Output back.

This is the whole of what a driving adapter does, and the file is short on purpose. There is no
rule here. There is no ``except DomainError`` either -- a use case's failures travel up to the one
error boundary in :mod:`.main`, which consults the table every inbound adapter shares (ADR-0012,
ADR-0019).

**No handler sees a** :class:`~academy.config.container.Scope`. Each one names the single driving
port it needs and receives that, which is the point of having grouped the ports by actor intent
in the first place (ADR-0003): a scope carries every repository as well as every use case, so a
handler holding one could read a transcript out of ``scope.histories`` and never call a use case
at all. It is a short walk from there to a rule living in an adapter, and the type is what stops
it -- ``grades_list`` cannot reach anything but ``ManageGrades`` because it has nothing else.

:class:`Command` is what makes that free: it pairs a handler with the accessor that pulls its port
out of a scope, and is itself callable with the uniform signature the dispatch table wants. The
port type is checked at the pairing and then forgotten, so the table stays one flat mapping and
does not need an ``Any`` to hold entries of different port types.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from academy.adapters.inbound.cli import render
from academy.adapters.inbound.cli.parser import Args
from academy.adapters.inbound.cli.render import Output
from academy.application.commands import (
    DownloadTemplateCommand,
    ListMyWardsCommand,
    ListSectionGradesCommand,
    RecordGradeCommand,
    SubmitImportCommand,
    ViewAcademicHistoryCommand,
    ViewImportJobCommand,
)
from academy.application.dtos import Actor, ImportResultDto
from academy.application.jobs import ImportContext, ImportKind
from academy.application.ports.inbound.grading import ManageGrades
from academy.application.ports.inbound.imports import ImportData
from academy.application.ports.inbound.records import ViewStudentRecords
from academy.config.container import Scope

# What the dispatch table holds: something callable with the scope, the actor and the arguments.
# Every entry is a `Command`, whose own port type has been checked and then erased -- which is why
# this alias does not mention one.
#
# Comments rather than attribute docstrings: the check-docstring-first hook reads a string
# literal after a module-level assignment as a second module docstring.
type Handler = Callable[[Scope, Actor, Args], Awaitable[Output]]


@dataclass(frozen=True, slots=True)
class Command[PortT]:
    """One subcommand: the port it needs, and what it does with it.

    Generic over the port so that the pairing is checked -- ``Command(Scope.grade_management,
    records_show)`` does not compile, because ``records_show`` does not take a ``ManageGrades``.
    That check happens once, here, at the table; after it the parameter is gone and every
    ``Command`` is just a ``Handler``.

    This is the whole mechanism by which a handler can be narrow and the dispatch table can still
    be flat. Without it the choice would be a table typed ``Any`` or a handler holding the entire
    scope, and both give up something worth more than the six lines below.
    """

    port: Callable[[Scope], PortT]
    handle: Callable[[PortT, Actor, Args], Awaitable[Output]]

    async def __call__(self, scope: Scope, actor: Actor, args: Args) -> Output:
        """Pull this command's port out of the scope and run it.

        The one line in the CLI that touches a scope at all, and it does nothing with it but ask
        for the single port it was told to ask for.
        """
        return await self.handle(self.port(scope), actor, args)


async def grades_list(grades: ManageGrades, actor: Actor, args: Args) -> Output:
    """List a section's roster with each student's standing (UC-21)."""
    dto = await grades.list_section_grades(ListSectionGradesCommand(actor=actor, section_id=args.text('section_id')))
    return render.section_grades(dto)


async def grades_record(grades: ManageGrades, actor: Actor, args: Args) -> Output:
    """Record one grade for one student (UC-22).

    The grade is passed as the integer argparse parsed and is not checked here. Whether ``11`` is
    a grade is the domain's question, answered once by ``Grade``, and an adapter that also
    answered it would be the second place to get it wrong.
    """
    dto = await grades.record_grade(
        RecordGradeCommand(
            actor=actor,
            section_id=args.text('section_id'),
            student_id=args.text('student_id'),
            grade=args.number('grade'),
        )
    )
    return render.grade_recorded(dto)


async def records_show(records: ViewStudentRecords, actor: Actor, args: Args) -> Output:
    """Read a student's transcript, as themselves or as their guardian (UC-26, UC-30)."""
    dto = await records.view_academic_history(
        ViewAcademicHistoryCommand(actor=actor, student_id=args.text('student_id'))
    )
    return render.academic_history(dto)


async def records_wards(records: ViewStudentRecords, actor: Actor, args: Args) -> Output:
    """List the students currently in the actor's care (UC-28).

    Takes no argument beyond the actor, and could not: the subject of the question is the person
    asking it, and a ``--student`` option here would hand anyone the ability to enumerate anyone
    else's wards.
    """
    del args
    people = await records.list_my_wards(ListMyWardsCommand(actor=actor))
    return render.wards(people)


async def import_template(imports: ImportData, actor: Actor, args: Args) -> Output:
    """Write an import template to a file (UC-36).

    ``--output`` is required rather than defaulting to stdout, because an XLSX workbook is the
    likelier of the two formats and nobody wants a workbook on their terminal twice.
    """
    data = await imports.download_template(
        DownloadTemplateCommand(
            actor=actor,
            kind=ImportKind(args.text('kind')),
            file_format=args.text('file_format'),
            context=_context(args),
        )
    )
    path = Path(args.text('output'))
    path.write_bytes(data)
    return render.template_written(str(path), len(data))


async def import_run(imports: ImportData, actor: Actor, args: Args) -> Output:
    """Import a spreadsheet, inline or queued as its size dictates (UC-40, UC-41).

    Reads the file here, in the adapter, which is the whole of the translation: below this line
    the import is bytes and a filename, exactly as it is for an HTTP upload.

    Goes through ``submit`` rather than ``run_inline`` so that where the work runs stays a
    deployment's decision (ADR-0009) rather than becoming a flag on a command line. The filename
    travels with it because a local file has a name and no MIME type, and it is the only thing
    that says whether these bytes are CSV or XLSX.
    """
    path = Path(args.text('path'))
    outcome = await imports.submit(
        SubmitImportCommand(
            actor=actor,
            kind=ImportKind(args.text('kind')),
            data=path.read_bytes(),
            filename=path.name,
            dry_run=args.flag('dry_run'),
            context=_context(args),
        )
    )
    if isinstance(outcome, ImportResultDto):
        return render.import_result(outcome)

    # It was queued. The job comes back as it was *submitted*, so its state is read again rather
    # than reported from the returned object: with an inline queue the run has already finished
    # by now, and with a real worker it genuinely is still pending. Re-reading is the only answer
    # that is true in both deployments.
    return render.import_job(await imports.view_job(ViewImportJobCommand(actor=actor, job_id=str(outcome.id))))


async def import_job(imports: ImportData, actor: Actor, args: Args) -> Output:
    """Read a queued import's current state (UC-41)."""
    job = await imports.view_job(ViewImportJobCommand(actor=actor, job_id=args.text('job_id')))
    return render.import_job(job)


# The grammar's `command` string to the command that serves it. A table rather than a chain of
# `if`s, and the one place the two halves of this adapter meet: `parser.py` names a command and
# never imports a handler, this names a handler and never parses anything.
#
# Each row also states, in the accessor on its left, exactly which driving port that subcommand is
# allowed to touch. That is the narrowest useful reading of the whole file: `records wards` may
# call `ViewStudentRecords` and nothing else, and the type checker holds it to that.
HANDLERS: Final[dict[str, Handler]] = {
    'grades list': Command(Scope.grade_management, grades_list),
    'grades record': Command(Scope.grade_management, grades_record),
    'records show': Command(Scope.student_records, records_show),
    'records wards': Command(Scope.student_records, records_wards),
    'import template': Command(Scope.import_data, import_template),
    'import run': Command(Scope.import_data, import_run),
    'import job': Command(Scope.import_data, import_job),
}


def _context(args: Args) -> ImportContext:
    """Collect the importer-specific parameters a command line supplied.

    Only ``--section`` so far. Absent stays absent rather than becoming an empty string: the
    importer answers a missing section and one that names nothing with the same
    ``NotFoundError``, and it should not have to distinguish "not given" from "given as nothing".
    """
    section_id = args.optional_text('section_id')
    return {} if section_id is None else {'section_id': section_id}
