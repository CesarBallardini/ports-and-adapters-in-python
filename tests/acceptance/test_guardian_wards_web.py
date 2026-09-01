"""The guardian-wards feature (UC-28) again, driven through the web adapter.

This is the debt ``test_guardian_wards.py`` wrote down for itself:

    "When the web adapter lands, the same feature file can be given a second set of steps that go
    through it, and the two must agree."

Same ``guardian_wards.feature``, same scenarios, same words. One set of steps calls the use case;
this set signs a guardian in and reads a page. If the two ever disagree, a rule has moved into an
adapter -- which is the single failure this repository is built to make visible, and until now
there was nothing that could see it.

What it takes to say that honestly is worth noticing: **nothing in the feature file changed**. The
scenarios talk about guardians, wards and birthdays, and neither set of steps needed a word about
HTTP, sessions or templates added to them. A spec that had to be reworded to be driven through a
second adapter would be a spec that had absorbed the first one.

The clock is the interesting fixture. "Given today is 2029-05-01" is a scenario controlling time,
and the web adapter has no way to be told what day it is -- correctly, since a deployment must not
be able to. It enters where every other deployment choice does, through the composition root:
``Container(settings, clock=FixedClock(...))``. The application above it cannot tell.

Steps are synchronous and call ``asyncio.run``: pytest-bdd never awaits a step.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Coroutine
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from pytest_bdd import given, parsers, scenarios, then, when

from academy.adapters.inbound.web import create_app
from academy.adapters.outbound.system import FixedClock
from academy.config.container import Container
from academy.config.settings import Settings
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

MARY_EMAIL = 'mary@academy.test'

SIGNING_KEY = 'a-key-for-the-acceptance-tier'  # noqa: S105 -- a literal in a test, not a credential


def run[T](work: Coroutine[Any, Any, T]) -> T:
    """Drive one coroutine to completion from a synchronous step."""
    return asyncio.run(work)


@dataclass(slots=True)
class World:
    """What the scenario has built, and what the last page said.

    The container is rebuilt whenever the clock moves, because the clock is fixed at construction
    -- which is the honest shape: a deployment picks its clock once, at startup, and this is a
    scenario picking a different startup.
    """

    today: date = date(2026, 8, 30)
    age_of_majority: int = 18
    links: list[GuardianshipId] = field(default_factory=lambda: [FIRST_LINK])
    born: date = date(2011, 5, 1)
    container: Container | None = None
    app: FastAPI | None = None
    cookies: dict[str, str] = field(default_factory=dict)
    response: httpx.Response | None = None

    def answered(self) -> httpx.Response:
        """The last response, or a failure saying no ``When`` step ran."""
        assert self.response is not None, 'no page has been requested in this scenario'
        return self.response


@pytest.fixture
def world() -> World:
    """One scenario's worth of state."""
    return World()


async def _build(world: World) -> None:
    """Wire a deployment for the day the scenario says it is, and put the records in it.

    Rebuilt from scratch each time rather than mutated, because the clock is a constructor
    argument. Cheap: the store is a dictionary.
    """
    clock = FixedClock(datetime(world.today.year, world.today.month, world.today.day, 9, 0, tzinfo=UTC))
    container = Container(Settings(secret_key=SIGNING_KEY), clock=clock)

    async with container.request_scope() as scope:
        unit_of_work = scope.unit_of_work()
        async with unit_of_work:
            await scope.people.add(
                Person(
                    id=MARY,
                    email=Email(MARY_EMAIL),
                    personal=PersonalData(full_name='Mary', birth_date=date(1980, 1, 1)),
                    roles={Role.GUARDIAN},
                )
            )
            await scope.people.add(
                Person(
                    id=ADA,
                    email=Email('ada@academy.test'),
                    personal=PersonalData(full_name='Ada', birth_date=world.born),
                    roles={Role.STUDENT},
                )
            )
            for link in world.links:
                await scope.guardianships.add(Guardianship(id=link, guardian_id=MARY, ward_id=ADA))
            await scope.configuration.set_age_of_majority(AgeOfMajority(world.age_of_majority))
            await unit_of_work.commit()

    world.container = container
    world.app = create_app(container)
    world.cookies.clear()


