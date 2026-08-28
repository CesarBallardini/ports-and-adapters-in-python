"""The state the in-memory repositories share, and the transaction that guards it.

An in-memory repository is easy; an in-memory repository with *honest transaction semantics*
is the part that matters. A store that could not roll back would quietly pass every test a
use case wrote about failure, and the dry-run import -- whose whole behaviour is "do the work,
then discard it" -- would appear to work here and destroy data behind the real adapter.

So the store is a set of tables plus a snapshot, and
:class:`~academy.application.ports.outbound.unit_of_work.UnitOfWork` restores the snapshot
unless the block committed. Two conventions make that sound:

* tables hold **private copies**, so a caller cannot mutate stored state by keeping a
  reference to something it saved;
* a rollback installs **copies of the snapshot**, so the snapshot stays usable and rolling
  back twice is not an error.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from types import TracebackType

from academy.domain.academics.course_section import CourseSection
from academy.domain.grades.academic_history import AcademicHistory
from academy.domain.guardianship.guardianship import Guardianship
from academy.domain.people.age_of_majority import AgeOfMajority
from academy.domain.people.person import Person
from academy.domain.shared.ids import GuardianshipId, PersonId, SectionId

# What the store answers before an administrator has configured anything. The
# ``ConfigurationRepository`` port forbids returning ``None``: every guardianship check depends
# on this value, and a system that cannot answer it can answer nothing about access.
#
# A comment rather than an attribute docstring: the check-docstring-first hook reads a string
# literal after a module-level assignment as a second module docstring.
DEFAULT_AGE_OF_MAJORITY = AgeOfMajority(18)


@dataclass(frozen=True, slots=True)
class _Snapshot:
    """The whole store at one instant, as the tables it is made of."""

    people: dict[PersonId, Person]
    sections: dict[SectionId, CourseSection]
    histories: dict[PersonId, AcademicHistory]
    guardianships: dict[GuardianshipId, Guardianship]
    age_of_majority: AgeOfMajority


class MemoryStore:
    """The tables the in-memory repositories read and write.

    Repositories are given the store rather than a table, because a rollback replaces the
    table objects wholesale and a repository holding a direct reference would go on reading
    the discarded one.
    """

    def __init__(self) -> None:
        """Create an empty store with the default configuration."""
        self.people: dict[PersonId, Person] = {}
        self.sections: dict[SectionId, CourseSection] = {}
        self.histories: dict[PersonId, AcademicHistory] = {}
        self.guardianships: dict[GuardianshipId, Guardianship] = {}
        self.age_of_majority: AgeOfMajority = DEFAULT_AGE_OF_MAJORITY

    def snapshot(self) -> _Snapshot:
        """Capture the current state, cheaply.

        The copies are shallow because nothing mutates a stored entity in place: a
        repository write replaces the entry with a fresh private copy, so the entities the
        snapshot references cannot change underneath it.
        """
        return _Snapshot(
            people=dict(self.people),
            sections=dict(self.sections),
            histories=dict(self.histories),
            guardianships=dict(self.guardianships),
            age_of_majority=self.age_of_majority,
        )

    def restore(self, snapshot: _Snapshot) -> None:
        """Discard everything written since ``snapshot`` was taken."""
        self.people = dict(snapshot.people)
        self.sections = dict(snapshot.sections)
        self.histories = dict(snapshot.histories)
        self.guardianships = dict(snapshot.guardianships)
        self.age_of_majority = snapshot.age_of_majority

    @staticmethod
    def copy_in[T](entity: T) -> T:
        """Take a private copy of an entity on its way into a table."""
        return deepcopy(entity)

    @staticmethod
    def copy_out[T](entity: T) -> T:
        """Hand out a copy, so a caller's later mutations do not reach the table."""
        return deepcopy(entity)


class MemoryUnitOfWork:
    """A transaction over a :class:`MemoryStore`.

    Satisfies :class:`~academy.application.ports.outbound.unit_of_work.UnitOfWork`.
    """

    def __init__(self, store: MemoryStore) -> None:
        """Bind the transaction to the store it will guard."""
        self._store = store
        self._snapshot: _Snapshot | None = None
        self._active = False

    async def __aenter__(self) -> MemoryUnitOfWork:
        """Begin the transaction.

        Raises:
            RuntimeError: If this unit of work is already active. Nesting is not supported.
        """
        if self._active:
            raise RuntimeError('unit of work is already active; nesting is not supported')
        self._active = True
        self._snapshot = self._store.snapshot()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """End the transaction, rolling back unless :meth:`commit` was called.

        Returns ``None`` rather than ``False`` deliberately: either suppresses nothing, and
        an exception raised inside the block must reach the inbound adapter to be translated
        into a status (ADR-0012).
        """
        await self.rollback()
        self._snapshot = None
        self._active = False

    async def commit(self) -> None:
        """Make every change in this transaction durable.

        Writes already landed in the tables, so committing is precisely the act of giving up
        the snapshot that could undo them. No constraint check happens here: this backend has
        no deferred constraints, and the repositories raise ``ConflictError`` at the write.
        """
        self._snapshot = None

    async def rollback(self) -> None:
        """Discard every change in this transaction.

        Idempotent, and a no-op after a commit -- which is what lets a dry-run import roll
        back unconditionally without first asking whether anything was written.
        """
        if self._snapshot is not None:
            self._store.restore(self._snapshot)
