"""Unit tests for the import use cases (UC-36, UC-40).

`unit` tier per ADR-0013: real domain, real application, in-memory adapters, and — unusually
for this tier — the **real spreadsheet adapters** too. They are production adapters (ADR-0014)
and they are pure CPU, so using them here costs nothing and tests the thing that actually runs.

Every scenario is run through **both** formats. That is the repository's second claim made
executable at the unit tier: the rules live above the port, so an outcome may not depend on
which adapter parsed the file. The acceptance tier will say the same thing in the spec's own
language; this says it first, and faster.
"""

from collections.abc import Callable
from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from academy.adapters.outbound.persistence.memory import (
    MemoryAcademicHistoryRepository,
    MemoryConfigurationRepository,
    MemoryGuardianshipRepository,
    MemoryImportJobRepository,
    MemoryPersonRepository,
    MemorySectionRepository,
    MemoryStore,
    MemoryUnitOfWork,
)
from academy.adapters.outbound.queue import MemoryJobQueue
from academy.adapters.outbound.spreadsheet import (
    CsvSpreadsheetReader,
    CsvSpreadsheetWriter,
    XlsxSpreadsheetReader,
    XlsxSpreadsheetWriter,
)
from academy.adapters.outbound.storage import MemoryFileStorage
from academy.adapters.outbound.system import FixedClock
from academy.adapters.outbound.system.ids import SequentialIdGenerator
from academy.application.authorization import AccessGuard, RelationshipResolver
from academy.application.commands import (
    DownloadTemplateCommand,
    ImportSpreadsheetCommand,
    RunImportJobCommand,
    SubmitImportCommand,
    ViewImportJobCommand,
)
from academy.application.dtos import Actor, ImportResultDto
from academy.application.errors import (
    AuthorizationError,
    MalformedSpreadsheetError,
    NotFoundError,
    PayloadTooLargeError,
)
from academy.application.importing import GradeSheetImporter, ImportService, SpreadsheetFormats
from academy.application.importing.service import DEFAULT_INLINE_THRESHOLD_BYTES, DEFAULT_MAX_BYTES
from academy.application.jobs import ImportJob, ImportKind, JobStateError, JobStatus
from academy.domain.academics.course_section import CourseSection
from academy.domain.academics.term import Term
from academy.domain.people.email import Email
from academy.domain.people.person import Person
from academy.domain.people.personal_data import PersonalData
from academy.domain.people.role import Role
from academy.domain.shared.ids import PersonId, SectionId, SubjectId

TODAY = date(2026, 8, 30)
TERM = Term(2026, 1)

TEACHER = PersonId(UUID(int=1))
ADA = PersonId(UUID(int=2))
BOB = PersonId(UUID(int=3))
OUTSIDER = PersonId(UUID(int=4))
UNENROLLED = PersonId(UUID(int=5))

SECTION = SectionId(UUID(int=10))
MATH = SubjectId(UUID(int=20))

TEACHER_ACTOR = Actor(person_id=TEACHER, roles=frozenset({Role.TEACHER}))
OUTSIDER_ACTOR = Actor(person_id=OUTSIDER, roles=frozenset())

HEADERS = ['student_email', 'student_name', 'grade']

FORMATS = ['csv', 'xlsx']


def _person(person_id: PersonId, name: str, *roles: Role) -> Person:
    local = name.split()[0].lower()
    return Person(
        id=person_id,
        email=Email(f'{local}@academy.test'),
        personal=PersonalData(full_name=name, birth_date=date(2005, 1, 1)),
        roles=set(roles),
    )


@pytest.fixture
def store() -> MemoryStore:
    """A section taught by Grace, with Ada and Bob enrolled and Zoe not."""
    store = MemoryStore()
    for person in (
        _person(TEACHER, 'Grace Hopper', Role.TEACHER),
        _person(ADA, 'Ada Lovelace', Role.STUDENT),
        _person(BOB, 'Bob Martin', Role.STUDENT),
        _person(UNENROLLED, 'Zoe Newcomer', Role.STUDENT),
        _person(OUTSIDER, 'Nemo Nobody'),
    ):
        store.people[person.id] = person

    section = CourseSection(id=SECTION, subject_id=MATH, term=TERM, teacher_id=TEACHER)
    section.enroll(ADA)
    section.enroll(BOB)
    store.sections[section.id] = section
    return store


