"""Putting rows into a database on purpose: a first administrator, and the demo fixtures.

Two operations that look similar and are not. **Bootstrapping** solves a real problem a durable
deployment has exactly once -- a freshly migrated database has no people, so nobody can sign in,
so nobody can reach the screen that would create the first person. **Demo data** is fictional
furniture for looking at the application, and no deployment needs it.

Neither is a migration, and that is a decision rather than an omission:

* **ADR-0018 splits the roles.** Migrations connect as the role that *owns the schema*;
  the application connects as the one that owns the rows. Seeding application data with the DDL
  role crosses precisely the boundary that split exists to draw.
* **A migration reaches one adapter.** Rule 4 gives every port an in-memory implementation as
  well as a SQLAlchemy one. Going through the repositories means ``make demo`` works against
  either, and means this module never learns which backend it is talking to.
* **Fixtures are not history.** A migration is immutable once shipped; demo data is edited
  whenever somebody wants a third student. Those do not belong in the same chain.
* **The domain gets a say.** ``section.enroll(...)`` enforces what an ``INSERT`` would not.

Alembic remains right for reference data a schema is meaningless without -- and this application
has none. ``ConfigurationRepository.age_of_majority`` returns a documented default when no row
exists, deliberately, so a blank database is already a working one.

Run it with ``make demo`` / ``make bootstrap``, or directly::

    python -m academy.config.seeding demo
    python -m academy.config.seeding bootstrap --email you@example.edu --name 'Your Name'
    python -m academy.config.seeding credentials
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Final
from uuid import UUID

from academy.application.errors import ConflictError
from academy.config.container import Container
from academy.config.settings import Environ, PersistenceBackend
from academy.domain.academics.course_section import CourseSection
from academy.domain.academics.term import Term
from academy.domain.people.email import Email
from academy.domain.people.person import Person
from academy.domain.people.personal_data import PersonalData
from academy.domain.people.role import Role
from academy.domain.shared.ids import PersonId, SectionId, SubjectId


@dataclass(frozen=True, slots=True)
class SeedPerson:
    """One person to create, and the line printed to say they exist."""

    person_id: PersonId
    email: str
    full_name: str
    born: date
    roles: frozenset[Role] = field(default_factory=frozenset)

    def to_person(self) -> Person:
        """Build the domain object, so the domain validates it rather than the database."""
        return Person(
            id=self.person_id,
            email=Email(self.email),
            personal=PersonalData(full_name=self.full_name, birth_date=self.born),
            roles=set(self.roles),
        )

    def describe(self) -> str:
        """One line: who they are and what they may do."""
        held = ', '.join(sorted(role.value for role in self.roles)) or 'no roles'
        return f'  {self.email:<28} {self.full_name:<16} ({held})'


# Fictional on purpose, and `.example` is the reserved domain for exactly this (RFC 2606), so
# none of these addresses can ever belong to anybody.
DEMO_TEACHER: Final = SeedPerson(
    PersonId(UUID(int=0xD3_0010)),
    'teacher@academy.example',
    'Tess Teacher',
    date(1985, 3, 14),
    frozenset({Role.TEACHER}),
)
DEMO_REGISTRAR: Final = SeedPerson(
    PersonId(UUID(int=0xD3_0011)),
    'registrar@academy.example',
    'Adele Admin',
    date(1979, 7, 2),
    frozenset({Role.ADMINISTRATIVE_EMPLOYEE}),
)
DEMO_STUDENT: Final = SeedPerson(
    PersonId(UUID(int=0xD3_0020)),
    'student@academy.example',
    'Sam Student',
    date(2006, 1, 20),
    frozenset({Role.STUDENT}),
)
DEMO_SECOND_STUDENT: Final = SeedPerson(
    PersonId(UUID(int=0xD3_0021)),
    'student2@academy.example',
    'Sol Student',
    date(2005, 11, 5),
    frozenset({Role.STUDENT}),
)
DEMO_OUTSIDER: Final = SeedPerson(
    PersonId(UUID(int=0xD3_0030)),
    'outsider@academy.example',
    'Nemo Nobody',
    date(1990, 6, 6),
)

# Each one shows a different answer from the same page: the teacher may record, the registrar may
# read and never write (ADR-0016), the students appear on the sheet, and the outsider is refused
# by the policy rather than by not existing.
DEMO_PEOPLE: Final[tuple[SeedPerson, ...]] = (
    DEMO_TEACHER,
    DEMO_REGISTRAR,
    DEMO_STUDENT,
    DEMO_SECOND_STUDENT,
    DEMO_OUTSIDER,
)

DEMO_SECTION: Final = SectionId(UUID(int=0xD3_0100))
DEMO_SUBJECT: Final = SubjectId(UUID(int=0xD3_0101))
DEMO_TERM: Final = Term(2026, 1)

DEMO_STUDENTS: Final = (DEMO_STUDENT, DEMO_SECOND_STUDENT)

# The id a bootstrap administrator gets when none is given. Fixed rather than random so that
# `ACADEMY_BOOTSTRAP_ADMIN` can name it, and so running the command twice is recognisably the
# same person rather than a second one.
BOOTSTRAP_ID: Final = PersonId(UUID(int=0xB0_0001))


async def seed_demo(container: Container) -> list[str]:
    """Create the demo people and one section they share.

    Idempotent by checking rather than by catching: running it twice reports that the data is
    already there and changes nothing, because the likeliest second run is somebody who forgot
    they had done it once.

    Returns:
        The lines to print. Returned rather than printed so a test can read them and so the
        caller decides where they go.
    """
    async with container.request_scope() as scope:
        if await scope.people.by_email(DEMO_TEACHER.email) is not None:
            return ['Demo data is already present; nothing to do.', *credentials()]

        unit_of_work = scope.unit_of_work()
        async with unit_of_work:
            for person in DEMO_PEOPLE:
                await scope.people.add(person.to_person())

            section = CourseSection(
                id=DEMO_SECTION,
                subject_id=DEMO_SUBJECT,
                term=DEMO_TERM,
                teacher_id=DEMO_TEACHER.person_id,
            )
            for student in DEMO_STUDENTS:
                section.enroll(student.person_id)
            await scope.sections.add(section)

            await unit_of_work.commit()

    return [f'Seeded {len(DEMO_PEOPLE)} people and one course section.', *credentials()]


async def seed_bootstrap(container: Container, email: str, full_name: str) -> list[str]:
    """Create the first administrator, so an empty deployment has a way in.

    The problem this solves is real and happens exactly once: a migrated database has no people,
    ``verify_credentials`` looks people up by email, and every administrative screen is therefore
    unreachable. ``ACADEMY_BOOTSTRAP_ADMIN`` (ADR-0022) covers the case where a session already
    names an id; it cannot help somebody standing at the sign-in form, because that form asks for
    an address and there is no record to match.

    Unlike the demo data this is meant for a **real** deployment, so it creates exactly one
    person, with the address the operator chose, and nothing else.

    Args:
        container: The wired composition root.
        email: The address this administrator will sign in with.
        full_name: Their name, as it should appear.

    Returns:
        The lines to print.

    Raises:
        ConflictError: If somebody already uses that address. Deliberately not swallowed: on a
            real deployment "there is already an account here" is the answer the operator needs,
            not a silent success.
    """
    async with container.request_scope() as scope:
        existing = await scope.people.by_email(email)
        if existing is not None:
            return [f'{email} already exists; no administrator was created.']

        unit_of_work = scope.unit_of_work()
        async with unit_of_work:
            await scope.people.add(
                SeedPerson(
                    person_id=BOOTSTRAP_ID,
                    email=email,
                    full_name=full_name,
                    # Not a real date of birth and not pretending to be one. An administrator's
                    # age is never asked -- only a student's is, against the age of majority --
                    # so this is the one field with nothing to say.
                    born=date(1970, 1, 1),
                    roles=frozenset({Role.ADMINISTRATIVE_EMPLOYEE}),
                ).to_person()
            )
            await unit_of_work.commit()

    return [
        f'Created administrator {email} ({BOOTSTRAP_ID}).',
        'The password is not checked (ADR-0010): sign in with any password at all.',
    ]


def credentials() -> list[str]:
    """What a developer needs on screen to use the thing they just started.

    The single source for these addresses, so ``make demo`` and ``make run`` cannot print
    different ones.
    """
    return [
        '',
        '  ' + '-' * 72,
        '  DEMO ACCOUNTS -- fictional, and the password is not checked (ADR-0010),',
        '  so sign in with any password at all.',
        '  ' + '-' * 72,
        '',
        *[person.describe() for person in DEMO_PEOPLE],
        '',
        '  Sign in:     http://localhost:8000/sign-in',
        f'  Grade sheet: http://localhost:8000/sections/{DEMO_SECTION}/grades',
        '',
    ]


def _parser() -> argparse.ArgumentParser:
    """The grammar for the seeding utility.

    A second argparse parser in this repository, and deliberately not a subcommand of the
    application's CLI. A CLI handler takes one driving port and never a repository (ADR-0021);
    seeding is repository work with no use case behind it, so putting it there would be the one
    row in that table that breaks the rule the table exists to keep.
    """
    parser = argparse.ArgumentParser(
        prog='python -m academy.config.seeding',
        description='Put rows into the configured database: demo fixtures, or a first administrator.',
    )
    commands = parser.add_subparsers(dest='command', required=True)

    commands.add_parser('demo', help='create the fictional demo people and a section')

    bootstrap = commands.add_parser('bootstrap', help='create the first administrator')
    bootstrap.add_argument('--email', required=True, help='the address they will sign in with')
    bootstrap.add_argument('--name', required=True, help='their full name')

    commands.add_parser('credentials', help='print the demo accounts without creating anything')

    return parser


def main(argv: Sequence[str] | None = None, environ: Environ | None = None) -> int:
    """Run one seeding command.

    Returns:
        ``0`` on success, ``1`` if the deployment could not be built or the write was refused.
    """
    args = _parser().parse_args(argv)

    if args.command == 'credentials':
        _write(credentials())
        return 0

    container = Container.from_env(environ)
    try:
        if args.command == 'demo':
            lines = asyncio.run(seed_demo(container))
        else:
            lines = asyncio.run(seed_bootstrap(container, str(args.email), str(args.name)))
    except ConflictError as error:
        print(f'refused: {error}', file=sys.stderr)
        return 1
    finally:
        asyncio.run(container.aclose())

    if container.settings.persistence is PersistenceBackend.MEMORY:
        lines.append(
            'WARNING: persistence is in-memory, so this was written to a store that has already '
            'been discarded. Set ACADEMY_PERSISTENCE=sqlalchemy to seed a real database.'
        )

    _write(lines)
    return 0


def _write(lines: Sequence[str]) -> None:
    """Print what a command produced."""
    for line in lines:
        print(line)


if __name__ == '__main__':
    raise SystemExit(main())
