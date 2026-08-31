"""The relational schema, declared with SQLAlchemy Core.

Tables live here and the domain lives untouched elsewhere; they are married in
:mod:`~academy.adapters.outbound.persistence.sqlalchemy.mappers` (ADR-0006). Nothing in this
file imports a domain class for any reason other than naming a column's type.

**This is not the source of the schema.** Alembic migrations are (ADR-0006), and they run from
an empty database in every environment, tests included -- so the schema under test is the schema
that will be deployed. This metadata is what the mappers bind to and what a migration is written
*against*; ``create_all`` is never called, anywhere.

Every column is nullable only where the domain permits absence. A schema that allowed a
transcript with no student, or a section with no term, would be describing a domain other than
this one.
"""

from __future__ import annotations

from sqlalchemy import Column, Date, DateTime, MetaData, String, Table, Text

from academy.adapters.outbound.persistence.sqlalchemy.types import (
    AgeOfMajorityColumn,
    CredentialIdsColumn,
    EmailColumn,
    EnrollmentsColumn,
    GradeEntriesColumn,
    IdColumn,
    ImportContextColumn,
    ImportResultColumn,
    RolesColumn,
    TermColumn,
)
from academy.application.jobs import JobId
from academy.domain.shared.ids import GuardianshipId, PersonId, SectionId, SubjectId

# Naming every constraint, so that a migration can drop one by name on both databases. Without
# this, SQLite and PostgreSQL invent different names and an `alembic downgrade` works on one of
# them -- which is the kind of difference that is only ever discovered in a hurry.
#
# Comments rather than attribute docstrings: the check-docstring-first hook reads a string
# literal after a module-level assignment as a second module docstring.
NAMING_CONVENTION = {
    'ix': 'ix_%(column_0_label)s',
    'uq': 'uq_%(table_name)s_%(column_0_name)s',
    'ck': 'ck_%(table_name)s_%(constraint_name)s',
    'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s',
    'pk': 'pk_%(table_name)s',
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)

people = Table(
    'people',
    metadata,
    Column('id', IdColumn(PersonId), primary_key=True),
    # Unique because email is the login identifier, and the repositories enforce it too. Both,
    # deliberately: the constraint is the truth, and the repository check is what turns a
    # database error into the `ConflictError` the port promises.
    Column('email', EmailColumn, nullable=False, unique=True),
    Column('full_name', String(200), nullable=False),
    Column('birth_date', Date, nullable=False),
    Column('roles', RolesColumn, nullable=False),
    Column('held_credentials', CredentialIdsColumn, nullable=False),
)

sections = Table(
    'course_sections',
    metadata,
    Column('id', IdColumn(SectionId), primary_key=True),
    Column('subject_id', IdColumn(SubjectId), nullable=False),
    Column('term', TermColumn, nullable=False),
    Column('teacher_id', IdColumn(PersonId), nullable=False, index=True),
    Column('enrollments', EnrollmentsColumn, nullable=False),
)

histories = Table(
    'academic_histories',
    metadata,
    # The student's own id, because a transcript is identified by its student: there is exactly
    # one per student and no separate key exists in the domain.
    Column('student_id', IdColumn(PersonId), primary_key=True),
    Column('entries', GradeEntriesColumn, nullable=False),
)

guardianships = Table(
    'guardianships',
    metadata,
    Column('id', IdColumn(GuardianshipId), primary_key=True),
    Column('guardian_id', IdColumn(PersonId), nullable=False, index=True),
    Column('ward_id', IdColumn(PersonId), nullable=False, index=True),
)

configuration = Table(
    'configuration',
    metadata,
    # A single row, pinned by a fixed key. The alternative -- a table with one row and no key --
    # invites a second row that nothing would ever read, and a system that cannot answer what
    # the age of majority is can answer nothing about access.
    Column('key', String(50), primary_key=True),
    Column('age_of_majority', AgeOfMajorityColumn, nullable=False),
)

import_jobs = Table(
    'import_jobs',
    metadata,
    Column('id', IdColumn(JobId), primary_key=True),
    Column('kind', String(32), nullable=False),
    Column('storage_key', String(256), nullable=False),
    Column('submitted_by', String(36), nullable=False, index=True),
    Column('submitted_at', DateTime(timezone=True), nullable=False),
    Column('status', String(16), nullable=False, index=True),
    Column('result', ImportResultColumn, nullable=True),
    Column('failure_reason', Text, nullable=True),
    Column('context', ImportContextColumn, nullable=False),
)
