"""Unit tests for the grading use cases (UC-21, UC-22).

`unit` tier per ADR-0013: the domain and the application are real, and every outbound port is
satisfied by its in-memory adapter rather than by a mock. Nothing here asserts that a call was
made; everything asserts what the outcome was.

These live outside ``tests/unit/`` because that directory is copied verbatim from
``multi-tenant-python`` alongside the domain (ADR-0002) and must stay byte-identical -- adding
a file to it would break the diff that guarantees the domain was not quietly edited.
"""

from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from academy.adapters.outbound.persistence.memory import (
    MemoryAcademicHistoryRepository,
    MemoryConfigurationRepository,
    MemoryGuardianshipRepository,
    MemoryPersonRepository,
    MemorySectionRepository,
    MemoryStore,
    MemoryUnitOfWork,
)
from academy.adapters.outbound.system import FixedClock
from academy.application.authorization import AccessGuard, RelationshipResolver
from academy.application.commands import ListSectionGradesCommand, RecordGradeCommand
from academy.application.dtos import Actor
from academy.application.errors import AuthorizationError, NotFoundError
from academy.application.grading import GradeManagement
from academy.application.ports.inbound.grading import ManageGrades
from academy.domain.academics.course_section import CourseSection
from academy.domain.academics.term import Term
from academy.domain.grades.grade import InvalidGradeError
from academy.domain.guardianship.guardianship import Guardianship
from academy.domain.people.email import Email
from academy.domain.people.person import Person
from academy.domain.people.personal_data import PersonalData
from academy.domain.people.role import Role
from academy.domain.services.grading_service import NotTeacherOfSectionError, StudentNotEnrolledError
from academy.domain.shared.ids import GuardianshipId, PersonId, SectionId, SubjectId

TODAY = date(2026, 8, 28)
TERM = Term(2026, 1)

TEACHER = PersonId(UUID(int=1))
BOB = PersonId(UUID(int=2))
ADA = PersonId(UUID(int=3))
ADMIN = PersonId(UUID(int=4))
OUTSIDER = PersonId(UUID(int=5))
GUARDIAN = PersonId(UUID(int=6))
OTHER_TEACHER = PersonId(UUID(int=7))
GHOST = PersonId(UUID(int=8))

MATH = SubjectId(UUID(int=20))
PHYSICS = SubjectId(UUID(int=21))
HISTORY = SubjectId(UUID(int=22))
ART = SubjectId(UUID(int=23))

# ADA alone. The section a guardian of ADA must still not be able to read.
SECTION_A = SectionId(UUID(int=10))
# BOB alone, same teacher: lets BOB be a student of TEACHER without being in SECTION_A.
SECTION_B = SectionId(UUID(int=11))
# BOB and ADA. Their ids sort the other way round from their names, so a listing that came
# back in id order would fail the ordering assertion.
SECTION_C = SectionId(UUID(int=12))
# ADA again, under a different teacher.
SECTION_D = SectionId(UUID(int=13))
# Nobody at all.
SECTION_E = SectionId(UUID(int=14))
# A roster naming somebody with no person record.
SECTION_F = SectionId(UUID(int=15))


def _person(person_id: PersonId, name: str, born: date, *roles: Role) -> Person:
    local = name.split()[0].lower()
    return Person(
        id=person_id,
        email=Email(f'{local}@academy.test'),
        personal=PersonalData(full_name=name, birth_date=born),
        roles=set(roles),
    )


def _section(section_id: SectionId, subject_id: SubjectId, teacher_id: PersonId, *students: PersonId) -> CourseSection:
    section = CourseSection(id=section_id, subject_id=subject_id, term=TERM, teacher_id=teacher_id)
    for student_id in students:
        section.enroll(student_id)
    return section


