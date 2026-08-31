"""The import use cases: where the work runs, and what surrounds a run.

``ImportService`` owns the plumbing and nothing else -- the transaction, the dry-run rollback,
the choice of adapter, and (from the queued path onward) storage and the queue. It knows
nothing about people, subjects, enrollments or grades; a :class:`RowImporter` knows those and
knows none of this.

That division is what makes the third claim of this repository checkable: the same importer is
driven by an htmx upload, a JSON API call, a CLI command and a background worker, because none
of them appears in either half.

The queued path (ADR-0009) adds a second decision and no second implementation: ``submit``
measures the payload and either calls ``run_inline`` itself or writes the bytes down and hands
an id to a queue. Whichever way it goes, the rows are applied by the same importer, through
the same method a worker calls. That is the point -- a threshold that changed *where* work
runs but also *what* it did would be two importers wearing one name.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace

from academy.application.authorization import AccessGuard
from academy.application.commands import (
    DownloadTemplateCommand,
    ImportSpreadsheetCommand,
    RunImportJobCommand,
    SubmitImportCommand,
    ViewImportJobCommand,
)
from academy.application.dtos import Actor, ImportResultDto
from academy.application.errors import ApplicationError, NotFoundError, PayloadTooLargeError
from academy.application.importing.formats import SpreadsheetFormats
from academy.application.importing.rows import RowImporter
from academy.application.jobs import ImportContext, ImportJob, ImportKind, JobId
from academy.application.ports.outbound.file_storage import FileStorage
from academy.application.ports.outbound.job_queue import JobQueue
from academy.application.ports.outbound.repositories import ImportJobRepository, PersonRepository
from academy.application.ports.outbound.system import Clock, IdGenerator
from academy.application.ports.outbound.unit_of_work import UnitOfWork
from academy.domain.authorization.models import Action, ResourceType
from academy.domain.shared.errors import DomainError
from academy.domain.shared.ids import PersonId

# Where the inline path stops and the queued one starts (ADR-0009), and the hard cap above
# which nothing is accepted. Defaults rather than required arguments so that a test or a CLI
# can build the service without deciding a deployment question; the composition root passes
# the deployment's own numbers.
#
# Comments rather than attribute docstrings: the check-docstring-first hook reads a string
# literal after a module-level assignment as a second module docstring.
DEFAULT_INLINE_THRESHOLD_BYTES = 256 * 1024
DEFAULT_MAX_BYTES = 16 * 1024 * 1024


class ImportService:
    """Runs an import, in a transaction, through the adapter the upload calls for."""

    def __init__(
        self,
        importers: Mapping[ImportKind, RowImporter],
        formats: SpreadsheetFormats,
        unit_of_work: Callable[[], UnitOfWork],
        jobs: ImportJobRepository,
        people: PersonRepository,
        storage: FileStorage,
        queue: JobQueue,
        clock: Clock,
        ids: IdGenerator,
        guard: AccessGuard,
        inline_threshold_bytes: int = DEFAULT_INLINE_THRESHOLD_BYTES,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        """Wire the service to its importers and its plumbing.

        Args:
            importers: One importer per kind. A mapping rather than a chain of ``if``s,
                because the kind arrives as data -- from a form field, or off a job that was
                written down before this process started.
            formats: Which spreadsheet adapters this deployment has.
            unit_of_work: A **factory**. Each run gets its own transaction; a shared one would
                make two concurrent imports refuse each other (ADR-0015).
            jobs: Where queued imports are recorded.
            people: Resolves a queued job's submitter back into an actor, with the roles they
                hold *now* rather than the ones they held when they uploaded.
            storage: Where a queued payload is written, so the queue carries only an id.
            queue: How the work is handed to whoever will run it.
            clock: Stamps the submission. The domain never reads a clock; neither does a job.
            ids: Supplies the job id, which is assigned before anything is stored.
            guard: Decides who may read a job.
            inline_threshold_bytes: At or above this size the work is queued (ADR-0009).
            max_bytes: The hard cap. Above it nothing is accepted at all.
        """
        self._importers = dict(importers)
        self._formats = formats
        self._unit_of_work = unit_of_work
        self._jobs = jobs
        self._people = people
        self._storage = storage
        self._queue = queue
        self._clock = clock
        self._ids = ids
        self._guard = guard
        self._inline_threshold_bytes = inline_threshold_bytes
        self._max_bytes = max_bytes

    async def submit(self, command: SubmitImportCommand) -> ImportResultDto | ImportJob:
        """Import now, or queue it, according to the payload's size (UC-41).

        The caller does not choose. Where the work runs is a deployment concern decided by one
        number, so there is no flag for an inbound adapter to get wrong -- and the two paths
        are the same import either way.

        The union return type is the honest signature, and the one place in this application
        where a caller must handle two shapes. Below the threshold the work is already done
        and the report comes back; at or above it, only a job does, and the report arrives
        later. Hiding that behind one shape would mean either blocking on large files or
        making every small upload poll.

        The payload is written and the job saved **before** the id is enqueued, and the queue
        is told outside the transaction that saved the job: a worker that received the id
        first would look for a job that does not exist yet.

        Raises:
            PayloadTooLargeError: If the payload exceeds the hard cap. Checked before anything
                is parsed or stored -- the point of a cap is to refuse the work, not to
                discover halfway through that it was too big.
            MalformedSpreadsheetError: If an inline run cannot parse the file.
            AuthorizationError: If the actor may not run this importer.
        """
        size = len(command.data)
        if size > self._max_bytes:
            raise PayloadTooLargeError(size, self._max_bytes)

        if size < self._inline_threshold_bytes:
            return await self.run_inline(
                ImportSpreadsheetCommand(
                    actor=command.actor,
                    kind=command.kind,
                    data=command.data,
                    content_type=command.content_type,
                    filename=command.filename,
                    dry_run=command.dry_run,
                    context=command.context,
                )
            )

        job = ImportJob(
            id=self._ids.next_job_id(),
            kind=command.kind,
            storage_key='',
            submitted_by=str(command.actor.person_id),
            submitted_at=self._clock.now(),
            context=_context(command.context),
        )
        # The key carries the format's extension, and that is the only record of it: an
        # `ImportJob` has no content type and the worker has no upload to ask. Deciding the
        # format here, where the MIME type and the filename are both still in hand, is what
        # stops a queued XLSX being read back by the default CSV reader -- which is what
        # happened before this line existed, and which failed every large XLSX upload.
        extension = self._formats.extension_for(command.content_type, command.filename)
        job.storage_key = f'imports/{job.id}{extension}'
        await self._storage.put(job.storage_key, command.data)

        unit_of_work = self._unit_of_work()
        async with unit_of_work:
            await self._jobs.add(job)
            await unit_of_work.commit()

        await self._queue.enqueue(job.id)
        return job

    async def run_job(self, command: RunImportJobCommand) -> ImportJob:
        """Execute one queued job and record its outcome (UC-42).

        The worker's entry point, and it holds every failure a worker can meet. A job whose
        payload has been swept away **fails with a stated reason** rather than importing an
        empty file and reporting success -- which is the difference between an operator who
        knows to re-upload and one who believes the file was empty.

        The actor is rebuilt from the **person record**, not from the worker and not from
        anything stored on the job, because authorization must not be frozen at submission
        time. A teacher who lost the section between submitting and running must not have the
        import go through on their behalf, and an administrator who lost the role must not
        keep it because a job remembered it. The job stores who submitted it; what they may do
        is asked again, now.

        Returns:
            The job in its terminal state, saved.

        Raises:
            NotFoundError: If no such job exists. A worker asked to run a job that is not
                there has been handed a bad id, which is not the job's failure to record.
            JobStateError: If the job is not pending.
        """
        job = await self._job(command.job_id)
        job.mark_running()
        await self._save(job)

        try:
            actor = await self._submitter_of(job)
            data = await self._storage.get(job.storage_key)
            result = await self.run_inline(
                ImportSpreadsheetCommand(
                    actor=actor,
                    kind=job.kind,
                    data=data,
                    # The storage key is the only thing that remembers what format was
                    # submitted, which is why `submit` gave it an extension. A key from before
                    # that -- or from a deployment whose formats have no extension -- has none,
                    # and falls back to the default reader as it always did.
                    filename=job.storage_key,
                    context=job.context,
                )
            )
        except (ApplicationError, DomainError) as error:
            # Everything that prevents a report: a missing payload, an unreadable file, an
            # actor who may no longer import, a context that no longer names anything.
            # Rejected *rows* are not failures -- they are in the report.
            job.mark_failed(str(error))
        else:
            job.mark_done(result)

        await self._save(job)
        return job

    async def view_job(self, command: ViewImportJobCommand) -> ImportJob:
        """Read a job's current state (UC-41).

        Polled by the htmx progress fragment until the status is terminal.

        Raises:
            AuthorizationError: If the actor did not submit the job and is not an
                administrator. Asked of the *submitter's* records, because a job is a fact
                about the person who ran the import.
            NotFoundError: If no such job exists.
        """
        job = await self._job(command.job_id)
        await self._guard.require(
            command.actor,
            Action.READ,
            ResourceType.ACADEMIC_HISTORY,
            PersonId.from_str(job.submitted_by),
        )
        return job

    async def _submitter_of(self, job: ImportJob) -> Actor:
        """Rebuild the submitting actor from the person record, as it stands now.

        The roles come from storage rather than from the job, and that is the whole point. An
        ``Actor`` assembled from the id alone would carry **no roles at all**, which is not a
        smaller actor but a different one: the queued path would then refuse imports the
        inline path allows, and would do it silently. Reading the person makes the two paths
        ask the same question of the same data, a moment apart.

        Raises:
            NotFoundError: If the submitter's id is unparseable, or names nobody any more.
                Caught by :meth:`run_job` and recorded as the job's failure reason -- a job
                submitted by someone since deleted has no one to run as, and guessing would
                mean running it as nobody.
        """
        try:
            person_id = PersonId.from_str(job.submitted_by)
        except ValueError as error:
            raise NotFoundError('submitter', job.submitted_by) from error

        submitter = await self._people.get(person_id)
        if submitter is None:
            raise NotFoundError('submitter', person_id)
        return Actor(person_id=submitter.id, roles=submitter.roles)

    async def _job(self, job_id: str) -> ImportJob:
        """Fetch a job that the operation cannot proceed without.

        Raises:
            NotFoundError: If no such job is stored.
        """
        try:
            identity = JobId.from_str(job_id)
        except ValueError as error:
            # A malformed id names nothing, which is what NotFoundError says. Letting the
            # ValueError escape would reach an inbound adapter with no entry for it in the
            # status table (ADR-0012) and become a 500 for a plainly bad request.
            raise NotFoundError('import job', job_id) from error

        job = await self._jobs.get(identity)
        if job is None:
            raise NotFoundError('import job', job_id)
        return job

    async def _save(self, job: ImportJob) -> None:
        """Persist a job's new state in its own transaction.

        Its own, and not the import's: the whole value of marking a job running is that the
        record survives whatever the run does next, including a crash. Sharing the import's
        transaction would roll the status back along with the rows.
        """
        unit_of_work = self._unit_of_work()
        async with unit_of_work:
            await self._jobs.save(job)
            await unit_of_work.commit()

    async def run_inline(self, command: ImportSpreadsheetCommand) -> ImportResultDto:
        """Parse the file and apply every row, in one transaction (UC-37 to UC-40).

        The order is deliberate: the file is parsed **before** the transaction opens. Parsing
        is CPU work that can fail on its own terms, and holding a transaction open across it
        would mean a malformed upload had briefly locked rows it was never going to touch.

        A dry run does the entire import and then rolls it back, rather than "checking without
        doing" — which is the only way the report can be trusted, because a rule that only
        fires on the third row is not visible to any amount of checking. It is the most useful
        thing on the whole import surface: a registrar finds out what a file *would* do
        (UC-40 §8a).

        Returns:
            The report. ``dry_run`` on it says which of the two runs this was, so a caller
            cannot mistake a rehearsal for the real thing.

        Raises:
            MalformedSpreadsheetError: If the file cannot be parsed at all.
            AuthorizationError: If the actor may not run this importer.
            NotFoundError: If the context names something that does not exist.
        """
        reader = self._formats.reader_for(command.content_type, command.filename)
        rows = reader.read_rows(command.data)
        importer = self._importer_for(command.kind)

        unit_of_work = self._unit_of_work()
        async with unit_of_work:
            result = await importer.import_rows(command.actor, rows, _context(command.context))
            if command.dry_run:
                await unit_of_work.rollback()
            else:
                await unit_of_work.commit()

        return replace(result, dry_run=command.dry_run)

    async def download_template(self, command: DownloadTemplateCommand) -> bytes:
        """Produce the template for an importer, in the requested format (UC-36).

        Outside any transaction: building a template reads, and a read that opened a write
        boundary would claim a guarantee it does not need.

        Raises:
            MalformedSpreadsheetError: If this system cannot write the requested format.
            NotFoundError: If the context names something that does not exist.
        """
        writer = self._formats.writer_for(command.file_format)
        template = await self._importer_for(command.kind).template(_context(command.context))
        return writer.write_sheet(template.headers, template.rows, sheet_name=command.kind.value)

    def _importer_for(self, kind: ImportKind) -> RowImporter:
        """Look up the importer for a kind.

        Raises:
            KeyError: If this deployment wired no importer for it. Deliberately not caught and
                turned into a friendly error: it is a composition-root bug, not something a
                user did, and the two should not look alike.
        """
        return self._importers[kind]


def _context(context: ImportContext | None) -> ImportContext:
    """Treat a missing context as an empty one.

    The commands make it optional because most imports need no parameters; an importer that
    does need one asks for it and raises ``NotFoundError`` when it is absent, which is the
    same answer it gives when the id is present and names nothing.
    """
    return dict(context or {})
