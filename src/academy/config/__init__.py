"""The composition root: settings, and the wiring of adapters into ports.

This is the only module in the codebase permitted to know both a port and the concrete
adapter that satisfies it. Every choice the rest of the application is deliberately
ignorant of -- SQLite or PostgreSQL, CSV or XLSX, local disk or S3, inline or queued --
is made exactly once, here.

It therefore depends on every layer, which is why it sits outside the layers contract in
``.importlinter`` rather than being exempted from it.

The wiring is written by hand: no dependency-injection container, for the reasons and at the
cost recorded in ADR-0015. ``create_app()`` -- the ASGI factory the Makefile's ``run`` target
points at -- arrives with the web adapter in Phase C; until then the drivers are the test
suite and, soon, the CLI, and both build their graph through :meth:`Container.request_scope`.
"""

from academy.config.container import Container, Scope
from academy.config.settings import (
    ENV_PERSISTENCE,
    ConfigurationError,
    Defaults,
    Environ,
    PersistenceBackend,
    Settings,
)

__all__ = [
    'ENV_PERSISTENCE',
    'ConfigurationError',
    'Environ',
    'Defaults',
    'Container',
    'PersistenceBackend',
    'Scope',
    'Settings',
]
