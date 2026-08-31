"""Turning a DTO into the two things a terminal wants: lines to read, or JSON to pipe.

Every handler renders its own result here, in a named function per shape. The alternative --
one ``render(value)`` that dispatches on type -- would put every output format in one place and
make each new command edit it, which is the shape that eventually grows an ``isinstance`` ladder
nobody wants to touch.

Both renderings come from the same :class:`Output`, so a command cannot print one thing and
report another. ``--json`` is not a debug mode: it is the interface a script uses, and the human
lines are the one a person reads. Neither is derived from the other's text.

Nothing here reaches into a domain object. Every function takes a DTO or an application-owned
value, which is what stops a formatter calling ``section.enroll()`` -- the reason use cases
return DTOs at all.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from academy.adapters.inbound.cli.exit_codes import ExitCode
from academy.application.dtos import (
    AcademicHistoryDto,
    GradeRecordedDto,
    ImportResultDto,
    PersonDto,
    SectionGradesDto,
)
from academy.application.jobs import ImportJob, JobStatus
from academy.config.settings import Settings

# What `json.dumps` accepts, spelled out rather than left as `Any`. Recursive, which PEP 695
# type aliases permit because their right-hand side is evaluated lazily.
#
# Comments rather than attribute docstrings: the check-docstring-first hook reads a string
# literal after a module-level assignment as a second module docstring.
type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class Output:
    """What one command produced, in both renderings, plus how it should exit.

    The exit code travels with the output because for some commands it *is* part of the output:
    an import that rejected rows succeeded as a command and failed as an import, and only the
    handler that ran it knows which (see
    :attr:`~academy.adapters.inbound.cli.exit_codes.ExitCode.IMPORT_INCOMPLETE`).
    """

    lines: tuple[str, ...] = ()
    data: JsonObject = field(default_factory=dict)
    exit_code: ExitCode = ExitCode.OK


def section_grades(dto: SectionGradesDto) -> Output:
    """Render a section's roster with each student's standing (UC-21)."""
    header = f'section {dto.section_id}  subject {dto.subject_id}  term {dto.term}'
    rows = [
        f'  {row.student_id}  {row.full_name}  '
        f'best={_grade(row.best_grade)}  passed={_yes_no(row.passed)}  attempts={row.attempts}'
        for row in dto.rows
    ]
    return Output(
        lines=(header, f'{len(dto.rows)} student(s)', *rows),
        data={
            'section_id': dto.section_id,
            'subject_id': dto.subject_id,
            'term': dto.term,
            'rows': [
                {
                    'student_id': row.student_id,
                    'full_name': row.full_name,
                    'best_grade': row.best_grade,
                    'passed': row.passed,
                    'attempts': row.attempts,
                }
                for row in dto.rows
            ],
        },
    )


def grade_recorded(dto: GradeRecordedDto) -> Output:
    """Render the standing that resulted from recording a grade (UC-22).

    Reports the standing rather than an acknowledgement, because that is what the use case
    returns and what the teacher actually asked: a 4 recorded after an earlier 7 changes nothing
    about whether the subject is passed, and the output should say so.
    """
    return Output(
        lines=(
            f'recorded {dto.recorded_grade} for student {dto.student_id} in subject {dto.subject_id}',
            f'best={dto.best_grade}  passed={_yes_no(dto.passed)}',
        ),
        data={
            'student_id': dto.student_id,
            'subject_id': dto.subject_id,
            'recorded_grade': dto.recorded_grade,
            'best_grade': dto.best_grade,
            'passed': dto.passed,
        },
    )


def academic_history(dto: AcademicHistoryDto) -> Output:
    """Render a student's transcript and per-subject standing (UC-26, UC-30)."""
    entries = [
        f'  {entry.term}  subject {entry.subject_id}  grade {entry.grade}'
        + (f'  (section {entry.from_section_id})' if entry.from_section_id else '')
        for entry in dto.entries
    ]
    standings = [
        f'  subject {standing.subject_id}  best={_grade(standing.best_grade)}  '
        f'passed={_yes_no(standing.passed)}  attempts={standing.attempts}'
        for standing in dto.standings
    ]
    return Output(
        lines=(
            f'student {dto.student_id}',
            f'{len(dto.entries)} entry(ies)',
            *entries,
            f'{len(dto.standings)} subject(s)',
            *standings,
        ),
        data={
            'student_id': dto.student_id,
            'entries': [
                {
                    'subject_id': entry.subject_id,
                    'term': entry.term,
                    'grade': entry.grade,
                    'from_section_id': entry.from_section_id,
                }
                for entry in dto.entries
            ],
            'standings': [
                {
                    'subject_id': standing.subject_id,
                    'best_grade': standing.best_grade,
                    'passed': standing.passed,
                    'attempts': standing.attempts,
                }
                for standing in dto.standings
            ],
        },
    )


