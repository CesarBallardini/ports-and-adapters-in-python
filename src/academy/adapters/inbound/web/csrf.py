"""Cross-site request forgery, handled for the cookie and deliberately not for the token.

The double-submit pattern: a page is served carrying a random value in both a cookie and its
form, and an unsafe request must present the two matching. A cross-site page can *cause* a
request to be sent with the cookie -- browsers attach cookies to cross-site form posts, which is
the whole vulnerability -- but it cannot read the cookie to know what to put in the body.

Only the browser router installs this. An ``Authorization: Bearer`` request cannot be forged this
way because nothing sends that header on a caller's behalf: the attacker's page would have to
already know the token, at which point it does not need a forgery. Checking it there would be
ceremony that suggests a protection nobody actually gets from it.

The token is not tied to a session and does not need to be. Its job is to prove the request came
from a page this application served, which is a different claim from who is making it, and the
session cookie already answers the second one.
"""

from __future__ import annotations

from typing import Final

from fastapi import Request

from academy.adapters.inbound.web.security import (
    CSRF_COOKIE,
    CSRF_FIELD,
    CSRF_HEADER,
    CsrfFailedError,
    CsrfToken,
    csrf_matches,
)

# The methods that do not change anything, and so cannot be forged into changing anything.
# Spelled out rather than "not POST", because HEAD and OPTIONS are equally safe and a browser
# issues both without anyone asking.
SAFE_METHODS: Final = frozenset({'GET', 'HEAD', 'OPTIONS', 'TRACE'})

_FORM_CONTENT_TYPES: Final = ('application/x-www-form-urlencoded', 'multipart/form-data')


def token_for(request: Request) -> CsrfToken:
    """The token this page should carry: the one already in the cookie, or a fresh one.

    Reusing an existing cookie matters for htmx: a fragment swapped into a page cannot set a
    cookie the rest of that page will see in time, so minting a new value on every render would
    leave older forms on the page carrying a token that no longer matches.
    """
    existing = request.cookies.get(CSRF_COOKIE)
    return CsrfToken(existing) if existing else CsrfToken.issue()


async def enforce(request: Request) -> None:
    """Refuse an unsafe browser request that did not prove where it came from.

    Attached to the browser router as a dependency, so it runs before any route body and cannot
    be forgotten by a route added later.

    The header is checked before the form because htmx sends it that way (``hx-headers``), and
    because reading the body here would consume it for a route that has not looked yet. Starlette
    caches a parsed form, so the fallback is safe for the routes that do submit one.

    Raises:
        CsrfFailedError: If the request is unsafe and the two values are absent or do not match.
    """
    if request.method in SAFE_METHODS:
        return

    submitted = request.headers.get(CSRF_HEADER)
    if submitted is None and request.headers.get('content-type', '').startswith(_FORM_CONTENT_TYPES):
        value = (await request.form()).get(CSRF_FIELD)
        submitted = value if isinstance(value, str) else None

    if not csrf_matches(request.cookies.get(CSRF_COOKIE), submitted):
        raise CsrfFailedError
