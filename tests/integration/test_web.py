"""The assembled application, driven over HTTP, against a real database.

In process, through ``httpx.ASGITransport``: no socket, no server, and every layer from the route
down to the store is the real one. What this tier is for is the joins -- that the routers are
actually included, that a cookie set by one response is accepted by the next, that a failure
raised in a use case comes back with the status ADR-0012's table assigns and in the shape the
router's surface implies.

The test the whole file is built around is
:func:`test_the_browser_and_the_api_reach_the_same_outcome`. ADR-0011 claims the htmx UI and the
JSON API "call identical objects"; a claim like that is worth nothing unasserted, and the way to
assert it is to do the same thing twice through different doors and demand the same answer.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from academy.adapters.inbound.web import create_app
from academy.adapters.inbound.web.security import CSRF_COOKIE, SESSION_COOKIE, Credentials
from academy.config.container import Container
from academy.config.settings import Settings
from academy.domain.academics.course_section import CourseSection
from academy.domain.academics.term import Term
from academy.domain.people.email import Email
from academy.domain.people.person import Person
from academy.domain.people.personal_data import PersonalData
from academy.domain.people.role import Role
from academy.domain.shared.ids import PersonId, SectionId, SubjectId

pytestmark = pytest.mark.integration

TEACHER = PersonId(UUID(int=1))
STUDENT = PersonId(UUID(int=2))
OTHER_STUDENT = PersonId(UUID(int=3))
INTRUDER = PersonId(UUID(int=4))
ADMIN = PersonId(UUID(int=5))
GHOST = PersonId(UUID(int=99))
SECTION = SectionId(UUID(int=10))
OTHER_SECTION = SectionId(UUID(int=11))
SUBJECT = SubjectId(UUID(int=20))

TEACHER_EMAIL = 'tess@academy.test'
STUDENT_EMAIL = 'sam@academy.test'
ADMIN_EMAIL = 'adele@academy.test'

# `json.loads` genuinely returns `Any`, and pretending otherwise with a cast would be a promise
# nothing keeps. Named so the looseness is visible and confined to response bodies.
type JsonBody = dict[str, Any]


@pytest.fixture
async def container() -> AsyncIterator[Container]:
    """A container over the in-memory backend, seeded with a section and a roster."""
    built = Container(Settings())
    async with built.request_scope() as scope:
        unit_of_work = scope.unit_of_work()
        async with unit_of_work:
            await scope.people.add(_person(TEACHER, TEACHER_EMAIL, 'Tess Teacher', Role.TEACHER))
            await scope.people.add(_person(STUDENT, STUDENT_EMAIL, 'Sam Student', Role.STUDENT))
            await scope.people.add(_person(OTHER_STUDENT, 'sol@academy.test', 'Sol Student', Role.STUDENT))
            await scope.people.add(_person(INTRUDER, 'ivan@academy.test', 'Ivan Intruder', Role.TEACHER))
            await scope.people.add(_person(ADMIN, ADMIN_EMAIL, 'Adele Admin', Role.ADMINISTRATIVE_EMPLOYEE))

            section = CourseSection(id=SECTION, subject_id=SUBJECT, term=Term(2026, 1), teacher_id=TEACHER)
            section.enroll(STUDENT)
            await scope.sections.add(section)

            other = CourseSection(id=OTHER_SECTION, subject_id=SUBJECT, term=Term(2026, 1), teacher_id=INTRUDER)
            await scope.sections.add(other)

            await unit_of_work.commit()

    yield built
    await built.aclose()


def _person(person_id: PersonId, email: str, name: str, *roles: Role) -> Person:
    return Person(
        id=person_id,
        email=Email(email),
        personal=PersonalData(full_name=name, birth_date=date(1990, 1, 1)),
        roles=set(roles),
    )


@pytest.fixture
async def client(container: Container) -> AsyncIterator[httpx.AsyncClient]:
    """A client over the real application, keeping cookies between requests like a browser."""
    transport = httpx.ASGITransport(app=create_app(container))
    async with httpx.AsyncClient(transport=transport, base_url='http://academy.test') as opened:
        yield opened


@pytest.fixture
def bearer(container: Container) -> dict[str, str]:
    """An ``Authorization`` header for the teacher, minted the way sign-in would."""
    return {'Authorization': f'Bearer {Credentials(container.secret_key).issue_token(TEACHER)}'}


def _csrf(body: str) -> str:
    """The token a rendered page put in its form."""
    match = re.search(r'name="csrf_token" value="([^"]+)"', body)
    assert match is not None, 'the page rendered no CSRF token'
    return match.group(1)


async def _sign_in(client: httpx.AsyncClient, email: str) -> None:
    """Sign in as this person, the way a browser would: fetch the form, post it back."""
    form = await client.get('/sign-in')
    signed = await client.post(
        '/sign-in', data={'email': email, 'password': 'ignored by the placeholder', 'csrf_token': _csrf(form.text)}
    )
    assert signed.status_code == 303, signed.text


# ---------------------------------------------------------------------------------------------
# The application is assembled at all
# ---------------------------------------------------------------------------------------------


async def test_the_health_check_answers_without_a_database(client: httpx.AsyncClient) -> None:
    response = await client.get('/healthz')

    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}


async def test_every_router_is_actually_included(client: httpx.AsyncClient) -> None:
    """What ``test_web_dependencies.py`` cannot say, because it reads the routers directly.

    A router written, tested and never passed to ``include_router`` would satisfy every unit test
    in this repository and serve nothing at all.
    """
    await _sign_in(client, TEACHER_EMAIL)

    assert (await client.get('/sign-in')).status_code == 200
    assert (await client.get(f'/sections/{SECTION}/grades')).status_code == 200


# ---------------------------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------------------------


async def test_a_signed_out_browser_is_sent_to_sign_in(client: httpx.AsyncClient) -> None:
    """A redirect, not a 403: nobody has been refused, nobody has been identified."""
    response = await client.get(f'/sections/{SECTION}/grades')

    assert response.status_code == 303
    assert response.headers['location'] == '/sign-in'


async def test_a_signed_out_api_client_gets_json(client: httpx.AsyncClient) -> None:
    """The same condition, rendered in the vocabulary the other router speaks (ADR-0019)."""
    response = await client.get(f'/api/sections/{SECTION}/grades')

    assert response.status_code == 401
    assert response.json() == {'error': 'not_authenticated'}


async def test_an_htmx_request_without_a_session_is_told_to_redirect(client: httpx.AsyncClient) -> None:
    """The expired-session case: a page left open, and htmx asks for something an hour later.

    A redirect *body* swapped into the page would put a whole sign-in form inside a table cell.
    ``HX-Redirect`` makes htmx do a full page load instead, which is the only sensible answer once
    the session is gone -- no fragment of the current page is still meaningful.

    A **GET**, deliberately. On an unsafe method the CSRF check runs first and answers 403 before
    anybody is identified, so a POST would exercise the wrong branch and pass for the wrong
    reason.
    """
    response = await client.get(f'/sections/{SECTION}/grades', headers={'HX-Request': 'true'})

    assert response.status_code == 401
    assert response.headers['HX-Redirect'] == '/sign-in'


async def test_an_unsafe_request_is_refused_for_csrf_before_anyone_is_identified(
    client: httpx.AsyncClient,
) -> None:
    """The ordering, asserted rather than left to be discovered.

    CSRF is a router-level dependency and authentication is a route-level one, so the token is
    checked first. That is the right way round -- a forged request should be refused without the
    application doing any work on its behalf -- but it does mean an unauthenticated POST reports
    the CSRF failure and not the missing session.
    """
    response = await client.post(
        f'/sections/{SECTION}/grades',
        data={'student_id': str(STUDENT), 'grade': '8'},
        headers={'HX-Request': 'true'},
    )

    assert response.status_code == 403
    assert 'not from this site' in response.text


async def test_signing_in_sets_an_http_only_session(client: httpx.AsyncClient) -> None:
    """HTTP-only, so an XSS cannot read it; that is the reason the API uses a header instead."""
    form = await client.get('/sign-in')
    response = await client.post(
        '/sign-in', data={'email': TEACHER_EMAIL, 'password': 'x', 'csrf_token': _csrf(form.text)}
    )

    cookie = response.headers['set-cookie']
    assert SESSION_COOKIE in cookie
    assert 'HttpOnly' in cookie
    assert 'SameSite=lax' in cookie.replace('Samesite', 'SameSite')


async def test_an_unknown_email_is_refused_without_saying_which_half_was_wrong(
    client: httpx.AsyncClient,
) -> None:
    """A form that distinguished the two would be a way of finding out who has an account."""
    form = await client.get('/sign-in')
    response = await client.post(
        '/sign-in', data={'email': 'nobody@academy.test', 'password': 'x', 'csrf_token': _csrf(form.text)}
    )

    assert response.status_code == 401
    assert 'not accepted' in response.text
    assert 'nobody@academy.test' not in response.text


async def test_signing_out_discards_the_session(client: httpx.AsyncClient) -> None:
    await _sign_in(client, TEACHER_EMAIL)
    page = await client.get(f'/sections/{SECTION}/grades')

    await client.post('/sign-out', data={'csrf_token': _csrf(page.text)})

    assert (await client.get(f'/sections/{SECTION}/grades')).status_code == 303


async def test_a_session_naming_a_deleted_person_is_unauthenticated(
    client: httpx.AsyncClient, container: Container
) -> None:
    """``ActorIdentity`` promises ``None`` here rather than an actor with no roles.

    The difference is the whole reason this is 401 and not 403: a person who no longer exists has
    not been refused permission, they have failed to be identified.
    """
    ghost = Credentials(container.secret_key).issue_session(GHOST)
    client.cookies.set(SESSION_COOKIE, ghost)

    assert (await client.get(f'/sections/{SECTION}/grades')).status_code == 303


async def test_roles_are_read_fresh_rather_than_from_the_session(
    client: httpx.AsyncClient, container: Container
) -> None:
    """An administrator demoted mid-session loses access on their next request, not their next
    sign-in.

    The contract suite asserts the *adapter* reads roles fresh; this asserts the whole chain does
    -- that nothing between the cookie and the use case cached them on the way past. The session
    is never touched: the same cookie is sent before and after, and only the answer changes.

    The administrator is the right actor for this and the teacher is not. Reading a section's
    sheet is granted to a teacher by the *relation* -- the section names them as its teacher --
    which no change to their roles affects (ADR-0016). The administrative grant is the role-based
    one, so it is the one whose freshness is worth asserting.
    """
    await _sign_in(client, ADMIN_EMAIL)
    assert (await client.get(f'/sections/{SECTION}/grades')).status_code == 200

    async with container.request_scope() as scope:
        unit_of_work = scope.unit_of_work()
        async with unit_of_work:
            administrator = await scope.people.get(ADMIN)
            assert administrator is not None
            administrator.revoke_role(Role.ADMINISTRATIVE_EMPLOYEE)
            await scope.people.save(administrator)
            await unit_of_work.commit()

    assert (await client.get(f'/sections/{SECTION}/grades')).status_code == 403


async def test_a_teachers_access_comes_from_the_relation_and_not_from_the_role(
    client: httpx.AsyncClient, container: Container
) -> None:
    """The other half of the same point, and the reason the test above uses an administrator.

    A teacher stripped of ``Role.TEACHER`` still teaches this section -- the section says so --
    and ADR-0016 grants on the relation. Asserting the opposite would be asserting a bug.
    """
    await _sign_in(client, TEACHER_EMAIL)

    async with container.request_scope() as scope:
        unit_of_work = scope.unit_of_work()
        async with unit_of_work:
            teacher = await scope.people.get(TEACHER)
            assert teacher is not None
            teacher.revoke_role(Role.TEACHER)
            await scope.people.save(teacher)
            await unit_of_work.commit()

    assert (await client.get(f'/sections/{SECTION}/grades')).status_code == 200


# ---------------------------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------------------------


async def test_an_unsafe_request_without_a_token_is_refused(client: httpx.AsyncClient) -> None:
    await _sign_in(client, TEACHER_EMAIL)
    await client.get(f'/sections/{SECTION}/grades')

    response = await client.post(f'/sections/{SECTION}/grades', data={'student_id': str(STUDENT), 'grade': '8'})

    assert response.status_code == 403


async def test_an_unsafe_request_with_the_wrong_token_is_refused(client: httpx.AsyncClient) -> None:
    await _sign_in(client, TEACHER_EMAIL)
    await client.get(f'/sections/{SECTION}/grades')

    response = await client.post(
        f'/sections/{SECTION}/grades',
        data={'student_id': str(STUDENT), 'grade': '8', 'csrf_token': uuid4().hex},
    )

    assert response.status_code == 403


async def test_the_token_may_travel_in_a_header(client: httpx.AsyncClient) -> None:
    """htmx sends it that way through ``hx-headers``, so both routes must accept it."""
    await _sign_in(client, TEACHER_EMAIL)
    page = await client.get(f'/sections/{SECTION}/grades')

    response = await client.post(
        f'/sections/{SECTION}/grades',
        data={'student_id': str(STUDENT), 'grade': '8'},
        headers={'X-CSRF-Token': _csrf(page.text), 'HX-Request': 'true'},
    )

    assert response.status_code == 200


async def test_the_json_api_needs_no_csrf_token(client: httpx.AsyncClient, bearer: dict[str, str]) -> None:
    """Nothing attaches a bearer header on a caller's behalf, so there is nothing to forge."""
    response = await client.post(
        f'/api/sections/{SECTION}/grades', json={'student_id': str(STUDENT), 'grade': 8}, headers=bearer
    )

    assert response.status_code == 200


