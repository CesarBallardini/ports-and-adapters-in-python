"""The Makefile targets a person actually types, run as a person runs them.

Everything these targets call is tested directly -- ``seed_demo``, ``seed_bootstrap``,
``migrate_to_head``, ``credentials``. What is **not** otherwise tested is the wiring between
them: that ``DEV_ENV`` exports the variables the commands need, that ``demo`` and ``bootstrap``
depend on ``migrate`` so they work against an empty file, and that the recipes are shell a make
will actually run. A broken prerequisite or a typo in ``DEV_ENV`` passes every other test in this
repository and fails the first time somebody follows the README.

The targets are only usable because of a property worth stating: ``DEV_ENV`` sets
``ACADEMY_PERSISTENCE`` and ``ACADEMY_SECRET_KEY`` inline but **not** the database URL, so an
inherited ``ACADEMY_DATABASE_URL`` passes through. That is what lets these tests point the same
recipes at a temporary file instead of the checkout's own ``academy_development.db`` -- without
it, running the suite would seed the developer's working database.

``make run`` is deliberately not here: it serves until interrupted, and the two things it adds
over ``migrate`` -- printing the credentials and starting uvicorn -- are covered by
``tests/config/test_seeding.py`` and ``tests/e2e/test_web_process.py`` respectively. Starting a
blocking server through ``make`` to kill it a second later would test the killing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

import pytest

from academy.config.seeding import BOOTSTRAP_ID, DEMO_PEOPLE, DEMO_TEACHER
from academy.config.settings import ENV_DATABASE_URL

pytestmark = pytest.mark.e2e


class MakeTarget(Protocol):
    """Runs one Makefile target against this test's throwaway database.

    A protocol rather than a bare `Callable`, so the tests below can annotate the fixture instead
    of silencing `ANN001` -- and so the keyword arguments are the Make variables they actually are.
    """

    def __call__(self, target: str, **variables: str) -> subprocess.CompletedProcess[str]:
        """Invoke ``make <target> NAME=value ...``."""
        ...


def _executable(name: str) -> str:
    """The absolute path to a command, or a skip saying why the test cannot run.

    Absolute rather than relying on ``PATH`` resolution inside ``subprocess``: it is what ``S607``
    asks for, and it means a machine with two ``make`` installations runs the one the developer's
    shell would.

    The ``assert`` is narrowing for pyrefly, which -- unlike pyright -- does not know that
    ``pytest.skip`` never returns, and so reads the value below as ``str | None``.
    """
    found = shutil.which(name)
    if found is None:  # pragma: no cover -- depends on the machine, not the code
        pytest.skip(f'{name} is not on PATH; these targets are checked on CI where it is')
    assert found is not None
    return found


# Generous: these shell out to `uv run`, which may resolve the environment before doing anything.
TARGET_TIMEOUT_SECONDS = 300

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    """A temporary database for a target to migrate and seed.

    Function-scoped and per-test, because these targets **write** -- the module-scoped, read-only
    rule that governs the rest of this directory exists for shared fixtures, and this deliberately
    does not share one.
    """
    return f'sqlite+aiosqlite:///{(tmp_path / "academy.db").as_posix()}'


@pytest.fixture
def make(database_url: str) -> Iterator[MakeTarget]:
    """Run a Makefile target against a throwaway database.

    Skips where ``make`` is not installed rather than failing: it is not a Python dependency and
    not something ``uv sync`` can provide. CI runs on Ubuntu, where it is present, so the skip
    only ever affects a developer on a machine without it.
    """
    executable = _executable('make')

    def run(target: str, **variables: str) -> subprocess.CompletedProcess[str]:
        """Invoke one target, with the database pointed at the temporary file."""
        return subprocess.run(  # noqa: S603
            [executable, target, *(f'{name}={value}' for name, value in variables.items())],
            cwd=PROJECT_ROOT,
            env={**os.environ, ENV_DATABASE_URL: database_url},
            capture_output=True,
            text=True,
            timeout=TARGET_TIMEOUT_SECONDS,
            check=False,
        )

    yield run


def test_make_migrate_builds_a_schema_from_nothing(make: MakeTarget, database_url: str) -> None:
    """The target the other two depend on, and the one that proves ``DEV_ENV`` is wired.

    The file does not exist when this starts. If ``ACADEMY_PERSISTENCE`` were missing from
    ``DEV_ENV`` the recipe would migrate whatever the default backend points at, and if the
    inherited URL were being overridden it would migrate the checkout's own database instead.
    """
    completed = make('migrate')

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert Path(database_url.removeprefix('sqlite+aiosqlite:///')).exists()


def test_make_demo_seeds_an_empty_database_and_prints_the_credentials(make: MakeTarget) -> None:
    """One command, from no file at all to a usable application.

    This is the whole promise of the target: a person clones the repository and types ``make
    demo``. It works only if the ``migrate`` prerequisite runs first -- without it the seeding
    would hit a database with no tables.
    """
    completed = make('demo')

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert 'Seeded' in completed.stdout
    for person in DEMO_PEOPLE:
        assert person.email in completed.stdout, person.email


def test_make_demo_twice_is_not_an_error(make: MakeTarget) -> None:
    """The likeliest second run is somebody who forgot they ran it once."""
    assert make('demo').returncode == 0
    second = make('demo')

    assert second.returncode == 0, second.stdout + second.stderr
    assert 'already present' in second.stdout


def test_make_bootstrap_creates_the_named_administrator(make: MakeTarget) -> None:
    """The variables are passed through the recipe's quoting intact, spaces and all."""
    completed = make('bootstrap', EMAIL='dana@example.edu', NAME='Dana Director')

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert 'dana@example.edu' in completed.stdout
    assert str(BOOTSTRAP_ID) in completed.stdout