@pytest.fixture
def formats() -> SpreadsheetFormats:
    """Both spreadsheet adapters, as the composition root will wire them."""
    return SpreadsheetFormats(
        readers={'csv': CsvSpreadsheetReader(), 'xlsx': XlsxSpreadsheetReader()},
        writers={'csv': CsvSpreadsheetWriter(), 'xlsx': XlsxSpreadsheetWriter()},
    )


@pytest.fixture
def jobs(store: MemoryStore) -> MemoryImportJobRepository:
    """Where queued imports are recorded."""
    return MemoryImportJobRepository(store)


@pytest.fixture
def storage() -> MemoryFileStorage:
    """Where a queued payload is written."""
    return MemoryFileStorage()


@pytest.fixture
def queue() -> MemoryJobQueue:
    """A queue that records ids rather than running them, so a test can see what was queued."""
    return MemoryJobQueue()


@pytest.fixture
def build_service(
    store: MemoryStore,
    formats: SpreadsheetFormats,
    jobs: MemoryImportJobRepository,
    storage: MemoryFileStorage,
    queue: MemoryJobQueue,
) -> Callable[..., ImportService]:
    """Build the import use cases, wired to the in-memory adapters.

    A factory rather than one fixture, because the size threshold is the only interesting
    difference between an inline run and a queued one, and a test that wants the queued path
    should say so by lowering it rather than by uploading a megabyte.
    """

    def build(
        inline_threshold_bytes: int = DEFAULT_INLINE_THRESHOLD_BYTES, max_bytes: int = DEFAULT_MAX_BYTES
    ) -> ImportService:
        people = MemoryPersonRepository(store)
        sections = MemorySectionRepository(store)
        clock = FixedClock(datetime(TODAY.year, TODAY.month, TODAY.day, 9, 0, tzinfo=UTC))
        guard = AccessGuard(
            RelationshipResolver(
                sections=sections,
                guardianships=MemoryGuardianshipRepository(store),
                people=people,
                configuration=MemoryConfigurationRepository(store),
                clock=clock,
            )
        )
        importer = GradeSheetImporter(
            sections=sections,
            histories=MemoryAcademicHistoryRepository(store),
            people=people,
            guard=guard,
        )
        return ImportService(
            importers={ImportKind.GRADE_SHEET: importer},
            formats=formats,
            unit_of_work=lambda: MemoryUnitOfWork(store),
            jobs=jobs,
            people=people,
            storage=storage,
            queue=queue,
            clock=clock,
            ids=SequentialIdGenerator(),
            guard=guard,
            inline_threshold_bytes=inline_threshold_bytes,
            max_bytes=max_bytes,
        )

    return build


@pytest.fixture
def service(build_service: Callable[..., ImportService]) -> ImportService:
    """The import use cases with this deployment's default sizes."""
    return build_service()


def _file(
    formats: SpreadsheetFormats, file_format: str, rows: list[list[str]], headers: list[str] | None = None
) -> bytes:
    """Render a grade sheet in one of the two formats."""
    return formats.writer_for(file_format).write_sheet(headers or HEADERS, rows)


def _command(
    data: bytes,
    file_format: str,
    *,
    actor: Actor = TEACHER_ACTOR,
    dry_run: bool = False,
    section: SectionId | None = SECTION,
) -> ImportSpreadsheetCommand:
    context = {'section_id': str(section)} if section is not None else {}
    return ImportSpreadsheetCommand(
        actor=actor,
        kind=ImportKind.GRADE_SHEET,
        data=data,
        content_type='text/csv' if file_format == 'csv' else 'application/vnd.ms-excel',
        dry_run=dry_run,
        context=context,
    )


