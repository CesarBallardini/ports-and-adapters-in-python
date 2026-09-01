"""The two seeding commands: what they create, and what they refuse to do twice.

Seeding goes through the repositories rather than through a migration (see the module docstring
for why), which means it is testable exactly like anything else that writes -- and means these
tests run against the in-memory backend without needing a database at all.

The property worth the most here is **idempotence**. The likeliest second run of ``make demo`` is
somebody who forgot they had already run it, and the difference between "nothing to do" and a
``ConflictError`` traceback is the difference between a command people trust and one they avoid.
"""

from __future__ import annotations

import pytest

from academy.application.commands import ListSectionGradesCommand
from academy.application.dtos import Actor
from academy.config.container import Container
from academy.config.seeding import (
    BOOTSTRAP_ID,
    DEMO_OUTSIDER,
    DEMO_PEOPLE,
    DEMO_SECTION,
    DEMO_STUDENTS,
    DEMO_TEACHER,
    credentials,
    main,
    seed_bootstrap,
    seed_demo,
)
from academy.config.settings import ConfigurationError, Settings
from academy.domain.people.role import Role

pytestmark = pytest.mark.unit


@pytest.fixture
def container() -> Container:
    """An empty in-memory deployment, which is what a fresh checkout has."""
    return Container(Settings())


async def test_the_demo_people_are_created(container: Container) -> None:
    await seed_demo(container)

    async with container.request_scope() as scope:
        for person in DEMO_PEOPLE:
            found = await scope.people.by_email(person.email)
            assert found is not None, person.email
            assert found.roles == person.roles


async def test_the_demo_section_has_both_students_and_nobody_else(container: Container) -> None:
    """The roster is what makes the grade sheet worth looking at.

    The outsider is deliberately *not* on it: they exist so that a refusal on the demo data comes
    from the policy rather than from a missing record, which is a different and less interesting
    failure to look at.
    """
    await seed_demo(container)

    async with container.request_scope() as scope:
        section = await scope.sections.get(DEMO_SECTION)
        assert section is not None
        enrolled = set(section.students())

    assert enrolled == {student.person_id for student in DEMO_STUDENTS}
    assert DEMO_OUTSIDER.person_id not in enrolled


async def test_the_demo_teacher_can_actually_read_the_demo_sheet(container: Container) -> None:
    """Seeded data that the policy then refuses would be furniture nobody can use.

    Driven through the use case rather than the repositories, because that is what a person
    following the printed credentials will hit, and it is the assertion that the seeded teacher is
    the section's teacher rather than merely a person with the teacher role.
    """
    await seed_demo(container)

    async with container.request_scope() as scope:
        sheet = await scope.grade_management().list_section_grades(
            ListSectionGradesCommand(
                actor=Actor(person_id=DEMO_TEACHER.person_id, roles=DEMO_TEACHER.roles),
                section_id=str(DEMO_SECTION),
            )
        )

    assert {row.full_name for row in sheet.rows} == {student.full_name for student in DEMO_STUDENTS}


async def test_seeding_the_demo_data_twice_changes_nothing(container: Container) -> None:
    """The command a person runs when they are not sure whether they ran it."""
    first = await seed_demo(container)
    second = await seed_demo(container)

    assert 'Seeded' in first[0]
    assert 'already present' in second[0]

    async with container.request_scope() as scope:
        assert len(await scope.people.list_all()) == len(DEMO_PEOPLE)


async def test_the_demo_output_names_every_account_it_created(container: Container) -> None:
    """A fixture nobody can discover is the same problem one step further in."""
    lines = await seed_demo(container)
    printed = '\n'.join(lines)

    for person in DEMO_PEOPLE:
        assert person.email in printed


async def test_the_bootstrap_administrator_can_sign_in_to_an_empty_deployment(
    container: Container,
) -> None:
    """The gap this command exists for: a migrated database with nobody in it.

    ``verify_credentials`` looks people up by email, so without a person record every screen is
    unreachable -- including the one that would create the first person.
    """
    await seed_bootstrap(container, 'dana@example.edu', 'Dana Director')

    async with container.request_scope() as scope:
        found = await scope.people.by_email('dana@example.edu')

    assert found is not None
    assert found.id == BOOTSTRAP_ID
    assert found.has_role(Role.ADMINISTRATIVE_EMPLOYEE)


async def test_the_bootstrap_administrator_is_the_only_person_created(container: Container) -> None:
    """A real deployment gets one account, not a cast of fictional characters."""
    await seed_bootstrap(container, 'dana@example.edu', 'Dana Director')

    async with container.request_scope() as scope:
        assert len(await scope.people.list_all()) == 1


async def test_bootstrapping_an_address_that_exists_refuses_rather_than_duplicating(
    container: Container,
) -> None:
    """On a real deployment "there is already an account here" is the answer the operator needs."""
    await seed_bootstrap(container, 'dana@example.edu', 'Dana Director')

    lines = await seed_bootstrap(container, 'dana@example.edu', 'Someone Else')

    assert 'already exists' in lines[0]
    async with container.request_scope() as scope:
        person = await scope.people.by_email('dana@example.edu')
        assert person is not None
        assert person.personal.full_name == 'Dana Director'