async def test_a_safe_request_needs_no_token(client: httpx.AsyncClient) -> None:
    """A GET changes nothing and so cannot be forged into changing anything."""
    await _sign_in(client, TEACHER_EMAIL)

    assert (await client.get(f'/sections/{SECTION}/grades')).status_code == 200


# ---------------------------------------------------------------------------------------------
# The grade sheet
# ---------------------------------------------------------------------------------------------


async def test_the_grade_sheet_lists_the_roster(client: httpx.AsyncClient) -> None:
    await _sign_in(client, TEACHER_EMAIL)

    response = await client.get(f'/sections/{SECTION}/grades')

    assert response.status_code == 200
    assert 'Sam Student' in response.text
    assert 'Sol Student' not in response.text


async def test_recording_a_grade_returns_the_one_row_that_changed(client: httpx.AsyncClient) -> None:
    """The row-replacement pattern ADR-0011 names, asserted as a shape and not just a status."""
    await _sign_in(client, TEACHER_EMAIL)
    page = await client.get(f'/sections/{SECTION}/grades')

    response = await client.post(
        f'/sections/{SECTION}/grades',
        data={'student_id': str(STUDENT), 'grade': '8', 'csrf_token': _csrf(page.text)},
        headers={'HX-Request': 'true'},
    )

    assert response.status_code == 200
    assert response.text.lstrip().startswith('<tr')
    assert '<html' not in response.text
    assert f'id="student-{STUDENT}"' in response.text


