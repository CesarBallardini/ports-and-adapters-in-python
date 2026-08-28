"""The driving port for bulk data loading.

The reason this repository has the shape it does. Every method here is reachable from three
different inbound adapters -- an htmx upload, a JSON API call and a CLI command -- over
exactly the same objects, and the queued path calls the very same importer as the inline one
(ADR-0009).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from academy.application.commands import (
    DownloadTemplateCommand,
    ImportSpreadsheetCommand,
    RunImportJobCommand,
    SubmitImportCommand,
    ViewImportJobCommand,
)
from academy.application.dtos import ImportResultDto
from academy.application.jobs import ImportJob


@runtime_checkable
class ImportData(Protocol):
    """Submitting, running and tracking bulk imports."""

    async def download_template(self, command: DownloadTemplateCommand) -> bytes:
        """Produce the template for an importer (UC-36).

        A grade-sheet template for a named section comes back pre-filled with the enrolled
        students, so the round trip is a workflow rather than a blank form.

        Returns:
            The file, ready to serve as a download.
        """
        ...

    async def submit(self, command: SubmitImportCommand) -> ImportResultDto | ImportJob:
        """Import now, or queue it, according to the payload's size (UC-41).

        The union return type is the honest signature for this operation, and it is the one
        place in the application where the caller must handle two shapes. Below the threshold
        the work is already done and the report is returned; at or above it, only a job is,
        and the report arrives later. Hiding that behind a single shape would mean either
        blocking on large files or making every small upload poll.

        Returns:
            An :class:`~academy.application.dtos.ImportResultDto` if the import ran inline,
            or the pending :class:`~academy.application.jobs.ImportJob` if it was queued.

        Raises:
            PayloadTooLargeError: If the payload exceeds the hard cap.
            AuthorizationError: If the actor may not run this importer.
        """
        ...

    async def run_inline(self, command: ImportSpreadsheetCommand) -> ImportResultDto:
        """Run an importer immediately, whatever the size (UC-37 to UC-40).

        The worker calls this after loading a queued payload, and so does :meth:`submit` on
        the inline path. One implementation, one set of rules, one set of tests.

        Raises:
            MalformedSpreadsheetError: If the file cannot be parsed at all.
            AuthorizationError: If the actor may not run this importer.
        """
        ...

    async def run_job(self, command: RunImportJobCommand) -> ImportJob:
        """Execute one queued job and record its outcome (UC-42).

        Marks the job running, loads the payload from storage, runs the same importer as the
        inline path, and records either the report or a failure reason. A job whose payload
        has vanished fails with a stated reason rather than importing an empty file and
        reporting success.

        Returns:
            The job in its terminal state.
        """
        ...

    async def view_job(self, command: ViewImportJobCommand) -> ImportJob:
        """Read a job's current state (UC-41).

        Polled by the htmx progress fragment until the status is terminal.

        Raises:
            AuthorizationError: If the actor did not submit the job and is not an administrator.
            NotFoundError: If no such job exists.
        """
        ...
