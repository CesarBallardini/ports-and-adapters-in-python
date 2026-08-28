"""The import job: the application's own small state machine.

This is the one stateful thing the application layer owns outright. It is deliberately *not*
in the domain: an import run is a fact about how data got into the system, not a fact about
academic records. A registrar would recognise a grade, a plan and a graduation; they would
not recognise a job.

Its lifecycle is drawn in ``docs/04-state-diagrams.md`` §2, and it exists only because
imports above a size threshold run in another process (ADR-0009). Below the threshold no job
is ever created -- a state machine that coordinates two processes has nothing to do when
there is only one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import NewType

from academy.application.dtos import ImportResultDto
from academy.application.errors import ApplicationError

#: Identity of an import job. Opaque to everything except the repository that stores it.
JobId = NewType('JobId', str)


class ImportKind(Enum):
    """Which importer a job should run.

    The job carries the *kind* rather than a reference to a use case object, because the job
    is data that outlives the process that created it. The worker resolves the kind back to
    a use case when it picks the job up.
    """

    PEOPLE = 'people'
    SUBJECTS = 'subjects'
    ENROLLMENTS = 'enrollments'
    GRADE_SHEET = 'grade_sheet'


class JobStatus(Enum):
    """Where a job is in its lifecycle."""

    PENDING = 'pending'
    RUNNING = 'running'
    DONE = 'done'
    FAILED = 'failed'

    @property
    def is_terminal(self) -> bool:
        """Whether no further transition is possible.

        The htmx progress fragment polls until this is true, so it is the single place that
        decides when polling stops.
        """
        return self in {JobStatus.DONE, JobStatus.FAILED}


class JobStateError(ApplicationError):
    """Raised on an invalid job state transition."""


@dataclass(slots=True)
class ImportJob:
    """One queued import, from submission to terminal state.

    Attributes:
        id: Identity of the job.
        kind: Which importer to run.
        storage_key: Where the uploaded bytes were put, through the ``FileStorage`` port.
            The queue carries only this job's id -- never the payload -- so a large upload
            does not have to fit through a message broker.
        submitted_by: The actor who uploaded the file. Re-checked when the job runs, because
            authorization must not be frozen at submission time.
        submitted_at: When the job was accepted.
        status: Current lifecycle state.
        result: The import report, once the run has finished.
        failure_reason: Why the run failed, if it did.
        context: Extra parameters the importer needs, such as the target section id.
    """

    id: JobId
    kind: ImportKind
    storage_key: str
    submitted_by: str
    submitted_at: datetime
    status: JobStatus = JobStatus.PENDING
    result: ImportResultDto | None = None
    failure_reason: str | None = None
    context: dict[str, str] = field(default_factory=dict)

    def mark_running(self) -> None:
        """Move the job from pending to running.

        Raises:
            JobStateError: If the job is not pending. Guarding this is what makes the
                transition safe to attempt from more than one worker: the second attempt
                fails loudly instead of running the import twice.
        """
        if self.status is not JobStatus.PENDING:
            raise JobStateError(f'cannot start a job in state {self.status.value}')
        self.status = JobStatus.RUNNING

    def mark_done(self, result: ImportResultDto) -> None:
        """Record a completed run and its report.

        ``DONE`` means the run finished, not that every row was accepted -- a run that
        rejected rows is still done, and ``result.ok`` is the separate question.

        Raises:
            JobStateError: If the job is not running.
        """
        if self.status is not JobStatus.RUNNING:
            raise JobStateError(f'cannot complete a job in state {self.status.value}')
        self.status = JobStatus.DONE
        self.result = result

    def mark_failed(self, reason: str) -> None:
        """Record a run that could not produce a report at all.

        Reserved for failures that prevent any row from being considered -- an unreadable
        file, a lost payload. Rejected rows are not failures; they belong in the result.

        Raises:
            JobStateError: If the job has already reached a terminal state.
        """
        if self.status.is_terminal:
            raise JobStateError(f'cannot fail a job in state {self.status.value}')
        self.status = JobStatus.FAILED
        self.failure_reason = reason
