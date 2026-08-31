"""Step definitions for the grade-import feature (UC-40).

The `bdd` tier's second feature, and the one the repository's argument rests on: each scenario
runs through the CSV adapter and the XLSX adapter and must reach the same outcome. The format
is a column in the ``Examples`` table, which is as close as Gherkin gets to saying "and the
choice of adapter is not part of this story".

The steps drive the **use case**. When the web adapter lands, the same feature file can be
given a second set of steps that upload over HTTP; if those disagree with these, the rules have
leaked into the adapter layer.

Steps are synchronous and call ``asyncio.run``: pytest-bdd never awaits an ``async def`` step,
so one would return a coroutine and the assertion after it would pass without the work having
happened.
"""

import asyncio
from collections.abc import Coroutine
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

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
from academy.application.commands import ImportSpreadsheetCommand
from academy.application.dtos import Actor, ImportResultDto
from academy.application.errors import AuthorizationError, MalformedSpreadsheetError
from academy.application.importing import GradeSheetImporter, ImportService, SpreadsheetFormats
from academy.application.jobs import ImportKind
from academy.domain.academics.course_section import CourseSection
from academy.domain.academics.term import Term
from academy.domain.people.email import Email
from academy.domain.people.person import Person
from academy.domain.people.personal_data import PersonalData
from academy.domain.people.role import Role
from academy.domain.shared.ids import PersonId, SectionId, SubjectId

pytestmark = pytest.mark.bdd

scenarios('grade_import.feature')

TODAY = date(2026, 8, 30)
TERM = Term(2026, 1)

GRACE = PersonId(UUID(int=1))
ADA = PersonId(UUID(int=2))
BOB = PersonId(UUID(int=3))
ZOE = PersonId(UUID(int=4))
NEMO = PersonId(UUID(int=5))

SECTION = SectionId(UUID(int=10))
MATHEMATICS = SubjectId(UUID(int=20))

CONTENT_TYPES = {'csv': 'text/csv', 'xlsx': 'application/vnd.ms-excel'}


class World:
    """What the steps build up and then assert on."""

    def __init__(self) -> None:
        """Start with an empty store and no file."""
        self.store = MemoryStore()
        self.data = b''
        self.file_format = 'csv'
        self.result: ImportResultDto | None = None
        self.refusal: Exception | None = None


def _run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    """Drive one async call to completion from a synchronous step."""
    return asyncio.run(coroutine)


def _person(person_id: PersonId, name: str, *roles: Role) -> Person:
    local = name.split()[0].lower()
    return Person(
        id=person_id,
        email=Email(f'{local}@academy.test'),
        personal=PersonalData(full_name=name, birth_date=date(2005, 1, 1)),
        roles=set(roles),
    )


def _formats() -> SpreadsheetFormats:
    return SpreadsheetFormats(
        readers={'csv': CsvSpreadsheetReader(), 'xlsx': XlsxSpreadsheetReader()},
        writers={'csv': CsvSpreadsheetWriter(), 'xlsx': XlsxSpreadsheetWriter()},
    )


def _service(world: World) -> ImportService:
    """Wire the import use cases to the in-memory adapters."""
    store = world.store
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
    return ImportService(
        importers={
            ImportKind.GRADE_SHEET: GradeSheetImporter(
                sections=sections,
                histories=MemoryAcademicHistoryRepository(store),
                people=people,
                guard=guard,
            )
        },
        formats=_formats(),
        unit_of_work=lambda: MemoryUnitOfWork(store),
        jobs=MemoryImportJobRepository(store),
        people=people,
        storage=MemoryFileStorage(),
        queue=MemoryJobQueue(),
        clock=clock,
        ids=SequentialIdGenerator(),
        guard=guard,
    )


def _import(world: World, actor: Actor, *, dry_run: bool = False) -> None:
    """Run the import, remembering either the report or the refusal."""
    command = ImportSpreadsheetCommand(
        actor=actor,
        kind=ImportKind.GRADE_SHEET,
        data=world.data,
        content_type=CONTENT_TYPES[world.file_format],
        dry_run=dry_run,
        context={'section_id': str(SECTION)},
    )
    try:
        world.result = _run(_service(world).run_inline(command))
    except (AuthorizationError, MalformedSpreadsheetError) as error:
        world.refusal = error


