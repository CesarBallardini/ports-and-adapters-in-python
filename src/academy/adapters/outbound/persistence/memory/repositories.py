"""In-memory repository adapters over a :class:`MemoryStore`.

A first slice: the repositories the grading use cases and the ``RelationshipResolver`` need.
The rest follow the same shape.

What they are *not* is test doubles (ADR-0014). They enforce the same asymmetry the ports
specify -- a lookup that finds nothing returns ``None``, an update of something absent raises
``NotFoundError`` -- they raise ``ConflictError`` on the same uniqueness constraints, and they
return the orderings the ports promise. Where they and the SQLAlchemy adapter disagree, the
contract suite says which one is wrong.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Hashable
from typing import ClassVar

from academy.adapters.outbound.persistence.memory.store import MemoryStore, Table
from academy.application.errors import ConflictError, NotFoundError
from academy.domain.academics.course_section import CourseSection
from academy.domain.academics.term import Term
from academy.domain.grades.academic_history import AcademicHistory
from academy.domain.guardianship.guardianship import Guardianship
from academy.domain.people.age_of_majority import AgeOfMajority
from academy.domain.people.person import Person
from academy.domain.shared.ids import CredentialId, GuardianshipId, PersonId, SectionId, SubjectId

# What a listing is ordered by: an aggregate's natural key, rendered as strings and ending in
# its id so that two records sharing a natural key still have a total order. Strings rather
# than the domain's own types because the orderings the ports promise are lexicographic, and a
# tuple mixing `str` with `UUID` would not compare at all.
#
# Comments rather than attribute docstrings: the check-docstring-first hook reads a string
# literal after a module-level assignment as a second module docstring.
type SortKey = tuple[str, ...]


class _MemoryRepository[E, IdT: Hashable](ABC):
    """The five storage operations every aggregate needs, over one table of the store.

    Subclasses say which table, how to read an aggregate's identity, and what its natural
    key is; everything else -- the copy discipline and the ``None``/``NotFoundError``
    asymmetry -- is decided once, here.

    ``IdT`` is bound to ``Hashable`` because it indexes a :class:`Table`, which is a dictionary
    underneath. The domain's id types satisfy it; the bound is what stops a future aggregate
    keyed by something unhashable from failing at the first write instead of at the type check.
    """

    entity_name: ClassVar[str] = 'entity'
    """How the aggregate is named in error messages.

    ``ClassVar``, because it is configuration a subclass overrides once for the whole class,
    not state an instance carries. Without it, the annotation would invite an instance to set
    its own and two repositories over the same table could name it differently.
    """

    def __init__(self, store: MemoryStore) -> None:
        """Bind the repository to the store it reads and writes.

        The *store*, not the table: every repository then reaches its table the same way, and
        the tables stay the store's to hand out. Rollback no longer replaces them -- it puts
        individual rows back (see :mod:`~academy.adapters.outbound.persistence.memory.store`)
        -- so holding a table would now be safe, and still says less about where state lives.
        """
        self._store = store

    @abstractmethod
    def _table(self) -> Table[IdT, E]:
        """The store table this repository owns."""

    @abstractmethod
    def _identity(self, entity: E) -> IdT:
        """The aggregate's identifier."""

    @abstractmethod
    def _sort_key(self, entity: E) -> SortKey:
        """The natural key ``list_all`` orders by, ending in the id to break ties."""

    async def get(self, entity_id: IdT) -> E | None:
        """Fetch one aggregate by identity, or ``None`` if no such record exists."""
        entity = self._table().get(entity_id)
        return MemoryStore.copy_out(entity) if entity is not None else None

    async def add(self, entity: E) -> None:
        """Store an aggregate that is not yet stored.

        Raises:
            ConflictError: If the identity is taken or a uniqueness constraint is violated.
        """
        identity = self._identity(entity)
        if identity in self._table():
            raise ConflictError(f'{self.entity_name} {identity!s} already exists')
        self._table()[identity] = MemoryStore.copy_in(entity)

    async def save(self, entity: E) -> None:
        """Persist changes to an already-stored aggregate.

        Raises:
            NotFoundError: If no aggregate with that identity is stored.
            ConflictError: If the change violates a uniqueness constraint.
        """
        identity = self._identity(entity)
        if identity not in self._table():
            raise NotFoundError(self.entity_name, identity)
        self._table()[identity] = MemoryStore.copy_in(entity)

    async def delete(self, entity_id: IdT) -> None:
        """Remove an aggregate.

        Raises:
            NotFoundError: If no aggregate with that identity is stored.
        """
        if entity_id not in self._table():
            raise NotFoundError(self.entity_name, entity_id)
        del self._table()[entity_id]

    async def list_all(self) -> list[E]:
        """Every stored aggregate, in natural-key order."""
        return self._copies(sorted(self._table().values(), key=self._sort_key))

    def _sorted(self, entities: list[E]) -> list[E]:
        """Order a subset the same way :meth:`list_all` orders the whole table."""
        return self._copies(sorted(entities, key=self._sort_key))

    @staticmethod
    def _copies(entities: list[E]) -> list[E]:
        """Hand out private copies of a result set."""
        return [MemoryStore.copy_out(entity) for entity in entities]


