"""Assembling the application: what is built once, and what is built per request.

The mirror of :func:`academy.adapters.inbound.cli.main.main` -- the whole of the adapter's
control flow, deliberately small. Build the process-lifetime things, hang them where a dependency
can find them, register the routers and the one error boundary, and hand back an ASGI app.

The container's lifetime is the application's, managed by the lifespan rather than by module
import, which is what lets a test build an app, drive it and dispose of its engine without
leaking a connection pool into the next test. On Windows it is also what lets a temporary SQLite
file be deleted afterwards.

This module and :func:`academy.adapters.inbound.web.dependencies.scope` are the **only** two
places in the adapter that may name a :class:`~academy.config.container.Scope` or a
:class:`~academy.config.container.Container`. A route that named either could answer a request
without going through a use case.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from academy.adapters.inbound.web import errors
from academy.adapters.inbound.web.rendering import STATIC_DIRECTORY, Templates
from academy.adapters.inbound.web.routers import api, auth, grades
from academy.adapters.inbound.web.security import Credentials
from academy.config.container import Container

STATIC_PATH = '/static'


def create_app(container: Container) -> FastAPI:
    """Build the ASGI application over an already-wired container.

    Takes the container rather than building one, because deciding what a deployment runs is the
    composition root's job and this is an adapter (ADR-0015). ``academy.config.create_app`` is
    the factory a deployment actually names; it reads the environment and calls this.

    Args:
        container: The process-lifetime half of the composition root. Its lifetime becomes the
            application's -- ``aclose`` is called on shutdown.

    Returns:
        The application, ready to serve.

    Raises:
        ConfigurationError: If this deployment is durable and set no signing key. Raised here,
            while the app is being built, so a misconfigured web process refuses to start rather
            than failing on its first sign-in.
    """

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        """Hold the container open for the life of the application."""
        try:
            yield
        finally:
            await container.aclose()

    app = FastAPI(
        title='academy',
        summary='Academic records, driven by a browser and by a JSON client over the same use cases.',
        lifespan=lifespan,
    )

    # Asked for *before* anything is registered, so a durable deployment with no key fails while
    # it is starting rather than when someone first tries to sign in.
    templates = Templates()
    app.state.container = container
    app.state.credentials = Credentials(container.secret_key)
    app.state.templates = templates

    app.mount(STATIC_PATH, StaticFiles(directory=STATIC_DIRECTORY), name='static')
    app.include_router(auth.router)
    app.include_router(grades.router)
    app.include_router(api.router)
    errors.install(app, templates)

    @app.get('/healthz', include_in_schema=False)
    async def healthz() -> dict[str, str]:
        """Liveness, answering without a database.

        Deliberately shallow: it says this process is up and serving, which is the question a
        load balancer is asking. A check that touched the database would take a healthy process
        out of rotation for a database problem that removing it cannot fix.
        """
        return {'status': 'ok'}

    @app.get('/', include_in_schema=False)
    async def index() -> RedirectResponse:
        """There is no landing page yet, so start where the one implemented screen is.

        A redirect rather than a stub, because a stub page would be a thing to maintain and to
        mistake for a decision. When the records and imports routes land this becomes a real
        index; until then it points at what exists.
        """
        return RedirectResponse('/sign-in', status_code=303)

    return app
