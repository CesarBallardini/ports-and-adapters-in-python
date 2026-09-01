"""Step definitions for the grade-recording feature (UC-21, UC-22), driven through the web.

The second BDD feature, and the first driven through an **inbound adapter** rather than through a
use case. ``test_guardian_wards.py`` says in its own docstring that when the web adapter landed
"the same feature file can be given a second set of steps that go through it, and the two must
agree". That exact promise is still owed for UC-28, whose routes do not exist yet; this pays the
same debt for the feature whose routes do.

What it buys is a claim no unit test makes: that the rules a reader can check in the spec survive
the trip through HTTP, a session cookie, a CSRF token, a Jinja2 template and htmx's swap rules.
If a rule had leaked into the adapter, this is where it would show, because the scenarios are
written in the domain's language and the steps speak only HTTP.

Steps are **synchronous and call ``asyncio.run``**, because pytest-bdd invokes them
synchronously; an ``async def`` step returns a coroutine nobody awaits, and the assertion after it
passes without the work having happened.

The in-memory backend, so each scenario gets a clean store with no database to migrate. What is
*stored* rather than *rendered* is asserted in ``tests/integration/test_web_persistence.py``,
which is the tier for that.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Coroutine
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from pytest_bdd import given, parsers, scenarios, then, when

from academy.adapters.inbound.web import create_app
from academy.config.container import Container
from academy.config.settings import Settings
from academy.domain.academics.course_section import CourseSection
from academy.domain.academics.term import Term
from academy.domain.grades.grade_entry import GradeEntry
from academy.domain.people.email import Email
from academy.domain.people.person import Person
from academy.domain.people.personal_data import PersonalData
from academy.domain.people.role import Role
from academy.domain.shared.ids import PersonId, SectionId, SubjectId

pytestmark = pytest.mark.bdd

scenarios('record_a_grade.feature')

TESS = PersonId(UUID(int=1))
IVAN = PersonId(UUID(int=2))
SAM = PersonId(UUID(int=10))
SOL = PersonId(UUID(int=11))
MATHEMATICS_SECTION = SectionId(UUID(int=100))
OTHER_SECTION = SectionId(UUID(int=101))
MATHEMATICS = SubjectId(UUID(int=200))
PHYSICS = SubjectId(UUID(int=201))

PEOPLE = {
    'Tess': (TESS, 'tess@academy.test', Role.TEACHER),
    'Ivan': (IVAN, 'ivan@academy.test', Role.TEACHER),
    'Sam': (SAM, 'sam@academy.test', Role.STUDENT),
    'Sol': (SOL, 'sol@academy.test', Role.STUDENT),
}

STUDENT_IDS = {'Sam': SAM, 'Sol': SOL}

SIGNING_KEY = 'a-key-for-the-acceptance-tier'  # noqa: S105 -- a literal in a test, not a credential


def run[T](work: Coroutine[Any, Any, T]) -> T:
    """Drive one coroutine to completion from a synchronous step.

    Named rather than inlined at fifteen call sites, and the reason is in the module docstring:
    pytest-bdd never awaits a step, so every ``await`` in this file has to happen inside one of
    these.
    """
    return asyncio.run(work)


@dataclass(slots=True)
class World:
    """What the scenario has built so far, and what the last request answered.

    A mutable bag on purpose: Gherkin steps share state and pretending otherwise means threading
    it through fixtures that add nothing. Confined to one scenario by the fixture's scope.
    """

    container: Container
    app: FastAPI | None = None
    cookies: dict[str, str] = field(default_factory=dict)
    response: httpx.Response | None = None

    def answered(self) -> httpx.Response:
        """The last response, or a failure that says a ``When`` step never ran."""
        assert self.response is not None, 'no request has been made in this scenario'
        return self.response


@pytest.fixture
def world() -> World:
    """A fresh in-memory deployment per scenario."""
    return World(container=Container(Settings(secret_key=SIGNING_KEY)))


def _person(name: str) -> Person:
    person_id, email, role = PEOPLE[name]
    return Person(
        id=person_id,
        email=Email(email),
        personal=PersonalData(full_name=name, birth_date=date(2000, 1, 1)),
        roles={role},
    )


async def _request(world: World, method: str, path: str, data: dict[str, str] | None = None) -> httpx.Response:
    """One request against the application, carrying whatever cookies the scenario has."""
    if world.app is None:
        world.app = create_app(world.container)

    transport = httpx.ASGITransport(app=world.app)
    async with httpx.AsyncClient(transport=transport, base_url='http://academy.test', cookies=world.cookies) as client:
        response = await client.request(method, path, data=data)
        # From *this response's* Set-Cookie headers, not from the whole jar. Reading the jar
        # raises `CookieConflict` once the same name has been set under two paths, which is what
        # a fresh client per request produces -- and a browser keeps one jar, not one per request.
        world.cookies.update(dict(response.cookies.items()))
        return response


def _csrf(body: str) -> str:
    """The token a rendered page put in its form."""
    match = re.search(r'name="csrf_token" value="([^"]+)"', body)
    assert match is not None, 'the page rendered no CSRF token'
    return match.group(1)


def _record(world: World, teacher: str, grade: str, student: str) -> httpx.Response:
    """Submit the grade form the way the browser does, token and all."""
    del teacher  # Whoever is signed in submits; the scenario has already said who that is.
    sheet = run(_request(world, 'GET', f'/sections/{MATHEMATICS_SECTION}/grades'))
    token = _csrf(sheet.text) if sheet.status_code == 200 else 'no-token-available'
    return run(
        _request(
            world,
            'POST',
            f'/sections/{MATHEMATICS_SECTION}/grades',
            data={'student_id': str(STUDENT_IDS[student]), 'grade': grade, 'csrf_token': token},
        )
    )


# --- Given -----------------------------------------------------------------------------------


@given(parsers.parse('{teacher} teaches Mathematics'))
def _(world: World, teacher: str) -> None:
    async def build() -> None:
        async with world.container.request_scope() as scope:
            unit_of_work = scope.unit_of_work()
            async with unit_of_work:
                for name in PEOPLE:
                    await scope.people.add(_person(name))
                await scope.sections.add(
                    CourseSection(
                        id=MATHEMATICS_SECTION,
                        subject_id=MATHEMATICS,
                        term=Term(2026, 1),
                        teacher_id=PEOPLE[teacher][0],
                    )
                )
                await unit_of_work.commit()

    run(build())


@given(parsers.parse('{student} is enrolled in that section'))
def _(world: World, student: str) -> None:
    async def enroll() -> None:
        async with world.container.request_scope() as scope:
            unit_of_work = scope.unit_of_work()
            async with unit_of_work:
                section = await scope.sections.get(MATHEMATICS_SECTION)
                assert section is not None
                section.enroll(STUDENT_IDS[student])
                await scope.sections.save(section)
                await unit_of_work.commit()

    run(enroll())


@given(parsers.parse('{teacher} teaches a different section'))
def _(world: World, teacher: str) -> None:
    async def build() -> None:
        async with world.container.request_scope() as scope:
            unit_of_work = scope.unit_of_work()
            async with unit_of_work:
                await scope.sections.add(
                    CourseSection(
                        id=OTHER_SECTION,
                        subject_id=PHYSICS,
                        term=Term(2026, 1),
                        teacher_id=PEOPLE[teacher][0],
                    )
                )
                await unit_of_work.commit()

    run(build())


@given(parsers.parse('{name} is signed in'))
def _(world: World, name: str) -> None:
    """Sign in through the form, so a real cookie is issued and carried."""
    world.cookies.clear()
    form = run(_request(world, 'GET', '/sign-in'))
    signed = run(
        _request(
            world,
            'POST',
            '/sign-in',
            data={'email': PEOPLE[name][1], 'password': 'not checked', 'csrf_token': _csrf(form.text)},
        )
    )
    assert signed.status_code == 303, signed.text


@given('the browser has no session')
def _(world: World) -> None:
    """Worded so it cannot also match ``{name} is signed in``.

    "nobody is signed in" reads better and parses worse: ``parsers.parse`` would bind
    ``name='nobody'`` and look it up in the cast list. A Gherkin phrase has to be unambiguous
    against every other phrase in the file, not just readable on its own.
    """
    world.cookies.clear()


@given(parsers.parse('{teacher} has recorded {grade:d} for {student}'))
def _(world: World, teacher: str, grade: int, student: str) -> None:
    response = _record(world, teacher, str(grade), student)
    assert response.status_code == 200, response.text


# --- When ------------------------------------------------------------------------------------


@when(parsers.parse('{teacher} records {grade:d} for {student}'))
def _(world: World, teacher: str, grade: int, student: str) -> None:
    world.response = _record(world, teacher, str(grade), student)


@when(parsers.parse('{teacher} opens the grade sheet'))
def _(world: World, teacher: str) -> None:
    del teacher
    world.response = run(_request(world, 'GET', f'/sections/{MATHEMATICS_SECTION}/grades'))


# --- Then ------------------------------------------------------------------------------------


def _sheet_row(world: World, student: str) -> str:
    """The student's row on a freshly loaded sheet, as text.

    Read back through the page rather than out of the store, because the question a scenario asks
    is what the teacher is *shown* -- and a standing computed correctly and rendered wrongly is
    still a teacher being told the wrong thing.
    """
    sheet = run(_request(world, 'GET', f'/sections/{MATHEMATICS_SECTION}/grades'))
    assert sheet.status_code == 200, sheet.text

    match = re.search(rf'<tr id="student-{STUDENT_IDS[student]}".*?</tr>', sheet.text, re.DOTALL)
    assert match is not None, f'{student} is not on the sheet'
    return match.group(0)


@then(parsers.parse("{student}'s standing is {grade:d}"))
def _(world: World, student: str, grade: int) -> None:
    assert f'>{grade}<' in _sheet_row(world, student)


@then(parsers.parse('{student} has passed'))
def _(world: World, student: str) -> None:
    row = _sheet_row(world, student)
    assert 'not passed' not in row
    assert 'passed' in row


@then(parsers.parse('{student} has {count:d} attempts'))
def _(world: World, student: str, count: int) -> None:
    row = _sheet_row(world, student)
    # The attempts cell is the fourth, and the only bare number left once the grade and the
    # standing have been matched; asserting on the whole row keeps this from depending on
    # column order more than it must.
    assert f'<td>{count}</td>' in row


@then(parsers.parse('{student} has no grade'))
def _(world: World, student: str) -> None:
    row = _sheet_row(world, student)
    assert '&mdash;' in row
    assert 'not passed' in row


@then('the request is refused as invalid')
def _(world: World) -> None:
    assert world.answered().status_code == 422, world.answered().text


@then('the request is refused as forbidden')
def _(world: World) -> None:
    assert world.answered().status_code == 403, world.answered().text


@then(parsers.parse('nothing was recorded for {student}'))
def _(world: World, student: str) -> None:
    """Absence of a side effect, checked in the store rather than on the page.

    Every other ``Then`` in this file reads the sheet, because what a teacher is *shown* is the
    question a scenario asks. This one cannot: the actor is anonymous, so the sheet redirects to
    the sign-in form and there is no row to read. Asserting through the store is the honest
    alternative -- signing somebody in to look would change the very thing under test.
    """

    async def transcript() -> list[GradeEntry]:
        async with world.container.request_scope() as scope:
            history = await scope.histories.get(STUDENT_IDS[student])
            return list(history.entries) if history is not None else []

    assert run(transcript()) == []


@then('the request is refused')
def _(world: World) -> None:
    """Any refusal will do: the scenario is about nothing being recorded, not about which code."""
    assert world.answered().status_code >= 400, world.answered().text
