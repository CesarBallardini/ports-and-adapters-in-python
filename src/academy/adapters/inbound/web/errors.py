"""The one place this adapter turns an exception into a response.

Same rule as the CLI's ``main``, in FastAPI's idiom: **no route contains** ``except DomainError``.
A route calls a use case, lets it raise, and the handlers registered here consult the shared
table (ADR-0012) and render the answer. That is what stops two surfaces disagreeing about what a
conflict is, and what stopped the reference application growing thirty translation sites that did
not agree with each other.

The table classifies; it does not render (ADR-0019). :func:`~academy.adapters.inbound.error_status.http_status`
is the HTTP rendering of a :class:`~academy.adapters.inbound.error_status.Failure`, fixed by
ADR-0012, and it is already written and already asserted -- this module is its first caller and
adds no classification of its own.

What *is* new here is that one status has two shapes. The JSON router answers a machine and the
browser router answers a person, and ADR-0019's principle applies one level down: the
classification is shared, the rendering is not. Which one a request gets is decided by the router
it entered (:func:`~academy.adapters.inbound.web.rendering.surface_of`), never by sniffing its
path or negotiating its ``Accept`` header.

An exception the table does not classify is a bug of ours. It is deliberately not handled here:
it propagates, Starlette answers 500, and the traceback reaches the log intact -- the same
decision the CLI makes when it lets an unclassified error exit 1 with its traceback. A bug that
looks like a user error is a bug nobody fixes.
"""

from __future__ import annotations

from typing import Final

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from academy.adapters.inbound.error_status import Failure, classify, http_status
from academy.adapters.inbound.web import rendering
from academy.adapters.inbound.web.rendering import Surface, Templates
from academy.adapters.inbound.web.security import CsrfFailedError, NotAuthenticatedError
from academy.application.errors import ApplicationError
from academy.domain.shared.errors import DomainError

UNAUTHENTICATED_STATUS: Final = 401
CSRF_STATUS: Final = 403
SIGN_IN_PATH: Final = '/sign-in'

# Where htmx is told to send a browser that has lost its session. A 401 body swapped into a page
# would put a sign-in form inside a table cell; this replaces the whole document instead.
HX_REDIRECT_HEADER: Final = 'HX-Redirect'


def install(app: FastAPI, templates: Templates) -> None:
    """Register every handler this adapter has.

    Args:
        app: The application to install them on.
        templates: The environment error pages are rendered with. Passed in rather than resolved
            per request, because a handler that had to look up its own dependencies would be one
            more thing that can fail while something else is already failing.
    """

    async def handle_expected_failure(request: Request, error: Exception) -> Response:
        """Render a use case's expected failure, per ADR-0012's table."""
        return _render(request, templates, status=http_status(error), failure=classify(error), detail=str(error))

    async def handle_not_authenticated(request: Request, error: Exception) -> Response:
        """Send an unidentified caller to sign in, or tell a script it was not identified."""
        del error  # One response for every way of not being authenticated; see NotAuthenticatedError.
        if rendering.surface_of(request) is Surface.API:
            return JSONResponse({'error': 'not_authenticated'}, status_code=UNAUTHENTICATED_STATUS)
        if rendering.is_htmx(request):
            # A full-page redirect, not a swap: the session is gone, so no fragment of this page
            # is still meaningful.
            return Response(status_code=UNAUTHENTICATED_STATUS, headers={HX_REDIRECT_HEADER: SIGN_IN_PATH})
        return RedirectResponse(SIGN_IN_PATH, status_code=303)

    async def handle_csrf(request: Request, error: Exception) -> Response:
        """Refuse an unsafe browser request whose token did not match its cookie."""
        del error
        return _render(request, templates, status=CSRF_STATUS, failure=None, detail='the form was not from this site')

    # `ApplicationError` and `DomainError` are separate registrations rather than one on a shared
    # base, because they have none: the domain is copied and knows nothing of ours (ADR-0002).
    app.add_exception_handler(ApplicationError, handle_expected_failure)
    app.add_exception_handler(DomainError, handle_expected_failure)
    app.add_exception_handler(NotAuthenticatedError, handle_not_authenticated)
    app.add_exception_handler(CsrfFailedError, handle_csrf)


def _render(
    request: Request,
    templates: Templates,
    *,
    status: int,
    failure: Failure | None,
    detail: str,
) -> Response:
    """Render one failure in the vocabulary the request's router speaks.

    The status is the same either way and comes from the same table. Only the body differs, and
    for the browser only the *shape* differs again -- an htmx request gets the error fragment
    retargeted at the page's error region, a plain one gets a whole error page.

    Args:
        request: The request that failed, carrying the surface its router marked.
        templates: The environment to render HTML with.
        status: The HTTP status ADR-0012's table assigns.
        failure: The classification, or ``None`` when this is not a use-case failure at all.
        detail: The message shown to a person and returned to a script.
    """
    if rendering.surface_of(request) is Surface.API:
        return JSONResponse(
            {'error': failure.value if failure else 'error', 'detail': detail},
            status_code=status,
        )

    context = {'failure': failure.value if failure else 'error', 'detail': detail, 'status': status}
    if rendering.is_htmx(request):
        return templates.fragment(
            '_error.html', context, status_code=status, headers=rendering.failure_headers(request)
        )
    return templates.page('error.html', context, status_code=status)
