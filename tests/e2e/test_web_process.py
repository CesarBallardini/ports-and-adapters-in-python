"""The web adapter as a shell starts it: uvicorn, a real socket, a real environment.

Everything in ``tests/integration/test_web.py`` drives the application through ``ASGITransport``
in this interpreter. That covers the adapter and misses exactly the things this tier exists for:

* that ``uvicorn --factory academy.config:create_app`` **resolves at all** -- the target ``make
  run`` names, which was pointing at a function that did not exist until this change;
* that the process reads its configuration from the **environment**, not from a dict a test
  handed it;
* that ``StaticFiles`` actually serves the vendored ``htmx.min.js``, which depends on the file
  being packaged and the mount path being right -- neither of which an in-process call proves;
* that a misconfigured deployment **refuses to start**, rather than starting and failing later.

Those are the failures no unit test can have. An entry point that raises on import, a factory
uvicorn cannot call, a static mount pointing at a directory that is not shipped: all of them pass
every other tier and break the first time someone runs the server.

**Reads only.** The database here is module-scoped, exactly as in ``test_cli_process.py``, because
spawning a server per test is already the expensive part. A test that *wrote* would leak into
every test after it in an order pytest is free to change. Signing in is safe -- it reads a person
and sets a cookie -- and recording a grade is not, which is why the write path is asserted in the
integration tier where each test gets its own store.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from academy.adapters.outbound.persistence.sqlalchemy.repositories import (
    SqlAlchemyPersonRepository,
    SqlAlchemySectionRepository,
)
from academy.adapters.outbound.persistence.sqlalchemy.session import (
    create_engine,
    create_session_factory,
    migrate_to_head,
)
from academy.adapters.outbound.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from academy.config.settings import ENV_DATABASE_URL, ENV_PERSISTENCE, ENV_SECRET_KEY
from academy.domain.academics.course_section import CourseSection
from academy.domain.academics.term import Term
from academy.domain.people.email import Email
from academy.domain.people.person import Person
from academy.domain.people.personal_data import PersonalData
from academy.domain.people.role import Role
from academy.domain.shared.ids import PersonId, SectionId, SubjectId

pytestmark = pytest.mark.e2e

TEACHER = PersonId(UUID(int=1))
STUDENT = PersonId(UUID(int=2))
SECTION = SectionId(UUID(int=10))
SUBJECT = SubjectId(UUID(int=20))

TEACHER_EMAIL = 'tess@academy.test'

SECRET_KEY = 'an-e2e-signing-key'  # noqa: S105 -- a throwaway key for a throwaway database

# Long enough that a machine under load does not fail the build, short enough that a hang is a
# failure rather than a wait.
STARTUP_TIMEOUT_SECONDS = 60
REQUEST_TIMEOUT_SECONDS = 15


@pytest.fixture(scope='module')
def database(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """One migrated, seeded SQLite database for the whole module.

    The schema comes only from Alembic (ADR-0006), from empty, here as everywhere else.
    ``migrate_to_head`` is synchronous because ``env.py`` drives its own event loop, so it is
    called from a thread rather than from inside a running one.
    """
    path = tmp_path_factory.mktemp('e2e-web') / 'academy.db'
    url = f'sqlite+aiosqlite:///{path.as_posix()}'
    asyncio.run(_build(url))
    yield url


async def _build(url: str) -> None:
    """Migrate from empty and put a teacher and one enrolled student in."""
    await asyncio.to_thread(migrate_to_head, url)

    engine = create_engine(url)
    try:
        async with create_session_factory(engine)() as session:
            people = SqlAlchemyPersonRepository(session)
            sections = SqlAlchemySectionRepository(session)
            unit_of_work = SqlAlchemyUnitOfWork(session)
            async with unit_of_work:
                await people.add(_person(TEACHER, TEACHER_EMAIL, 'Tess Teacher', Role.TEACHER))
                await people.add(_person(STUDENT, 'sam@academy.test', 'Sam Student', Role.STUDENT))
                section = CourseSection(id=SECTION, subject_id=SUBJECT, term=Term(2026, 1), teacher_id=TEACHER)
                section.enroll(STUDENT)
                await sections.add(section)
                await unit_of_work.commit()
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


def _free_port() -> int:
    """A port the operating system says is free right now.

    Racy in principle and fine in practice: the socket is closed and immediately reused, and the
    alternative -- a hard-coded port -- fails whenever two builds share a machine.
    """
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return int(probe.getsockname()[1])


class Server:
    """A uvicorn process serving the application, addressed over a real socket."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    def get(self, path: str) -> httpx.Response:
        """One GET against the running server, with no session and no redirects followed."""
        with httpx.Client(base_url=self.base_url, timeout=REQUEST_TIMEOUT_SECONDS) as client:
            return client.get(path)


