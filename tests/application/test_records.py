"""Unit tests for the transcript-reading use cases (UC-26, UC-28, UC-30).

`unit` tier per ADR-0013: the domain and the application are real, every outbound port is
satisfied by its in-memory adapter, and nothing asserts that a call was made -- only what the
outcome was.

The cast is built around the one rule these use cases exist to exercise: a guardianship that
expires on a birthday, with nothing written and no job run. MINOR and ADULT differ only in
their birth dates relative to ``TODAY``.
"""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest

from academy.adapters.outbound.persistence.memory import (
    MemoryAcademicHistoryRepository,
    MemoryConfigurationRepository,
    MemoryGuardianshipRepository,
    MemoryPersonRepository,
    MemorySectionRepository,
    MemoryStore,
)
from academy.adapters.outbound.system import FixedClock
from academy.application.authorization import AccessGuard, RelationshipResolver
from academy.application.commands import ListMyWardsCommand, ViewAcademicHistoryCommand
from academy.application.dtos import Actor
from academy.application.errors import AuthorizationError, NotFoundError
from academy.application.ports.inbound.records import ViewStudentRecords
from academy.application.records import StudentRecords
from academy.domain.academics.term import Term
from academy.domain.grades.academic_history import AcademicHistory
from academy.domain.grades.grade import Grade
from academy.domain.grades.grade_entry import GradeEntry
from academy.domain.guardianship.guardianship import Guardianship
from academy.domain.people.age_of_majority import AgeOfMajority
from academy.domain.people.email import Email
from academy.domain.people.person import Person
from academy.domain.people.personal_data import PersonalData
from academy.domain.people.role import Role
from academy.domain.shared.ids import GuardianshipId, PersonId, SubjectId

TODAY = date(2026, 8, 30)
TERM = Term(2026, 1)

MINOR = PersonId(UUID(int=1))
ADULT = PersonId(UUID(int=2))
GUARDIAN = PersonId(UUID(int=3))
ADMIN = PersonId(UUID(int=4))
OUTSIDER = PersonId(UUID(int=5))
UNGRADED = PersonId(UUID(int=6))
GHOST = PersonId(UUID(int=7))

MATH = SubjectId(UUID(int=20))
PHYSICS = SubjectId(UUID(int=21))

MINOR_LINK = GuardianshipId(UUID(int=30))
ADULT_LINK = GuardianshipId(UUID(int=31))
DUPLICATE_LINK = GuardianshipId(UUID(int=32))
GHOST_LINK = GuardianshipId(UUID(int=33))


def _person(person_id: PersonId, name: str, born: date, *roles: Role) -> Person:
    local = name.split()[0].lower()
    return Person(
        id=person_id,
        email=Email(f'{local}@academy.test'),
        personal=PersonalData(full_name=name, birth_date=born),
        roles=set(roles),
    )


def _actor(person_id: PersonId, *roles: Role) -> Actor:
    return Actor(person_id=person_id, roles=frozenset(roles))


MINOR_ACTOR = _actor(MINOR, Role.STUDENT)
ADULT_ACTOR = _actor(ADULT, Role.STUDENT)
GUARDIAN_ACTOR = _actor(GUARDIAN, Role.GUARDIAN)
ADMIN_ACTOR = _actor(ADMIN, Role.ADMINISTRATIVE_EMPLOYEE)
OUTSIDER_ACTOR = _actor(OUTSIDER)


@pytest.fixture
def store() -> MemoryStore:
    """A guardian with three links: a minor ward, a ward who has come of age, and a ghost."""
    store = MemoryStore()
    for person in (
        # Fifteen years old as of TODAY, and so still a ward.
        _person(MINOR, 'Ada Lovelace', date(2011, 5, 1), Role.STUDENT),
        # Twenty-two, and so not -- the link is stored all the same.
        _person(ADULT, 'Bob Martin', date(2004, 1, 1), Role.STUDENT),
        _person(GUARDIAN, 'Mary Lovelace', date(1982, 1, 1), Role.GUARDIAN),
        _person(ADMIN, 'Ida Registrar', date(1985, 1, 1), Role.ADMINISTRATIVE_EMPLOYEE),
        _person(OUTSIDER, 'Nemo Nobody', date(1990, 1, 1)),
        _person(UNGRADED, 'Zoe Newcomer', date(2010, 3, 1), Role.STUDENT),
    ):
        store.people[person.id] = person

    for link in (
        Guardianship(id=MINOR_LINK, guardian_id=GUARDIAN, ward_id=MINOR),
        Guardianship(id=ADULT_LINK, guardian_id=GUARDIAN, ward_id=ADULT),
        # A second link to the same ward: two guardianship records, one ward.
        Guardianship(id=DUPLICATE_LINK, guardian_id=GUARDIAN, ward_id=MINOR),
        # A link naming somebody with no person record.
        Guardianship(id=GHOST_LINK, guardian_id=GUARDIAN, ward_id=GHOST),
    ):
        store.guardianships[link.id] = link

    history = AcademicHistory(MINOR)
    history.record(GradeEntry(subject_id=MATH, term=TERM, grade=Grade(4)))
    history.record(GradeEntry(subject_id=MATH, term=TERM, grade=Grade(8)))
    history.record(GradeEntry(subject_id=PHYSICS, term=TERM, grade=Grade(3)))
    store.histories[history.id] = history
    return store


