"""One dependency per driving port, and the actor every route carries.

The rule the CLI established, kept across a change of protocol: **a handler takes one driving
port, never the** :class:`~academy.config.container.Scope`. There it was ``Command[PortT]``
pairing a handler with the accessor for its port; here it is a FastAPI dependency per port. The
mechanism is different and the property is the same, which is the point -- it was a property of
the architecture and not of argparse.

Why it matters is worth restating, because a scope looks harmless. It carries every repository as
well as every use case, so a route holding one could answer ``GET /sections/{id}/grades`` by
reading ``scope.histories`` directly -- no use case, no ``AccessGuard``, no authorization -- and
the result would look right in every test anyone thought to write. The rule removes the
possibility instead of relying on nobody taking it.

**Exactly two places in the adapter may name a** ``Scope``: :func:`scope` below, and the lifespan
in :mod:`academy.adapters.inbound.web.app` that owns the container it comes from. A third is a
bug, and ``tests/adapters/test_web_dependencies.py`` fails if a route grows one.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request

from academy.adapters.inbound.web import rendering
from academy.adapters.inbound.web.rendering import Surface, Templates
from academy.adapters.inbound.web.security import SESSION_COOKIE, Credentials, NotAuthenticatedError
from academy.application.dtos import Actor
from academy.application.ports.inbound.grading import ManageGrades
from academy.application.ports.outbound.repositories import PersonRepository
from academy.config.container import Container, Scope


def container_of(request: Request) -> Container:
    """The process-lifetime half of the composition root, put on the app at startup.

    ``app.state`` is untyped, so the ``isinstance`` is where that ``Any`` stops. Its failure mode
    is a programming error rather than a request error -- the app was built without a container,
    which no request can cause and none can fix -- so it raises rather than becoming a status.
    """
    container = getattr(request.app.state, 'container', None)
    if not isinstance(container, Container):
        raise RuntimeError('the application was built without a container; use academy.config.create_app')
    return container


def credentials_of(request: Request) -> Credentials:
    """The session and token signers, built once at startup from the container's key."""
    signers = getattr(request.app.state, 'credentials', None)
    if not isinstance(signers, Credentials):
        raise RuntimeError('the application was built without credentials; use academy.config.create_app')
    return signers


def templates_of(request: Request) -> Templates:
    """The Jinja2 environment, built once at startup."""
    templates = getattr(request.app.state, 'templates', None)
    if not isinstance(templates, Templates):
        raise RuntimeError('the application was built without templates; use academy.config.create_app')
    return templates


async def scope(request: Request) -> AsyncIterator[Scope]:
    """Open one scope for one request, and close it when the response is done.

    One of the two places allowed to name a ``Scope``. Everything else in the adapter depends on
    a *port* built from this, which FastAPI caches per request -- so the whole request shares one
    scope, one session and one transaction boundary, exactly as a CLI invocation does.
    """
    async with container_of(request).request_scope() as opened:
        yield opened


ScopeDependency = Annotated[Scope, Depends(scope)]


async def current_actor(request: Request, opened: ScopeDependency) -> Actor:
    """Resolve who is making this request, from whichever credential they presented.

    The cookie is tried first and the header second, which is only an ordering and not a
    precedence anyone should rely on: a request carrying both is a client confused about what it
    is, and either answer would be defensible.

    Both paths end at the same :class:`~academy.application.ports.outbound.identity.ActorIdentity`
    call, and that is ADR-0010's claim in one line of code -- what leaves here is an ``Actor``,
    and nothing downstream can tell which door it came through.

    Roles come from that resolution and never from the credential, so an administrator demoted
    since they signed in is demoted on their next request rather than on their next sign-in.

    Raises:
        NotAuthenticatedError: If no credential was presented, if it does not verify, or if it names
            a person who no longer exists. One exception for all three -- the response is the
            same and distinguishing them would only report which part of a guess was right.
    """
    signers = credentials_of(request)
    person_id = signers.read_session(request.cookies.get(SESSION_COOKIE))
    if person_id is None:
        person_id = signers.read_token(request.headers.get('Authorization'))
    if person_id is None:
        raise NotAuthenticatedError

    actor = await opened.identity.resolve(person_id)
    if actor is None:
        # A credential that verifies but names nobody: a person deleted while signed in. The port
        # promises `None` here rather than an actor with no roles, and the difference is the
        # whole reason this is 401 and not 403.
        raise NotAuthenticatedError
    return actor


def grade_management(opened: ScopeDependency) -> ManageGrades:
    """Build the grading use cases for this request.

    Returns the *inbound port*, never the implementing class, so a route cannot reach past it
    into ``GradeManagement``'s collaborators.
    """
    return opened.grade_management()


def people_for_sign_in(opened: ScopeDependency) -> PersonRepository:
    """The one repository an adapter is allowed to hold, and only for signing in.

    Authentication is not a use case and has no driving port: ADR-0010 places credential
    verification at the adapter edge on purpose, because a cookie, a token and a password are
    things the application layer is meant never to have heard of. There is therefore nothing to
    depend on except a repository, and this names it narrowly -- one repository, read-only in
    practice -- rather than handing the sign-in route a whole scope.

    It is the exception that proves the rule holds everywhere else: every other route in this
    adapter reaches the database only through a use case.
    """
    return opened.people


def web_surface(request: Request) -> None:
    """Mark this request as one the browser router owns.

    Attached to the router rather than to each route, so a route added later cannot forget it
    and quietly start answering a browser with JSON.
    """
    rendering.mark(request, Surface.WEB)


def api_surface(request: Request) -> None:
    """Mark this request as one the JSON router owns."""
    rendering.mark(request, Surface.API)


# The names routes actually spell. An alias per port, so a route signature reads as the one
# capability it needs -- and so adding a port here is the deliberate act of widening what some
# route may reach, rather than a parameter someone tacked on.
CurrentActor = Annotated[Actor, Depends(current_actor)]
Grades = Annotated[ManageGrades, Depends(grade_management)]
PageTemplates = Annotated[Templates, Depends(templates_of)]
SignInCredentials = Annotated[Credentials, Depends(credentials_of)]
SignInPeople = Annotated[PersonRepository, Depends(people_for_sign_in)]