# --------------------------------------------------------------------------------------
# The same outcome through either adapter
# --------------------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize('file_format', FORMATS)
async def test_every_valid_row_is_recorded(
    service: ImportService, formats: SpreadsheetFormats, store: MemoryStore, file_format: str
) -> None:
    data = _file(
        formats,
        file_format,
        [['ada@academy.test', 'Ada Lovelace', '8'], ['bob@academy.test', 'Bob Martin', '4']],
    )

    result = await service.run_inline(_command(data, file_format))

    assert result.created == 2
    assert result.ok
    assert store.histories[ADA].best_grade(MATH) is not None
    assert store.histories[BOB].best_grade(MATH) is not None


@pytest.mark.unit
@pytest.mark.parametrize('file_format', FORMATS)
async def test_headers_are_matched_case_and_space_insensitively(
    service: ImportService, formats: SpreadsheetFormats, file_format: str
) -> None:
    # The file a registrar exports from their own spreadsheet, with whatever spelling their
    # locale chose. Normalising is the use case's job -- doing it in each adapter would be two
    # chances to do it differently.
    data = _file(
        formats,
        file_format,
        [['ada@academy.test', 'Ada Lovelace', '8']],
        headers=['Student Email', 'STUDENT-NAME', ' grade '],
    )

    result = await service.run_inline(_command(data, file_format))

    assert result.created == 1
    assert result.ok


@pytest.mark.unit
@pytest.mark.parametrize('file_format', FORMATS)
async def test_a_bad_row_costs_only_itself(
    service: ImportService, formats: SpreadsheetFormats, store: MemoryStore, file_format: str
) -> None:
    # Partial success is the whole reason this reports rather than raises: one typo in a
    # hundred rows must not send the teacher back to the spreadsheet with nothing recorded.
    data = _file(
        formats,
        file_format,
        [
            ['ada@academy.test', 'Ada Lovelace', '8'],
            ['nobody@academy.test', 'Ghost', '7'],
            ['bob@academy.test', 'Bob Martin', '4'],
        ],
    )

    result = await service.run_inline(_command(data, file_format))

    assert result.created == 2
    assert [error.line for error in result.errors] == [3]
    assert 'no student with email' in result.errors[0].reason
    assert store.histories[ADA].best_grade(MATH) is not None


@pytest.mark.unit
@pytest.mark.parametrize('file_format', FORMATS)
async def test_the_second_row_for_a_student_is_rejected_as_a_duplicate(
    service: ImportService, formats: SpreadsheetFormats, store: MemoryStore, file_format: str
) -> None:
    # The later row loses. Taking the last would make the outcome depend on the order a
    # teacher happened to paste rows in.
    data = _file(
        formats,
        file_format,
        [['ada@academy.test', 'Ada Lovelace', '8'], ['ada@academy.test', 'Ada Lovelace', '3']],
    )

    result = await service.run_inline(_command(data, file_format))

    assert result.created == 1
    assert [error.line for error in result.errors] == [3]
    assert store.histories[ADA].best_grade(MATH) is not None
    assert [entry.grade.value for entry in store.histories[ADA].entries] == [8]


@pytest.mark.unit
@pytest.mark.parametrize('file_format', FORMATS)
@pytest.mark.parametrize('grade', ['eleven', '11', '-1', ''])
async def test_a_grade_the_domain_refuses_is_a_rejected_row(
    service: ImportService, formats: SpreadsheetFormats, file_format: str, grade: str
) -> None:
    data = _file(formats, file_format, [['ada@academy.test', 'Ada Lovelace', grade]])

    result = await service.run_inline(_command(data, file_format))

    assert result.created == 0
    assert [error.line for error in result.errors] == [2]
    assert 'is not a grade' in result.errors[0].reason