@pytest.fixture
def records(store: MemoryStore) -> StudentRecords:
    """The use cases under test, wired to the in-memory adapters."""
    people = MemoryPersonRepository(store)
    guardianships = MemoryGuardianshipRepository(store)
    configuration = MemoryConfigurationRepository(store)
    clock = FixedClock(datetime(TODAY.year, TODAY.month, TODAY.day, 9, 0, tzinfo=UTC))
    resolver = RelationshipResolver(
        sections=MemorySectionRepository(store),
        guardianships=guardianships,
        people=people,
        configuration=configuration,
        clock=clock,
    )
    return StudentRecords(
        histories=MemoryAcademicHistoryRepository(store),
        people=people,
        guardianships=guardianships,
        configuration=configuration,
        clock=clock,
        guard=AccessGuard(resolver),
    )


def _view(actor: Actor, student_id: PersonId) -> ViewAcademicHistoryCommand:
    return ViewAcademicHistoryCommand(actor=actor, student_id=str(student_id))


@pytest.mark.unit
def test_student_records_satisfies_its_port(records: StudentRecords) -> None:
    assert isinstance(records, ViewStudentRecords)


@pytest.mark.unit
async def test_a_student_reads_their_own_transcript(records: StudentRecords) -> None:
    history = await records.view_academic_history(_view(MINOR_ACTOR, MINOR))

    assert history.student_id == str(MINOR)
    assert [entry.grade for entry in history.entries] == [4, 8, 3]


@pytest.mark.unit
async def test_the_standing_is_the_best_attempt_not_the_last(records: StudentRecords) -> None:
    history = await records.view_academic_history(_view(MINOR_ACTOR, MINOR))

    standings = {standing.subject_id: standing for standing in history.standings}
    assert standings[str(MATH)].best_grade == 8
    assert standings[str(MATH)].attempts == 2
    assert standings[str(MATH)].passed
    assert not standings[str(PHYSICS)].passed


@pytest.mark.unit
async def test_a_student_with_no_grades_reads_as_an_empty_transcript(records: StudentRecords) -> None:
    # Not an error: "no grades yet" is the normal state of every student on their first day.
    history = await records.view_academic_history(_view(_actor(UNGRADED, Role.STUDENT), UNGRADED))

    assert history.student_id == str(UNGRADED)
    assert history.entries == ()
    assert history.standings == ()


@pytest.mark.unit
async def test_reading_an_empty_transcript_stores_nothing(records: StudentRecords, store: MemoryStore) -> None:
    # `get_or_create` would have been the easy way to build the projection, and it would have
    # made a read path write -- outside any transaction, at that.
    await records.view_academic_history(_view(_actor(UNGRADED, Role.STUDENT), UNGRADED))

    assert UNGRADED not in store.histories


@pytest.mark.unit
async def test_a_guardian_reads_a_minor_wards_transcript(records: StudentRecords) -> None:
    history = await records.view_academic_history(_view(GUARDIAN_ACTOR, MINOR))

    assert history.student_id == str(MINOR)


@pytest.mark.unit
async def test_a_guardian_cannot_read_a_ward_who_has_come_of_age(records: StudentRecords) -> None:
    # The link is still stored. Nothing was written, no job ran, and access is gone.
    with pytest.raises(AuthorizationError):
        await records.view_academic_history(_view(GUARDIAN_ACTOR, ADULT))


@pytest.mark.unit
async def test_an_administrator_reads_any_transcript(records: StudentRecords) -> None:
    history = await records.view_academic_history(_view(ADMIN_ACTOR, MINOR))

    assert history.student_id == str(MINOR)


@pytest.mark.unit
@pytest.mark.parametrize('actor', [OUTSIDER_ACTOR, ADULT_ACTOR])
async def test_an_unrelated_actor_is_refused(records: StudentRecords, actor: Actor) -> None:
    with pytest.raises(AuthorizationError):
        await records.view_academic_history(_view(actor, MINOR))


@pytest.mark.unit
async def test_authorization_is_checked_before_the_student_is_looked_up(records: StudentRecords) -> None:
    # A stranger asking about an id that does not exist must not be able to tell that from an
    # id that does: both answers have to be the same one.
    with pytest.raises(AuthorizationError):
        await records.view_academic_history(_view(OUTSIDER_ACTOR, GHOST))


