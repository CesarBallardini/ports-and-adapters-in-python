"""A queue that remembers ids until somebody asks for them.

The adapter a worker polls in-process, and the one a test uses to assert that a job was
queued rather than run. Production-grade for a single process (ADR-0014): the queue is a
list, and its limitation is that it dies with the process that holds it.
"""

from __future__ import annotations

from collections import deque

from academy.application.jobs import JobId


class MemoryJobQueue:
    """Enqueued job ids, in the order they arrived.

    Satisfies :class:`~academy.application.ports.outbound.job_queue.JobQueue`.
    """

    def __init__(self) -> None:
        """Start with nothing queued."""
        self._pending: deque[JobId] = deque()

    async def enqueue(self, job_id: JobId) -> None:
        """Submit a stored job for execution."""
        self._pending.append(job_id)

    def pop(self) -> JobId | None:
        """Take the next queued id, or ``None``.

        Not part of the port: the port is what the *application* needs, and the application
        only ever submits. Draining is a worker's business, and a worker is an inbound
        adapter -- which is why this method exists on the class and not on the protocol.
        """
        return self._pending.popleft() if self._pending else None

    def queued(self) -> list[JobId]:
        """Every id still waiting, oldest first."""
        return list(self._pending)