class MemoryPersonRepository(_MemoryRepository[Person, PersonId]):
    """People, with their roles and held credentials, ordered by email."""

    entity_name = 'person'

    def _table(self) -> Table[PersonId, Person]:
        return self._store.people

    def _identity(self, entity: Person) -> PersonId:
        return entity.id

    def _sort_key(self, entity: Person) -> SortKey:
        return (entity.email.value, str(entity.id))

    async def add(self, entity: Person) -> None:
        """Store a new person.

        Raises:
            ConflictError: If the identity is taken, or another person already uses this
                email address.
        """
        self._require_email_unused(entity)
        await super().add(entity)

    async def save(self, entity: Person) -> None:
        """Persist changes to a stored person.

        Existence is checked before uniqueness, so that saving an unknown person reports the
        unknown person rather than an email conflict with whoever holds that address.

        Raises:
            NotFoundError: If no person with that identity is stored.
            ConflictError: If another person already uses this email address.
        """
        if entity.id not in self._store.people:
            raise NotFoundError(self.entity_name, entity.id)
        self._require_email_unused(entity)
        await super().save(entity)

    def _require_email_unused(self, entity: Person) -> None:
        """Enforce that email, the login identifier, is unique across people."""
        for other in self._store.people.values():
            if other.id != entity.id and other.email == entity.email:
                raise ConflictError(f'email {entity.email.value} is already registered')

    async def by_email(self, email: str) -> Person | None:
        """Fetch the person with this email address, matching case-insensitively.

        The argument is normalised the way ``Email`` normalises on construction, rather than
        being turned into an ``Email`` -- callers include an authentication adapter holding
        whatever a user typed, and a malformed address here is a miss, not an error.
        """
        wanted = email.strip().lower()
        for person in self._store.people.values():
            if person.email.value == wanted:
                return MemoryStore.copy_out(person)
        return None

    async def by_ids(self, person_ids: list[PersonId]) -> list[Person]:
        """Fetch several people at once, in the order asked, omitting ids that are unknown."""
        found = [person for person_id in person_ids if (person := self._store.people.get(person_id)) is not None]
        return self._copies(found)

    async def holders_of(self, credential_id: CredentialId) -> list[Person]:
        """Every person holding this credential."""
        return self._sorted([p for p in self._store.people.values() if p.holds_credential(credential_id)])


