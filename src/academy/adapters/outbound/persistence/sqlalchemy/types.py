"""Column types that carry the domain's value objects into SQL and back.

Every conversion between a column and a value object happens here, once. That is what keeps
the mapping in :mod:`~academy.adapters.outbound.persistence.sqlalchemy.mappers` readable: it
says which column is which attribute, and nothing about how a ``Term`` is spelled in a
database.

Two rules hold throughout:

* **The domain validates on the way in.** Every type reconstructs through the value object's
  own constructor, so an ``Email`` read back is re-normalised and re-checked, and a ``Grade``
  outside 0..10 raises where it is read rather than where it is used. A row corrupted by a bad
  migration fails loudly, at the repository, naming the value.
* **The stored form is portable.** Identifiers are the canonical UUID string, terms are their
  own label, collections are JSON arrays -- nothing depends on a dialect's own types, because
  the same schema runs on SQLite and PostgreSQL (ADR-0007) and the two must not diverge.

Collections are stored as JSON on the aggregate's own row (ADR-0017): the domain's value
objects are ``slots=True``, so SQLAlchemy cannot instrument them, and these four collections
are values inside an aggregate rather than entities of their own.
"""

from __future__ import annotations

import json
from typing import Protocol, Self, TypedDict, cast

from sqlalchemy import Dialect, String, Text, TypeDecorator

from academy.application.dtos import ImportResultDto, RowError
from academy.domain.academics.course_section import Enrollment
from academy.domain.academics.term import Term
from academy.domain.grades.grade import Grade
from academy.domain.grades.grade_entry import GradeEntry
from academy.domain.people.age_of_majority import AgeOfMajority
from academy.domain.people.email import Email
from academy.domain.people.role import Role
from academy.domain.shared.ids import CredentialId, PersonId, SectionId, SubjectId


class RawEnrollment(TypedDict):
    """One enrollment, as it sits in a section's JSON array."""

    student_id: str


class RawGradeEntry(TypedDict):
    """One attempt, as it sits in a transcript's JSON array.

    A ``TypedDict`` rather than ``dict[str, str | int | None]``: the union would type every key
    as every possibility, so reading ``grade`` would need narrowing that says nothing. This
    names the shape a migration would have to rewrite, and the decoder below needs no casts.
    """

    subject_id: str
    term: str
    grade: int
    source_section_id: str | None


class Identifier(Protocol):
    """What :class:`IdColumn` needs of an identifier: a canonical string, and a way back.

    A structural protocol rather than the domain's own ``_Id`` base, which is private to
    :mod:`academy.domain.shared.ids`. An adapter reaching for another package's underscore is
    reaching for something that was not offered; this says what is actually required, and every
    id class satisfies it without being told.
    """

    @classmethod
    def from_str(cls, raw: str) -> Self:
        """Rebuild the identifier from its canonical string form."""
        ...

    def __str__(self) -> str:
        """Render the canonical string form."""
        ...


class IdColumn[IdT: Identifier](TypeDecorator[IdT]):
    """One of the domain's typed identifiers, stored as its canonical UUID string.

    Text rather than a native UUID column, because the same schema has to run on SQLite and
    PostgreSQL and only one of them has the type. The cost is a wider index; the gain is that
    the two databases hold identical bytes, which is what makes a dump portable and a
    cross-dialect bug impossible to blame on storage.
    """

    impl = String(36)
    cache_ok = True

    def __init__(self, id_type: type[IdT]) -> None:
        """Bind the column to the identifier class it reconstructs.

        Args:
            id_type: The concrete id class -- ``PersonId``, ``SectionId`` and so on. Passing
                it explicitly is what keeps a ``SectionId`` from being read out of a column
                that holds people.
        """
        super().__init__()
        self._id_type = id_type

    def process_bind_param(self, value: IdT | None, dialect: Dialect) -> str | None:
        """Render an identifier as its canonical string."""
        return None if value is None else str(value)

    def process_result_value(self, value: str | None, dialect: Dialect) -> IdT | None:
        """Rebuild the identifier, of the right type."""
        return None if value is None else self._id_type.from_str(value)