async def test_the_returned_row_shows_the_standing_and_not_merely_the_last_grade(
    client: httpx.AsyncClient,
) -> None:
    """Recording a 4 after a 7 changes nothing about whether the subject is passed.

    A row that showed the attempt rather than the standing would tell a teacher they had just
    failed a student they had not.
    """
    await _sign_in(client, TEACHER_EMAIL)
    page = await client.get(f'/sections/{SECTION}/grades')
    token = _csrf(page.text)

    await client.post(
        f'/sections/{SECTION}/grades', data={'student_id': str(STUDENT), 'grade': '7', 'csrf_token': token}
    )
    response = await client.post(
        f'/sections/{SECTION}/grades', data={'student_id': str(STUDENT), 'grade': '4', 'csrf_token': token}
    )

    assert '>7<' in response.text
    assert 'not passed' not in response.text


async def test_a_recorded_grade_survives_into_the_next_page_load(client: httpx.AsyncClient) -> None:
    """It was stored, and not merely rendered back."""
    await _sign_in(client, TEACHER_EMAIL)
    page = await client.get(f'/sections/{SECTION}/grades')
    await client.post(
        f'/sections/{SECTION}/grades',
        data={'student_id': str(STUDENT), 'grade': '9', 'csrf_token': _csrf(page.text)},
    )

    reloaded = await client.get(f'/sections/{SECTION}/grades')

    assert '>9<' in reloaded.text


