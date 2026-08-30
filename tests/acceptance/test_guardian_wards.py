"""Step definitions for the guardian-wards feature (UC-28).

The first test in the `bdd` tier (ADR-0013), and the scenario chosen for it on purpose: the
transition it describes -- a guardianship ending on a birthday, with nothing written -- is the
one behaviour in this system that a table of stored records cannot show and a scenario can.

The steps drive the **use case**, not an HTTP endpoint. That is what an acceptance test is for
here: the spec's language, checked against the application's own vocabulary, with the adapters
underneath swappable. When the web adapter lands, the same feature file can be given a second
set of steps that go through it, and the two must agree.

Steps are synchronous and call ``asyncio.run`` because pytest-bdd invokes them synchronously;
an ``async def`` step would return a coroutine nobody awaits, and the assertion after it would
pass without the work having happened.
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
    MemoryPersonRepository,
    MemorySectionRepository,
    MemoryStore,
)
from academy.adapters.outbound.system import FixedClock
from academy.application.authorization import AccessGuard, RelationshipResolver
from academy.application.commands import ListMyWardsCommand
from academy.application.dtos import Actor, PersonDto
from academy.application.records import StudentRecords
from academy.domain.guardianship.guardianship import Guardianship
from academy.domain.people.age_of_majority import AgeOfMajority
from academy.domain.people.email import Email
from academy.domain.people.person import Person
from academy.domain.people.personal_data import PersonalData
from academy.domain.people.role import Role
from academy.domain.shared.ids import GuardianshipId, PersonId

pytestmark = pytest.mark.bdd

scenarios('guardian_wards.feature')

MARY = PersonId(UUID(int=1))
ADA = PersonId(UUID(int=2))
FIRST_LINK = GuardianshipId(UUID(int=10))
SECOND_LINK = GuardianshipId(UUID(int=11))


class World:
    """What the steps build up and then assert on.

    A plain object rather than a dict, so a typo in a step is an attribute error at the step
    that made it rather than a ``KeyError`` three steps later.
    """

    def __init__(self) -> None:
        """Start with an empty store and no date chosen."""
        self.store = MemoryStore()
        self.today = date(2026, 8, 30)
        self.wards: list[PersonDto] = []


def _run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    """Drive one async call to completion from a synchronous step."""
    return asyncio.run(coroutine)


def _person(person_id: PersonId, name: str, born: date, *roles: Role) -> Person:
    local = name.split()[0].lower()
    return Person(
        id=person_id,
        email=Email(f'{local}@academy.test'),
        personal=PersonalData(full_name=name, birth_date=born),
        roles=set(roles),
    )


def _records(world: World) -> StudentRecords:
    """Wire the use case to the in-memory adapters, with the clock the scenario chose."""
    store = world.store
    people = MemoryPersonRepository(store)
    guardianships = MemoryGuardianshipRepository(store)
    configuration = MemoryConfigurationRepository(store)
    clock = FixedClock(datetime(world.today.year, world.today.month, world.today.day, 9, 0, tzinfo=UTC))
    return StudentRecords(
        histories=MemoryAcademicHistoryRepository(store),
        people=people,
        guardianships=guardianships,
        configuration=configuration,
        clock=clock,
        guard=AccessGuard(
            RelationshipResolver(
                sections=MemorySectionRepository(store),
                guardianships=guardianships,
                people=people,
                configuration=configuration,
                clock=clock,
            )
        ),
    )


@pytest.fixture
def world() -> World:
    """The scenario's own store and clock."""
    return World()


@given(parsers.parse('the age of majority is {years:d}'))
def _(world: World, years: int) -> None:
    world.store.age_of_majority = AgeOfMajority(years)


@given('Mary is a guardian')
def _(world: World) -> None:
    world.store.people[MARY] = _person(MARY, 'Mary Lovelace', date(1982, 1, 1), Role.GUARDIAN)


@given(parsers.parse('Ada is a student born on {born}'))
def _(world: World, born: str) -> None:
    world.store.people[ADA] = _person(ADA, 'Ada Lovelace', date.fromisoformat(born), Role.STUDENT)


@given("Mary is registered as Ada's guardian")
def _(world: World) -> None:
    link = Guardianship(id=FIRST_LINK, guardian_id=MARY, ward_id=ADA)
    world.store.guardianships[link.id] = link


@given("Mary is registered as Ada's guardian a second time")
def _(world: World) -> None:
    link = Guardianship(id=SECOND_LINK, guardian_id=MARY, ward_id=ADA)
    world.store.guardianships[link.id] = link


@given(parsers.parse('today is {day}'))
def _(world: World, day: str) -> None:
    world.today = date.fromisoformat(day)


@when('Mary lists her wards')
def _(world: World) -> None:
    actor = Actor(person_id=MARY, roles=frozenset({Role.GUARDIAN}))
    world.wards = _run(_records(world).list_my_wards(ListMyWardsCommand(actor=actor)))


@then('the list is Ada')
def _(world: World) -> None:
    assert [ward.id for ward in world.wards] == [str(ADA)]


@then('the list is empty')
def _(world: World) -> None:
    assert world.wards == []


@then('the guardianship between Mary and Ada is still stored')
def _(world: World) -> None:
    # The point of the whole feature: access ended and the record did not change.
    link = world.store.guardianships.get(FIRST_LINK)
    assert link is not None
    assert link.guardian_id == MARY
    assert link.ward_id == ADA