@pytest.mark.unit
@pytest.mark.parametrize('file_format', FORMATS)
async def test_a_student_not_in_this_section_is_a_rejected_row(
    service: ImportService, formats: SpreadsheetFormats, file_format: str
) -> None:
    # The domain's rule, asked by calling it rather than restated here.
    data = _file(formats, file_format, [['zoe@academy.test', 'Zoe Newcomer', '8']])

    result = await service.run_inline(_command(data, file_format))

    assert [error.reason for error in result.errors] == ['this student is not enrolled in this section']


@pytest.mark.unit
@pytest.mark.parametrize('file_format', FORMATS)
async def test_a_dry_run_reports_exactly_what_the_real_run_would_do_and_writes_nothing(
    service: ImportService, formats: SpreadsheetFormats, store: MemoryStore, file_format: str
) -> None:
    # The most useful thing on the import surface. It does the whole import and rolls it back,
    # rather than "checking without doing" -- which is the only way the report can be trusted,
    # because a rule that only fires on the third row is invisible to any amount of checking.
    data = _file(
        formats,
        file_format,
        [['ada@academy.test', 'Ada Lovelace', '8'], ['nobody@academy.test', 'Ghost', '7']],
    )

    rehearsal = await service.run_inline(_command(data, file_format, dry_run=True))
    assert store.histories.get(ADA) is None

    real = await service.run_inline(_command(data, file_format))

    assert rehearsal.dry_run
    assert not real.dry_run
    assert (rehearsal.created, [e.line for e in rehearsal.errors]) == (real.created, [e.line for e in real.errors])
    assert store.histories[ADA].best_grade(MATH) is not None


@pytest.mark.unit
@pytest.mark.parametrize('file_format', FORMATS)
async def test_an_actor_who_may_not_grade_is_refused_before_any_row_is_read(
    service: ImportService, formats: SpreadsheetFormats, store: MemoryStore, file_format: str
) -> None:
    # A permissions problem is one failure, not ninety-nine data problems.
    data = _file(formats, file_format, [['ada@academy.test', 'Ada Lovelace', '8']])

    with pytest.raises(AuthorizationError):
        await service.run_inline(_command(data, file_format, actor=OUTSIDER_ACTOR))

    assert store.histories.get(ADA) is None


# --------------------------------------------------------------------------------------
# Failures that are not about rows
# --------------------------------------------------------------------------------------


@pytest.mark.unit
async def test_a_file_that_is_not_a_spreadsheet_fails_before_the_transaction_opens(service: ImportService) -> None:
    command = ImportSpreadsheetCommand(
        actor=TEACHER_ACTOR,
        kind=ImportKind.GRADE_SHEET,
        data=b'PK\x03\x04 not a workbook',
        content_type='application/vnd.ms-excel',
        context={'section_id': str(SECTION)},
    )

    with pytest.raises(MalformedSpreadsheetError):
        await service.run_inline(command)


@pytest.mark.unit
@pytest.mark.parametrize('section', [None, SectionId(UUID(int=99))])
async def test_an_import_with_no_real_target_is_not_a_partial_success(
    service: ImportService, formats: SpreadsheetFormats, section: SectionId | None
) -> None:
    data = _file(formats, 'csv', [['ada@academy.test', 'Ada Lovelace', '8']])

    with pytest.raises(NotFoundError):
        await service.run_inline(_command(data, 'csv', section=section))


# --------------------------------------------------------------------------------------
# Templates (UC-36)
# --------------------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize('file_format', FORMATS)
async def test_a_grade_sheet_template_arrives_pre_filled_with_the_roster(
    service: ImportService, formats: SpreadsheetFormats, file_format: str
) -> None:
    # Pre-filled is what turns the round trip into a workflow: the teacher fills in a column
    # against emails the system can certainly resolve.
    data = await service.download_template(
        DownloadTemplateCommand(
            actor=TEACHER_ACTOR,
            kind=ImportKind.GRADE_SHEET,
            file_format=file_format,
            context={'section_id': str(SECTION)},
        )
    )

    rows = formats.reader_for(filename=f'x.{file_format}').read_rows(data)

    assert [row['student_email'] for row in rows] == ['ada@academy.test', 'bob@academy.test']
    assert [row['grade'] for row in rows] == ['', '']