@pytest.fixture
def store() -> MemoryStore:
    """A store populated with the cast the grading tests share."""
    store = MemoryStore()
    for person in (
        _person(TEACHER, 'Grace Hopper', date(1980, 1, 1), Role.TEACHER),
        _person(OTHER_TEACHER, 'Edsger Dijkstra', date(1975, 1, 1), Role.TEACHER),
        _person(BOB, 'Bob Martin', date(2000, 1, 1), Role.STUDENT),
        _person(ADA, 'Ada Lovelace', date(2012, 5, 1), Role.STUDENT),
        _person(ADMIN, 'Ida Registrar', date(1985, 1, 1), Role.ADMINISTRATIVE_EMPLOYEE),
        _person(OUTSIDER, 'Nemo Nobody', date(1990, 1, 1)),
        _person(GUARDIAN, 'Mary Lovelace', date(1982, 1, 1), Role.GUARDIAN),
    ):
        store.people[person.id] = person

    for section in (
        _section(SECTION_A, MATH, TEACHER, ADA),
        _section(SECTION_B, PHYSICS, TEACHER, BOB),
        _section(SECTION_C, HISTORY, TEACHER, BOB, ADA),
        _section(SECTION_D, MATH, OTHER_TEACHER, ADA),
        _section(SECTION_E, ART, TEACHER),
        _section(SECTION_F, ART, TEACHER, GHOST),
    ):
        store.sections[section.id] = section

    link = Guardianship(id=GuardianshipId(UUID(int=30)), guardian_id=GUARDIAN, ward_id=ADA)
    store.guardianships[link.id] = link
    return store


@pytest.fixture
def grades(store: MemoryStore) -> GradeManagement:
    """The use cases under test, wired to the in-memory adapters."""
    people = MemoryPersonRepository(store)
    sections = MemorySectionRepository(store)
    histories = MemoryAcademicHistoryRepository(store)
    resolver = RelationshipResolver(
        sections=sections,
        guardianships=MemoryGuardianshipRepository(store),
        people=people,
        configuration=MemoryConfigurationRepository(store),
        clock=FixedClock(datetime(TODAY.year, TODAY.month, TODAY.day, 9, 0, tzinfo=UTC)),
    )
    return GradeManagement(
        sections=sections,
        histories=histories,
        people=people,
        uow=MemoryUnitOfWork(store),
        guard=AccessGuard(resolver),
    )


def _actor(person_id: PersonId, *roles: Role) -> Actor:
    return Actor(person_id=person_id, roles=frozenset(roles))


TEACHER_ACTOR = _actor(TEACHER, Role.TEACHER)
OTHER_TEACHER_ACTOR = _actor(OTHER_TEACHER, Role.TEACHER)
ADMIN_ACTOR = _actor(ADMIN, Role.ADMINISTRATIVE_EMPLOYEE)
OUTSIDER_ACTOR = _actor(OUTSIDER)
GUARDIAN_ACTOR = _actor(GUARDIAN, Role.GUARDIAN)
ADA_ACTOR = _actor(ADA, Role.STUDENT)


def _record(actor: Actor, section_id: SectionId, student_id: PersonId, grade: int) -> RecordGradeCommand:
    return RecordGradeCommand(actor=actor, section_id=str(section_id), student_id=str(student_id), grade=grade)


def _list(actor: Actor, section_id: SectionId) -> ListSectionGradesCommand:
    return ListSectionGradesCommand(actor=actor, section_id=str(section_id))


@pytest.mark.unit
def test_grade_management_satisfies_its_port(grades: GradeManagement) -> None:
    assert isinstance(grades, ManageGrades)


@pytest.mark.unit
async def test_record_grade_reports_the_resulting_standing(grades: GradeManagement) -> None:
    result = await grades.record_grade(_record(TEACHER_ACTOR, SECTION_A, ADA, 8))

    assert result.student_id == str(ADA)
    assert result.subject_id == str(MATH)
    assert result.recorded_grade == 8
    assert result.best_grade == 8
    assert result.passed


@pytest.mark.unit
async def test_record_grade_persists_the_entry(grades: GradeManagement, store: MemoryStore) -> None:
    await grades.record_grade(_record(TEACHER_ACTOR, SECTION_A, ADA, 8))

    entries = store.histories[ADA].entries_for(MATH)
    assert [entry.grade.value for entry in entries] == [8]
    assert entries[0].source_section_id == SECTION_A
    assert entries[0].term == TERM


@pytest.mark.unit
async def test_a_later_worse_attempt_does_not_change_the_standing(grades: GradeManagement) -> None:
    await grades.record_grade(_record(TEACHER_ACTOR, SECTION_A, ADA, 7))
    result = await grades.record_grade(_record(TEACHER_ACTOR, SECTION_A, ADA, 4))

    assert result.recorded_grade == 4
    assert result.best_grade == 7
    assert result.passed


