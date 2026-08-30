# ADR-0015 — Manual composition root over a DI container

- **Status** Accepted
- **Date** 2026-08-30

## Context

"Dependency injection" bundles three separate problems, and this repository has already answered
two of them:

1. **Passing collaborators in.** Constructor injection, everywhere. `GradeManagement.__init__`
   takes five ports and holds no other way of reaching the outside world.
2. **Choosing which implementation.** The composition root, `config/`, is the only module
   permitted to know both a port and the adapter that satisfies it (ADR-0003).
3. **Lifetime and scope.** Open. A request needs its own `AsyncSession` and `UnitOfWork`, an
   `Actor` resolved from a cookie or a bearer token (ADR-0010), and the import worker needs the
   same graph built per job rather than per request.

Only the third is unsolved, so the question is not "should dependencies be injected" — they
already are — but whether a container library should own the scoping, and what that costs the
hexagon.

Four constraints make this repository a harder case than most:

- **The hexagon imports no framework.** `.importlinter` forbids `fastapi`, `sqlalchemy` and the
  rest from `domain` and `application` (ADR-0004). A container whose wiring markers or
  decorators must appear on the injected class puts a framework import exactly where the
  contract exists to keep one out.
- **The core has no dependencies.** `dependencies = []` in `pyproject.toml` is a claim the
  repository makes on purpose; every adapter dependency is an extra. A container would be the
  first runtime dependency of the shipped package that belongs to no adapter family.
- **Strict type checking.** `make types` runs pyright and pyrefly, and `Any` is treated as a
  defect. A container that resolves by type at runtime moves "is the graph complete?" from the
  type checker to the first request that exercises the missing edge.
- **Four drivers, one graph.** htmx web, JSON API, CLI and background worker all build the same
  use cases. A mechanism available to only one of them means the graph is assembled more than
  once, which is the coupling the hexagon exists to prevent.

Availability was checked and excluded nothing: every candidate below resolves on Python 3.14,
and `dependency-injector`, though a C extension, ships `cp310-abi3` wheels and installs fine.
The argument that follows is about design, not packaging.

## Decision

Wire by hand, in `config/`, and add no DI library.

A `Container` holds the collaborators whose lifetime is the process — engine, session factory,
clock, id generator, notifier — and exposes one async scope that yields the collaborators whose
lifetime is a request or a job:

```python
class Container:
    def __init__(self, settings: Settings) -> None:
        self._sessions = async_sessionmaker(create_async_engine(settings.database_url))
        self._clock: Clock = SystemClock()

    @asynccontextmanager
    async def request_scope(self) -> AsyncIterator[Scope]:
        async with self._sessions() as session:
            yield Scope(session, self._clock)


@dataclass(frozen=True, slots=True)
class Scope:
    session: AsyncSession
    clock: Clock

    def grade_management(self, actor: Actor) -> ManageGrades:
        uow = SqlAlchemyUnitOfWork(self.session)
        return GradeManagement(SqlAlchemySectionRepository(self.session), ..., uow, guard)
```

Three rules follow from it:

- **FastAPI's `Depends` is allowed at the edge only.** The web adapter may use it for a shim that
  reads `request.app.state.container` and yields a `Scope`. A router receives a port — never a
  concrete adapter, and never a session — so no adapter module names another adapter.
- **The CLI and the worker call `container.request_scope()` directly.** Same graph, same scope
  object, no second wiring mechanism.
- **If a container is ever adopted, it joins `.importlinter`'s forbidden list.** That list is an
  explicit denylist, so `@inject` inside `application/` would pass the gate today while
  destroying the property the gate exists to protect. The list has to name the library for the
  contract to keep meaning what it says.

## Consequences

- The object graph is readable in one file. What talks to what is a matter of reading code, not
  of knowing a container's resolution rules.
- A missing or mistyped collaborator is a pyright error at the root, not a runtime resolution
  failure on the one endpoint nobody exercised.
- `dependencies = []` stays true, and the licence and audit gates keep their current surface.
- The request scope is roughly forty lines that we own and must keep correct for each driver.
  That is the price paid for the three points above, and it is the honest one to state.
- The root grows as Phase B and C adapters land. When it stops being readable, it is split by
  adapter family (`config/persistence.py`, `config/web.py`), not rescued by a container.
- Tests build the graph explicitly, which is what makes the in-memory adapters usable as real
  adapters rather than as fixtures a container happens to substitute (ADR-0014).
- Adopting a container later would be a mechanical change confined to `config/`. That it *would*
  be confined is itself the evidence that the boundary is in the right place.

## Alternatives considered

| Option | What it gives | Cost in this repository |
|---|---|---|
| **Manual root + factory functions** (chosen) | Full control, zero dependencies, everything type-checked, a readable graph | The request scope is hand-written |
| **FastAPI `Depends` as the container** | Already a dependency; native async scope with generator cleanup | Web-only — CLI, worker and API each need their own wiring, so the same use case gets built three ways |
| **`svcs`** | Registry plus container, async cleanup, per-request scope, Starlette integration | A service *locator*: call sites ask by type. Tolerable only if confined to inbound adapters |
| **`rodi`, `punq`, `lagom`** | Constructor autowiring from annotations, no decorators or markers on our classes | Wiring becomes implicit; a missing binding is a runtime error rather than a type error |
| **`that-depends`** | Async-first, explicit context scopes, actively maintained | Same implicitness, and a runtime dependency for the one part we could write by hand |
| **`dependency-injector`** | Declarative containers, configuration providers, overriding for tests | Intrusive: `@inject` and `Provide[...]` markers land in the injected module, which here means `application/` |
| **`injector`** | Guice-style modules, binding by annotation | Same intrusion — `@inject` on use-case constructors |
| **Closures or `functools.partial`** | Bind ports at the root and hand out callables | Gives up the one-implementation-class-per-port shape (`docs/06-class-diagram.md` §2) |
| **`contextvars`** | Request scope without threading state through call chains | Ambient state: hard to test, and it leaks across tasks when a scope is forgotten |

The container libraries share one property that decides this: their headline feature is choosing
an implementation from configuration, and that is the feature this repository already has, by
hand, in a single module. What remains on offer is lifetime management, and lifetime management
is the part that is cheap to write and expensive to hide.
