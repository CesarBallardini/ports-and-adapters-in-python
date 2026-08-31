"""The state the in-memory repositories share, and the transaction that guards it.

An in-memory repository is easy; an in-memory repository with *honest transaction semantics*
is the part that matters. A store that could not roll back would quietly pass every test a
use case wrote about failure, and the dry-run import -- whose whole behaviour is "do the work,
then discard it" -- would appear to work here and destroy data behind the real adapter.

Rollback is **copy-on-write, per row**. A table about to change a row first hands the active
transaction an undo entry naming that row and what it held; a rollback replays those entries
in reverse. Nothing else in the store is touched, so a transaction can only ever undo its own
writes.

The obvious cheaper design -- snapshot the whole store on entry, restore it on rollback -- is
wrong as soon as two transactions overlap, which is exactly what a served process does. One
request rolling back would restore the store to how it looked before it started and silently
discard everything another request had committed in the meantime. That failure leaves no trace
and no error; it is the reason for the machinery below.

Three conventions make the result sound, and any table or repository added here must keep
them:

* tables hold **private copies**, so a caller cannot mutate stored state by keeping a
  reference to something it saved;
* an undo entry captures the row's value **before** the change, and entries replay in reverse,
  so a row written twice in one transaction goes back to what it held before the first write;
* an undo entry also remembers what the write *left* there, and puts the row back only while
  it still holds that value: a row another transaction has written since is left exactly as
  that writer left it, because undoing it would destroy a committed write;
* a rollback is **idempotent**, and so is a rollback after a commit -- committing is precisely
  the act of discarding the undo log.

**Atomicity, not isolation.** A transaction here is all-or-nothing for the rows it wrote, and
that is the whole of what it promises. It takes no locks and keeps no read view, so a
transaction reads other transactions' uncommitted writes, and two that overlap on the same data
can still reach a state neither would have reached alone -- one deleting the row whose
uniqueness constraint another is checking, say. Serialising them would need a lock held across
``await`` boundaries, which turns the nesting this module already refuses into a deadlock.

That boundary is deliberate and it is where this adapter's honesty ends: it is exactly right for
the test suite, the CLI and a single worker, which is what it is for, and a served process with
real request concurrency wants the SQLAlchemy adapter, whose isolation comes from a database
built to provide it.

Isolation is per :mod:`asyncio` task, because the active transaction is held in a
:class:`~contextvars.ContextVar`: a unit of work entered in one task does not capture writes
made in a task that was already running. Every driver here opens its unit of work and does its
work in the same task, which is what makes that boundary the right one.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, ValuesView
from contextvars import ContextVar, Token
from copy import deepcopy
from types import TracebackType
from typing import Final

from academy.application.jobs import ImportJob, JobId
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

# One undo entry: put one row back the way it was. A closure rather than a record, because the
# only thing anyone does with an entry is call it, and the table that made it is the only code
# that knows how to reverse its own write.
type _Undo = Callable[[], None]

# The transactions currently open, innermost last. Module-level and shared by every store,
# because a ContextVar is meant to live for the life of the module; each entry knows which
# store it belongs to, so several stores in one process do not see each other's transactions.
_OPEN: Final[ContextVar[tuple[_Transaction, ...]]] = ContextVar('academy_memory_transactions', default=())


class _Transaction:
    """The undo log of one unit of work.

    Holds no data of its own: the rows live in the tables, and this is only the record of how
    to put back the ones this transaction changed.
    """

    def __init__(self, store: MemoryStore) -> None:
        """Open a transaction against ``store``, not yet active."""
        self.store = store
        self._undo: list[_Undo] = []
        self._token: Token[tuple[_Transaction, ...]] | None = None

    def activate(self) -> None:
        """Make this the transaction that subsequent writes in this task are recorded against."""
        self._token = _OPEN.set((*_OPEN.get(), self))

    def deactivate(self) -> None:
        """Stop recording. Idempotent, so an aborted entry cannot leave the stack dirty."""
        if self._token is not None:
            _OPEN.reset(self._token)
            self._token = None

    def record(self, undo: _Undo) -> None:
        """Remember how to reverse one write."""
        self._undo.append(undo)

    def undo(self) -> None:
        """Put every row this transaction changed back, most recent change first.

        Draining the log as it goes is what makes a second rollback a no-op rather than a
        second application of the same entries.
        """
        while self._undo:
            self._undo.pop()()

    def forget(self) -> None:
        """Give up the ability to undo -- which is all committing is, for this backend."""
        self._undo.clear()


class Table[K: Hashable, V]:
    """One table of the store, journalling every change into the open transaction.

    Internal to the memory adapter: repositories read and write it, nothing outside does. It
    deliberately offers only the handful of operations a repository needs, so that a mutation
    cannot arrive by a route that forgets to journal.

    ``K`` is bound to ``Hashable`` because a row is found by dictionary lookup. Every key here
    is one of the domain's id types, which are hashable by construction; the bound is what
    makes that a checked fact rather than a convention, and it is the same bound
    ``_MemoryRepository`` puts on the identity it indexes by.

    A row is never ``None``, which is what lets an undo entry use ``None`` to mean "this row
    did not exist" without a sentinel.
    """

    def __init__(self, store: MemoryStore) -> None:
        """Bind the table to the store whose transactions it journals into."""
        self._store = store
        self._rows: dict[K, V] = {}

    def __contains__(self, key: K) -> bool:
        """Whether a row is stored under this key."""
        return key in self._rows

    def __getitem__(self, key: K) -> V:
        """The row stored under this key.

        Raises:
            KeyError: If there is none. Repositories check membership first; this is the
                programming-error path, not the not-found path the ports specify.
        """
        return self._rows[key]

    def __setitem__(self, key: K, value: V) -> None:
        """Store a row, recording how to put back whatever was there."""
        prior = self._rows.get(key)
        self._rows[key] = value
        self._journal(key, prior=prior, written=value)

    def __delitem__(self, key: K) -> None:
        """Remove a row, recording how to put it back."""
        prior = self._rows[key]
        del self._rows[key]
        self._journal(key, prior=prior, written=None)

    def get(self, key: K) -> V | None:
        """The row stored under this key, or ``None``."""
        return self._rows.get(key)

    def values(self) -> ValuesView[V]:
        """Every stored row, in insertion order.

        A view rather than an ``Iterable``: callers filter it, count it and iterate it more
        than once, none of which an iterator would survive, and every one of which the type
        now promises. It is a live view, which is why repositories copy what they hand out.

        The repositories sort what they hand out, so this order is an implementation detail
        and not something a port promises.
        """
        return self._rows.values()

    def _journal(self, key: K, *, prior: V | None, written: V | None) -> None:
        """Hand the open transaction an entry that reverses this one write.

        Args:
            key: The row that changed.
            prior: What it held before, or ``None`` if it did not exist.
            written: What this write left there, or ``None`` for a delete. Kept so the undo
                can tell whether the row is still the one it wrote.
        """
        transaction = self._store.open_transaction()
        if transaction is None:
            # Outside a unit of work, a write is immediate and final. Seeding a store and a
            # read-only path that repairs a cache both do this, and neither has anything to
            # roll back to.
            return

        transaction.record(lambda: self._restore(key, prior=prior, written=written))

    def _restore(self, key: K, *, prior: V | None, written: V | None) -> None:
        """Put one row back to ``prior``, unless someone else has written it since.

        The check is what makes rollback safe under overlap. Restoring unconditionally would
        reinstate this transaction's *predecessor* over a value another transaction committed
        in the meantime -- destroying a committed write with no error and no trace, which is
        the whole failure the per-row journal exists to prevent and which a whole-store
        snapshot got wrong at a larger scale.

        Identity, not equality: :class:`~academy.domain.shared.entity.Entity` compares by id,
        so an equality check would read another transaction's replacement of the same record
        as our own write and undo it. Rows enter the tables as private copies, so the object
        actually stored is unique to the write that put it there.

        A row that has moved on is left exactly as its writer left it. This transaction's
        change to it is already lost -- overwritten by that writer -- so there is nothing to
        undo, and the rollback stays silent rather than raising into an ``__aexit__`` that is
        very often already handling an exception.
        """
        current = self._rows.get(key)
        if current is not written:
            return

        if prior is None:
            self._rows.pop(key, None)
        else:
            self._rows[key] = prior


class MemoryStore:
    """The tables the in-memory repositories read and write.

    Repositories are given the store rather than a table so that every one of them reaches the
    tables the same way, and so a repository added later cannot hold a reference that outlives
    what it points at.
    """

    def __init__(self) -> None:
        """Create an empty store with the default configuration."""
        self.people: Table[PersonId, Person] = Table(self)
        self.sections: Table[SectionId, CourseSection] = Table(self)
        self.histories: Table[PersonId, AcademicHistory] = Table(self)
        self.guardianships: Table[GuardianshipId, Guardianship] = Table(self)
        self.jobs: Table[JobId, ImportJob] = Table(self)
        self._age_of_majority = DEFAULT_AGE_OF_MAJORITY

    @property
    def age_of_majority(self) -> AgeOfMajority:
        """The global age of majority.

        A single row rather than a table, and journalled the same way: an administrator
        changing it inside a transaction that then fails must not leave the new value behind,
        because every guardianship check in the system reads it.
        """
        return self._age_of_majority

    @age_of_majority.setter
    def age_of_majority(self, age: AgeOfMajority) -> None:
        transaction = self.open_transaction()
        prior = self._age_of_majority
        self._age_of_majority = age
        if transaction is not None:
            transaction.record(lambda: self._restore_age_of_majority(prior=prior, written=age))

    def _restore_age_of_majority(self, *, prior: AgeOfMajority, written: AgeOfMajority) -> None:
        """Put the configuration row back, unless someone else has set it since.

        The same rule as :meth:`Table._restore`, for the one value that is not in a table.
        """
        if self._age_of_majority is written:
            self._age_of_majority = prior

    def open_transaction(self) -> _Transaction | None:
        """The innermost transaction open against *this* store in this task, if any."""
        for transaction in reversed(_OPEN.get()):
            if transaction.store is self:
                return transaction
        return None

    def begin(self) -> _Transaction:
        """Open and activate a transaction against this store.

        Raises:
            RuntimeError: If a transaction is already open on this store in this task. The
                port says nesting is not supported, and the unit of work can only enforce
                that for its own instance -- two *different* units of work over one store
                would otherwise nest silently, and every write the outer one made while the
                inner was open would be journalled into the inner log and undone by the
                inner rollback. Two nested ``request_scope()`` blocks are exactly that.
        """
        if self.open_transaction() is not None:
            raise RuntimeError('a unit of work is already open on this store; nesting is not supported')

        transaction = _Transaction(self)
        transaction.activate()
        return transaction

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
        self._transaction: _Transaction | None = None

    async def __aenter__(self) -> MemoryUnitOfWork:
        """Begin the transaction.

        Raises:
            RuntimeError: If this unit of work is already active. Nesting is not supported.
        """
        if self._transaction is not None:
            raise RuntimeError('unit of work is already active; nesting is not supported')
        self._transaction = self._store.begin()
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
        transaction = self._transaction
        if transaction is not None:
            transaction.undo()
            transaction.deactivate()
        self._transaction = None

    async def commit(self) -> None:
        """Make every change in this transaction durable.

        Writes already landed in the tables, so committing is precisely the act of discarding
        the undo log that could reverse them. No constraint check happens here: this backend
        has no deferred constraints, and the repositories raise ``ConflictError`` at the write.

        The transaction stays open until the block ends, so a write made *after* committing is
        journalled like any other and is rolled back on the way out -- it belongs to no
        committed transaction.
        """
        if self._transaction is not None:
            self._transaction.forget()

    async def rollback(self) -> None:
        """Discard every change in this transaction.

        Reverses only this transaction's own writes, and only where they still stand: a row
        another transaction has written since is left exactly as that writer left it, because
        this transaction's change to it is already gone and undoing further would destroy a
        committed write.

        That is atomicity, not isolation -- see this module's docstring for where the
        difference bites.

        Idempotent, and a no-op after a commit -- which is what lets a dry-run import roll
        back unconditionally without first asking whether anything was written.
        """
        if self._transaction is not None:
            self._transaction.undo()