def test_make_bootstrap_without_an_email_refuses_and_says_how(make: MakeTarget) -> None:
    """A target that silently created an administrator called ``''`` would be worse than one that
    fails."""
    completed = make('bootstrap', NAME='Dana Director')

    assert completed.returncode != 0
    assert 'EMAIL is required' in completed.stdout + completed.stderr


def test_the_demo_teacher_can_be_signed_in_with_after_make_demo(make: MakeTarget, database_url: str) -> None:
    """What the printed credentials promise, checked rather than assumed.

    ``make demo`` prints an address and says any password will do. This confirms the address it
    printed belongs to somebody the application will actually accept -- the gap between "a row was
    written" and "a person can sign in" is where a seeding command usually goes wrong.
    """
    assert make('demo').returncode == 0

    completed = subprocess.run(  # noqa: S603
        [
            _executable('uv'),
            'run',
            '--frozen',
            'python',
            '-c',
            'import sys, asyncio, httpx, re\n'
            'from academy.config import create_app\n'
            'async def main():\n'
            '    app = create_app()\n'
            '    async with app.router.lifespan_context(app):\n'
            '        t = httpx.ASGITransport(app=app)\n'
            '        async with httpx.AsyncClient(transport=t, base_url="http://t") as c:\n'
            '            form = await c.get("/sign-in")\n'
            '            tok = re.search(r\'name="csrf_token" value="([^"]+)"\', form.text).group(1)\n'
            f'            r = await c.post("/sign-in", data={{"email": "{DEMO_TEACHER.email}",'
            ' "password": "x", "csrf_token": tok})\n'
            '            print(r.status_code)\n'
            'asyncio.run(main())',
        ],
        cwd=PROJECT_ROOT,
        env={
            **os.environ,
            ENV_DATABASE_URL: database_url,
            'ACADEMY_PERSISTENCE': 'sqlalchemy',
            'ACADEMY_SECRET_KEY': 'a-test-key',
        },
        capture_output=True,
        text=True,
        timeout=TARGET_TIMEOUT_SECONDS,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert '303' in completed.stdout, completed.stdout