# ---------------------------------------------------------------------------------------------
# The error boundary, over the real table
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('grade', 'expected_status', 'expected_failure'),
    [
        pytest.param('11', 422, 'validation', id='invalid-grade'),
        pytest.param('-1', 422, 'validation', id='negative-grade'),
    ],
)
async def test_a_rejected_grade_carries_the_status_the_table_assigns(
    client: httpx.AsyncClient, grade: str, expected_status: int, expected_failure: str
) -> None:
    """ADR-0012's table, reached through a real use case rather than asserted against directly."""
    await _sign_in(client, TEACHER_EMAIL)
    page = await client.get(f'/sections/{SECTION}/grades')

    response = await client.post(
        f'/sections/{SECTION}/grades',
        data={'student_id': str(STUDENT), 'grade': grade, 'csrf_token': _csrf(page.text)},
        headers={'HX-Request': 'true'},
    )

    assert response.status_code == expected_status
    assert expected_failure in response.text


async def test_a_failure_is_retargeted_away_from_the_row_that_caused_it(client: httpx.AsyncClient) -> None:
    """The htmx rule, end to end: an honest 422 *and* something the user can see.

    Without the retarget the error would replace the student's row; without the swap-rule
    override in ``base.html`` htmx would discard it and the page would appear to do nothing.
    """
    await _sign_in(client, TEACHER_EMAIL)
    page = await client.get(f'/sections/{SECTION}/grades')

    response = await client.post(
        f'/sections/{SECTION}/grades',
        data={'student_id': str(STUDENT), 'grade': '11', 'csrf_token': _csrf(page.text)},
        headers={'HX-Request': 'true'},
    )

    assert response.headers['HX-Retarget'] == '#academy-errors'
    assert response.headers['HX-Reswap'] == 'innerHTML'