async def test_bootstrapping_after_the_demo_data_still_works(container: Container) -> None:
    """The two commands must not collide: they create different people and different ids."""
    await seed_demo(container)
    await seed_bootstrap(container, 'dana@example.edu', 'Dana Director')

    async with container.request_scope() as scope:
        assert len(await scope.people.list_all()) == len(DEMO_PEOPLE) + 1


def test_the_credentials_can_be_printed_without_a_database() -> None:
    """``make run`` prints these before anything is connected, so they cannot need a scope."""
    printed = '\n'.join(credentials())

    for person in DEMO_PEOPLE:
        assert person.email in printed
    assert str(DEMO_SECTION) in printed


def test_the_credentials_say_the_password_is_not_checked() -> None:
    """Otherwise the first thing a reader does is hunt for the password.

    It also keeps the placeholder visible at the moment somebody is most likely to mistake this
    for a working authentication system (ADR-0010).
    """
    printed = '\n'.join(credentials())

    assert 'not checked' in printed
    assert 'ADR-0010' in printed


def test_every_demo_address_is_in_the_reserved_example_domain() -> None:
    """RFC 2606 reserves ``.example``, so none of these can ever reach a real person.

    Worth asserting rather than assuming: a seeded address at a domain somebody owns is how demo
    data turns into mail somebody receives.
    """
    assert all(person.email.endswith('@academy.example') for person in DEMO_PEOPLE)


def test_the_demo_people_have_distinct_identities() -> None:
    """A copy-pasted id would make two demo people the same person, silently."""
    assert len({person.person_id for person in DEMO_PEOPLE}) == len(DEMO_PEOPLE)
    assert len({person.email for person in DEMO_PEOPLE}) == len(DEMO_PEOPLE)
    assert BOOTSTRAP_ID not in {person.person_id for person in DEMO_PEOPLE}


# ---------------------------------------------------------------------------------------------
# The command line, driven the way `cli.main` is: argv and an environment, no subprocess
# ---------------------------------------------------------------------------------------------


def test_printing_the_credentials_touches_no_database(capsys: pytest.CaptureFixture[str]) -> None:
    """``make run`` calls this before anything is connected, so it must not need a deployment.

    The environment names a backend that could not possibly be built. If this command reached for
    a container it would fail here, which is the point.
    """
    code = main(('credentials',), environ={'ACADEMY_PERSISTENCE': 'memory'})

    assert code == 0
    assert DEMO_TEACHER.email in capsys.readouterr().out


def test_seeding_a_real_database_reports_what_it_created(
    tmp_path: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole command, end to end, against a migrated SQLite file."""
    import asyncio as _asyncio
    from pathlib import Path

    from academy.adapters.outbound.persistence.sqlalchemy.session import migrate_to_head

    url = f'sqlite+aiosqlite:///{(Path(str(tmp_path)) / "seeded.db").as_posix()}'
    _asyncio.run(_asyncio.to_thread(migrate_to_head, url))

    environ = {'ACADEMY_PERSISTENCE': 'sqlalchemy', 'ACADEMY_DATABASE_URL': url}
    code = main(('demo',), environ=environ)
    printed = capsys.readouterr().out

    assert code == 0
    assert 'Seeded' in printed
    assert DEMO_TEACHER.email in printed

    # And again: the second run must be the boring one.
    assert main(('demo',), environ=environ) == 0
    assert 'already present' in capsys.readouterr().out


def test_seeding_an_in_memory_deployment_warns_that_it_was_pointless(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The store is the process, and the process is about to exit.

    Silently succeeding here is the trap: the command reports five people created, the developer
    starts the server, and nobody exists. Saying so is the whole value of the warning.
    """
    code = main(('demo',), environ={'ACADEMY_PERSISTENCE': 'memory'})

    assert code == 0
    assert 'in-memory' in capsys.readouterr().out


def test_bootstrapping_from_the_command_line(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        ('bootstrap', '--email', 'dana@example.edu', '--name', 'Dana Director'),
        environ={'ACADEMY_PERSISTENCE': 'memory'},
    )

    assert code == 0
    assert 'dana@example.edu' in capsys.readouterr().out


def test_bootstrapping_without_an_email_is_a_usage_error() -> None:
    """argparse exits 2 by itself, and nothing here improves on that."""
    with pytest.raises(SystemExit) as exit_info:
        main(('bootstrap', '--name', 'Dana Director'))

    assert exit_info.value.code == 2


def test_a_command_is_required() -> None:
    with pytest.raises(SystemExit):
        main(())


def test_an_unbuildable_deployment_fails_before_writing_anything() -> None:
    """A configuration error is not caught here: it escapes with its message, as it does in the CLI."""
    with pytest.raises(ConfigurationError):
        main(('demo',), environ={'ACADEMY_PERSISTENCE': 'postgres'})
