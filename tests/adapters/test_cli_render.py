"""Rendering, tested as an interface rather than as prose.

The exit codes and the JSON keys are the promise; the human lines are not. So these tests assert
the codes and the shapes exactly, and assert of the text only what a person would actually
complain about -- that a missing grade is not printed as ``None``, and that a rejected row says
which line it was.
"""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from academy.adapters.inbound.cli import render
from academy.adapters.inbound.cli.exit_codes import ExitCode
from academy.application.dtos import (
    AcademicHistoryDto,
    GradeEntryDto,
    GradeRecordedDto,
    ImportResultDto,
    PersonDto,
    RowError,
    SectionGradeRowDto,
    SectionGradesDto,
    SubjectStandingDto,
)
from academy.application.jobs import ImportJob, ImportKind, JobId, JobStatus
from academy.config.settings import PersistenceBackend, Settings

SUBMITTED_AT = datetime(2026, 8, 31, 9, 0, 0)


def _job(status: JobStatus, *, result: ImportResultDto | None = None, reason: str | None = None) -> ImportJob:
    return ImportJob(
        id=JobId.from_str('11111111-1111-4111-8111-111111111111'),
        kind=ImportKind.GRADE_SHEET,
        storage_key='imports/11111111-1111-4111-8111-111111111111.csv',
        submitted_by='22222222-2222-4222-8222-222222222222',
        submitted_at=SUBMITTED_AT,
        status=status,
        result=result,
        failure_reason=reason,
    )


ANN = PersonDto(id='P1', email='ann@a.test', full_name='Ann', birth_date=date(2013, 1, 1), roles=('student',))
ZOE = PersonDto(id='P2', email='zoe@a.test', full_name='Zoe', birth_date=date(2012, 1, 1), roles=('student',))


@pytest.mark.unit
def test_a_section_grade_sheet_renders_every_row_in_both_shapes() -> None:
    output = render.section_grades(
        SectionGradesDto(
            section_id='S1',
            subject_id='SUB1',
            term='2026-1',
            rows=(
                SectionGradeRowDto(student_id='P1', full_name='Ann', best_grade=8, passed=True, attempts=2),
                SectionGradeRowDto(student_id='P2', full_name='Bo', best_grade=None, passed=False, attempts=0),
            ),
        )
    )

    assert output.exit_code == ExitCode.OK
    assert output.data['section_id'] == 'S1'
    assert output.data['rows'] == [
        {'student_id': 'P1', 'full_name': 'Ann', 'best_grade': 8, 'passed': True, 'attempts': 2},
        {'student_id': 'P2', 'full_name': 'Bo', 'best_grade': None, 'passed': False, 'attempts': 0},
    ]


@pytest.mark.unit
def test_a_student_with_no_grade_yet_is_not_shown_the_word_none() -> None:
    output = render.section_grades(
        SectionGradesDto(
            section_id='S1',
            subject_id='SUB1',
            term='2026-1',
            rows=(SectionGradeRowDto(student_id='P2', full_name='Bo', best_grade=None, passed=False, attempts=0),),
        )
    )

    assert 'None' not in '\n'.join(output.lines)
    assert 'best=-' in output.lines[-1]


@pytest.mark.unit
def test_recording_a_grade_reports_the_resulting_standing_not_an_acknowledgement() -> None:
    output = render.grade_recorded(
        GradeRecordedDto(student_id='P1', subject_id='SUB1', recorded_grade=4, best_grade=7, passed=True)
    )

    assert output.data == {
        'student_id': 'P1',
        'subject_id': 'SUB1',
        'recorded_grade': 4,
        'best_grade': 7,
        'passed': True,
    }
    assert 'best=7' in output.lines[1]


@pytest.mark.unit
def test_a_transcript_renders_its_entries_and_its_standings() -> None:
    output = render.academic_history(
        AcademicHistoryDto(
            student_id='P1',
            entries=(GradeEntryDto(subject_id='SUB1', term='2026-1', grade=7, from_section_id='S1'),),
            standings=(SubjectStandingDto(subject_id='SUB1', best_grade=7, passed=True, attempts=1),),
        )
    )

    assert output.data['entries'] == [{'subject_id': 'SUB1', 'term': '2026-1', 'grade': 7, 'from_section_id': 'S1'}]
    assert output.data['standings'] == [{'subject_id': 'SUB1', 'best_grade': 7, 'passed': True, 'attempts': 1}]


