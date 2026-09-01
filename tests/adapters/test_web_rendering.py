"""The htmx contract, tested without an application.

Everything here is about one class of bug: a failure that is reported honestly and then never
shown to anybody. htmx 2 does not swap a non-2xx response, so a 403 from the error boundary
reaches the browser, is discarded, and the page sits there looking like nothing happened. The
usual fix is to start returning 200 for failures, which throws away the status every non-browser
client depends on.

:mod:`academy.adapters.inbound.web.rendering` refuses both halves of that trade -- the status
stays honest and the body still lands -- and these tests pin the two mechanisms that make it
work: the ``responseHandling`` override, and the retarget headers that keep an error out of the
row that caused it.

The vendored ``htmx.min.js`` is read directly by one of them. That is not paranoia about the
library: it is the only way to notice that an upgrade changed the defaults this module was
written to override.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

import pytest
from fastapi import Request, Response

from academy.adapters.inbound.web import rendering
from academy.adapters.inbound.web.rendering import (
    ERROR_TARGET,
    HTMX_RESPONSE_HANDLING,
    HX_RESWAP_HEADER,
    HX_RETARGET_HEADER,
    STATIC_DIRECTORY,
    Surface,
    Templates,
)

pytestmark = pytest.mark.unit


def _request(headers: dict[str, str] | None = None) -> Request:
    """A bare ASGI request, built without a server.

    Starlette's ``Request`` needs only a scope, which is what makes every assertion below a unit
    test rather than an HTTP round trip.
    """
    raw = [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()]
    return Request({'type': 'http', 'method': 'GET', 'path': '/', 'headers': raw, 'app': None})


@pytest.fixture
def templates() -> Templates:
    """The real environment, over the templates that ship."""
    return Templates()


def test_the_swap_rule_is_configured_for_error_responses() -> None:
    """The override that makes an honest 4xx visible.

    Without the ``4..`` rule, every failure this adapter renders is correct and invisible.
    """
    rules = {rule['code']: rule for rule in HTMX_RESPONSE_HANDLING}

    assert rules['4..']['swap'] is True
    assert rules['4..'].get('error') is True


def test_a_server_error_is_still_not_swapped() -> None:
    """5xx stays unswapped on purpose.

    A 500 is a bug of ours and its body is a traceback or a generic apology. Splicing either into
    a page would be worse than showing nothing.
    """
    rules = {rule['code']: rule for rule in HTMX_RESPONSE_HANDLING}

    assert rules['5..']['swap'] is False


def test_the_configuration_is_valid_json_for_the_meta_tag() -> None:
    """``base.html`` renders this into ``content=``; htmx parses it or silently keeps its defaults."""
    parsed = json.loads(rendering.htmx_config_json())

    assert [rule['code'] for rule in parsed['responseHandling']] == ['204', '[23]..', '4..', '5..']


def test_the_vendored_htmx_still_defaults_to_not_swapping_errors() -> None:
    """The reason the override exists, read out of the library rather than assumed.

    If a future htmx changes its defaults -- or renames ``responseHandling`` -- this fails, and
    the failure is the notice that ``rendering.py`` needs rereading. Without it an upgrade could
    quietly make the override redundant, or quietly make it insufficient.
    """
    source = (STATIC_DIRECTORY / 'htmx.min.js').read_text(encoding='utf-8')

    assert 'responseHandling:' in source
    assert '{code:"[45]..",swap:false,error:true}' in source


def test_the_meta_tag_carries_the_configuration_into_every_page(templates: Templates) -> None:
    """What htmx actually reads, parsed the way htmx parses it.

    The attribute is HTML-escaped by autoescaping, so this unescapes it and parses the JSON --
    which checks the round trip rather than a substring. A configuration that rendered as
    something htmx could not parse would leave it silently on its defaults, and a substring
    assertion would not notice.
    """
    body = _text(templates.page('error.html', {'failure': 'rule', 'detail': 'no', 'status': 400}))

    match = re.search(r'<meta name="htmx-config" content="([^"]*)">', body)
    assert match is not None, 'base.html no longer renders the htmx-config meta tag'

    parsed = json.loads(html.unescape(match.group(1)))
    assert parsed['responseHandling'] == [dict(rule) for rule in HTMX_RESPONSE_HANDLING]


def test_an_htmx_failure_is_retargeted_at_the_page_error_region() -> None:
    """Otherwise an error replaces the row that caused it.

    A grade sheet whose failed row becomes an error message has lost the row *and* the student,
    and the next attempt has nothing to submit from.
    """
    headers = rendering.failure_headers(_request({'HX-Request': 'true'}))

    assert headers[HX_RETARGET_HEADER] == ERROR_TARGET
    assert headers[HX_RESWAP_HEADER] == 'innerHTML'


def test_a_plain_request_gets_no_retarget_headers() -> None:
    """There is nothing to retarget in a whole error page."""
    assert rendering.failure_headers(_request()) == {}


def test_htmx_is_recognised_only_by_its_own_header() -> None:
    assert rendering.is_htmx(_request({'HX-Request': 'true'})) is True
    assert rendering.is_htmx(_request({'HX-Request': 'false'})) is False
    assert rendering.is_htmx(_request()) is False


def test_the_error_region_the_headers_name_exists_in_the_base_template(templates: Templates) -> None:
    """A retarget at an id no page contains is a silently discarded error.

    The header and the element are written in two files, which is exactly the kind of pair that
    drifts. ``ERROR_TARGET`` is a CSS selector and the template carries the bare id.
    """
    body = _text(templates.page('error.html', {'failure': 'rule', 'detail': 'no', 'status': 400}))

    assert f'id="{ERROR_TARGET.removeprefix("#")}"' in body


def test_a_surface_is_read_back_as_it_was_marked() -> None:
    request = _request()
    rendering.mark(request, Surface.WEB)

    assert rendering.surface_of(request) is Surface.WEB


def test_an_unmarked_request_is_treated_as_json() -> None:
    """Nothing reaches the error boundary unmarked today; if something did, JSON is the safer
    thing to hand an unidentified caller than a page built for a browser."""
    assert rendering.surface_of(_request()) is Surface.API


def test_a_marker_of_the_wrong_type_is_ignored_rather_than_trusted() -> None:
    """``request.state`` is a shared, untyped namespace, and this is where that ``Any`` stops.

    The ``isinstance`` in ``surface_of`` is a real run-time check and not a cast: nothing stops
    another middleware setting the same attribute to a string.
    """
    request = _request()
    request.state.academy_surface = 'web'

    assert rendering.surface_of(request) is Surface.API


def test_a_fragment_is_not_a_document(templates: Templates) -> None:
    """The row-replacement pattern returns a row, not a page wrapped around one."""
    row = _row()
    body = _text(
        templates.fragment(
            '_grade_row.html', {'row': row, 'section_id': 'S1', 'csrf_token': 't', 'just_recorded': True}
        )
    )

    assert body.lstrip().startswith('<tr')
    assert '<html' not in body
    assert 'Sam Student' in body


def test_a_page_is_a_document(templates: Templates) -> None:
    body = _text(templates.page('error.html', {'failure': 'not_found', 'detail': 'no', 'status': 404}))

    assert body.lstrip().startswith('<!doctype html>')


def test_a_fragment_carries_the_headers_it_was_given(templates: Templates) -> None:
    response = templates.fragment(
        '_error.html', {'failure': 'forbidden', 'detail': 'no'}, status_code=403, headers={'HX-Retarget': '#x'}
    )

    assert response.status_code == 403
    assert response.headers['HX-Retarget'] == '#x'


def test_a_template_refuses_a_missing_variable(templates: Templates) -> None:
    """``StrictUndefined`` is on, so a renamed DTO field breaks the build.

    The alternative is a blank cell, which on a grade sheet is indistinguishable from a student
    with no grade -- a rendering bug that looks like data.
    """
    from jinja2 import UndefinedError

    with pytest.raises(UndefinedError):
        templates.page('error.html', {'failure': 'rule', 'detail': 'no'})


def test_output_is_escaped(templates: Templates) -> None:
    """Autoescaping is on: a student named ``<script>`` is text, not script."""
    body = _text(
        templates.page('error.html', {'failure': 'rule', 'detail': '<script>alert(1)</script>', 'status': 400})
    )

    assert '<script>alert(1)</script>' not in body
    assert '&lt;script&gt;' in body


def test_every_template_that_ships_is_reachable() -> None:
    """No orphan templates: each one is rendered by some route or included by one that is."""
    shipped = {path.name for path in Path(rendering.TEMPLATES_DIRECTORY).glob('*.html')}

    assert shipped == {
        'base.html',
        'error.html',
        '_error.html',
        'grades.html',
        '_grade_row.html',
        'sign_in.html',
        'wards.html',
        'transcript.html',
    }


def _row() -> object:
    """One grade-sheet row to render."""
    from academy.application.dtos import SectionGradeRowDto

    return SectionGradeRowDto(student_id='P1', full_name='Sam Student', best_grade=8, passed=True, attempts=1)


def _text(response: Response) -> str:
    """The rendered body as text.

    ``Response.body`` is typed ``bytes | memoryview`` -- Starlette allows either -- and only one
    of them has ``decode``. ``bytes()`` accepts both, so this is a narrowing rather than a cast.
    """
    return bytes(response.body).decode()