class EmailColumn(TypeDecorator[Email]):
    """An email address, normalised by the value object on the way in *and* out."""

    impl = String(320)
    cache_ok = True

    def process_bind_param(self, value: Email | None, dialect: Dialect) -> str | None:
        """Store the normalised form the value object guarantees."""
        return None if value is None else value.value

    def process_result_value(self, value: str | None, dialect: Dialect) -> Email | None:
        """Rebuild, and re-validate: a malformed address in the database is an error here."""
        return None if value is None else Email(value)


class TermColumn(TypeDecorator[Term]):
    """An academic term, stored as its own label -- ``2026-T1``.

    The label rather than two integer columns, because a term is one value and every query
    that touches it wants the whole thing. It sorts correctly as text within a century, and
    the repositories that need ordering sort in Python against ``Term``'s own ordering rather
    than trusting the string.
    """

    impl = String(16)
    cache_ok = True

    def process_bind_param(self, value: Term | None, dialect: Dialect) -> str | None:
        """Render the term as its canonical label."""
        return None if value is None else value.label()

    def process_result_value(self, value: str | None, dialect: Dialect) -> Term | None:
        """Parse the label back, through the value object's own validation."""
        if value is None:
            return None
        year, _, number = value.partition('-T')
        return Term(int(year), int(number))


class AgeOfMajorityColumn(TypeDecorator[AgeOfMajority]):
    """The global age of majority, stored as whole years."""

    impl = String(3)
    cache_ok = True

    def process_bind_param(self, value: AgeOfMajority | None, dialect: Dialect) -> str | None:
        """Store the number of years."""
        return None if value is None else str(value.years)

    def process_result_value(self, value: str | None, dialect: Dialect) -> AgeOfMajority | None:
        """Rebuild, and re-validate that it is positive."""
        return None if value is None else AgeOfMajority(int(value))


class _JsonColumn[T, RawT](TypeDecorator[T]):
    """Base for the collections that are stored as a JSON array on their aggregate's row.

    Generic over **both** the domain collection and the shape of one serialised element, so a
    subclass says exactly what its rows look like -- ``str`` for a role, a three-key dict for a
    grade entry -- instead of every element being `Any` on the way through.

    ``Text`` and ``json`` rather than a dialect's JSON type, for the reason ADR-0017 records:
    the alternative is dialect-specific SQL in the one layer that must behave identically on
    both databases.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: T | None, dialect: Dialect) -> str:
        """Serialise, treating a missing collection as an empty one."""
        return json.dumps([] if value is None else self._encode(value))

    def process_result_value(self, value: str | None, dialect: Dialect) -> T:
        """Deserialise, treating a missing column as an empty collection.

        The one ``cast`` in this package, and it is at the only place types are genuinely lost:
        ``json.loads`` returns ``Any`` by construction. Everything after it is checked, and a
        row whose shape does not match raises in the subclass's ``_decode`` -- at the
        repository, naming the value.
        """
        raw = cast(list[RawT], json.loads(value)) if value else []
        return self._decode(raw)

    def _encode(self, value: T) -> list[RawT]:
        """Turn the collection into JSON-safe data."""
        raise NotImplementedError

    def _decode(self, raw: list[RawT]) -> T:
        """Rebuild the collection from JSON-safe data."""
        raise NotImplementedError


class RolesColumn(_JsonColumn[set[Role], str]):
    """The roles a person holds, as an array of role values."""

    def _encode(self, value: set[Role]) -> list[str]:
        """Store the roles in a stable order, so an unchanged person produces unchanged bytes."""
        return sorted(role.value for role in value)

    def _decode(self, raw: list[str]) -> set[Role]:
        """Rebuild the roles, through the enum's own validation."""
        return {Role(item) for item in raw}


