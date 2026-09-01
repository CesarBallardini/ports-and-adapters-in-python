"""The htmx contract, in one place, because the alternative is thirty places that disagree.

ADR-0011 asks for exactly this module and names the reason: htmx's rules about *when a response
is swapped into the page* are easy to re-derive slightly differently in every handler, and the
delete route that got it wrong is the one nobody tests.

The rule that costs people the most:

    **htmx 2 does not swap a non-2xx response.** Its default ``responseHandling`` is
    ``[{code:"204",swap:false},{code:"[23]..",swap:true},{code:"[45]..",swap:false,error:true}]``.

So a route that honestly answers 403 -- which is what ADR-0012's table says to answer -- produces
a page where *nothing happens*. The usual fix is to stop being honest and return 200 with an
error in the body, which throws away the status every non-browser client depends on. The fix here
is to change htmx's mind instead, once: :data:`HTMX_RESPONSE_HANDLING` makes 4xx swappable, is
rendered into ``base.html``'s ``htmx-config`` meta tag, and is asserted by a unit test. Failures
then arrive with their real status *and* land somewhere the user can see, and
:func:`failure_headers` says where.

Templates receive DTOs and never domain entities (ADR-0011), which is not a style rule: a
template holding a ``CourseSection`` could call ``enroll`` on it, and a page render would become
a write. ``StrictUndefined`` is on for the same family of reasons -- a renamed DTO field should
break the build rather than render an empty cell that looks like a student with no grade.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from enum import Enum
from pathlib import Path
from typing import Final, NotRequired, TypedDict

from fastapi import Request, Response
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

TEMPLATES_DIRECTORY: Final = Path(__file__).parent / 'templates'
STATIC_DIRECTORY: Final = Path(__file__).parent / 'static'

# Where a failure is shown when the request came from htmx. One region per page, declared in
# `base.html`, so every handler's errors land in the same place without any handler choosing.
ERROR_TARGET: Final = '#academy-errors'

HX_REQUEST_HEADER: Final = 'HX-Request'
HX_RETARGET_HEADER: Final = 'HX-Retarget'
HX_RESWAP_HEADER: Final = 'HX-Reswap'


class ResponseRule(TypedDict):
    """One entry of htmx's ``responseHandling`` array.

    Typed rather than left as a dict of ``object``, so a rule with a misspelled key is a type
    error here instead of a rule htmx silently ignores at runtime.
    """

    code: str
    swap: bool
    error: NotRequired[bool]


# The whole of the adapter's answer to htmx's swap rule, and the only copy of it.
#
# Identical to htmx 2.0.7's default except for the 4xx line, which is split out of `[45]..` and
# given `swap:true`. 5xx stays unswapped: a 500 is a bug of ours and its body is a traceback or a
# generic apology, neither of which belongs spliced into a page.
#
# `error:true` is kept on the 4xx rule so `htmx:responseError` still fires -- a page that wants
# to react to a failure can, and does not have to infer one from the content it received.
HTMX_RESPONSE_HANDLING: Final[tuple[ResponseRule, ...]] = (
    {'code': '204', 'swap': False},
    {'code': '[23]..', 'swap': True},
    {'code': '4..', 'swap': True, 'error': True},
    {'code': '5..', 'swap': False, 'error': True},
)


class Surface(Enum):
    """Which rendering a request asked for, decided by the router it entered.

    Not sniffed from the path and not negotiated from ``Accept``. The router already knows which
    it is -- that is what makes it a different router -- and a string test like
    ``path.startswith('/api/')`` is the kind of thing that is written once, copied, and then
    wrong everywhere at the same time.
    """

    WEB = 'web'
    API = 'api'


# The attribute the surface is stashed under on `request.state`. Prefixed, because `state` is a
# single namespace shared with anything else that ever wants to put something there.
_SURFACE_ATTRIBUTE: Final = 'academy_surface'


def mark(request: Request, surface: Surface) -> None:
    """Record which rendering this request's router speaks.

    Called by each router's own dependency, which is the one place that knows the answer.
    """
    setattr(request.state, _SURFACE_ATTRIBUTE, surface)


def surface_of(request: Request) -> Surface:
    """Read back the rendering this request asked for.

    ``request.state`` is untyped -- every attribute on it is ``Any`` -- so this is where that
    stops, the same way :class:`~academy.adapters.inbound.cli.parser.Args` is where argparse's
    ``Any`` stops. The ``isinstance`` is a real run-time check and not a cast, because the value
    genuinely arrives untyped.

    Returns:
        The surface the router marked, or :attr:`Surface.API` if none did. Nothing reaches the
        error boundary without passing through a router today, so the default only covers a
        future route that forgets -- and JSON is the safer thing to hand something unidentified
        than a page built for a browser.
    """
    marked = getattr(request.state, _SURFACE_ATTRIBUTE, None)
    return marked if isinstance(marked, Surface) else Surface.API


def is_htmx(request: Request) -> bool:
    """Whether htmx made this request, rather than the address bar.

    The distinction decides *shape*, never *content*: an htmx request gets the fragment that
    changed and a plain one gets the whole page, and both are built from the same DTO.
    """
    return request.headers.get(HX_REQUEST_HEADER) == 'true'


def failure_headers(request: Request) -> dict[str, str]:
    """The headers that put a failure somewhere the user will actually see it.

    An htmx request targets whatever element it came from -- a table row, a button -- and
    swapping an error message into that is how a grade sheet ends up with an error message where
    a row used to be. ``HX-Retarget`` sends it to the page's error region instead and
    ``HX-Reswap`` replaces that region's contents.

    Returns:
        The two headers for an htmx request, and nothing at all for a plain one, which is
        getting a whole error page and has nothing to retarget.
    """
    if not is_htmx(request):
        return {}
    return {HX_RETARGET_HEADER: ERROR_TARGET, HX_RESWAP_HEADER: 'innerHTML'}


class Templates:
    """The Jinja2 environment, and the two shapes anything is ever rendered in.

    Built once at startup. Holding it rather than reaching for a module-level environment is
    what lets a test render against the real templates without starting an application.
    """

    def __init__(self, directory: Path = TEMPLATES_DIRECTORY) -> None:
        """Build the environment.

        Args:
            directory: Where the templates live. Defaulted rather than injected from settings:
                these ship inside the package and a deployment that could point them elsewhere
                could point them at anything.
        """
        self._environment = Environment(
            loader=FileSystemLoader(directory),
            autoescape=select_autoescape(('html',)),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        # Rendered once and then handed to every template, rather than registered as a Jinja
        # global: `Environment.globals` is typed from its own defaults, so assigning a string
        # into it is a type error, and threading it through the context keeps the value visible
        # in the one function that builds a context instead of hidden in environment state.
        self._htmx_config = htmx_config_json()

    def page(self, name: str, context: Mapping[str, object], *, status_code: int = 200) -> HTMLResponse:
        """Render a full page: everything inside ``base.html``.

        What the address bar gets, and what an htmx request gets when it asked for a whole
        screen rather than a piece of one.
        """
        return HTMLResponse(
            self._environment.get_template(name).render(self._with_config(context)), status_code=status_code
        )

    def _with_config(self, context: Mapping[str, object]) -> dict[str, object]:
        """Add the htmx configuration every page's ``base.html`` renders into its meta tag.

        Added first, so a caller could in principle override it -- and no caller does, which is
        the point: there is one configuration and it is :data:`HTMX_RESPONSE_HANDLING`.
        """
        return {'htmx_config': self._htmx_config, **context}

    def fragment(
        self,
        name: str,
        context: Mapping[str, object],
        *,
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
    ) -> Response:
        """Render one piece of a page, for htmx to swap in.

        The row-replacement pattern ADR-0011 names: ``hx-post`` a form, get back the single row
        that changed. A fragment extends nothing and is not a document -- returning a full page
        here would nest an ``<html>`` inside a ``<td>``.
        """
        return HTMLResponse(
            self._environment.get_template(name).render(self._with_config(context)),
            status_code=status_code,
            headers=dict(headers) if headers else None,
        )


def htmx_config_json() -> str:
    """Render :data:`HTMX_RESPONSE_HANDLING` as the ``htmx-config`` meta tag's content.

    A meta tag rather than an inline ``<script>``: htmx reads
    ``<meta name="htmx-config" content="...">`` and merges it over its defaults, and doing it
    that way keeps the page free of inline script for a deployment that adds a content-security
    policy later.

    Generated from the Python value rather than typed into the template, so the constant above
    is the single source and a template cannot drift from it.
    """
    return json.dumps({'responseHandling': [dict(rule) for rule in HTMX_RESPONSE_HANDLING]}, separators=(',', ':'))