async def _request(world: World, method: str, path: str, data: dict[str, str] | None = None) -> httpx.Response:
    """One request, carrying whatever cookies the scenario has."""
    assert world.app is not None, 'the deployment has not been built yet'
    transport = httpx.ASGITransport(app=world.app)
    async with httpx.AsyncClient(transport=transport, base_url='http://academy.test', cookies=world.cookies) as client:
        response = await client.request(method, path, data=data)
        world.cookies.update(dict(response.cookies.items()))
        return response


def _csrf(body: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', body)
    assert match is not None, 'the page rendered no CSRF token'
    return match.group(1)


def _sign_in(world: World) -> None:
    """Sign Mary in through the real form."""
    form = run(_request(world, 'GET', '/sign-in'))
    signed = run(
        _request(
            world,
            'POST',
            '/sign-in',
            data={'email': MARY_EMAIL, 'password': 'not checked', 'csrf_token': _csrf(form.text)},
        )
    )
    assert signed.status_code == 303, signed.text


# --- Given -----------------------------------------------------------------------------------


@given(parsers.parse('the age of majority is {years:d}'))
def _(world: World, years: int) -> None:
    world.age_of_majority = years


@given('Mary is a guardian')
def _(world: World) -> None:
    """Recorded in the world; the deployment is built once the scenario has said what day it is."""


@given(parsers.parse('Ada is a student born on {born}'))
def _(world: World, born: str) -> None:
    world.born = date.fromisoformat(born)


@given("Mary is registered as Ada's guardian")
def _(world: World) -> None:
    world.links = [FIRST_LINK]


@given("Mary is registered as Ada's guardian a second time")
def _(world: World) -> None:
    """Two stored links, one ward. The list must still name Ada once."""
    world.links = [FIRST_LINK, SECOND_LINK]


@given(parsers.parse('today is {day}'))
def _(world: World, day: str) -> None:
    world.today = date.fromisoformat(day)


# --- When ------------------------------------------------------------------------------------


@when('Mary lists her wards')
def _(world: World) -> None:
    """Build the deployment for the day just named, sign in, and open the page."""
    run(_build(world))
    _sign_in(world)
    world.response = run(_request(world, 'GET', '/wards'))


# --- Then ------------------------------------------------------------------------------------


@then('the list is Ada')
def _(world: World) -> None:
    """A literal step, not ``the list is {name}``.

    A parser there would also match "the list is empty" and bind ``name='empty'`` -- the same trap
    that ``record_a_grade``'s sign-in step fell into. ``test_guardian_wards.py`` spells both
    literally for this reason, and copying that is cheaper than being clever.
    """
    page = world.answered()

    assert page.status_code == 200, page.text
    # Named once however many guardianship records connect the pair.
    assert page.text.count('<td>Ada</td>') == 1, page.text


@then('the list is empty')
def _(world: World) -> None:
    page = world.answered()

    assert page.status_code == 200, page.text
    assert 'Nobody is currently in your care.' in page.text
    assert '<td>Ada</td>' not in page.text


@then('the guardianship between Mary and Ada is still stored')
def _(world: World) -> None:
    """The rule this whole feature exists for: the list shortened and **nothing was written**.

    Asserted against storage rather than the page, because the claim is precisely that the page
    stopped showing something the database still holds.
    """
    assert world.container is not None

    async def stored() -> list[Guardianship]:
        async with world.container.request_scope() as scope:  # type: ignore[union-attr]
            return await scope.guardianships.wards_of(MARY)

    assert [link.ward_id for link in run(stored())] == [ADA] * len(world.links)