def wards(people: Sequence[PersonDto]) -> Output:
    """Render the students currently in the actor's care (UC-28).

    Sorted by name here and not by the use case, which promises no ordering: a listing a person
    reads should be stable between two invocations that returned the same set, and deciding that
    is the presenting adapter's business.
    """
    ordered = sorted(people, key=lambda person: (person.full_name, person.id))
    return Output(
        lines=(
            f'{len(ordered)} ward(s)',
            *(f'  {person.id}  {person.full_name}  <{person.email}>' for person in ordered),
        ),
        data={'wards': [_person(person) for person in ordered]},
    )


def import_result(dto: ImportResultDto) -> Output:
    """Render one import report (UC-37 to UC-40).

    A run that rejected thirty of a hundred rows still *completed*, so the summary is printed
    either way and the exit code carries the separate question of whether it was acceptable.
    """
    errors = [f'  line {error.line}: {error.reason}' for error in dto.errors]
    prefix = 'dry run: ' if dto.dry_run else ''
    return Output(
        lines=(
            f'{prefix}created={dto.created} updated={dto.updated} skipped={dto.skipped} rejected={len(dto.errors)}',
            *errors,
        ),
        data=_result(dto),
        exit_code=ExitCode.OK if dto.ok else ExitCode.IMPORT_INCOMPLETE,
    )


def import_job(job: ImportJob) -> Output:
    """Render a queued import's current state (UC-41, UC-42).

    A job that is still pending is a *success*: the command was asked to submit it and it did.
    Only a terminal state that did not fully succeed changes the exit code.
    """
    lines = [f'job {job.id}  kind={job.kind.value}  status={job.status.value}']
    if job.failure_reason:
        lines.append(f'  failed: {job.failure_reason}')
    if job.result is not None:
        lines.extend(import_result(job.result).lines)

    return Output(
        lines=tuple(lines),
        data={
            'job_id': str(job.id),
            'kind': job.kind.value,
            'status': job.status.value,
            'submitted_by': job.submitted_by,
            'submitted_at': job.submitted_at.isoformat(),
            'failure_reason': job.failure_reason,
            'result': None if job.result is None else _result(job.result),
        },
        exit_code=_job_exit_code(job),
    )


def template_written(path: str, size: int) -> Output:
    """Render the outcome of writing an import template to a file (UC-36).

    The bytes went to a file rather than to stdout, so the only thing to report is where they
    went and how many there were -- and an XLSX workbook on a terminal is not a thing anyone
    wants twice.
    """
    return Output(lines=(f'wrote {size} byte(s) to {path}',), data={'path': path, 'bytes': size})


def configuration(settings: Settings) -> Output:
    """Render what this deployment was configured with.

    Every datum a deployment can choose, resolved -- defaults filled in -- because "what am I
    actually running with?" is the first question of every support conversation and the answer
    should not require reading :class:`~academy.config.settings.Defaults`.

    The database URLs are shown as configured, credentials included. That is deliberate for a
    tool whose caller already holds them (ADR-0020), and it is the reason this output must not be
    pasted into a bug report.
    """
    data: JsonObject = {
        'persistence': settings.persistence.value,
        'database_url': settings.database_url,
        'migration_database_url': settings.migration_database_url,
        'upload_directory': settings.upload_directory,
        'import_inline_threshold_bytes': settings.import_inline_threshold_bytes,
        'import_max_bytes': settings.import_max_bytes,
    }
    return Output(lines=tuple(f'{name} = {value}' for name, value in data.items()), data=data)


def _job_exit_code(job: ImportJob) -> ExitCode:
    """Decide what a job's state means to a shell."""
    if job.status is JobStatus.FAILED:
        return ExitCode.IMPORT_INCOMPLETE
    if job.result is not None and not job.result.ok:
        return ExitCode.IMPORT_INCOMPLETE
    return ExitCode.OK


def _result(dto: ImportResultDto) -> JsonObject:
    """The JSON shape of an import report, shared by the inline and queued renderings.

    The per-row lists are annotated rather than inferred: a ``list[str]`` is not a
    ``list[JsonValue]`` -- lists are invariant -- and widening it here is what makes the shape
    checkable rather than an ``Any`` that would have hidden the difference.
    """
    errors: list[JsonValue] = [
        {'line': error.line, 'reason': error.reason, 'values': _strings(error.values)} for error in dto.errors
    ]
    return {
        'created': dto.created,
        'updated': dto.updated,
        'skipped': dto.skipped,
        'dry_run': dto.dry_run,
        'ok': dto.ok,
        'errors': errors,
    }


def _person(person: PersonDto) -> JsonObject:
    """The JSON shape of a person."""
    return {
        'id': person.id,
        'email': person.email,
        'full_name': person.full_name,
        'birth_date': person.birth_date.isoformat(),
        'roles': _strings(person.roles),
    }


def _strings(values: tuple[str, ...]) -> list[JsonValue]:
    """Widen a tuple of strings into the list type JSON is written from."""
    return list(values)


def _grade(value: int | None) -> str:
    """Render a grade that may not exist yet, without printing ``None`` at a person."""
    return '-' if value is None else str(value)


def _yes_no(value: bool) -> str:
    """Render a boolean for a human, where ``True``/``False`` reads as debug output."""
    return 'yes' if value else 'no'
