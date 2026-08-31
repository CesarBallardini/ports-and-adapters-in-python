"""Marrying the untouched domain classes to the tables (ADR-0006).

This is the file the whole repository is arranged to make possible. The domain classes above it
inherit from nothing of SQLAlchemy's, declare no columns, and do not know this module exists;
the tables beside it know no domain behaviour. One call per aggregate joins them, and the
verbatim diff against ``multi-tenant-python`` still prints nothing afterwards.

Three things are worth knowing before changing anything here.

**Private attributes are mapped directly.** ``Person._roles`` and ``CourseSection._enrollments``
are what the aggregates actually hold; their public faces are read-only views. Mapping the
private name is what lets the aggregate keep its invariants instead of being reshaped into
whatever an ORM finds convenient -- which is ADR-0006's second consequence, in practice.

**Value objects arrive two ways.** One that spans columns is a ``composite``; one that fits a
column is a ``TypeDecorator``. Neither is instrumented, which is why both work on the domain's
frozen, slotted classes where a mapped collection cannot (ADR-0017).

**Mapping happens once per process.** :func:`configure_mappers_once` is idempotent because a
second ``map_imperatively`` on the same class raises, and a test suite that builds two
containers would otherwise fail on the second.
"""

from __future__ import annotations

from sqlalchemy.orm import composite, registry

from academy.adapters.outbound.persistence.sqlalchemy import tables
from academy.domain.academics.course_section import CourseSection
from academy.domain.grades.academic_history import AcademicHistory
from academy.domain.guardianship.guardianship import Guardianship
from academy.domain.people.person import Person
from academy.domain.people.personal_data import PersonalData

# The registry every mapping is made in. Module-level because a mapping is a fact about a class,
# not about a session or a deployment: mapping the same class into two registries is what
# produces "class is already mapped" at the least convenient moment.
#
# Comments rather than attribute docstrings: the check-docstring-first hook reads a string
# literal after a module-level assignment as a second module docstring.
mapper_registry = registry(metadata=tables.metadata)

_configured = False


def configure_mappers_once() -> None:
    """Bind the domain classes to the tables, at most once per process.

    Idempotent by design. The composition root calls it while building a container, and a test
    suite builds many containers; ``map_imperatively`` raises on a second call for the same
    class, so the guard is what makes the second container work.
    """
    global _configured  # noqa: PLW0603 - the flag guards a process-wide, one-time side effect
    if _configured:
        return

    mapper_registry.map_imperatively(
        Person,
        tables.people,
        properties={
            'personal': composite(PersonalData, tables.people.c.full_name, tables.people.c.birth_date),
            # The private sets, not the read-only views over them. `Person.roles` is a property
            # returning a frozenset; mapping that would be mapping a computed value.
            '_roles': tables.people.c.roles,
            '_held_credentials': tables.people.c.held_credentials,
        },
    )

    mapper_registry.map_imperatively(
        CourseSection,
        tables.sections,
        properties={'_enrollments': tables.sections.c.enrollments},
    )

    mapper_registry.map_imperatively(
        AcademicHistory,
        tables.histories,
        properties={
            # A transcript is identified by its student: `AcademicHistory.id` *is* the student's
            # id, and the column is named for what it holds rather than for the attribute.
            'id': tables.histories.c.student_id,
            '_entries': tables.histories.c.entries,
        },
    )

    mapper_registry.map_imperatively(Guardianship, tables.guardianships)

    _configured = True