async def test_a_teacher_of_another_section_is_refused(client: httpx.AsyncClient) -> None:
    """Authorization is untouched by which door the request came through."""
    await _sign_in(client, 'ivan@academy.test')

    response = await client.get(f'/sections/{SECTION}/grades')

    assert response.status_code == 403


async def test_an_unknown_section_is_not_found(client: httpx.AsyncClient) -> None:
    await _sign_in(client, TEACHER_EMAIL)

    response = await client.get(f'/sections/{SectionId(UUID(int=404))}/grades')

    assert response.status_code == 404


async def test_a_student_not_enrolled_cannot_be_graded(client: httpx.AsyncClient) -> None:
    await _sign_in(client, TEACHER_EMAIL)
    page = await client.get(f'/sections/{SECTION}/grades')

    response = await client.post(
        f'/sections/{SECTION}/grades',
        data={'student_id': str(OTHER_STUDENT), 'grade': '8', 'csrf_token': _csrf(page.text)},
    )

    assert response.status_code in (403, 404, 409, 422)


async def test_a_plain_browser_failure_is_a_whole_page(client: httpx.AsyncClient) -> None:
    """No ``HX-Request`` header means no fragment: there is no page to swap it into."""
    await _sign_in(client, 'ivan@academy.test')

    response = await client.get(f'/sections/{SECTION}/grades')

    assert response.text.lstrip().startswith('<!doctype html>')
    assert 'HX-Retarget' not in response.headers


