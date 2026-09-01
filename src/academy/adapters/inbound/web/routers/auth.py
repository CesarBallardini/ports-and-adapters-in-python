"""Signing in and out: the only routes that touch a credential.

Everything the rest of the adapter does happens *after* this, with an
:class:`~academy.application.dtos.Actor` already in hand. Keeping that boundary in one small file
is what makes ADR-0010's claim inspectable rather than merely asserted -- a reader can see the
whole of "how a person becomes an actor" without reading anything else.

**The credential check here is a placeholder** and
:func:`~academy.adapters.inbound.web.security.verify_credentials` carries the banner saying so.
What is real is everything around it: the session is genuinely signed, genuinely expires, and
carries an id and no roles, so authorization is decided fresh on every request from the person
record rather than from whatever was true at sign-in.

There is no ``/sign-up``, and none is coming here. Creating a person is an administrative use
case behind a driving port that does not exist yet, and a sign-up form that wrote a person row
directly would be the adapter reaching past the application layer to do it.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import RedirectResponse

from academy.adapters.inbound.web.csrf import enforce, token_for
from academy.adapters.inbound.web.dependencies import PageTemplates, SignInCredentials, SignInPeople, web_surface
from academy.adapters.inbound.web.security import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    SESSION_MAX_AGE_SECONDS,
    verify_credentials,
)

router = APIRouter(dependencies=[Depends(web_surface), Depends(enforce)], tags=['auth'])

# 303 and not 302: the browser must follow a successful POST with a GET, so a reload of the
# landing page does not re-submit the sign-in form.
_SEE_OTHER = 303


@router.get('/sign-in')
async def sign_in_form(request: Request, templates: PageTemplates) -> Response:
    """Show the sign-in form, carrying the token its POST will have to present."""
    token = token_for(request)
    response = templates.page('sign_in.html', {'csrf_token': token.value, 'error': None})
    response.set_cookie(CSRF_COOKIE, token.value, httponly=True, samesite='lax', max_age=SESSION_MAX_AGE_SECONDS)
    return response


@router.post('/sign-in')
async def sign_in(
    request: Request,
    templates: PageTemplates,
    people: SignInPeople,
    signers: SignInCredentials,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
) -> Response:
    """Establish a session, or say that the credentials were not accepted.

    A rejected sign-in re-renders the form with one message and a 401. It does not say whether
    the address was unknown or the password wrong, because answering that would turn the form
    into a way of finding out who has an account here.
    """
    person = await verify_credentials(people, email, password)
    if person is None:
        token = token_for(request)
        response = templates.page(
            'sign_in.html',
            {'csrf_token': token.value, 'error': 'Those credentials were not accepted.'},
            status_code=401,
        )
        response.set_cookie(CSRF_COOKIE, token.value, httponly=True, samesite='lax', max_age=SESSION_MAX_AGE_SECONDS)
        return response

    response = RedirectResponse('/', status_code=_SEE_OTHER)
    response.set_cookie(
        SESSION_COOKIE,
        signers.issue_session(person.id),
        httponly=True,
        samesite='lax',
        max_age=SESSION_MAX_AGE_SECONDS,
    )
    return response


@router.post('/sign-out')
async def sign_out() -> Response:
    """Discard the session.

    A POST rather than a GET, so that a link on another site -- or a prefetching browser -- cannot
    sign someone out, and so the CSRF check on the browser router applies to it like any other
    unsafe request.
    """
    response = RedirectResponse('/sign-in', status_code=_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE, httponly=True, samesite='lax')
    return response