@pytest.fixture(scope='module')
def server(database: str) -> Iterator[Server]:
    """``uvicorn --factory academy.config:create_app``, exactly as ``make run`` invokes it.

    An argv list and never a shell string: nothing here is interpolated by a shell, which is why
    the ``S603`` subprocess rules are waived for this directory rather than for the codebase.
    """
    port = _free_port()
    process = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            '-m',
            'uvicorn',
            '--factory',
            'academy.config:create_app',
            '--host',
            '127.0.0.1',
            '--port',
            str(port),
            '--log-level',
            'warning',
        ],
        env={
            **os.environ,
            ENV_PERSISTENCE: 'sqlalchemy',
            ENV_DATABASE_URL: database,
            ENV_SECRET_KEY: SECRET_KEY,
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    base_url = f'http://127.0.0.1:{port}'
    try:
        _await_health(process, base_url)
        yield Server(base_url)
    finally:
        process.terminate()
        # `communicate` and not `wait`: it drains *and closes* the stdout pipe. `wait` leaves the
        # pipe to be closed by the garbage collector, which raises during finalisation -- and
        # since warnings are errors here, that turns a passing module into a teardown failure.
        try:
            process.communicate(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover -- a server that will not stop
            process.kill()
            process.communicate()


def _await_health(process: subprocess.Popen[str], base_url: str) -> None:
    """Block until the server answers, or fail with whatever it printed instead.

    Polling ``/healthz`` rather than sleeping a fixed time: a slow machine should wait longer
    rather than fail, and a server that died should fail immediately with its own output rather
    than after the full timeout.
    """
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ''
            raise AssertionError(f'the server exited during startup with {process.returncode}:\n{output}')
        try:
            with httpx.Client(timeout=2) as client:
                if client.get(f'{base_url}/healthz').status_code == 200:
                    return
        except httpx.HTTPError:
            time.sleep(0.2)

    raise AssertionError(f'the server did not answer within {STARTUP_TIMEOUT_SECONDS}s')  # pragma: no cover


def test_the_factory_the_makefile_names_actually_serves(server: Server) -> None:
    """``uvicorn --factory academy.config:create_app`` resolves and answers.

    Nothing else in the suite can say so. ``make run`` pointed at this name for two phases while
    the function did not exist, and no test went red.
    """
    response = server.get('/healthz')

    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}


def test_the_process_reads_its_database_from_the_environment(server: Server) -> None:
    """The one thing an in-process ``create_app(container)`` can never prove.

    The seeded person exists only in the database named by ``ACADEMY_DATABASE_URL``, so signing in
    as them is the proof that the variable was read and the connection made.
    """
    with httpx.Client(base_url=server.base_url, timeout=REQUEST_TIMEOUT_SECONDS) as client:
        form = client.get('/sign-in')
        token = _csrf(form.text)
        signed = client.post('/sign-in', data={'email': TEACHER_EMAIL, 'password': 'x', 'csrf_token': token})

        assert signed.status_code == 303


def test_a_signed_in_teacher_can_read_the_sheet_over_a_socket(server: Server) -> None:
    """A read, end to end, through the real stack: socket, ASGI, session, use case, SQLite."""
    with httpx.Client(base_url=server.base_url, timeout=REQUEST_TIMEOUT_SECONDS) as client:
        form = client.get('/sign-in')
        client.post('/sign-in', data={'email': TEACHER_EMAIL, 'password': 'x', 'csrf_token': _csrf(form.text)})

        sheet = client.get(f'/sections/{SECTION}/grades')

    assert sheet.status_code == 200
    assert 'Sam Student' in sheet.text


def test_the_vendored_htmx_is_actually_served(server: Server) -> None:
    """The mount, the packaged file and the path in ``base.html``, all at once.

    An in-process test renders ``<script src="/static/htmx.min.js">`` happily whether or not
    anything is behind it. This is what notices a file that did not make it into the package.
    """
    response = server.get('/static/htmx.min.js')

    assert response.status_code == 200
    assert 'htmx' in response.text[:200]
    assert 'javascript' in response.headers['content-type']


def test_the_page_asks_for_the_script_that_is_served(server: Server) -> None:
    """The two halves of the previous test, joined: the src attribute and the served path."""
    body = server.get('/sign-in').text

    assert 'src="/static/htmx.min.js"' in body


def test_the_root_sends_a_visitor_somewhere_that_exists(server: Server) -> None:
    with httpx.Client(base_url=server.base_url, timeout=REQUEST_TIMEOUT_SECONDS) as client:
        landing = client.get('/', follow_redirects=True)

    assert landing.status_code == 200
    assert 'Sign in' in landing.text


def test_a_durable_deployment_without_a_signing_key_refuses_to_start(database: str) -> None:
    """Exit non-zero, before serving anything, with the variable named.

    A generated key would differ between workers and between restarts, so a signed-in user would
    be signed out by whichever worker answered next. That is indistinguishable from flaky
    sessions, which is why it is a startup failure and not a warning.

    Run as its own process rather than against the module's server, because the thing under test
    is that the process does *not* come up.
    """
    completed = subprocess.run(  # noqa: S603
        [sys.executable, '-c', 'from academy.config import create_app; create_app()'],
        env={
            **os.environ,
            ENV_PERSISTENCE: 'sqlalchemy',
            ENV_DATABASE_URL: database,
            ENV_SECRET_KEY: '',
        },
        capture_output=True,
        text=True,
        timeout=STARTUP_TIMEOUT_SECONDS,
        check=False,
    )

    assert completed.returncode != 0
    assert 'ACADEMY_SECRET_KEY' in completed.stderr


def test_an_unknown_persistence_backend_refuses_to_start() -> None:
    """The startup check that already existed, now reached through the web entry point too."""
    completed = subprocess.run(  # noqa: S603
        [sys.executable, '-c', 'from academy.config import create_app; create_app()'],
        env={**os.environ, ENV_PERSISTENCE: 'postgres', ENV_SECRET_KEY: SECRET_KEY},
        capture_output=True,
        text=True,
        timeout=STARTUP_TIMEOUT_SECONDS,
        check=False,
    )

    assert completed.returncode != 0
    assert 'memory, sqlalchemy' in completed.stderr


def test_the_cli_still_works_without_the_web_extra_installed() -> None:
    """``academy.config`` imports FastAPI lazily, and this is why that matters.

    The CLI needs no extra at all (ADR-0020). A top-level ``from academy.adapters.inbound.web
    import ...`` in the composition root would make ``python -m academy config show`` fail with
    ``ModuleNotFoundError: fastapi`` on a bare ``uv sync`` -- the dependency-free core
    reintroduced as a hard dependency by the wiring.

    This cannot uninstall FastAPI, so it asserts the mechanism instead: importing the composition
    root must not drag the web adapter in with it.
    """
    completed = subprocess.run(  # noqa: S603
        [
            sys.executable,
            '-c',
            'import sys; import academy.config; print("fastapi" in sys.modules)',
        ],
        capture_output=True,
        text=True,
        timeout=STARTUP_TIMEOUT_SECONDS,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == 'False'


def _csrf(body: str) -> str:
    """The token a rendered page put in its form."""
    import re

    match = re.search(r'name="csrf_token" value="([^"]+)"', body)
    assert match is not None, 'the page rendered no CSRF token'
    return match.group(1)


def test_the_static_directory_that_ships_is_the_one_that_is_served() -> None:
    """A guard on packaging rather than on the server: the file exists where the mount looks."""
    from academy.adapters.inbound.web.rendering import STATIC_DIRECTORY

    assert (Path(STATIC_DIRECTORY) / 'htmx.min.js').is_file()
