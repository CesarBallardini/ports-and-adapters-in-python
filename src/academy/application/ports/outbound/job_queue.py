"""The background work port.

**Async** (ADR-0005): handing work to another process is I/O.

The port is one method wide, and that is the whole point. Everything interesting about a
queued import -- what it does, what it validates, what it reports -- lives in the use case
that the worker will call. The queue only carries an identifier.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from academy.application.jobs import JobId


@runtime_checkable
class JobQueue(Protocol):
    """A way to ask for work to happen somewhere else, later."""

    async def enqueue(self, job_id: JobId) -> None:
        """Submit a stored job for execution.

        Only the **id** crosses this port. The uploaded payload has already been written
        through :class:`~academy.application.ports.outbound.file_storage.FileStorage`, and
        the job record already holds the key. Putting the bytes on the queue instead would
        couple the maximum importable file size to a message broker's payload limit, for no
        gain -- the worker needs a database connection regardless.

        Implementations must be safe to call inside the same transaction that created the
        job, and must not deliver the id before that transaction commits. A worker that
        received the id first would look for a job that does not exist yet.

        Args:
            job_id: Identity of a job already persisted in the ``PENDING`` state.
        """
        ...
