"""The composition root: settings, and the wiring of adapters into ports.

This is the only module in the codebase permitted to know both a port and the concrete
adapter that satisfies it. Every choice the rest of the application is deliberately
ignorant of -- SQLite or PostgreSQL, CSV or XLSX, local disk or S3, inline or queued --
is made exactly once, here.

It therefore depends on every layer, which is why it sits outside the layers contract in
``.importlinter`` rather than being exempted from it.

The wiring is written by hand: no dependency-injection container, for the reasons and at the
cost recorded in ADR-0015. Four drivers assemble their graph through the same
:meth:`Container.request_scope`: the test suite, the CLI, the browser and the JSON API.

:func:`create_app` is the ASGI factory the Makefile's ``run`` target names. It lives here rather
than in the web adapter for the reason everything else here does -- reading the environment and
choosing a backend is the composition root's job, and an adapter that did it for itself would be
an adapter that knows which database it is talking to.
"""

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any, Protocol, runtime_checkable

from academy.config.container import Container, Scope
from academy.config.settings import (
    ENV_BOOTSTRAP_ADMIN,
    ENV_IDENTITY,
    ENV_PERSISTENCE,
    ENV_SECRET_KEY,
    ConfigurationError,
    Defaults,
    Environ,
    IdentityBackend,
    PersistenceBackend,
    Settings,
)

type AsgiMessage = MutableMapping[str, Any]
type AsgiReceive = Callable[[], Awaitable[AsgiMessage]]
type AsgiSend = Callable[[AsgiMessage], Awaitable[None]]


@runtime_checkable
class AsgiApplication(Protocol):
    """What ``create_app`` returns, said precisely without naming a framework.

    An ASGI application is a callable of three arguments and nothing more, so the interface can
    be spelled here in the standard library alone -- which is what lets :func:`create_app`
    promise something better than ``object`` while still importing FastAPI lazily.

    ``Any`` inside the message type is ASGI's own, not ours: a scope carries a path (``str``), a
    header list (``list[tuple[bytes, bytes]]``) and a server address (``tuple[str, int]``) under
    the same mapping, and the specification types it that way. Narrowing it to ``object`` here
    would make FastAPI stop satisfying this protocol for a precision nobody gains.
    """

    async def __call__(self, scope: AsgiMessage, receive: AsgiReceive, send: AsgiSend) -> None:
        """Serve one connection."""
        ...


def create_app(environ: Environ | None = None) -> AsgiApplication:
    """Build the web application this deployment's environment describes.

    The entry point ``uvicorn --factory academy.config:create_app`` calls, and the one place the
    environment is read for a web process.

    Imported inside the function rather than at module scope, and this is load-bearing: the web
    adapter needs the ``web`` extra, while the CLI needs no extra at all (ADR-0020). A top-level
    import here would make ``python -m academy config show`` fail with ``ModuleNotFoundError:
    fastapi`` on a bare ``uv sync`` -- the hexagon's own dependency-free core reintroduced as a
    hard dependency by the composition root, which is precisely the thing the extras exist to
    avoid.

    Args:
        environ: Where to read settings from. Defaults to ``os.environ``.

    Returns:
        The ASGI application, typed as :class:`AsgiApplication` rather than as ``FastAPI`` --
        naming the class here would import it at module scope and undo the deferral above. The
        protocol says everything a caller of this function needs: uvicorn wants an ASGI callable
        and nothing more. The web adapter's own
        :func:`academy.adapters.inbound.web.create_app` returns the concrete ``FastAPI``, and
        this is the wrapper that reads the environment first.

    Raises:
        ConfigurationError: If the environment describes something that cannot be built -- an
            unknown backend, a ``static`` identity with no id, or a durable deployment with no
            signing key. All at startup, before a single request is served.
    """
    from academy.adapters.inbound.web import create_app as build

    return build(Container.from_env(environ))


__all__ = [
    'ENV_BOOTSTRAP_ADMIN',
    'ENV_IDENTITY',
    'ENV_PERSISTENCE',
    'ENV_SECRET_KEY',
    'ConfigurationError',
    'Environ',
    'Defaults',
    'Container',
    'IdentityBackend',
    'PersistenceBackend',
    'Scope',
    'Settings',
    'AsgiApplication',
    'create_app',
]