class MemorySectionRepository(_MemoryRepository[CourseSection, SectionId]):
    """Course sections and the enrollments inside them, ordered by term then subject."""

    entity_name = 'course section'

    def _table(self) -> Table[SectionId, CourseSection]:
        return self._store.sections

    def _identity(self, entity: CourseSection) -> SectionId:
        return entity.id

    def _sort_key(self, entity: CourseSection) -> SortKey:
        return (entity.term.label(), str(entity.subject_id), str(entity.id))

    async def for_teacher(self, teacher_id: PersonId) -> list[CourseSection]:
        """Every section this person teaches, most recent term first."""
        return self._recent_first([s for s in self._store.sections.values() if s.teacher_id == teacher_id])

    async def for_student(self, student_id: PersonId) -> list[CourseSection]:
        """Every section this person is enrolled in, most recent term first."""
        return self._recent_first([s for s in self._store.sections.values() if s.is_enrolled(student_id)])

    async def in_term(self, term: Term) -> list[CourseSection]:
        """Every section running in this term."""
        return self._sorted([s for s in self._store.sections.values() if s.term == term])

    async def subjects_enrolled_by(self, student_id: PersonId) -> frozenset[SubjectId]:
        """The subjects this student already has a section for."""
        return frozenset(s.subject_id for s in self._store.sections.values() if s.is_enrolled(student_id))

    async def teaching_students_of(self, teacher_id: PersonId) -> frozenset[PersonId]:
        """Every student enrolled in any section this person teaches."""
        return frozenset(
            student_id
            for section in self._store.sections.values()
            if section.teacher_id == teacher_id
            for student_id in section.students()
        )

    def _recent_first(self, sections: list[CourseSection]) -> list[CourseSection]:
        """Order by term descending, ties broken by id ascending.

        Two passes rather than one reversed sort: reversing would also reverse the tiebreak,
        and the port promises a *total* order, not merely a recent-first one.
        """
        by_id = sorted(sections, key=lambda section: str(section.id))
        return self._copies(sorted(by_id, key=lambda section: section.term, reverse=True))


class MemoryAcademicHistoryRepository(_MemoryRepository[AcademicHistory, PersonId]):
    """Student transcripts, keyed by the student they belong to."""

    entity_name = 'academic history'

    def _table(self) -> Table[PersonId, AcademicHistory]:
        return self._store.histories

    def _identity(self, entity: AcademicHistory) -> PersonId:
        return entity.student_id

    def _sort_key(self, entity: AcademicHistory) -> SortKey:
        return (str(entity.student_id),)

    async def get_or_create(self, student_id: PersonId) -> AcademicHistory:
        """Fetch this student's transcript, storing an empty one if they have none.

        The created history is stored, not merely returned, so that the caller's subsequent
        ``save`` finds it. Recording a student's first grade is exactly that sequence.
        """
        if student_id not in self._store.histories:
            self._store.histories[student_id] = AcademicHistory(student_id)
        return MemoryStore.copy_out(self._store.histories[student_id])

    async def for_students(self, student_ids: list[PersonId]) -> list[AcademicHistory]:
        """Fetch several transcripts at once, in the order asked, omitting students with none."""
        found = [
            history for student_id in student_ids if (history := self._store.histories.get(student_id)) is not None
        ]
        return self._copies(found)


class MemoryGuardianshipRepository(_MemoryRepository[Guardianship, GuardianshipId]):
    """Stored guardian-to-ward links.

    Storage answers only who is linked to whom. Whether a link currently *applies* is a
    domain rule computed on read, so these queries deliberately do not filter by the ward's
    age -- doing so would move that rule into storage and make the transition that happens on
    a birthday invisible to the test suite.
    """

    entity_name = 'guardianship'

    def _table(self) -> Table[GuardianshipId, Guardianship]:
        return self._store.guardianships

    def _identity(self, entity: Guardianship) -> GuardianshipId:
        return entity.id

    def _sort_key(self, entity: Guardianship) -> SortKey:
        return (str(entity.guardian_id), str(entity.ward_id), str(entity.id))

    async def wards_of(self, guardian_id: PersonId) -> list[Guardianship]:
        """Every stored guardianship where this person is the guardian."""
        return self._sorted([g for g in self._store.guardianships.values() if g.guardian_id == guardian_id])

    async def guardians_of(self, ward_id: PersonId) -> list[Guardianship]:
        """Every stored guardianship where this person is the ward."""
        return self._sorted([g for g in self._store.guardianships.values() if g.ward_id == ward_id])


class MemoryConfigurationRepository:
    """System-wide settings an administrator can change at runtime.

    Not a :class:`_MemoryRepository`: configuration is a single row, not an aggregate table,
    and giving it ``add``/``delete`` would invent operations the port does not have.
    """

    def __init__(self, store: MemoryStore) -> None:
        """Bind the repository to its store."""
        self._store = store

    async def age_of_majority(self) -> AgeOfMajority:
        """The current global age of majority, defaulting rather than returning ``None``."""
        return self._store.age_of_majority

    async def set_age_of_majority(self, age: AgeOfMajority) -> None:
        """Set the global age of majority."""
        self._store.age_of_majority = age
