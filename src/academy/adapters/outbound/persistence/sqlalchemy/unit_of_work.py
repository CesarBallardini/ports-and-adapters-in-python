"""The transaction boundary, over an async SQLAlchemy session.

The counterpart to the in-memory unit of work, and the reason that one had to be honest about
rollback: everything the contract suite asserts about transactions is asserted against both, so
a use case that works against a dictionary works against PostgreSQL for the same reasons.

What differs is where the guarantee comes from. The in-memory adapter builds atomicity out of an
undo log and explicitly does not provide isolation (ADR-0017); this one gets both from the
database, which is what a database is for.
"""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession


class SqlAlchemyUnitOfWork:
    """A transaction over an :class:`~sqlalchemy.ext.asyncio.AsyncSession`.

    Satisfies :class:`~academy.application.ports.outbound.unit_of_work.UnitOfWork`.

    The session is the scope's, not this object's: repositories in the same scope read and write
    through it, and the transaction this opens is what makes their writes one unit. A unit of
    work that owned its own session would leave the repositories outside the boundary it claims
    to draw.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Bind the transaction to the session it will guard."""
        self._session = session
        self._active = False

    async def __aenter__(self) -> SqlAlchemyUnitOfWork:
        """Begin the transaction.

        Raises:
            RuntimeError: If this unit of work is already active. Nesting is not supported --
                the port says so, and a session that is already in a transaction would
                otherwise silently join it, making the inner block's rollback undo the outer
                block's writes.
        """
        if self._active:
            raise RuntimeError('unit of work is already active; nesting is not supported')

        self._active = True
        # `begin()` only where one is not already open: an AsyncSession starts a transaction
        # implicitly on first use, and asking for a second is an error rather than a nesting.
        if not self._session.in_transaction():
            await self._session.begin()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """End the transaction, rolling back unless :meth:`commit` was called.

        Returns ``None`` rather than ``False`` deliberately: either suppresses nothing, and an
        exception raised inside the block must reach the inbound adapter to be translated into
        a status (ADR-0012).
        """
        await self.rollback()
        self._active = False

    async def commit(self) -> None:
        """Make every change in this transaction durable.

        Raises:
            ConflictError: The port promises this for a constraint violation. It is raised by
                the repositories at the write that causes it, because SQLAlchemy flushes there
                and that is where the offending row is still known. A violation that only a
                deferred constraint could produce would surface here instead -- neither
                database in use defers any.
        """
        await self._session.commit()

    async def rollback(self) -> None:
        """Discard every change in this transaction.

        Idempotent, and a no-op after a commit -- which is what lets a dry-run import roll back
        unconditionally without first asking whether anything was written.
        """
        await self._session.rollback()