@pytest.mark.unit
def test_wards_are_ordered_by_name_because_the_use_case_promises_no_order() -> None:
    """A listing a person reads should be stable between two runs that returned the same set."""
    forwards = render.wards([ZOE, ANN])
    backwards = render.wards([ANN, ZOE])

    assert forwards.data == backwards.data
    assert forwards.data['wards'] == [
        {'id': 'P1', 'email': 'ann@a.test', 'full_name': 'Ann', 'birth_date': '2013-01-01', 'roles': ['student']},
        {'id': 'P2', 'email': 'zoe@a.test', 'full_name': 'Zoe', 'birth_date': '2012-01-01', 'roles': ['student']},
    ]


@pytest.mark.unit
def test_an_import_that_accepted_everything_exits_zero() -> None:
    output = render.import_result(ImportResultDto(created=3, updated=1))

    assert output.exit_code == ExitCode.OK
    assert output.data['ok'] is True


@pytest.mark.unit
def test_an_import_that_rejected_a_row_still_reports_and_exits_ten() -> None:
    """A run that rejected thirty of a hundred rows completed; whether that is acceptable is separate."""
    output = render.import_result(
        ImportResultDto(created=2, errors=(RowError(line=4, reason='no such student', values=('x', '7')),))
    )

    assert output.exit_code == ExitCode.IMPORT_INCOMPLETE
    assert output.data['created'] == 2
    assert 'line 4' in output.lines[-1]


@pytest.mark.unit
def test_a_dry_run_says_so_in_both_renderings() -> None:
    """Nobody should be able to mistake a rehearsal for the real thing."""
    output = render.import_result(ImportResultDto(created=3, dry_run=True))

    assert output.data['dry_run'] is True
    assert output.lines[0].startswith('dry run: ')


@pytest.mark.unit
@pytest.mark.parametrize(
    ('job', 'expected'),
    [
        pytest.param(_job(JobStatus.PENDING), ExitCode.OK, id='pending-is-a-success'),
        pytest.param(_job(JobStatus.RUNNING), ExitCode.OK, id='running-is-a-success'),
        pytest.param(_job(JobStatus.DONE, result=ImportResultDto(created=1)), ExitCode.OK, id='done-and-clean'),
        pytest.param(
            _job(JobStatus.DONE, result=ImportResultDto(errors=(RowError(line=2, reason='bad'),))),
            ExitCode.IMPORT_INCOMPLETE,
            id='done-with-rejected-rows',
        ),
        pytest.param(
            _job(JobStatus.FAILED, reason='payload has gone'), ExitCode.IMPORT_INCOMPLETE, id='failed-outright'
        ),
    ],
)
def test_a_jobs_state_decides_what_a_shell_sees(job: ImportJob, expected: ExitCode) -> None:
    assert render.import_job(job).exit_code == expected


@pytest.mark.unit
def test_a_failed_job_says_why() -> None:
    output = render.import_job(_job(JobStatus.FAILED, reason='payload has gone'))

    assert output.data['failure_reason'] == 'payload has gone'
    assert 'payload has gone' in '\n'.join(output.lines)


@pytest.mark.unit
def test_a_job_renders_its_report_when_it_has_one() -> None:
    output = render.import_job(_job(JobStatus.DONE, result=ImportResultDto(created=5)))

    assert output.data['result'] == {
        'created': 5,
        'updated': 0,
        'skipped': 0,
        'dry_run': False,
        'ok': True,
        'errors': [],
    }


@pytest.mark.unit
def test_configuration_shows_every_datum_a_deployment_can_choose() -> None:
    """ "What am I running with?" should not require reading ``Defaults``."""
    output = render.configuration(Settings(persistence=PersistenceBackend.MEMORY))

    assert set(output.data) == {
        'persistence',
        'database_url',
        'migration_database_url',
        'upload_directory',
        'import_inline_threshold_bytes',
        'import_max_bytes',
    }
    assert output.data['persistence'] == 'memory'
    assert len(output.lines) == len(output.data)


@pytest.mark.unit
@pytest.mark.parametrize(
    'output',
    [
        pytest.param(render.import_result(ImportResultDto(created=1)), id='import-result'),
        pytest.param(render.import_job(_job(JobStatus.PENDING)), id='import-job'),
        pytest.param(render.configuration(Settings()), id='configuration'),
        pytest.param(render.template_written('out.xlsx', 42), id='template'),
        pytest.param(render.wards([ANN]), id='wards'),
    ],
)
def test_every_rendering_survives_json_serialisation(output: render.Output) -> None:
    """``--json`` is an interface, so a value that cannot be written is a broken command."""
    assert json.loads(json.dumps(output.data)) == output.data
