"""Options for the browser tier: watching it happen, and slowing it down enough to see.

The browser tests run **headless by default** and must keep doing so. CI has no display, a headed
browser is slower, and a suite whose default depends on a window manager is a suite that behaves
differently on somebody else's machine.

But the whole value of a browser test, when it fails, is that a person can watch what the browser
actually did -- and a headless run gives you a stack trace and nothing to look at. These two flags
are that escape hatch:

    make test-browser HEADED=1
    uv run pytest -m e2e --headed --slowmo 400

``--slowmo`` is the one that matters in practice. Headed alone still finishes in under a second
per action, which is too fast to see an htmx swap land; a few hundred milliseconds turns it into
something a person can follow.

Command-line options rather than an environment variable, deliberately. Every ``ACADEMY_``
variable in this repository is deployment configuration read once by ``Settings`` (see
``config/settings.py``), and adding one that only a test reads would put a test concern into the
namespace a deployment greps.
"""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add the browser-tier flags.

    Scoped to ``tests/e2e/`` by living in this directory's ``conftest.py``, so the options exist
    where they mean something rather than on every invocation of the suite.
    """
    group = parser.getgroup('browser', 'options for the Playwright browser tests')
    group.addoption(
        '--headed',
        action='store_true',
        default=False,
        help='run the browser tests in a visible window instead of headless',
    )
    group.addoption(
        '--slowmo',
        type=int,
        default=0,
        metavar='MS',
        help='pause this many milliseconds between browser actions, so a person can follow them',
    )
