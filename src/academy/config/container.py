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

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Self

from academy.adapters.outbound.persistence.memory import (
    MemoryAcademicHistoryRepository,
    MemoryConfigurationRepository,
    MemoryGuardianshipRepository,
    MemoryPersonRepository,
    MemorySectionRepository,
    MemoryStore,
    MemoryUnitOfWork,
)
from academy.adapters.outbound.system import SystemClock
from academy.application.authorization import AccessGuard, RelationshipResolver
from academy.application.grading import GradeManagement
from academy.application.ports.inbound.grading import ManageGrades
from academy.application.ports.outbound.repositories import (
    AcademicHistoryRepository,
    ConfigurationRepository,
    GuardianshipRepository,
    PersonRepository,
    SectionRepository,
)
from academy.application.ports.outbound.system import Clock
from academy.application.ports.outbound.unit_of_work import UnitOfWork
from academy.config.settings import ENV_PERSISTENCE, ConfigurationError, Environ, PersistenceBackend, Settings


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
    clock: Clock

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
            ConfigurationError: If the chosen persistence backend has no adapter yet. Raised
                here, at startup, rather than on the first request that needs a repository.
        """
        if settings.persistence is not PersistenceBackend.MEMORY:
            raise ConfigurationError(
                f'the {settings.persistence.value} persistence adapter is not written yet; '
                f'set {ENV_PERSISTENCE}=memory'
            )

        self._settings = settings
        self._clock: Clock = clock or SystemClock()
        self._store = MemoryStore()

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

    @asynccontextmanager
    async def request_scope(self) -> AsyncIterator[Scope]:
        """Open a scope for one request, one CLI command or one job.

        "Request" names the lifetime, not the transport: the import worker and the CLI open
        one of these per job and per invocation, so all four drivers assemble the same graph
        through the same method.

        With the in-memory backend the tables *outlive* the scope -- the process is the
        database -- and only the transaction is scoped, which is why the unit of work is built
        here and the store is not. When the SQLAlchemy adapter lands this is where a session
        opens and closes, and nothing outside this method has to change.

        Yields:
            A scope bound to a fresh unit of work.
        """
        store = self._store
        yield Scope(
            unit_of_work=lambda: MemoryUnitOfWork(store),
            people=MemoryPersonRepository(store),
            sections=MemorySectionRepository(store),
            histories=MemoryAcademicHistoryRepository(store),
            guardianships=MemoryGuardianshipRepository(store),
            configuration=MemoryConfigurationRepository(store),
            clock=self._clock,
        )