class CredentialIdsColumn(_JsonColumn[set[CredentialId], str]):
    """The credentials a person holds, as an array of identifier strings."""

    def _encode(self, value: set[CredentialId]) -> list[str]:
        """Store the ids in a stable order."""
        return sorted(str(credential_id) for credential_id in value)

    def _decode(self, raw: list[str]) -> set[CredentialId]:
        """Rebuild the identifiers, of the right type."""
        return {CredentialId.from_str(item) for item in raw}


class EnrollmentsColumn(_JsonColumn[list[Enrollment], RawEnrollment]):
    """A section's enrollments, as an array of student identifiers.

    An ``Enrollment`` carries nothing but its student today. It is still stored as an array of
    objects rather than of bare strings, so that the day it carries a date as well, the change
    is a key in this file rather than a migration of every row's shape.
    """

    def _encode(self, value: list[Enrollment]) -> list[RawEnrollment]:
        """Store the enrollments in the order the section holds them."""
        return [{'student_id': str(enrollment.student_id)} for enrollment in value]

    def _decode(self, raw: list[RawEnrollment]) -> list[Enrollment]:
        """Rebuild the enrollments."""
        return [Enrollment(student_id=PersonId.from_str(item['student_id'])) for item in raw]


class GradeEntriesColumn(_JsonColumn[list[GradeEntry], RawGradeEntry]):
    """A transcript's entries, as an array of objects.

    Order is preserved and load-bearing: a transcript is a record of attempts in the order
    they happened, and ``best_grade`` is computed over all of them.
    """

    def _encode(self, value: list[GradeEntry]) -> list[RawGradeEntry]:
        """Store every attempt, including which section it came from -- or that it has none."""
        return [
            {
                'subject_id': str(entry.subject_id),
                'term': entry.term.label(),
                'grade': entry.grade.value,
                'source_section_id': None if entry.source_section_id is None else str(entry.source_section_id),
            }
            for entry in value
        ]

    def _decode(self, raw: list[RawGradeEntry]) -> list[GradeEntry]:
        """Rebuild the entries, re-validating each grade and term."""
        return [
            GradeEntry(
                subject_id=SubjectId.from_str(item['subject_id']),
                term=_term(item['term']),
                grade=Grade(item['grade']),
                source_section_id=(
                    None if item['source_section_id'] is None else SectionId.from_str(item['source_section_id'])
                ),
            )
            for item in raw
        ]


class ImportContextColumn(TypeDecorator[dict[str, str]]):
    """An importer's extra parameters, stored as a JSON object.

    Strings to strings, because the context crosses a queue: a job outlives the process that
    created it, so everything in it has to survive being written down and read back.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: dict[str, str] | None, dialect: Dialect) -> str:
        """Serialise, treating a missing context as an empty one."""
        return json.dumps(value or {}, sort_keys=True)

    def process_result_value(self, value: str | None, dialect: Dialect) -> dict[str, str]:
        """Deserialise, treating a missing column as an empty context."""
        return cast(dict[str, str], json.loads(value)) if value else {}


class ImportResultColumn(TypeDecorator[ImportResultDto]):
    """An import report, stored as JSON on the job that produced it."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: ImportResultDto | None, dialect: Dialect) -> str | None:
        """Serialise the report, or store nothing for a job that has not finished."""
        if value is None:
            return None
        return json.dumps(
            {
                'created': value.created,
                'updated': value.updated,
                'skipped': value.skipped,
                'dry_run': value.dry_run,
                'errors': [{'line': e.line, 'reason': e.reason, 'values': list(e.values)} for e in value.errors],
            }
        )

    def process_result_value(self, value: str | None, dialect: Dialect) -> ImportResultDto | None:
        """Rebuild the report, rejected rows and all."""
        if value is None:
            return None
        raw = json.loads(value)
        return ImportResultDto(
            created=raw['created'],
            updated=raw['updated'],
            skipped=raw['skipped'],
            dry_run=raw['dry_run'],
            errors=tuple(
                RowError(line=e['line'], reason=e['reason'], values=tuple(e['values'])) for e in raw['errors']
            ),
        )


def _term(label: str) -> Term:
    """Parse a term label into its value object."""
    year, _, number = label.partition('-T')
    return Term(int(year), int(number))
