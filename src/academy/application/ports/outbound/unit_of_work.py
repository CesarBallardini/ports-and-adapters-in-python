"""The transaction boundary port.

**Async** (ADR-0005): committing waits on the database.

A use case is the unit of work. Every write it performs either lands or does not, and the
use case says where that boundary is -- not the repository, and certainly not the router.
``docs/03-sequence-diagrams.md`` §6 is the clearest case: deleting a course section detaches
the section from every enrolled student's transcript in a loop, and a failure halfway must
leave no history half-detached and no section deleted.
"""

from __future__ import annotations

from types import TracebackType
from typing import Protocol, runtime_checkable


@runtime_checkable
class UnitOfWork(Protocol):
    """An atomic transaction, used as an async context manager.

    The intended shape at every call site::

        async with self._uow:
            section = await self._sections.get(section_id)
            ...
            await self._uow.commit()

    Leaving the block without committing rolls back. That default is deliberate: forgetting
    to commit loses work visibly on the next read, whereas forgetting to roll back would
    commit a half-finished operation silently, and only one of those two failures announces
    itself.
    """

    async def __aenter__(self) -> UnitOfWork:
        """Begin the transaction.

        Returns:
            This unit of work, so the context manager can be bound with ``as`` if wanted.

        Raises:
            RuntimeError: If the unit of work is already active. Nesting is not supported;
                a use case that needs a second boundary is a use case that should be two.
        """
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """End the transaction, rolling back unless :meth:`commit` was called.

        Must not suppress exceptions: a domain error raised inside the block has to reach
        the inbound adapter to be translated into a status (ADR-0012), and a unit of work
        that swallowed it would turn a 409 into a silent success.
        """
        ...

    async def commit(self) -> None:
        """Make every change in this transaction durable.

        Raises:
            ConflictError: If the commit violates a uniqueness constraint. Constraint
                violations surface here, not at the repository call that caused them,
                because that is genuinely when the database checks them -- and a use case
                that assumed otherwise would be correct only until the first deferred
                constraint.
        """
        ...

    async def rollback(self) -> None:
        """Discard every change in this transaction.

        Idempotent: rolling back an already-finished transaction is not an error, which is
        what lets a dry-run import (``docs/03-sequence-diagrams.md`` §7) roll back
        unconditionally without first checking whether anything was written.
        """
        ...
