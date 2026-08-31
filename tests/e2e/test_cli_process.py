"""The first end-to-end tests: the real CLI, in a real process, seen the way a shell sees it.

Everything else in the suite calls ``main()`` in this interpreter. That checks the adapter and
misses the three things only a subprocess can check: that ``python -m academy`` resolves at all,
that the exit status reaches the shell rather than being swallowed by a wrapper, and that the
environment -- not a dict a test handed in -- is what configures the process.

Those are exactly the failures a unit test cannot have. An entry point that raises on import, a
``main`` that returns a code nobody passes to ``SystemExit``, a settings read that goes to the
wrong place: all three pass every other tier and break the first time someone types the command.

This tier is excluded from ``make test`` by the marker (``-m 'not e2e'``) and run by
``make test-e2e``, because it spawns interpreters and is slow by construction.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from uuid import UUID

import pytest

from academy.adapters.inbound.cli import ExitCode
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
from academy.config.settings import ENV_DATABASE_URL, ENV_PERSISTENCE
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
MATHEMATICS = SubjectId(UUID(int=20))

TEACHER_EMAIL = 'tess@academy.test'
STUDENT_EMAIL = 'sam@academy.test'

# Long enough that a machine under load does not fail the build, short enough that a hang is a
# failure rather than a wait. The suite's own `timeout = 300` would otherwise kill the test
# without saying which command never returned.
COMMAND_TIMEOUT_SECONDS = 60


class Cli:
    """The installed command, run as a separate process against a seeded database."""

    def __init__(self, environ: dict[str, str]) -> None:
        """Record the environment the child process is to be started with."""
        self._environ = environ

    def run(self, *argv: str) -> subprocess.CompletedProcess[str]:
        """Run ``python -m academy`` with these arguments.

        An argv list and never a shell string: nothing here is interpolated by a shell, which is
        why the ``S603`` subprocess rules are waived for this directory rather than for the
        codebase.
        """
        return subprocess.run(
            [sys.executable, '-m', 'academy', *argv],
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            env={**os.environ, **self._environ},
            check=False,
        )


@pytest.fixture(scope='module')
def database(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """One migrated, seeded SQLite database for the whole module.

    Module-scoped because every test here only reads: spawning an interpreter per test is already
    the expensive part, and re-migrating for each one would double it for no isolation anyone
    needs.
    """
    path = tmp_path_factory.mktemp('e2e') / 'academy.db'
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
                await people.add(_person(STUDENT, STUDENT_EMAIL, 'Sam Student', Role.STUDENT))
                section = CourseSection(id=SECTION, subject_id=MATHEMATICS, term=Term(2026, 1), teacher_id=TEACHER)
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


@pytest.fixture
def cli(database: str, tmp_path: Path) -> Cli:
    """The CLI, pointed at the seeded database through the environment."""
    return Cli(
        {
            ENV_PERSISTENCE: 'sqlalchemy',
            ENV_DATABASE_URL: database,
            'ACADEMY_UPLOAD_DIRECTORY': str(tmp_path / 'uploads'),
        }
    )


def test_the_module_entry_point_resolves_and_prints_its_help() -> None:
    """``python -m academy`` exists. Nothing else in the suite can say so."""
    completed = subprocess.run(
        [sys.executable, '-m', 'academy', '--help'],
        capture_output=True,
        text=True,
        timeout=COMMAND_TIMEOUT_SECONDS,
        check=False,
    )

    assert completed.returncode == ExitCode.OK
    assert 'usage: academy' in completed.stdout


def test_a_successful_command_exits_zero(cli: Cli) -> None:
    completed = cli.run('grades', 'list', str(SECTION), '--as', TEACHER_EMAIL)

    assert completed.returncode == ExitCode.OK
    assert 'Sam Student' in completed.stdout


def test_json_output_is_parseable_from_stdout_alone(cli: Cli) -> None:
    """A script pipes stdout. Anything else on it -- a log line, a warning -- breaks the parse."""
    completed = cli.run('grades', 'list', str(SECTION), '--as', TEACHER_EMAIL, '--json')

    payload = json.loads(completed.stdout)
    assert payload['section_id'] == str(SECTION)


@pytest.mark.parametrize(
    ('argv', 'expected'),
    [
        pytest.param(('config', 'show'), ExitCode.OK, id='ok'),
        pytest.param(('grades',), ExitCode.USAGE, id='usage'),
        pytest.param(('grades', 'list', str(SECTION), '--as', 'nobody@academy.test'), ExitCode.NOT_FOUND, id='404'),
        pytest.param(
            ('grades', 'record', str(SECTION), str(STUDENT), '9', '--as', STUDENT_EMAIL),
            ExitCode.FORBIDDEN,
            id='403',
        ),
        pytest.param(
            ('grades', 'record', str(SECTION), str(STUDENT), '11', '--as', TEACHER_EMAIL),
            ExitCode.VALIDATION,
            id='422',
        ),
    ],
)
def test_the_exit_status_a_shell_sees_is_the_documented_one(cli: Cli, argv: tuple[str, ...], expected: int) -> None:
    """The whole reason exit codes are an interface (ADR-0019, ADR-0020).

    Every other tier asserts what ``main`` *returns*. Only this one asserts what a shell *gets*,
    which is a different claim: it also covers ``__main__`` handing the value to ``SystemExit``.
    """
    assert cli.run(*argv).returncode == expected


def test_a_configuration_error_refuses_to_start(cli: Cli) -> None:
    """Exit 9, from the real process, before anything could have been read or written."""
    broken = Cli({ENV_PERSISTENCE: 'postgres'})

    completed = broken.run('config', 'show')

    assert completed.returncode == ExitCode.CONFIGURATION
    assert 'memory, sqlalchemy' in completed.stderr


def test_a_failure_goes_to_stderr_and_leaves_stdout_empty(cli: Cli) -> None:
    completed = cli.run('grades', 'record', str(SECTION), str(STUDENT), '9', '--as', STUDENT_EMAIL)

    assert completed.stdout == ''
    assert completed.stderr.startswith('forbidden: ')


def test_the_process_reads_its_configuration_from_the_environment(cli: Cli, database: str) -> None:
    """The one thing a dict passed to ``main()`` can never prove."""
    completed = cli.run('config', 'show', '--json')

    payload = json.loads(completed.stdout)
    assert payload['persistence'] == 'sqlalchemy'
    assert payload['database_url'] == database


def test_a_clean_dry_run_exits_zero(cli: Cli, tmp_path: Path) -> None:
    """The shape the README recommends for CI, run the way CI would run it.

    Safe against this module's shared database precisely because it is a dry run: the import
    happens in full and is then rolled back, so the next test sees the same rows this one did.
    """
    sheet = _sheet(tmp_path / 'clean.csv', f'{STUDENT_EMAIL},8')

    completed = cli.run('import', 'run', str(sheet), '--section', str(SECTION), '--dry-run', '--as', TEACHER_EMAIL)

    assert completed.returncode == ExitCode.OK


def test_a_dry_run_that_would_reject_a_row_exits_ten(cli: Cli, tmp_path: Path) -> None:
    """Exit 10 from a real process, which is the whole reason ``--dry-run`` is worth scheduling.

    A registrar can find out what a file *would* do before it does it, and a CI job can fail on
    the answer without anyone parsing prose. Asserted here rather than only in process, because
    the promise being made is to a shell.
    """
    sheet = _sheet(tmp_path / 'broken.csv', f'{STUDENT_EMAIL},8', 'nobody@academy.test,9')

    completed = cli.run('import', 'run', str(sheet), '--section', str(SECTION), '--dry-run', '--as', TEACHER_EMAIL)

    assert completed.returncode == ExitCode.IMPORT_INCOMPLETE
    assert 'rejected=1' in completed.stdout


def _sheet(path: Path, *rows: str) -> Path:
    """Write a grade sheet with the importer's own header."""
    path.write_text('student_email,grade\n' + ''.join(f'{row}\n' for row in rows), encoding='utf-8')
    return path