@pytest.mark.unit
async def test_a_template_this_system_cannot_write_says_what_it_can(service: ImportService) -> None:
    with pytest.raises(MalformedSpreadsheetError, match='csv'):
        await service.download_template(
            DownloadTemplateCommand(
                actor=TEACHER_ACTOR,
                kind=ImportKind.GRADE_SHEET,
                file_format='ods',
                context={'section_id': str(SECTION)},
            )
        )


# --------------------------------------------------------------------------------------
# Choosing the adapter
# --------------------------------------------------------------------------------------


@pytest.mark.unit
def test_the_declared_content_type_chooses_the_reader(formats: SpreadsheetFormats) -> None:
    assert isinstance(formats.reader_for('text/csv'), CsvSpreadsheetReader)
    assert isinstance(formats.reader_for('application/vnd.ms-excel'), XlsxSpreadsheetReader)
    assert isinstance(formats.reader_for('text/csv; charset=utf-8'), CsvSpreadsheetReader)


@pytest.mark.unit
def test_the_filename_decides_when_the_content_type_says_nothing(formats: SpreadsheetFormats) -> None:
    # What a client that did not sniff the file sends, next to a user who was perfectly clear
    # when they named it.
    assert isinstance(formats.reader_for('application/octet-stream', 'grades.xlsx'), XlsxSpreadsheetReader)
    assert isinstance(formats.reader_for('', 'grades.csv'), CsvSpreadsheetReader)


@pytest.mark.unit
def test_an_upload_that_identifies_itself_not_at_all_gets_the_default(formats: SpreadsheetFormats) -> None:
    assert isinstance(formats.reader_for('', ''), CsvSpreadsheetReader)


@pytest.mark.unit
def test_a_format_with_only_half_an_adapter_is_a_wiring_error() -> None:
    # A template a registrar can download and cannot upload back. The composition root is
    # where that is cheap to notice.
    with pytest.raises(ValueError, match='both a reader and a writer'):
        SpreadsheetFormats(
            readers={'csv': CsvSpreadsheetReader(), 'xlsx': XlsxSpreadsheetReader()},
            writers={'csv': CsvSpreadsheetWriter()},
        )


# --------------------------------------------------------------------------------------
# Where the work runs (UC-41, UC-42, ADR-0009)
# --------------------------------------------------------------------------------------


def _submit(data: bytes, *, actor: Actor = TEACHER_ACTOR) -> SubmitImportCommand:
    return SubmitImportCommand(
        actor=actor,
        kind=ImportKind.GRADE_SHEET,
        data=data,
        filename='grades.csv',
        content_type='text/csv',
        context={'section_id': str(SECTION)},
    )


@pytest.mark.unit
async def test_a_small_upload_is_imported_before_the_call_returns(
    service: ImportService, formats: SpreadsheetFormats, store: MemoryStore, queue: MemoryJobQueue
) -> None:
    data = _file(formats, 'csv', [['ada@academy.test', 'Ada Lovelace', '8']])

    outcome = await service.submit(_submit(data))

    assert isinstance(outcome, ImportResultDto)
    assert outcome.created == 1
    assert store.histories[ADA].best_grade(MATH) is not None
    assert queue.queued() == []


@pytest.mark.unit
async def test_a_large_upload_comes_back_as_a_pending_job(
    build_service: Callable[..., ImportService],
    formats: SpreadsheetFormats,
    store: MemoryStore,
    jobs: MemoryImportJobRepository,
    queue: MemoryJobQueue,
    storage: MemoryFileStorage,
) -> None:
    # The threshold moves, not the file: where the work runs is one number, and a test that
    # had to upload a megabyte to reach the other branch would be testing the filesystem.
    service = build_service(inline_threshold_bytes=1)
    data = _file(formats, 'csv', [['ada@academy.test', 'Ada Lovelace', '8']])

    job = await service.submit(_submit(data))

    assert isinstance(job, ImportJob)
    assert job.status is JobStatus.PENDING
    assert store.histories.get(ADA) is None
    assert queue.queued() == [job.id]
    assert await storage.get(job.storage_key) == data
    assert await jobs.get(job.id) is not None