async def test_the_same_failure_is_json_on_the_api(client: httpx.AsyncClient, container: Container) -> None:
    """One classification, two renderings (ADR-0019)."""
    token = Credentials(container.secret_key).issue_token(INTRUDER)

    response = await client.get(f'/api/sections/{SECTION}/grades', headers={'Authorization': f'Bearer {token}'})

    assert response.status_code == 403
    assert response.json()['error'] == 'forbidden'
    assert 'html' not in response.headers['content-type']


# ---------------------------------------------------------------------------------------------
# The claim the whole adapter exists to make good on
# ---------------------------------------------------------------------------------------------


async def test_the_browser_and_the_api_reach_the_same_outcome(container: Container, bearer: dict[str, str]) -> None:
    """ADR-0011: the browser UI and the JSON API "call identical objects".

    The same grade recorded twice -- once through a form with a session cookie, once through JSON
    with a bearer token -- against two applications over two identical databases. The rendering
    differs completely and the *outcome* must not differ at all.

    This is the test that would fail if a rule ever leaked into an adapter, because a rule in one
    of them would have to be duplicated exactly in the other to keep passing.
    """
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(container)), base_url='http://academy.test'
    ) as browser:
        await _sign_in(browser, TEACHER_EMAIL)
        page = await browser.get(f'/sections/{SECTION}/grades')
        await browser.post(
            f'/sections/{SECTION}/grades',
            data={'student_id': str(STUDENT), 'grade': '8', 'csrf_token': _csrf(page.text)},
        )

        through_browser: JsonBody = (await browser.get(f'/api/sections/{SECTION}/grades', headers=bearer)).json()

    other = Container(Settings())
    try:
        await _seed(other)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(other)), base_url='http://academy.test'
        ) as api:
            token = Credentials(other.secret_key).issue_token(TEACHER)
            await api.post(
                f'/api/sections/{SECTION}/grades',
                json={'student_id': str(STUDENT), 'grade': 8},
                headers={'Authorization': f'Bearer {token}'},
            )
            through_api: JsonBody = (
                await api.get(f'/api/sections/{SECTION}/grades', headers={'Authorization': f'Bearer {token}'})
            ).json()
    finally:
        await other.aclose()

    assert through_browser == through_api


async def test_both_doors_refuse_the_same_actor(container: Container) -> None:
    """The other half of the claim: authorization does not depend on the credential either.

    ``Actor`` carries no trace of how it was authenticated, so a use case could not branch on the
    difference -- and the refusal arrives identically classified through both.
    """
    signers = Credentials(container.secret_key)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(container)), base_url='http://academy.test'
    ) as client:
        client.cookies.set(SESSION_COOKIE, signers.issue_session(INTRUDER))
        by_cookie = await client.get(f'/sections/{SECTION}/grades')

        client.cookies.delete(SESSION_COOKIE)
        by_token = await client.get(
            f'/api/sections/{SECTION}/grades',
            headers={'Authorization': f'Bearer {signers.issue_token(INTRUDER)}'},
        )

    assert by_cookie.status_code == by_token.status_code == 403


async def _seed(container: Container) -> None:
    """The same fixture data, for the second application in the parity test."""
    async with container.request_scope() as scope:
        unit_of_work = scope.unit_of_work()
        async with unit_of_work:
            await scope.people.add(_person(TEACHER, TEACHER_EMAIL, 'Tess Teacher', Role.TEACHER))
            await scope.people.add(_person(STUDENT, STUDENT_EMAIL, 'Sam Student', Role.STUDENT))
            section = CourseSection(id=SECTION, subject_id=SUBJECT, term=Term(2026, 1), teacher_id=TEACHER)
            section.enroll(STUDENT)
            await scope.sections.add(section)
            await unit_of_work.commit()


async def test_the_csrf_cookie_is_set_by_the_page_that_needs_it(client: httpx.AsyncClient) -> None:
    """A page rendering a token whose cookie was never set would refuse its own form."""
    response = await client.get('/sign-in')

    assert CSRF_COOKIE in response.cookies
    assert response.cookies[CSRF_COOKIE] == _csrf(response.text)