@pytest.mark.unit
async def test_every_attempt_is_kept(grades: GradeManagement, store: MemoryStore) -> None:
    await grades.record_grade(_record(TEACHER_ACTOR, SECTION_A, ADA, 7))
    await grades.record_grade(_record(TEACHER_ACTOR, SECTION_A, ADA, 4))

    assert [entry.grade.value for entry in store.histories[ADA].entries_for(MATH)] == [7, 4]


@pytest.mark.unit
@pytest.mark.parametrize('actor', [ADMIN_ACTOR, OUTSIDER_ACTOR, GUARDIAN_ACTOR, ADA_ACTOR])
async def test_only_a_teacher_of_the_student_may_write_a_grade(grades: GradeManagement, actor: Actor) -> None:
    with pytest.raises(AuthorizationError):
        await grades.record_grade(_record(actor, SECTION_A, ADA, 8))


@pytest.mark.unit
async def test_teaching_the_student_elsewhere_does_not_grant_this_section(grades: GradeManagement) -> None:
    """OTHER_TEACHER teaches ADA in SECTION_D, so the guard allows the write.

    Which section it is remains the domain's check, not a second copy of the guard's.
    """
    with pytest.raises(NotTeacherOfSectionError):
        await grades.record_grade(_record(OTHER_TEACHER_ACTOR, SECTION_A, ADA, 8))


@pytest.mark.unit
async def test_the_student_must_be_enrolled_in_the_graded_section(grades: GradeManagement) -> None:
    with pytest.raises(StudentNotEnrolledError):
        await grades.record_grade(_record(TEACHER_ACTOR, SECTION_A, BOB, 8))


@pytest.mark.unit
async def test_a_failed_recording_writes_nothing(grades: GradeManagement, store: MemoryStore) -> None:
    """The empty history created on the way in is rolled back with everything else.

    ``get_or_create`` stores a transcript before the domain gets to reject the grading, so
    this is the assertion that the unit of work is real rather than decorative.
    """
    with pytest.raises(StudentNotEnrolledError):
        await grades.record_grade(_record(TEACHER_ACTOR, SECTION_A, BOB, 8))

    assert BOB not in store.histories


@pytest.mark.unit
@pytest.mark.parametrize('bad', [-1, 11])
async def test_a_grade_outside_the_scale_is_refused(grades: GradeManagement, bad: int) -> None:
    with pytest.raises(InvalidGradeError):
        await grades.record_grade(_record(TEACHER_ACTOR, SECTION_A, ADA, bad))


@pytest.mark.unit
async def test_recording_into_an_unknown_section_is_not_found(grades: GradeManagement) -> None:
    unknown = SectionId(UUID(int=999))
    with pytest.raises(NotFoundError):
        await grades.record_grade(_record(TEACHER_ACTOR, unknown, ADA, 8))


@pytest.mark.unit
async def test_grading_a_roster_entry_with_no_person_is_not_found(grades: GradeManagement) -> None:
    """GHOST is enrolled in SECTION_F, so the guard grants the write; the person is missing.

    Without the explicit lookup this would surface as ``StudentNotEnrolledError`` -- true,
    but not what the port promises and not what tells a caller what to fix.
    """
    with pytest.raises(NotFoundError) as raised:
        await grades.record_grade(_record(TEACHER_ACTOR, SECTION_F, GHOST, 8))

    assert raised.value.entity == 'student'


@pytest.mark.unit
async def test_list_section_grades_describes_the_section(grades: GradeManagement) -> None:
    sheet = await grades.list_section_grades(_list(TEACHER_ACTOR, SECTION_C))

    assert sheet.section_id == str(SECTION_C)
    assert sheet.subject_id == str(HISTORY)
    assert sheet.term == '2026-T1'


@pytest.mark.unit
async def test_list_section_grades_orders_by_name_not_by_id(grades: GradeManagement) -> None:
    sheet = await grades.list_section_grades(_list(TEACHER_ACTOR, SECTION_C))

    assert [row.full_name for row in sheet.rows] == ['Ada Lovelace', 'Bob Martin']