@pytest.mark.unit
async def test_a_payload_over_the_cap_is_refused_before_anything_is_stored(
    build_service: Callable[..., ImportService], formats: SpreadsheetFormats, queue: MemoryJobQueue
) -> None:
    service = build_service(inline_threshold_bytes=1, max_bytes=10)
    data = _file(formats, 'csv', [['ada@academy.test', 'Ada Lovelace', '8']])

    with pytest.raises(PayloadTooLargeError):
        await service.submit(_submit(data))

    assert queue.queued() == []


@pytest.mark.unit
async def test_running_a_queued_job_imports_the_stored_payload(
    build_service: Callable[..., ImportService], formats: SpreadsheetFormats, store: MemoryStore
) -> None:
    service = build_service(inline_threshold_bytes=1)
    rows = [['ada@academy.test', 'Ada Lovelace', '8'], ['nobody@academy.test', 'Ghost', '7']]
    submitted = await service.submit(_submit(_file(formats, 'csv', rows)))
    assert isinstance(submitted, ImportJob)

    job = await service.run_job(RunImportJobCommand(job_id=str(submitted.id)))

    assert job.status is JobStatus.DONE
    assert job.result is not None
    assert job.result.created == 1
    assert [error.line for error in job.result.errors] == [3]
    assert store.histories[ADA].best_grade(MATH) is not None


@pytest.mark.unit
async def test_rejected_rows_do_not_make_a_job_fail(
    build_service: Callable[..., ImportService], formats: SpreadsheetFormats
) -> None:
    # DONE means the run finished, not that every row was accepted. Collapsing the two would
    # make a partially rejected import indistinguishable from an unreadable file.
    service = build_service(inline_threshold_bytes=1)
    data = _file(formats, 'csv', [['nobody@academy.test', 'Ghost', '7']])
    submitted = await service.submit(_submit(data))
    assert isinstance(submitted, ImportJob)

    job = await service.run_job(RunImportJobCommand(job_id=str(submitted.id)))

    assert job.status is JobStatus.DONE
    assert job.result is not None
    assert not job.result.ok


@pytest.mark.unit
async def test_a_job_whose_payload_has_vanished_fails_with_a_reason(
    build_service: Callable[..., ImportService], formats: SpreadsheetFormats, storage: MemoryFileStorage
) -> None:
    # The failure a worker actually meets. Importing an empty file and reporting success is
    # the difference between an operator who re-uploads and one who thinks the file was empty.
    service = build_service(inline_threshold_bytes=1)
    data = _file(formats, 'csv', [['ada@academy.test', 'Ada Lovelace', '8']])
    submitted = await service.submit(_submit(data))
    assert isinstance(submitted, ImportJob)
    await storage.delete(submitted.storage_key)

    job = await service.run_job(RunImportJobCommand(job_id=str(submitted.id)))

    assert job.status is JobStatus.FAILED
    assert job.failure_reason is not None
    assert 'stored file' in job.failure_reason


@pytest.mark.unit
async def test_a_queued_run_carries_the_submitters_roles_not_an_empty_actor(
    build_service: Callable[..., ImportService], formats: SpreadsheetFormats, store: MemoryStore
) -> None:
    # The queued path rebuilds its actor, and an actor rebuilt from an id alone would hold no
    # roles -- not a smaller actor but a different one. It would refuse imports the inline
    # path allows, silently, which is the one difference these two paths must never have.
    store.people[TEACHER] = _person(TEACHER, 'Grace Hopper', Role.TEACHER, Role.ADMINISTRATIVE_EMPLOYEE)
    service = build_service(inline_threshold_bytes=1)
    data = _file(formats, 'csv', [['ada@academy.test', 'Ada Lovelace', '8']])
    submitted = await service.submit(_submit(data))
    assert isinstance(submitted, ImportJob)

    job = await service.run_job(RunImportJobCommand(job_id=str(submitted.id)))

    assert job.status is JobStatus.DONE
    assert job.result is not None
    assert job.result.created == 1


