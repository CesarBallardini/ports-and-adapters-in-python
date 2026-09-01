"""The wiring: the only code that knows both a port and the adapter that satisfies it.

Two objects, because the graph has two lifetimes (ADR-0015).

* :class:`Container` holds what lives as long as the process -- the store or, later, the
  engine and session factory, and the clock. Built once, at startup.
* :class:`Scope` holds what lives as long as one request, one CLI command or one job, and
  builds the use cases from it. Built per unit of work, and thrown away after.

No container library is involved and none is wanted: the graph below is small enough to read,
and every edge in it is checked by pyright rather than resolved at runtime. The cost of that
choice is this file, written by hand, which is the trade ADR-0015 makes explicit.

An inbound adapter never constructs anything from here. It receives a :class:`Scope`, asks it
for a port, and cannot reach a concrete adapter even by accident -- which is the property that
lets the same use case be driven by htmx, a JSON API, the CLI and the import worker without
any of them knowing which database is underneath.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Self

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from academy.adapters.outbound.identity import RepositoryActorIdentity, StaticActorIdentity
from academy.adapters.outbound.persistence.memory import (
    MemoryAcademicHistoryRepository,
    MemoryConfigurationRepository,
    MemoryGuardianshipRepository,
    MemoryImportJobRepository,
    MemoryPersonRepository,
    MemorySectionRepository,
    MemoryStore,
    MemoryUnitOfWork,
)
from academy.adapters.outbound.persistence.sqlalchemy.repositories import (
    SqlAlchemyAcademicHistoryRepository,
    SqlAlchemyConfigurationRepository,
    SqlAlchemyGuardianshipRepository,
    SqlAlchemyImportJobRepository,
    SqlAlchemyPersonRepository,
    SqlAlchemySectionRepository,
)
from academy.adapters.outbound.persistence.sqlalchemy.session import create_engine, create_session_factory
from academy.adapters.outbound.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from academy.adapters.outbound.queue import InlineJobQueue
from academy.adapters.outbound.spreadsheet import (
    CsvSpreadsheetReader,
    CsvSpreadsheetWriter,
    XlsxSpreadsheetReader,
    XlsxSpreadsheetWriter,
)
from academy.adapters.outbound.storage import LocalFileStorage, MemoryFileStorage
from academy.adapters.outbound.system import SystemClock
from academy.adapters.outbound.system.ids import Uuid4IdGenerator
from academy.application.authorization import AccessGuard, RelationshipResolver
from academy.application.commands import RunImportJobCommand
from academy.application.dtos import Actor
from academy.application.grading import GradeManagement
from academy.application.importing import GradeSheetImporter, ImportService, SpreadsheetFormats
from academy.application.jobs import ImportJob, ImportKind, JobId
from academy.application.ports.inbound.grading import ManageGrades
from academy.application.ports.inbound.imports import ImportData
from academy.application.ports.inbound.records import ViewStudentRecords
from academy.application.ports.outbound.file_storage import FileStorage
from academy.application.ports.outbound.identity import ActorIdentity
from academy.application.ports.outbound.repositories import (
    AcademicHistoryRepository,
    ConfigurationRepository,
    GuardianshipRepository,
    ImportJobRepository,
    PersonRepository,
    SectionRepository,
)
from academy.application.ports.outbound.system import Clock, IdGenerator
from academy.application.ports.outbound.unit_of_work import UnitOfWork
from academy.application.records import StudentRecords
from academy.config.settings import (
    ENV_BOOTSTRAP_ADMIN,
    ENV_IDENTITY,
    ENV_SECRET_KEY,
    ConfigurationError,
    Environ,
    IdentityBackend,
    PersistenceBackend,
    Settings,
)
from academy.domain.people.role import Role
from academy.domain.shared.ids import PersonId


def _regardless_of(identity: ActorIdentity, _people: PersonRepository) -> ActorIdentity:
    """Adapt a process-lifetime identity to the per-scope signature the other branch needs.

    :class:`~academy.adapters.outbound.identity.static.StaticActorIdentity` reads no repository,
    so it is built once and shared; ``RepositoryActorIdentity`` is built per scope from that
    scope's repository. This makes the two branches the same shape, which is what lets
    :meth:`Container._scope` name neither of them.
    """
    return identity


def _bootstrap_actors(bootstrap_admin: str | None) -> Mapping[PersonId, Actor]:
    """Build the population a ``static`` identity resolves, from what the deployment configured.

    Args:
        bootstrap_admin: The person id, as text, or ``None`` if unset.

    Returns:
        A single-entry mapping: the configured id, holding the administrative role. One entry
        because bootstrapping needs exactly one person -- the one who creates the others -- and
        a second configured administrator would be a second thing to forget to remove.

    Raises:
        ConfigurationError: If the id is missing or is not a UUID. Both at startup, because a
            ``static`` identity with nothing to resolve is a process that can serve no
            authenticated request at all, and finding that out on the first sign-in attempt
            would be finding it out from a user.
    """
    if bootstrap_admin is None:
        raise ConfigurationError(
            f'{ENV_IDENTITY}={IdentityBackend.STATIC.value} needs {ENV_BOOTSTRAP_ADMIN} set to a person id'
        )

    try:
        person_id = PersonId.from_str(bootstrap_admin)
    except ValueError as error:
        raise ConfigurationError(f'{ENV_BOOTSTRAP_ADMIN}={bootstrap_admin!r} is not a UUID') from error

    return {person_id: Actor(person_id=person_id, roles=frozenset({Role.ADMINISTRATIVE_EMPLOYEE}))}


def _signing_key(settings: Settings) -> str:
    """Decide what signs this deployment's sessions.

    A deployment that named a key gets it. One that did not gets an answer that depends on
    whether anything about it is durable:

    * **In-memory persistence** -- a random key per process. Nothing survives a restart there
      anyway, so a session that does not either is consistent rather than surprising, and it is
      what makes ``make run`` and the whole test suite work with no environment at all.
    * **A real database** -- an error. A generated key would be a different key in every worker
      of a multi-process deployment, so a signed-in user would be signed out by whichever worker
      answered next; and every deploy would log everyone out. Both look like flaky sessions and
      neither points at the cause.

    Raises:
        ConfigurationError: If persistence is durable and no key was set.
    """
    if settings.secret_key is not None:
        return settings.secret_key

    if settings.persistence is PersistenceBackend.MEMORY:
        return secrets.token_urlsafe(32)

    raise ConfigurationError(
        f'{ENV_SECRET_KEY} must be set when persistence is {PersistenceBackend.SQLALCHEMY.value}: '
        'a generated key differs between workers and between restarts, which signs users out at random'
    )


@dataclass(frozen=True, slots=True)
class Scope:
    """The collaborators whose lifetime is one request, one command or one job.

    Every field is a **port**, never an adapter class, so this type says nothing about which
    backend is underneath and does not change when one is swapped.

    The use-case builders are methods rather than fields because a use case is cheap to build
    and not every request needs every one of them. They return the inbound port, not the
    implementing class: an adapter that held a ``GradeManagement`` could reach past the port,
    and the point of the port is that it cannot.

    ``unit_of_work`` is a **factory**, not an instance, and that distinction is load-bearing.
    A unit of work refuses re-entry while it is active, so one shared across every use case a
    scope builds would turn two overlapping calls -- an ``asyncio.gather`` of two grade
    recordings in a batch importer -- into ``RuntimeError`` rather than two transactions. Each
    use case gets its own; the repositories, which hold no transaction state, are shared.
    """

    unit_of_work: Callable[[], UnitOfWork]
    people: PersonRepository
    sections: SectionRepository
    histories: AcademicHistoryRepository
    guardianships: GuardianshipRepository
    configuration: ConfigurationRepository
    jobs: ImportJobRepository
    clock: Clock
    ids: IdGenerator
    storage: FileStorage
    formats: SpreadsheetFormats
    import_inline_threshold_bytes: int
    import_max_bytes: int
    # Scoped rather than process-lifetime because the repository-backed adapter reads through
    # this scope's repositories, and a request must not resolve its actor through another
    # request's session. The static adapter has no such need and is simply shared.
    identity: ActorIdentity

    def grade_management(self) -> ManageGrades:
        """Build the grading use cases (UC-21, UC-22).

        Takes no actor: authentication produces an ``Actor`` that travels inside the command
        (ADR-0010), so the graph is identical for every caller and can be built before anyone
        is known.

        Returns:
            The inbound port, satisfied by
            :class:`~academy.application.grading.GradeManagement`.
        """
        return GradeManagement(
            sections=self.sections,
            histories=self.histories,
            people=self.people,
            uow=self.unit_of_work(),
            guard=self.access_guard(),
        )

    def student_records(self) -> ViewStudentRecords:
        """Build the transcript-reading use cases (UC-26, UC-28, UC-30).

        No unit of work is passed, and none is built: this use case only reads, and a
        transaction is a write boundary. The factory being on the scope rather than the
        container is still right -- the repositories it closes over are the scope's.

        Returns:
            The inbound port, satisfied by
            :class:`~academy.application.records.StudentRecords`.
        """
        return StudentRecords(
            histories=self.histories,
            people=self.people,
            guardianships=self.guardianships,
            configuration=self.configuration,
            clock=self.clock,
            guard=self.access_guard(),
        )

    def import_data(self) -> ImportData:
        """Build the bulk-import use cases (UC-36, UC-40 to UC-42).

        The queue is wired **inline**: this deployment has no worker process, so ``enqueue``
        runs the job in the caller. The seam is real all the same -- swapping in a queue that
        defers to a worker changes this method and nothing else, because the worker's entry
        point is the same ``run_job`` the inline queue calls.

        Returns:
            The inbound port, satisfied by
            :class:`~academy.application.importing.service.ImportService`.
        """
        # The knot every inline queue has to tie: the queue runs the service, and the service
        # holds the queue. It is tied here, with a local, so that neither of them knows the
        # other exists -- which is precisely the composition root's job. A deployment with a
        # real worker passes a queue that defers instead, and this is the only line that
        # changes.
        built: list[ImportService] = []

        async def run(job_id: JobId) -> ImportJob:
            return await built[0].run_job(RunImportJobCommand(job_id=str(job_id)))

        service = ImportService(
            importers={
                ImportKind.GRADE_SHEET: GradeSheetImporter(
                    sections=self.sections,
                    histories=self.histories,
                    people=self.people,
                    guard=self.access_guard(),
                )
            },
            formats=self.formats,
            unit_of_work=self.unit_of_work,
            jobs=self.jobs,
            people=self.people,
            storage=self.storage,
            queue=InlineJobQueue(run),
            clock=self.clock,
            ids=self.ids,
            guard=self.access_guard(),
            inline_threshold_bytes=self.import_inline_threshold_bytes,
            max_bytes=self.import_max_bytes,
        )
        built.append(service)
        return service

    def access_guard(self) -> AccessGuard:
        """Build the guard that resolves relations and enforces what they grant.

        Public because every use case that lands after this one needs it, and building it
        twice in two places is how the two would eventually disagree about which repositories
        a relation is derived from.
        """
        return AccessGuard(
            RelationshipResolver(
                sections=self.sections,
                guardianships=self.guardianships,
                people=self.people,
                configuration=self.configuration,
                clock=self.clock,
            )
        )


class Container:
    """The process-lifetime half of the composition root.

    Built once at startup and shared. It holds no request state, so nothing here has to be
    thread-safe or task-safe beyond what the adapters themselves promise.
    """

    def __init__(self, settings: Settings, clock: Clock | None = None) -> None:
        """Wire the process-lifetime adapters.

        Args:
            settings: What the deployment chose. Read once, at startup.
            clock: The source of "now". Defaults to
                :class:`~academy.adapters.outbound.system.clock.SystemClock`; it is injectable
                so a test or a reproducible batch run can freeze time, never so a deployment
                configures it.

        Raises:
            ConfigurationError: If the chosen persistence backend has no adapter yet, or if the
                ``static`` identity was chosen without a bootstrap id to resolve. Both raised
                here, at startup, rather than on the first request that needs the missing piece.
                The signing key is the one deliberate exception -- see :attr:`secret_key`.
        """
        self._settings = settings
        self._clock: Clock = clock or SystemClock()
        self._ids: IdGenerator = Uuid4IdGenerator()
        self._formats = SpreadsheetFormats(
            readers={'csv': CsvSpreadsheetReader(), 'xlsx': XlsxSpreadsheetReader()},
            writers={'csv': CsvSpreadsheetWriter(), 'xlsx': XlsxSpreadsheetWriter()},
        )

        # The one branch in the whole application that knows which database exists, and the
        # payload storage follows it: a durable database with in-memory payloads would leave a
        # pending job pointing at bytes that vanished on restart.
        #
        # Nothing here migrates. The application connects as a role that cannot (ADR-0018), so
        # a database that has not been migrated fails at the first query rather than being
        # quietly repaired -- which is what makes migration a deploy step instead of a race
        # between two starting instances.
        self._engine: AsyncEngine | None = None

        if settings.persistence is PersistenceBackend.MEMORY:
            store = MemoryStore()
            self._storage: FileStorage = MemoryFileStorage()
            self._open_scope: Callable[[], AbstractAsyncContextManager[Scope]] = partial(self._memory_scope, store)
        else:
            self._engine = create_engine(settings.database_url)
            sessions = create_session_factory(self._engine)
            self._storage = LocalFileStorage(Path(settings.upload_directory))
            self._open_scope = partial(self._session_scope, sessions)

        # The second branch, and it is deliberately independent of the first: which database is
        # underneath says nothing about how a person id becomes an actor, and a deployment that
        # had to change both together would be one where the bootstrap case could not exist.
        if settings.identity is IdentityBackend.STATIC:
            static = StaticActorIdentity(_bootstrap_actors(settings.bootstrap_admin))
            self._identity_for: Callable[[PersonRepository], ActorIdentity] = partial(_regardless_of, static)
        else:
            self._identity_for = RepositoryActorIdentity

        # Resolved on demand rather than here, because not every driver has sessions to sign.
        # The CLI and the import worker never ask, and a deployment of either should not have to
        # invent a signing key to satisfy a check for a surface it does not run (ADR-0019: each
        # inbound adapter owns its own vocabulary, and this is part of the web adapter's).
        self._secret_key: str | None = settings.secret_key

    @classmethod
    def from_env(cls, environ: Environ | None = None, clock: Clock | None = None) -> Self:
        """Build the container a deployment's environment describes.

        Args:
            environ: Where to read the settings from. Defaults to ``os.environ``.
            clock: As for :meth:`__init__`.

        Returns:
            A container ready to open scopes.
        """
        return cls(Settings.from_env(environ), clock=clock)

    @property
    def settings(self) -> Settings:
        """What this container was built from.

        Exposed because an inbound adapter legitimately needs a deployment's own choices --
        a port number, a template directory -- and must read them from here rather than from
        the environment a second time.
        """
        return self._settings

    @property
    def secret_key(self) -> str:
        """What an inbound adapter signs sessions and tokens with.

        Read from here rather than from :attr:`settings` because the setting is optional and
        this is not: "the deployment said nothing" is turned into either a generated key or a
        refusal to start (see :func:`_signing_key`), so a caller gets an answer and never a
        ``None`` to handle.

        Resolved on first access and then fixed, which is what makes the two halves true at
        once: a web process asking for it during ``create_app`` fails at startup if the
        deployment is durable and set no key, while a CLI invocation that never asks is never
        troubled by a requirement that does not apply to it.

        Raises:
            ConfigurationError: If this deployment is durable and named no key.
        """
        if self._secret_key is None:
            self._secret_key = _signing_key(self._settings)
        return self._secret_key

    @asynccontextmanager
    async def request_scope(self) -> AsyncIterator[Scope]:
        """Open a scope for one request, one CLI command or one job.

        "Request" names the lifetime, not the transport: the import worker and the CLI open
        one of these per job and per invocation, so all four drivers assemble the same graph
        through the same method.

        Which backend answers was decided once, at startup: this method opens whatever
        ``__init__`` chose and knows nothing about the choice. Branching here instead would
        mean asking the same settled question on every request, and would leave the two
        alternatives as fields that must both be checked and only one of which is ever set.

        Yields:
            A scope bound to a fresh unit of work.
        """
        async with self._open_scope() as scope:
            yield scope

    @asynccontextmanager
    async def _memory_scope(self, store: MemoryStore) -> AsyncIterator[Scope]:
        """A scope over the in-memory store.

        The tables *outlive* the scope -- the process is the database -- so only the
        transaction is scoped, which is why the unit of work is a factory here and the store
        is not.
        """
        yield self._scope(
            unit_of_work=lambda: MemoryUnitOfWork(store),
            people=MemoryPersonRepository(store),
            sections=MemorySectionRepository(store),
            histories=MemoryAcademicHistoryRepository(store),
            guardianships=MemoryGuardianshipRepository(store),
            configuration=MemoryConfigurationRepository(store),
            jobs=MemoryImportJobRepository(store),
        )

    @asynccontextmanager
    async def _session_scope(self, sessions: async_sessionmaker[AsyncSession]) -> AsyncIterator[Scope]:
        """A scope over one database session, opened and closed with the scope."""
        async with sessions() as session:
            yield self._scope(
                unit_of_work=lambda: SqlAlchemyUnitOfWork(session),
                people=SqlAlchemyPersonRepository(session),
                sections=SqlAlchemySectionRepository(session),
                histories=SqlAlchemyAcademicHistoryRepository(session),
                guardianships=SqlAlchemyGuardianshipRepository(session),
                configuration=SqlAlchemyConfigurationRepository(session),
                jobs=SqlAlchemyImportJobRepository(session),
            )

    def _scope(
        self,
        *,
        unit_of_work: Callable[[], UnitOfWork],
        people: PersonRepository,
        sections: SectionRepository,
        histories: AcademicHistoryRepository,
        guardianships: GuardianshipRepository,
        configuration: ConfigurationRepository,
        jobs: ImportJobRepository,
    ) -> Scope:
        """Assemble a scope from one backend's repositories and the process-lifetime rest.

        Exists so the two branches above differ *only* in which adapters they name. Anything
        common that drifted between them -- a clock in one and not the other -- would be a
        difference between backends that no port describes.
        """
        return Scope(
            unit_of_work=unit_of_work,
            people=people,
            sections=sections,
            histories=histories,
            guardianships=guardianships,
            configuration=configuration,
            jobs=jobs,
            clock=self._clock,
            ids=self._ids,
            storage=self._storage,
            formats=self._formats,
            import_inline_threshold_bytes=self._settings.import_inline_threshold_bytes,
            import_max_bytes=self._settings.import_max_bytes,
            identity=self._identity_for(people),
        )

    async def aclose(self) -> None:
        """Release what the process holds.

        Only the engine holds anything: an open pool, and on SQLite a file handle that Windows
        will not let a temporary directory delete. A memory container has nothing to close and
        this is a no-op for it.
        """
        if self._engine is not None:
            await self._engine.dispose()
