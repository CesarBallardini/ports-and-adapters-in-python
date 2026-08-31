"""A queue that runs the job immediately, in the caller's process.

For a deployment with no worker -- a CLI run, a small installation, a demo. It satisfies the
same port, so nothing above it changes; what changes is that `enqueue` returns after the work
is done rather than before it starts.

The honesty this costs is worth stating: with this adapter a large import blocks its caller,
which is the very thing ADR-0009's threshold exists to avoid. It is the right adapter when
there is nowhere else for the work to go, and the wrong one behind a web server.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from academy.application.jobs import JobId

# What the queue calls to run one job: the worker's side of ADR-0009, reduced to the only
# thing this adapter needs from it.
#
# Comments rather than attribute docstrings: the check-docstring-first hook reads a string
# literal after a module-level assignment as a second module docstring.
type JobRunner = Callable[[JobId], Awaitable[object]]


class InlineJobQueue:
    """Runs each submitted job before returning.

    Satisfies :class:`~academy.application.ports.outbound.job_queue.JobQueue`.
    """

    def __init__(self, run: JobRunner) -> None:
        """Bind the queue to the runner it will call.

        Args:
            run: How to execute one job by id. The composition root passes the use case's
                own ``run_job``, which is the same entry point a real worker calls -- one
                implementation, one set of rules, whichever adapter is wired.
        """
        self._run = run

    async def enqueue(self, job_id: JobId) -> None:
        """Run the job now.

        The port says an implementation must not deliver the id before the transaction that
        created the job commits. This one *is* the delivery, so the composition root must
        wire it where that holds -- after the submitting transaction, not inside it.
        """
        await self._run(job_id)