@pytest.fixture
def world() -> World:
    """The scenario's own store."""
    return World()


@given('Grace teaches Mathematics this term')
def _(world: World) -> None:
    world.store.people[GRACE] = _person(GRACE, 'Grace Hopper', Role.TEACHER)
    world.store.people[NEMO] = _person(NEMO, 'Nemo Nobody')
    section = CourseSection(id=SECTION, subject_id=MATHEMATICS, term=TERM, teacher_id=GRACE)
    world.store.sections[section.id] = section


@given('Ada and Bob are enrolled in it')
def _(world: World) -> None:
    for person_id, name in ((ADA, 'Ada Lovelace'), (BOB, 'Bob Martin')):
        world.store.people[person_id] = _person(person_id, name, Role.STUDENT)
        world.store.sections[SECTION].enroll(person_id)


@given('Zoe is a student who is not')
def _(world: World) -> None:
    world.store.people[ZOE] = _person(ZOE, 'Zoe Newcomer', Role.STUDENT)


@given(parsers.parse('a {file_format} grade sheet with'), target_fixture='sheet')
def _(world: World, file_format: str, datatable: list[list[str]]) -> None:
    # The table's first line is the header, exactly as it is in the file a teacher uploads --
    # including whatever capitalisation they used, which one scenario exercises deliberately.
    world.file_format = file_format
    headers, *rows = datatable
    world.data = _formats().writer_for(file_format).write_sheet(headers, rows)


@given('a file that is not a spreadsheet at all')
def _(world: World) -> None:
    world.file_format = 'xlsx'
    world.data = b'PK\x03\x04 this is not a workbook'


@when('Grace imports it')
def _(world: World) -> None:
    _import(world, Actor(person_id=GRACE, roles=frozenset({Role.TEACHER})))


@when('Grace tries it out without saving')
def _(world: World) -> None:
    _import(world, Actor(person_id=GRACE, roles=frozenset({Role.TEACHER})), dry_run=True)


@when('Nemo imports it')
def _(world: World) -> None:
    _import(world, Actor(person_id=NEMO, roles=frozenset()))


@then(parsers.parse('{count:d} rows are recorded'))
def _(world: World, count: int) -> None:
    assert world.result is not None, f'the import was refused: {world.refusal}'
    assert world.result.created == count


@then('no row is rejected')
def _(world: World) -> None:
    assert world.result is not None
    assert world.result.ok


@then(parsers.parse('row {line:d} is rejected because no student has that email'))
def _(world: World, line: int) -> None:
    assert world.result is not None
    assert [error.line for error in world.result.errors] == [line]
    assert 'no student with email' in world.result.errors[0].reason


@then(parsers.parse('row {line:d} is rejected because the student is not enrolled'))
def _(world: World, line: int) -> None:
    assert world.result is not None
    assert [error.line for error in world.result.errors] == [line]
    assert 'not enrolled' in world.result.errors[0].reason


@then(parsers.parse('row {line:d} is rejected because the grade is not a grade'))
def _(world: World, line: int) -> None:
    assert world.result is not None
    assert [error.line for error in world.result.errors] == [line]
    assert 'is not a grade' in world.result.errors[0].reason


@then(parsers.parse("Ada's best grade in Mathematics is {grade:d}"))
def _(world: World, grade: int) -> None:
    history = world.store.histories.get(ADA)
    assert history is not None
    best = history.best_grade(MATHEMATICS)
    assert best is not None
    assert best.value == grade


@then('Ada has no grades at all')
def _(world: World) -> None:
    # The dry-run assertion, and the refusal one: in both cases the store must look exactly
    # as it did before the file arrived.
    assert world.store.histories.get(ADA) is None


@then('the import is refused')
def _(world: World) -> None:
    assert isinstance(world.refusal, AuthorizationError)


@then('the file is refused as unreadable')
def _(world: World) -> None:
    # One error for the file, not one per row: there are no rows to report on.
    assert isinstance(world.refusal, MalformedSpreadsheetError)