@pytest.mark.unit
async def test_an_ungraded_student_is_a_row_not_an_omission(grades: GradeManagement) -> None:
    sheet = await grades.list_section_grades(_list(TEACHER_ACTOR, SECTION_C))
    ada = next(row for row in sheet.rows if row.student_id == str(ADA))

    assert ada.best_grade is None
    assert ada.attempts == 0
    assert not ada.passed


@pytest.mark.unit
async def test_a_row_carries_the_standing_for_this_sections_subject(grades: GradeManagement) -> None:
    """ADA's MATH grade must not show up on the HISTORY sheet."""
    await grades.record_grade(_record(TEACHER_ACTOR, SECTION_A, ADA, 9))
    await grades.record_grade(_record(TEACHER_ACTOR, SECTION_C, ADA, 5))
    await grades.record_grade(_record(TEACHER_ACTOR, SECTION_C, ADA, 6))

    sheet = await grades.list_section_grades(_list(TEACHER_ACTOR, SECTION_C))
    ada = next(row for row in sheet.rows if row.student_id == str(ADA))

    assert ada.best_grade == 6
    assert ada.attempts == 2
    assert ada.passed


@pytest.mark.unit
async def test_an_administrative_employee_may_read_a_sheet(grades: GradeManagement) -> None:
    sheet = await grades.list_section_grades(_list(ADMIN_ACTOR, SECTION_C))

    assert len(sheet.rows) == 2


@pytest.mark.unit
@pytest.mark.parametrize('actor', [OUTSIDER_ACTOR, ADA_ACTOR])
async def test_a_sheet_is_not_readable_without_a_granting_relation(grades: GradeManagement, actor: Actor) -> None:
    with pytest.raises(AuthorizationError):
        await grades.list_section_grades(_list(actor, SECTION_C))


@pytest.mark.unit
async def test_a_guardian_of_the_only_student_may_not_read_the_sheet(grades: GradeManagement) -> None:
    """SECTION_A holds ADA alone, and GUARDIAN may read ADA's grades.

    Checking only the roster would let them read the whole sheet; requiring the teacher as
    well is what closes that hole.
    """
    with pytest.raises(AuthorizationError):
        await grades.list_section_grades(_list(GUARDIAN_ACTOR, SECTION_A))


@pytest.mark.unit
async def test_an_empty_section_is_still_not_public(grades: GradeManagement) -> None:
    """Checking only the roster would authorize anybody at all here, vacuously."""
    with pytest.raises(AuthorizationError):
        await grades.list_section_grades(_list(OUTSIDER_ACTOR, SECTION_E))


@pytest.mark.unit
async def test_an_empty_section_lists_as_an_empty_sheet(grades: GradeManagement) -> None:
    sheet = await grades.list_section_grades(_list(TEACHER_ACTOR, SECTION_E))

    assert sheet.rows == ()


@pytest.mark.unit
async def test_a_roster_entry_with_no_person_is_omitted(grades: GradeManagement) -> None:
    sheet = await grades.list_section_grades(_list(TEACHER_ACTOR, SECTION_F))

    assert sheet.rows == ()


@pytest.mark.unit
async def test_listing_an_unknown_section_is_not_found(grades: GradeManagement) -> None:
    with pytest.raises(NotFoundError):
        await grades.list_section_grades(_list(TEACHER_ACTOR, SectionId(UUID(int=999))))


@pytest.mark.unit
@pytest.mark.parametrize('raw', ['', 'not-a-uuid'])
async def test_an_unparseable_section_id_is_not_found(grades: GradeManagement, raw: str) -> None:
    """A malformed id names nothing, and must not escape as a ValueError.

    ADR-0012's table has no entry for one, so it would become a 500 for what is plainly a
    bad request.
    """
    with pytest.raises(NotFoundError):
        await grades.list_section_grades(ListSectionGradesCommand(actor=TEACHER_ACTOR, section_id=raw))


@pytest.mark.unit
async def test_an_unparseable_student_id_is_not_found(grades: GradeManagement) -> None:
    command = RecordGradeCommand(actor=TEACHER_ACTOR, section_id=str(SECTION_A), student_id='nope', grade=8)
    with pytest.raises(NotFoundError):
        await grades.record_grade(command)
