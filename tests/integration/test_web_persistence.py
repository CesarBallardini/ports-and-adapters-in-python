"""The web adapter against a real database, asserting what was **stored** and not what was said.

``test_web.py`` drives the same routes over the in-memory backend, which is the right tier for
behaviour and is structurally blind to one whole class of bug. Its read-backs go through the same
store the write went into, so a route that returned a correct-looking response while persisting
nothing would pass every assertion in that file.

This repository has already been bitten by exactly that. The SQLAlchemy adapter discarded every
change to a JSON collection for months while the repository contract suite stayed green, because
the suite never left its transaction: the identity map handed back the very object that had been
mutated. The fix was `flag_modified`; the lesson was that **anything asserting something was
stored has to commit, drop the session and ask again.**

So every test here does that. The application under test is disposed before the assertion runs,
and the read-back happens through an engine that has never seen it.

The three properties are the ones the in-memory tier cannot reach:

* a grade recorded over HTTP survives into a session that did not write it;
* a request that fails leaves **nothing** behind, because the unit of work is per request;
* two concurrent requests get independent units of work rather than fighting over one.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from academy.adapters.inbound.web import create_app
from academy.adapters.outbound.persistence.sqlalchemy.repositories import (
    SqlAlchemyAcademicHistoryRepository,
    SqlAlchemyPersonRepository,
    SqlAlchemySectionRepository,
)
from academy.adapters.outbound.persistence.sqlalchemy.session import (
    create_engine,
    create_session_factory,
    migrate_to_head,
)
from academy.adapters.outbound.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from academy.config.container import Container
from academy.config.settings import PersistenceBackend, Settings
from academy.domain.academics.course_section import CourseSection
from academy.domain.academics.term import Term
from academy.domain.grades.grade import Grade
from academy.domain.grades.grade_entry import GradeEntry
from academy.domain.people.email import Email
from academy.domain.people.person import Person
from academy.domain.people.personal_data import PersonalData
from academy.domain.people.role import Role
from academy.domain.shared.ids import PersonId, SectionId, SubjectId

pytestmark = pytest.mark.integration

TEACHER = PersonId(UUID(int=1))
STUDENT = PersonId(UUID(int=2))
# A second enrolled student, so a concurrency test can touch two aggregates rather than one.
SECOND_STUDENT = PersonId(UUID(int=3))
SECTION = SectionId(UUID(int=10))
# Same teacher, nobody enrolled. Recording a grade here is refused *after* the use case has
# already begun a transaction, which is what makes it a rollback test rather than a validation one.
UNENROLLED_SECTION = SectionId(UUID(int=11))
MATHEMATICS = SubjectId(UUID(int=20))
PHYSICS = SubjectId(UUID(int=21))

TEACHER_EMAIL = 'tess@academy.test'

SIGNING_KEY = 'a-key-for-these-tests'  # noqa: S105 -- a literal in a test, not a credential


@pytest.fixture
async def database(tmp_path: Path) -> AsyncIterator[str]:
    """A migrated SQLite database with a teacher, a student and two sections.

    Migrated, never ``create_all`` (ADR-0006). The migration runs in a worker thread because
    Alembic drives its own event loop, and ``asyncio.run`` inside a running one is an error.

    Function-scoped, unlike the e2e tier's: these tests **write**, so each needs its own database
    or they would leak into one another in an order pytest is free to change.
    """
    url = f'sqlite+aiosqlite:///{(tmp_path / "academy.db").as_posix()}'
    await asyncio.to_thread(migrate_to_head, url)

    engine = create_engine(url)
    try:
        async with create_session_factory(engine)() as session:
            unit_of_work = SqlAlchemyUnitOfWork(session)
            async with unit_of_work:
                people = SqlAlchemyPersonRepository(session)
                await people.add(_person(TEACHER, TEACHER_EMAIL, 'Tess Teacher', Role.TEACHER))
                await people.add(_person(STUDENT, 'sam@academy.test', 'Sam Student', Role.STUDENT))
                await people.add(_person(SECOND_STUDENT, 'sol@academy.test', 'Sol Student', Role.STUDENT))

                sections = SqlAlchemySectionRepository(session)
                enrolled = CourseSection(id=SECTION, subject_id=MATHEMATICS, term=Term(2026, 1), teacher_id=TEACHER)
                enrolled.enroll(STUDENT)
                enrolled.enroll(SECOND_STUDENT)
                await sections.add(enrolled)
                await sections.add(
                    CourseSection(id=UNENROLLED_SECTION, subject_id=PHYSICS, term=Term(2026, 1), teacher_id=TEACHER)
                )
                await unit_of_work.commit()
        yield url
    finally:
        # Windows will not delete the temporary directory while a handle is open on the file.
        await engine.dispose()


def _person(person_id: PersonId, email: str, name: str, *roles: Role) -> Person:
    return Person(
        id=person_id,
        email=Email(email),
        personal=PersonalData(full_name=name, birth_date=date(1990, 1, 1)),
        roles=set(roles),
    )


@pytest.fixture
async def signed_in(database: str) -> AsyncIterator[httpx.AsyncClient]:
    """The web application over that database, with a session cookie already established.

    The container is closed when the client is, which disposes the engine -- so any read-back
    after this fixture tears down is genuinely going through a fresh connection.
    """
    container = Container(
        Settings(persistence=PersistenceBackend.SQLALCHEMY, database_url=database, secret_key=SIGNING_KEY)
    )
    transport = httpx.ASGITransport(app=create_app(container))
    try:
        async with httpx.AsyncClient(transport=transport, base_url='http://academy.test') as client:
            form = await client.get('/sign-in')
            await client.post(
                '/sign-in',
                data={'email': TEACHER_EMAIL, 'password': 'ignored', 'csrf_token': _csrf(form.text)},
            )
            yield client
    finally:
        await container.aclose()


def _csrf(body: str) -> str:
    """The token a rendered page put in its form."""
    match = re.search(r'name="csrf_token" value="([^"]+)"', body)
    assert match is not None, 'the page rendered no CSRF token'
    return match.group(1)


async def _entries_in_a_fresh_session(url: str) -> list[GradeEntry]:
    """Read the student's transcript through an engine that has never been used before.

    The whole point of this module. A read-back through the application's own session proves
    nothing: the identity map returns the object that was mutated, whether or not an ``UPDATE``
    was ever emitted.
    """
    engine = create_engine(url)
    try:
        async with create_session_factory(engine)() as session:
            history = await SqlAlchemyAcademicHistoryRepository(session).get(STUDENT)
            return list(history.entries) if history is not None else []
    finally:
        await engine.dispose()


def _entry(grade: int) -> GradeEntry:
    """One transcript entry, in the section the fixtures set up."""
    return GradeEntry(subject_id=MATHEMATICS, term=Term(2026, 1), grade=Grade(grade), source_section_id=SECTION)


async def test_a_grade_recorded_over_http_is_actually_written(signed_in: httpx.AsyncClient, database: str) -> None:
    """The assertion the in-memory tier cannot make.

    A route that rendered the right row and persisted nothing passes every test in
    ``test_web.py``. This one commits, drops the session and asks the database.
    """
    page = await signed_in.get(f'/sections/{SECTION}/grades')
    recorded = await signed_in.post(
        f'/sections/{SECTION}/grades',
        data={'student_id': str(STUDENT), 'grade': '8', 'csrf_token': _csrf(page.text)},
    )
    assert recorded.status_code == 200

    entries = await _entries_in_a_fresh_session(database)

    assert [entry.grade.value for entry in entries] == [8]


async def test_every_attempt_is_kept_not_just_the_last(signed_in: httpx.AsyncClient, database: str) -> None:
    """The domain keeps every attempt and derives the standing; storage has to agree.

    This is the shape of the bug that hid before: ``history.record(...)`` appends **in place**, so
    the ORM sees the same object it loaded and emits no ``UPDATE`` unless the attribute is flagged.
    Two appends in two requests is precisely the case that would silently keep only one.
    """
    page = await signed_in.get(f'/sections/{SECTION}/grades')
    token = _csrf(page.text)

    for grade in ('7', '4', '9'):
        response = await signed_in.post(
            f'/sections/{SECTION}/grades',
            data={'student_id': str(STUDENT), 'grade': grade, 'csrf_token': token},
        )
        assert response.status_code == 200, grade

    entries = await _entries_in_a_fresh_session(database)

    assert sorted(entry.grade.value for entry in entries) == [4, 7, 9]


async def test_the_standing_survives_a_restart_and_is_still_the_best_attempt(
    signed_in: httpx.AsyncClient, database: str
) -> None:
    """A 4 recorded after a 7 must not become the answer, in storage any more than on the page."""
    page = await signed_in.get(f'/sections/{SECTION}/grades')
    token = _csrf(page.text)
    await signed_in.post(
        f'/sections/{SECTION}/grades', data={'student_id': str(STUDENT), 'grade': '7', 'csrf_token': token}
    )
    await signed_in.post(
        f'/sections/{SECTION}/grades', data={'student_id': str(STUDENT), 'grade': '4', 'csrf_token': token}
    )

    engine = create_engine(database)
    try:
        async with create_session_factory(engine)() as session:
            history = await SqlAlchemyAcademicHistoryRepository(session).get(STUDENT)
            assert history is not None
            best = history.best_grade(MATHEMATICS)
            assert best is not None
            assert best.value == 7
            assert history.has_passed(MATHEMATICS)
    finally:
        await engine.dispose()


async def test_a_request_that_fails_writes_nothing(signed_in: httpx.AsyncClient, database: str) -> None:
    """The transaction boundary is the request, and a refusal has to take the write with it.

    The section is one this teacher *does* teach, so the actor is authorized and the use case
    proceeds -- and then refuses, because the student is not enrolled in it. That is a failure
    raised **after** work has begun, which is the only kind that can leave a partial write behind.
    A rollback that did not happen would show up here and nowhere else.
    """
    page = await signed_in.get(f'/sections/{SECTION}/grades')
    response = await signed_in.post(
        f'/sections/{UNENROLLED_SECTION}/grades',
        data={'student_id': str(STUDENT), 'grade': '7', 'csrf_token': _csrf(page.text)},
    )

    assert response.status_code >= 400
    assert await _entries_in_a_fresh_session(database) == []


async def test_a_failed_request_does_not_poison_the_next_one(signed_in: httpx.AsyncClient, database: str) -> None:
    """A rolled-back session must be usable again, not left dirty for whoever comes next.

    Requests share a container and, on this backend, a session factory. If a failure left its
    session in a broken transaction, the *following* request would fail for a reason having
    nothing to do with itself -- the worst kind of bug to be handed in production.
    """
    page = await signed_in.get(f'/sections/{SECTION}/grades')
    token = _csrf(page.text)

    failed = await signed_in.post(
        f'/sections/{UNENROLLED_SECTION}/grades',
        data={'student_id': str(STUDENT), 'grade': '7', 'csrf_token': token},
    )
    assert failed.status_code >= 400

    recovered = await signed_in.post(
        f'/sections/{SECTION}/grades',
        data={'student_id': str(STUDENT), 'grade': '6', 'csrf_token': token},
    )

    assert recovered.status_code == 200
    assert [entry.grade.value for entry in await _entries_in_a_fresh_session(database)] == [6]


async def test_two_concurrent_requests_each_get_their_own_unit_of_work(
    signed_in: httpx.AsyncClient, database: str
) -> None:
    """The failure the container's docstring predicts, asserted over HTTP.

    A unit of work refuses re-entry while it is active. One shared across a scope -- or a scope
    shared across requests -- would turn two overlapping calls into ``RuntimeError`` rather than
    two transactions, and that only ever appears under concurrency.

    Two *different* students, each of whom already has a transcript, so this exercises the
    update path. The create path -- two concurrent *first* grades for one student -- is a
    different race and gets its own test: see
    :func:`test_two_concurrent_first_grades_both_succeed` below, which covers the create path.
    """
    page = await signed_in.get(f'/sections/{SECTION}/grades')
    token = _csrf(page.text)

    # Give both students a transcript first, so the concurrent pair is an update and not a
    # create -- the property under test is the unit of work, not the insert race.
    for student in (STUDENT, SECOND_STUDENT):
        await signed_in.post(
            f'/sections/{SECTION}/grades',
            data={'student_id': str(student), 'grade': '5', 'csrf_token': token},
        )

    first, second = await asyncio.gather(
        signed_in.post(
            f'/sections/{SECTION}/grades',
            data={'student_id': str(STUDENT), 'grade': '9', 'csrf_token': token},
        ),
        signed_in.post(
            f'/sections/{SECTION}/grades',
            data={'student_id': str(SECOND_STUDENT), 'grade': '8', 'csrf_token': token},
        ),
    )

    # Neither may be a 500: a `RuntimeError` from a re-entered unit of work is a bug of ours and
    # is exactly what this is watching for.
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text

    assert sorted(entry.grade.value for entry in await _entries_in_a_fresh_session(database)) == [5, 9]


async def test_two_concurrent_first_grades_are_both_accepted(signed_in: httpx.AsyncClient, database: str) -> None:
    """The get-or-create race, fixed: neither request is a 500 any more.

    This replaces a test that was written to expire. Two requests recording a student's *first*
    grade both find no transcript and both try to create one; the loser used to violate the
    primary key and raise ``IntegrityError``, which no entry in ADR-0012's table classifies, so it
    reached the client as a 500 with a traceback. The port promises the opposite -- *"This method
    never raises for a student with no grades yet"* -- so the adapter was wrong, and it now
    retries inside a SAVEPOINT and returns the winner's row.

    **What this deliberately does not assert is that both grades are kept.** They may not be, and
    the reason has nothing to do with the race this test is named after: see
    :func:`test_a_concurrent_writer_still_overwrites_the_other`, which pins that defect on its
    own. Asserting it here would have made one test fail for two unrelated causes, and it did --
    this assertion was written as ``== [5, 6]`` and CI was right to reject it.
    """
    page = await signed_in.get(f'/sections/{SECTION}/grades')
    token = _csrf(page.text)

    first, second = await asyncio.gather(
        signed_in.post(
            f'/sections/{SECTION}/grades',
            data={'student_id': str(STUDENT), 'grade': '5', 'csrf_token': token},
        ),
        signed_in.post(
            f'/sections/{SECTION}/grades',
            data={'student_id': str(STUDENT), 'grade': '6', 'csrf_token': token},
        ),
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text

    # A transcript exists and holds at least one of the two attempts. Which ones survive is the
    # subject of the next test; that the request was answered rather than crashing is this one's.
    stored = sorted(entry.grade.value for entry in await _entries_in_a_fresh_session(database))
    assert stored, 'the transcript is empty: neither concurrent first grade was stored at all'
    assert set(stored) <= {5, 6}


async def test_a_concurrent_writer_still_overwrites_the_other(database: str) -> None:
    """A known defect, asserted deterministically so that fixing it is noticed. **Delete this test
    when it starts failing** -- it is written to expire, and a failure here is the good news.

    Two units of work read the same transcript, each append one entry, and each ``save`` writes
    the collection **whole**, because ADR-0017 keeps it as JSON on the aggregate's row. The second
    commit therefore overwrites the first, and a grade a teacher was told had been recorded is
    gone. Nothing raises and nothing logs.

    This is *not* the get-or-create race, and the SAVEPOINT fix does not touch it: the run below
    seeds an existing transcript first, so no row is ever created and no ``IntegrityError`` is
    ever possible. It is the plain lost update, and it is reachable from two ordinary HTTP
    requests -- CI found it that way, through ``asyncio.gather`` over two POSTs, before it was
    pinned here.

    It is sequenced by hand rather than raced, because a test for a defect has to fail every time
    or it is not a test. It is also *not* in the contract suite: the in-memory adapter does not
    have this defect -- both scopes mutate the one object it stores -- so there is no shared
    assertion to write, and this belongs beside the adapter that has it.

    **What fixing it looks like:** optimistic concurrency, via SQLAlchemy's ``version_id_col``. A
    version column per aggregate turns ``save`` into ``UPDATE ... WHERE id = ? AND version = ?``,
    and a stale write raises ``StaleDataError`` instead of succeeding -- silent data loss becomes
    a ``ConflictError``, which ADR-0012's table already classifies. Plain SQL, so it behaves the
    same on SQLite and PostgreSQL; row locking would not, and ``SELECT ... FOR UPDATE`` is a
    no-op on SQLite, which would leave this test passing while proving nothing.
    """
    engine = create_engine(database)
    try:
        factory = create_session_factory(engine)

        async with factory() as seeding:
            repository = SqlAlchemyAcademicHistoryRepository(seeding)
            existing = await repository.get_or_create(STUDENT)
            existing.record(_entry(3))
            await repository.save(existing)
            await seeding.commit()

        async with factory() as one, factory() as other:
            # Both read before either writes -- the shape of any two concurrent requests.
            first = await SqlAlchemyAcademicHistoryRepository(one).get_or_create(STUDENT)
            second = await SqlAlchemyAcademicHistoryRepository(other).get_or_create(STUDENT)

            first.record(_entry(5))
            await SqlAlchemyAcademicHistoryRepository(one).save(first)
            await one.commit()

            second.record(_entry(6))
            await SqlAlchemyAcademicHistoryRepository(other).save(second)
            await other.commit()
    finally:
        await engine.dispose()

    stored = sorted(entry.grade.value for entry in await _entries_in_a_fresh_session(database))

    assert stored == [3, 6], (
        f'expected the lost update this test documents, and got {stored}. '
        'If this reads [3, 5, 6] the defect is fixed -- delete this test rather than adjusting it.'
    )


async def test_the_json_api_writes_through_the_same_way(signed_in: httpx.AsyncClient, database: str) -> None:
    """The parity claim, extended to durability.

    ``test_web.py`` shows the two surfaces produce the same *answer*. This shows they produce the
    same *effect* -- which is the half a reader would otherwise have to take on trust.
    """
    container = Container(
        Settings(persistence=PersistenceBackend.SQLALCHEMY, database_url=database, secret_key=SIGNING_KEY)
    )
    try:
        from academy.adapters.inbound.web.security import Credentials

        token = Credentials(container.secret_key).issue_token(TEACHER)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(container)), base_url='http://academy.test'
        ) as api:
            response = await api.post(
                f'/api/sections/{SECTION}/grades',
                json={'student_id': str(STUDENT), 'grade': 10},
                headers={'Authorization': f'Bearer {token}'},
            )
            assert response.status_code == 200
    finally:
        await container.aclose()

    assert [entry.grade.value for entry in await _entries_in_a_fresh_session(database)] == [10]


async def test_the_recorded_entry_names_the_section_it_came_from(signed_in: httpx.AsyncClient, database: str) -> None:
    """A transcript entry carries its source section, and it has to survive the round trip.

    Worth its own assertion because it is the field a reader is least likely to look at, and the
    one a serialisation bug would drop without changing any grade anybody checks.
    """
    page = await signed_in.get(f'/sections/{SECTION}/grades')
    await signed_in.post(
        f'/sections/{SECTION}/grades',
        data={'student_id': str(STUDENT), 'grade': '8', 'csrf_token': _csrf(page.text)},
    )

    entries = await _entries_in_a_fresh_session(database)

    assert len(entries) == 1
    assert entries[0].source_section_id == SECTION
    assert entries[0].subject_id == MATHEMATICS
