"""What every implementation of a repository port must do (ADR-0014).

The port docstrings are the specification; these are the assertions. A repository adapter is
correct when it passes this file, and two adapters that disagree fail here rather than in
production.

**Parametrised over every implementation.** Today that list has one entry, which looks
degenerate and is the point: the SQLAlchemy adapter joins by adding one line to ``BACKENDS``,
not by someone writing a second suite that slowly drifts from this one. A port with one
implementation has never been tested as an abstraction.

`unit` tier while every backend is in-memory. When a backend needs a database, its parameter
moves to the integration tier and this file gets a marker per parameter rather than one for the
module.

Only what the ports *promise* is asserted. ``list_all`` promises a total order, so order is
asserted; ``wards_of`` promises no order, so contents are. Asserting more than the port says
would make the suite reject a legitimate adapter -- which is the failure mode that turns a
contract suite into a description of whichever implementation was written first.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from uuid import UUID

import pytest

from academy.adapters.outbound.persistence.memory import (
    DEFAULT_AGE_OF_MAJORITY,
    MemoryAcademicHistoryRepository,
    MemoryConfigurationRepository,
    MemoryGuardianshipRepository,
    MemoryPersonRepository,
    MemorySectionRepository,
    MemoryStore,
)
from academy.application.errors import ConflictError, NotFoundError
from academy.application.ports.outbound.repositories import (
    AcademicHistoryRepository,
    ConfigurationRepository,
    GuardianshipRepository,
    PersonRepository,
    SectionRepository,
)
from academy.domain.academics.course_section import CourseSection
from academy.domain.academics.term import Term
from academy.domain.guardianship.guardianship import Guardianship
from academy.domain.people.age_of_majority import AgeOfMajority
from academy.domain.people.email import Email
from academy.domain.people.person import Person
from academy.domain.people.personal_data import PersonalData
from academy.domain.people.role import Role
from academy.domain.shared.ids import CredentialId, GuardianshipId, PersonId, SectionId, SubjectId

ANN = PersonId(UUID(int=1))
BEA = PersonId(UUID(int=2))
CAL = PersonId(UUID(int=3))
GUARDIAN = PersonId(UUID(int=4))
UNKNOWN = PersonId(UUID(int=99))

MATH = SubjectId(UUID(int=20))
PHYSICS = SubjectId(UUID(int=21))

TEACHING = SectionId(UUID(int=10))
OLDER = SectionId(UUID(int=11))
OTHER_TEACHER_SECTION = SectionId(UUID(int=12))

DEGREE = CredentialId(UUID(int=30))
LINK = GuardianshipId(UUID(int=40))

THIS_TERM = Term(2026, 1)
LAST_TERM = Term(2025, 2)


@dataclass(frozen=True, slots=True)
class Backend:
    """One implementation of the persistence ports, ready to use.

    The bundle rather than one repository at a time, because several promises span two of them
    -- a history keyed by a person, a section holding people -- and a backend that satisfied
    them separately but not together would pass a narrower suite.
    """

    people: PersonRepository
    sections: SectionRepository
    histories: AcademicHistoryRepository
    guardianships: GuardianshipRepository
    configuration: ConfigurationRepository


def _memory() -> Backend:
    """The in-memory backend, sharing one store across its repositories."""
    store = MemoryStore()
    return Backend(
        people=MemoryPersonRepository(store),
        sections=MemorySectionRepository(store),
        histories=MemoryAcademicHistoryRepository(store),
        guardianships=MemoryGuardianshipRepository(store),
        configuration=MemoryConfigurationRepository(store),
    )


# Add the SQLAlchemy backend here when it lands; every test below then runs against it too.
BACKENDS = [pytest.param(_memory, id='memory')]


@pytest.fixture(params=BACKENDS)
def backend(request: pytest.FixtureRequest) -> Backend:
    """A fresh backend, one per test, per implementation."""
    build: Callable[[], Backend] = request.param
    return build()


def _person(person_id: PersonId, handle: str, *roles: Role) -> Person:
    return Person(
        id=person_id,
        email=Email(f'{handle}@academy.test'),
        personal=PersonalData(full_name=handle.title(), birth_date=date(1990, 1, 1)),
        roles=set(roles),
    )


def _section(
    section_id: SectionId, subject_id: SubjectId, term: Term, teacher: PersonId, *students: PersonId
) -> CourseSection:
    section = CourseSection(id=section_id, subject_id=subject_id, term=term, teacher_id=teacher)
    for student in students:
        section.enroll(student)
    return section


# --------------------------------------------------------------------------------------
# Repository: the operations every aggregate needs
# --------------------------------------------------------------------------------------


@pytest.mark.unit
async def test_get_returns_none_when_nothing_is_stored(backend: Backend) -> None:
    assert await backend.people.get(UNKNOWN) is None


@pytest.mark.unit
async def test_an_added_aggregate_can_be_read_back(backend: Backend) -> None:
    await backend.people.add(_person(ANN, 'ann'))

    stored = await backend.people.get(ANN)
    assert stored is not None
    assert stored.id == ANN


@pytest.mark.unit
async def test_add_refuses_an_identity_already_stored(backend: Backend) -> None:
    await backend.people.add(_person(ANN, 'ann'))

    with pytest.raises(ConflictError):
        await backend.people.add(_person(ANN, 'someone-else'))


@pytest.mark.unit
async def test_add_refuses_a_uniqueness_violation(backend: Backend) -> None:
    # Email is the login identifier, so it is unique across people even when the ids differ.
    await backend.people.add(_person(ANN, 'ann'))

    with pytest.raises(ConflictError):
        await backend.people.add(_person(BEA, 'ann'))


@pytest.mark.unit
async def test_save_refuses_an_aggregate_that_is_not_stored(backend: Backend) -> None:
    with pytest.raises(NotFoundError):
        await backend.people.save(_person(ANN, 'ann'))


@pytest.mark.unit
async def test_save_refuses_a_uniqueness_violation(backend: Backend) -> None:
    await backend.people.add(_person(ANN, 'ann'))
    await backend.people.add(_person(BEA, 'bea'))

    with pytest.raises(ConflictError):
        await backend.people.save(_person(BEA, 'ann'))


@pytest.mark.unit
async def test_save_persists_the_change(backend: Backend) -> None:
    await backend.people.add(_person(ANN, 'ann'))
    await backend.people.save(_person(ANN, 'ann', Role.TEACHER))

    stored = await backend.people.get(ANN)
    assert stored is not None
    assert stored.has_role(Role.TEACHER)


@pytest.mark.unit
async def test_delete_removes_the_aggregate(backend: Backend) -> None:
    await backend.people.add(_person(ANN, 'ann'))
    await backend.people.delete(ANN)

    assert await backend.people.get(ANN) is None


@pytest.mark.unit
async def test_delete_refuses_an_aggregate_that_is_not_stored(backend: Backend) -> None:
    with pytest.raises(NotFoundError):
        await backend.people.delete(UNKNOWN)


@pytest.mark.unit
async def test_list_all_is_ordered_by_natural_key(backend: Backend) -> None:
    # People sort by email. Inserted out of order on purpose: an adapter returning insertion
    # order, or whatever the database volunteers, fails here rather than in a paginated view.
    await backend.people.add(_person(CAL, 'cal'))
    await backend.people.add(_person(ANN, 'ann'))
    await backend.people.add(_person(BEA, 'bea'))

    assert [person.id for person in await backend.people.list_all()] == [ANN, BEA, CAL]


# --------------------------------------------------------------------------------------
# PersonRepository
# --------------------------------------------------------------------------------------


@pytest.mark.unit
async def test_by_email_matches_case_insensitively(backend: Backend) -> None:
    await backend.people.add(_person(ANN, 'ann'))

    found = await backend.people.by_email('ANN@ACADEMY.TEST')
    assert found is not None
    assert found.id == ANN


@pytest.mark.unit
async def test_by_email_returns_none_for_an_address_nobody_uses(backend: Backend) -> None:
    assert await backend.people.by_email('nobody@academy.test') is None


@pytest.mark.unit
async def test_by_ids_answers_in_the_order_asked_and_omits_the_unknown(backend: Backend) -> None:
    await backend.people.add(_person(ANN, 'ann'))
    await backend.people.add(_person(BEA, 'bea'))

    found = await backend.people.by_ids([BEA, UNKNOWN, ANN])

    assert [person.id for person in found] == [BEA, ANN]


@pytest.mark.unit
async def test_holders_of_finds_everyone_holding_the_credential(backend: Backend) -> None:
    holder = _person(ANN, 'ann', Role.TEACHER)
    holder.hold_credential(DEGREE)
    await backend.people.add(holder)
    await backend.people.add(_person(BEA, 'bea'))

    assert [person.id for person in await backend.people.holders_of(DEGREE)] == [ANN]


# --------------------------------------------------------------------------------------
# SectionRepository
# --------------------------------------------------------------------------------------


@pytest.fixture
async def sections(backend: Backend) -> Backend:
    """A backend with three sections: two taught by ANN, one by CAL."""
    await backend.sections.add(_section(TEACHING, MATH, THIS_TERM, ANN, BEA))
    await backend.sections.add(_section(OLDER, PHYSICS, LAST_TERM, ANN, BEA))
    await backend.sections.add(_section(OTHER_TEACHER_SECTION, MATH, THIS_TERM, CAL, BEA))
    return backend


@pytest.mark.unit
async def test_for_teacher_answers_most_recent_term_first(sections: Backend) -> None:
    found = await sections.sections.for_teacher(ANN)

    assert [section.id for section in found] == [TEACHING, OLDER]


@pytest.mark.unit
async def test_for_teacher_is_empty_for_someone_who_teaches_nothing(sections: Backend) -> None:
    assert await sections.sections.for_teacher(UNKNOWN) == []


@pytest.mark.unit
async def test_for_student_answers_most_recent_term_first(sections: Backend) -> None:
    found = await sections.sections.for_student(BEA)

    assert [section.id for section in found] == [TEACHING, OTHER_TEACHER_SECTION, OLDER]


@pytest.mark.unit
async def test_in_term_filters_by_term(sections: Backend) -> None:
    found = await sections.sections.in_term(LAST_TERM)

    assert [section.id for section in found] == [OLDER]


@pytest.mark.unit
async def test_subjects_enrolled_by_answers_the_subjects_not_the_sections(sections: Backend) -> None:
    # MATH twice, from two different sections, and the answer is a set: the rule it feeds
    # asks whether the subject is taken, not how often.
    assert await sections.sections.subjects_enrolled_by(BEA) == frozenset({MATH, PHYSICS})


@pytest.mark.unit
async def test_teaching_students_of_spans_every_section_taught(sections: Backend) -> None:
    assert await sections.sections.teaching_students_of(ANN) == frozenset({BEA})
    assert await sections.sections.teaching_students_of(UNKNOWN) == frozenset()


# --------------------------------------------------------------------------------------
# AcademicHistoryRepository
# --------------------------------------------------------------------------------------


@pytest.mark.unit
async def test_get_or_create_stores_the_history_it_creates(backend: Backend) -> None:
    # The promise that a naive adapter breaks: returning an unstored object satisfies every
    # other assertion here, then makes the caller's `save` raise NotFoundError. Recording a
    # student's first grade is exactly that sequence.
    created = await backend.histories.get_or_create(ANN)

    assert created.id == ANN
    await backend.histories.save(created)
    assert await backend.histories.get(ANN) is not None


@pytest.mark.unit
async def test_get_or_create_returns_the_existing_history(backend: Backend) -> None:
    first = await backend.histories.get_or_create(ANN)
    second = await backend.histories.get_or_create(ANN)

    assert first.id == second.id
    assert len(await backend.histories.list_all()) == 1


@pytest.mark.unit
async def test_for_students_omits_students_with_no_history(backend: Backend) -> None:
    await backend.histories.get_or_create(ANN)

    found = await backend.histories.for_students([ANN, UNKNOWN])

    assert [history.id for history in found] == [ANN]


# --------------------------------------------------------------------------------------
# GuardianshipRepository
# --------------------------------------------------------------------------------------


@pytest.mark.unit
async def test_a_stored_link_is_found_from_both_ends(backend: Backend) -> None:
    # Contents, not order: the port promises no ordering for these two.
    await backend.guardianships.add(Guardianship(id=LINK, guardian_id=GUARDIAN, ward_id=ANN))

    assert [link.id for link in await backend.guardianships.wards_of(GUARDIAN)] == [LINK]
    assert [link.id for link in await backend.guardianships.guardians_of(ANN)] == [LINK]


@pytest.mark.unit
async def test_an_unlinked_person_has_no_guardianships(backend: Backend) -> None:
    assert await backend.guardianships.wards_of(UNKNOWN) == []
    assert await backend.guardianships.guardians_of(UNKNOWN) == []


# --------------------------------------------------------------------------------------
# ConfigurationRepository
# --------------------------------------------------------------------------------------


@pytest.mark.unit
async def test_the_age_of_majority_has_a_documented_default(backend: Backend) -> None:
    # Never None: every guardianship check depends on this, and a system that cannot answer
    # it can answer nothing about access.
    assert await backend.configuration.age_of_majority() == DEFAULT_AGE_OF_MAJORITY


@pytest.mark.unit
async def test_the_age_of_majority_can_be_changed(backend: Backend) -> None:
    await backend.configuration.set_age_of_majority(AgeOfMajority(21))

    assert await backend.configuration.age_of_majority() == AgeOfMajority(21)