@pytest.mark.unit
async def test_a_role_lost_between_submitting_and_running_is_lost_for_the_run(
    build_service: Callable[..., ImportService], formats: SpreadsheetFormats, store: MemoryStore
) -> None:
    # Authorization is not frozen at submission time. The teacher stops teaching the section
    # while the job waits, and the run is refused -- with the job recording why.
    service = build_service(inline_threshold_bytes=1)
    data = _file(formats, 'csv', [['ada@academy.test', 'Ada Lovelace', '8']])
    submitted = await service.submit(_submit(data))
    assert isinstance(submitted, ImportJob)
    store.sections[SECTION] = CourseSection(id=SECTION, subject_id=MATH, term=TERM, teacher_id=OUTSIDER)
    store.sections[SECTION].enroll(ADA)

    job = await service.run_job(RunImportJobCommand(job_id=str(submitted.id)))

    assert job.status is JobStatus.FAILED
    assert store.histories.get(ADA) is None


@pytest.mark.unit
async def test_a_job_whose_submitter_has_gone_fails_rather_than_running_as_nobody(
    build_service: Callable[..., ImportService], formats: SpreadsheetFormats, store: MemoryStore
) -> None:
    service = build_service(inline_threshold_bytes=1)
    data = _file(formats, 'csv', [['ada@academy.test', 'Ada Lovelace', '8']])
    submitted = await service.submit(_submit(data))
    assert isinstance(submitted, ImportJob)
    del store.people[TEACHER]

    job = await service.run_job(RunImportJobCommand(job_id=str(submitted.id)))

    assert job.status is JobStatus.FAILED
    assert job.failure_reason is not None
    assert 'submitter' in job.failure_reason


@pytest.mark.unit
async def test_a_job_that_was_never_stored_is_not_a_job_that_failed(service: ImportService) -> None:
    with pytest.raises(NotFoundError):
        await service.run_job(RunImportJobCommand(job_id='not-a-uuid'))


@pytest.mark.unit
async def test_the_same_job_cannot_be_run_twice(
    build_service: Callable[..., ImportService], formats: SpreadsheetFormats
) -> None:
    # What stops two workers polling one queue from running the same import twice.
    service = build_service(inline_threshold_bytes=1)
    data = _file(formats, 'csv', [['ada@academy.test', 'Ada Lovelace', '8']])
    submitted = await service.submit(_submit(data))
    assert isinstance(submitted, ImportJob)
    await service.run_job(RunImportJobCommand(job_id=str(submitted.id)))

    with pytest.raises(JobStateError):
        await service.run_job(RunImportJobCommand(job_id=str(submitted.id)))


@pytest.mark.unit
async def test_a_submitter_can_watch_their_own_job(
    build_service: Callable[..., ImportService], formats: SpreadsheetFormats
) -> None:
    service = build_service(inline_threshold_bytes=1)
    data = _file(formats, 'csv', [['ada@academy.test', 'Ada Lovelace', '8']])
    submitted = await service.submit(_submit(data))
    assert isinstance(submitted, ImportJob)

    seen = await service.view_job(ViewImportJobCommand(actor=TEACHER_ACTOR, job_id=str(submitted.id)))

    assert seen.id == submitted.id


@pytest.mark.unit
async def test_a_stranger_cannot_watch_someone_elses_job(
    build_service: Callable[..., ImportService], formats: SpreadsheetFormats
) -> None:
    service = build_service(inline_threshold_bytes=1)
    data = _file(formats, 'csv', [['ada@academy.test', 'Ada Lovelace', '8']])
    submitted = await service.submit(_submit(data))
    assert isinstance(submitted, ImportJob)

    with pytest.raises(AuthorizationError):
        await service.view_job(ViewImportJobCommand(actor=OUTSIDER_ACTOR, job_id=str(submitted.id)))