@pytest.mark.unit
async def test_an_administrator_asking_for_nobody_gets_not_found(records: StudentRecords) -> None:
    with pytest.raises(NotFoundError):
        await records.view_academic_history(_view(ADMIN_ACTOR, GHOST))


@pytest.mark.unit
async def test_an_unparseable_student_id_names_nobody(records: StudentRecords) -> None:
    with pytest.raises(NotFoundError):
        await records.view_academic_history(ViewAcademicHistoryCommand(actor=ADMIN_ACTOR, student_id='not-a-uuid'))


@pytest.mark.unit
async def test_a_guardian_lists_only_the_wards_still_in_their_care(records: StudentRecords) -> None:
    wards = await records.list_my_wards(ListMyWardsCommand(actor=GUARDIAN_ACTOR))

    # ADULT has come of age, GHOST has no person record, and MINOR is named by two links.
    assert [ward.id for ward in wards] == [str(MINOR)]


@pytest.mark.unit
async def test_a_ward_coming_of_age_needs_no_write_to_disappear(records: StudentRecords, store: MemoryStore) -> None:
    # Move the goalposts rather than the calendar: raising the age of majority is the same
    # transition seen from the other side, and it is one an administrator really does perform.
    before = await records.list_my_wards(ListMyWardsCommand(actor=GUARDIAN_ACTOR))
    store.age_of_majority = AgeOfMajority(30)
    after = await records.list_my_wards(ListMyWardsCommand(actor=GUARDIAN_ACTOR))

    assert [ward.id for ward in before] == [str(MINOR)]
    assert {ward.id for ward in after} == {str(MINOR), str(ADULT)}


@pytest.mark.unit
async def test_someone_with_no_wards_gets_an_empty_list(records: StudentRecords) -> None:
    assert await records.list_my_wards(ListMyWardsCommand(actor=OUTSIDER_ACTOR)) == []


@pytest.mark.unit
async def test_a_ward_who_turns_of_age_exactly_today_is_no_longer_in_care(
    store: MemoryStore, records: StudentRecords
) -> None:
    # The boundary the whole rule turns on. `applies` is "not of legal age", and legal age is
    # reached *on* the birthday -- so the ward is out of care that morning, not the next day.
    store.people[MINOR] = _person(MINOR, 'Ada Lovelace', TODAY.replace(year=TODAY.year - 18), Role.STUDENT)

    assert await records.list_my_wards(ListMyWardsCommand(actor=GUARDIAN_ACTOR)) == []


@pytest.mark.unit
async def test_a_ward_one_day_short_of_it_is_still_in_care(store: MemoryStore, records: StudentRecords) -> None:
    born = TODAY.replace(year=TODAY.year - 18) + timedelta(days=1)
    store.people[MINOR] = _person(MINOR, 'Ada Lovelace', born, Role.STUDENT)

    wards = await records.list_my_wards(ListMyWardsCommand(actor=GUARDIAN_ACTOR))

    assert [ward.id for ward in wards] == [str(MINOR)]


@pytest.mark.unit
async def test_a_stored_but_empty_transcript_reads_like_no_transcript(
    store: MemoryStore, records: StudentRecords
) -> None:
    # Two different storage states -- a row with no entries, and no row at all -- that the
    # port promises to render identically. A caller must not be able to tell them apart.
    store.histories[UNGRADED] = AcademicHistory(UNGRADED)
    ungraded_actor = _actor(UNGRADED, Role.STUDENT)

    stored_empty = await records.view_academic_history(_view(ungraded_actor, UNGRADED))
    del store.histories[UNGRADED]
    absent = await records.view_academic_history(_view(ungraded_actor, UNGRADED))

    assert stored_empty == absent


@pytest.mark.unit
async def test_the_listing_does_not_depend_on_the_asker_having_a_person_record(
    store: MemoryStore, records: StudentRecords
) -> None:
    # A characterisation, and a deliberate one: the answer is derived from the links and the
    # wards, never from the asker's own record. An Actor comes from authentication, so a
    # guardian without a person row is a data problem elsewhere -- not a reason to refuse
    # them the list of children they are responsible for.
    del store.people[GUARDIAN]

    wards = await records.list_my_wards(ListMyWardsCommand(actor=GUARDIAN_ACTOR))

    assert [ward.id for ward in wards] == [str(MINOR)]


@pytest.mark.unit
async def test_the_ward_listing_answers_for_the_actor_and_nobody_else(records: StudentRecords) -> None:
    # There is no parameter to point somewhere else. The guardian's own actor gets the
    # guardian's wards; the outsider's gets nothing, and cannot ask about the guardian's.
    assert [ward.id for ward in await records.list_my_wards(ListMyWardsCommand(actor=GUARDIAN_ACTOR))] == [str(MINOR)]
    assert await records.list_my_wards(ListMyWardsCommand(actor=OUTSIDER_ACTOR)) == []
