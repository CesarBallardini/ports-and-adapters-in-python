"""One suite, both :class:`~academy.application.ports.outbound.identity.ActorIdentity` adapters.

The port's docstring is the specification and this is the specification executed (ADR-0014): what
a lookup returns when the person is absent, that ``None`` and "an actor with no roles" are
different answers, and that roles are read *fresh* on every call rather than fixed when the
session began.

The two adapters could hardly be less alike -- one reads a repository, the other reads a mapping
configured at startup -- which is exactly what makes running them through the same assertions
worth doing. A property that survives both is a property of the port; one that does not was an
accident of an implementation.

Roles being fresh is the assertion that pays for the suite. It is the difference between a
teacher losing write access when they are removed from the staff and losing it whenever their
cookie happens to expire, and it is invisible to any test that resolves an actor only once.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from typing import Protocol
from uuid import UUID, uuid4

import pytest

from academy.adapters.outbound.identity import RepositoryActorIdentity, StaticActorIdentity
from academy.adapters.outbound.persistence.memory import MemoryPersonRepository, MemoryStore
from academy.application.dtos import Actor
from academy.application.ports.outbound.identity import ActorIdentity
from academy.domain.people.email import Email
from academy.domain.people.person import Person
from academy.domain.people.personal_data import PersonalData
from academy.domain.people.role import Role
from academy.domain.shared.ids import PersonId

KNOWN = PersonId(UUID(int=1))
STRANGER = PersonId(UUID(int=99))


class Population(Protocol):
    """The one thing the two adapters need said differently: who exists, holding what.

    Everything below this line is identical for both. Without it the suite would either be two
    suites or a single one branching on which adapter it got, and a contract test that knows
    which implementation it is running is not a contract test.
    """

    @property
    def identity(self) -> ActorIdentity:
        """The adapter under test, reading this population."""
        ...

    async def enroll(self, person_id: PersonId, *roles: Role) -> None:
        """Make this person exist, holding these roles."""
        ...

    async def set_roles(self, person_id: PersonId, *roles: Role) -> None:
        """Change what an existing person holds, as an administrator would."""
        ...


class _RepositoryPopulation:
    """People in a repository, resolved by reading them."""

    def __init__(self) -> None:
        self._people = MemoryPersonRepository(MemoryStore())
        self._identity = RepositoryActorIdentity(self._people)

    @property
    def identity(self) -> ActorIdentity:
        return self._identity

    async def enroll(self, person_id: PersonId, *roles: Role) -> None:
        await self._people.add(
            Person(
                id=person_id,
                # Unique per person: the repository refuses a duplicate address, and this suite
                # cares about ids rather than about who anyone is.
                email=Email(f'{uuid4().hex}@academy.test'),
                personal=PersonalData(full_name='A Person', birth_date=date(1990, 1, 1)),
                roles=set(roles),
            )
        )

    async def set_roles(self, person_id: PersonId, *roles: Role) -> None:
        person = await self._people.get(person_id)
        assert person is not None, 'the test asked to change the roles of someone it never added'
        for role in person.roles:
            person.revoke_role(role)
        for role in roles:
            person.grant_role(role)
        await self._people.save(person)


class _StaticPopulation:
    """Actors configured at startup, resolved without touching storage."""

    def __init__(self) -> None:
        self._actors: dict[PersonId, Actor] = {}
        # The adapter holds the mapping rather than a copy of it, which is what lets a role
        # change here be visible to the next `resolve`. That is not a convenience for the test:
        # it is the port's freshness requirement, and an adapter that snapshotted its argument
        # would fail the same assertion the repository-backed one has to pass.
        self._identity = StaticActorIdentity(self._actors)

    @property
    def identity(self) -> ActorIdentity:
        return self._identity

    async def enroll(self, person_id: PersonId, *roles: Role) -> None:
        self._actors[person_id] = Actor(person_id=person_id, roles=frozenset(roles))

    async def set_roles(self, person_id: PersonId, *roles: Role) -> None:
        self._actors[person_id] = Actor(person_id=person_id, roles=frozenset(roles))


@dataclass(frozen=True, slots=True)
class Identity:
    """One adapter, with the way to populate it."""

    name: str
    build: type[_RepositoryPopulation] | type[_StaticPopulation]


IDENTITIES = (
    Identity('repository', _RepositoryPopulation),
    Identity('static', _StaticPopulation),
)


@pytest.fixture(params=IDENTITIES, ids=lambda identity: identity.name)
def population(request: pytest.FixtureRequest) -> Iterator[Population]:
    """Every test in this module runs once per adapter."""
    identity: Identity = request.param
    yield identity.build()


pytestmark = pytest.mark.unit


async def test_a_known_person_resolves_to_themselves(population: Population) -> None:
    await population.enroll(KNOWN, Role.TEACHER)

    actor = await population.identity.resolve(KNOWN)

    assert actor is not None
    assert actor.person_id == KNOWN


async def test_the_actor_carries_the_roles_the_person_holds(population: Population) -> None:
    await population.enroll(KNOWN, Role.TEACHER, Role.ADMINISTRATIVE_EMPLOYEE)

    actor = await population.identity.resolve(KNOWN)

    assert actor is not None
    assert actor.roles == frozenset({Role.TEACHER, Role.ADMINISTRATIVE_EMPLOYEE})
    assert actor.is_administrator


async def test_an_unknown_person_resolves_to_none(population: Population) -> None:
    """``None``, and not an actor with no roles.

    A valid session naming someone who no longer exists is *unauthenticated* and should not
    reach a page at all. An actor with an empty role set is a real person who is allowed to do
    very little, which is a different thing and gets a different answer -- 401 against 403.
    """
    assert await population.identity.resolve(STRANGER) is None


async def test_a_person_with_no_roles_still_resolves(population: Population) -> None:
    """The other side of the same line: existing and holding nothing is not the same as absent."""
    await population.enroll(KNOWN)

    actor = await population.identity.resolve(KNOWN)

    assert actor is not None
    assert actor.roles == frozenset()


async def test_roles_are_read_fresh_on_every_call(population: Population) -> None:
    """The assertion the suite exists for: a role revoked at 10:00 is gone at 10:00.

    Both adapters are asked twice with the same id across a change. An implementation that
    cached the first answer -- in the session, in an instance attribute, behind an
    ``lru_cache`` -- passes every other test here and fails this one.
    """
    await population.enroll(KNOWN, Role.TEACHER)
    before = await population.identity.resolve(KNOWN)

    await population.set_roles(KNOWN)
    after = await population.identity.resolve(KNOWN)

    assert before is not None
    assert after is not None
    assert before.roles == frozenset({Role.TEACHER})
    assert after.roles == frozenset()


async def test_a_granted_role_appears_without_anything_being_re_authenticated(population: Population) -> None:
    """The direction that matters for an administrator promoting someone mid-session."""
    await population.enroll(KNOWN)

    await population.set_roles(KNOWN, Role.ADMINISTRATIVE_EMPLOYEE)
    actor = await population.identity.resolve(KNOWN)

    assert actor is not None
    assert actor.is_administrator


async def test_resolving_twice_gives_equal_actors(population: Population) -> None:
    """No identity map, no accumulated state: two reads of an unchanged person agree.

    ``Actor`` is a frozen dataclass, so this is equality and deliberately not identity -- an
    adapter is free to build a new one each time and the repository-backed one does.
    """
    await population.enroll(KNOWN, Role.STUDENT)

    assert await population.identity.resolve(KNOWN) == await population.identity.resolve(KNOWN)


async def test_the_adapters_satisfy_the_port(population: Population) -> None:
    """``ActorIdentity`` is ``runtime_checkable``, so this is a real check and not a comment."""
    assert isinstance(population.identity, ActorIdentity)
