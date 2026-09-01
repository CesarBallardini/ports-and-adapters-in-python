"""The claims in this repository that cannot be checked without a browser.

Everything else about the web adapter is asserted over HTTP, and cheaply: the status a route
returns, the headers on a failure, the shape of the fragment, the JSON in the ``htmx-config`` meta
tag, even the default ``responseHandling`` rules read straight out of the vendored
``htmx.min.js``. All of those are assertions about **what the server sends**.

None of them observes htmx *doing* anything. ADR-0021 makes a claim that lives entirely on the
other side of that line:

    htmx 2 does not swap a non-2xx response, so an honest 403 or 422 from the error boundary
    would reach the browser and be silently discarded. The adapter overrides that once, in
    ``base.html``'s ``htmx-config`` meta tag, so a failure keeps its real status **and** is still
    shown to the person who caused it.

If that meta tag is malformed, if htmx merges it differently than assumed, or if ``HX-Retarget``
behaves otherwise on an error response, **every other test still passes** and the page silently
does nothing.

Writing this found something, which is the argument for it existing. The obvious scenario --
type 11 into the grade box and watch the 422 appear -- **cannot happen in a browser**, because
``<input type="number" max="10">`` makes the browser refuse to submit before any request is made.
That is correct behaviour and good for the user, and it means the invalid-grade path is reachable
only from a non-browser client. So it gets its own test below, asserting the request is never
sent, and the swap-rule claim is made with a failure a browser can actually produce: a stale CSRF
token, which is exactly what a page left open across a cookie expiry has.

Keep this file small. Playwright is a second toolchain and a browser download in CI; it earns
that for claims nothing else can reach, and not for anything httpx can assert more cheaply. A new
browser test is a signal that something was put here that belonged a tier down.

**Nothing here writes.** One request is refused by CSRF and one is never sent at all, so the
module-scoped database is unchanged and the e2e tier's read-only rule holds.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

import httpx
import pytest
from playwright.async_api import Browser, Error, Page, Playwright, async_playwright

from academy.adapters.outbound.persistence.sqlalchemy.session import migrate_to_head
from academy.config.container import Container
from academy.config.seeding import DEMO_SECTION, DEMO_STUDENT, DEMO_TEACHER, seed_demo
from academy.config.settings import (
    ENV_DATABASE_URL,
    ENV_PERSISTENCE,
    ENV_SECRET_KEY,
    PersistenceBackend,
    Settings,
)

pytestmark = pytest.mark.e2e

SECRET_KEY = 'a-browser-test-signing-key'  # noqa: S105 -- a literal in a test, not a credential

STARTUP_TIMEOUT_SECONDS = 60
# Generous, because a first navigation also starts the browser's own machinery. A hang should
# still fail rather than sit until the suite's timeout kills it with no useful message.
ACTION_TIMEOUT_MS = 15_000

# Outside the domain's 0..10 scale, and outside the input's own `max`, which is the point.
REFUSED_GRADE = '11'
ACCEPTED_GRADE = '7'


# What `playwright install chromium` says when it has not been run. Matched on rather than
# guessed at: a developer who has synced the environment but not downloaded the browser should be
# told the one command that fixes it, not handed a stack trace from inside the driver.
_MISSING_BROWSER = "executable doesn't exist"

INSTALL_HINT = "chromium is not installed -- run 'uv run playwright install chromium'"


@dataclass(frozen=True, slots=True)
class LaunchOptions:
    """How to start Chromium for this run.

    Headless by default and headed only when asked (``--headed``, or ``make test-browser
    HEADED=1``). ``--slowmo`` is the one worth remembering: headed alone still finishes each
    action in milliseconds, which is far too fast to watch an htmx swap land.
    """

    headless: bool = True
    slow_mo: int = 0


@pytest.fixture(scope='module')
def launch_options(pytestconfig: pytest.Config) -> LaunchOptions:
    """Read the browser flags once for the module."""
    # `getoption` is typed `Any | None`, so this is where that stops -- the same rule as `Args`
    # for argparse and `surface_of` for `request.state`. A run-time check and not a cast, because
    # a mistyped option genuinely arrives untyped.
    slowmo = pytestconfig.getoption('--slowmo')
    return LaunchOptions(
        headless=not pytestconfig.getoption('--headed'),
        slow_mo=slowmo if isinstance(slowmo, int) else 0,
    )


async def _launch(playwright: Playwright, options: LaunchOptions) -> Browser:
    """Start Chromium, or skip with the command that would have made it work.

    Skipping rather than failing is safe **only because CI installs the browser** (see the
    `pytest-e2e` job): the check is therefore enforced on every pull request, and the skip covers
    just the developer who has not downloaded it yet.
    """
    try:
        return await playwright.chromium.launch(headless=options.headless, slow_mo=options.slow_mo)
    except Error as error:  # pragma: no cover -- depends on the machine, not the code
        if _MISSING_BROWSER in str(error).lower():
            pytest.skip(INSTALL_HINT)
        raise


@pytest.fixture(scope='module')
def database(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """A migrated SQLite database carrying the demo data.

    Seeded through :func:`academy.config.seeding.seed_demo`, the same path ``make demo`` takes --
    so this file is also the proof that what that command creates is usable in a browser, which
    is what its printed credentials promise.
    """
    path = tmp_path_factory.mktemp('e2e-browser') / 'academy.db'
    url = f'sqlite+aiosqlite:///{path.as_posix()}'
    asyncio.run(asyncio.to_thread(migrate_to_head, url))
    asyncio.run(_seed(url))
    yield url


async def _seed(url: str) -> None:
    """Put the demo people and section in, then let go of the database."""
    container = Container(Settings(persistence=PersistenceBackend.SQLALCHEMY, database_url=url, secret_key=SECRET_KEY))
    try:
        await seed_demo(container)
    finally:
        # Windows will not delete the temporary directory while a handle is open on the file.
        await container.aclose()


def _free_port() -> int:
    """A port the operating system says is free right now."""
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return int(probe.getsockname()[1])


@pytest.fixture(scope='module')
def server(database: str) -> Iterator[str]:
    """A real uvicorn serving the seeded database, addressed over a real socket."""
    port = _free_port()
    process = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            '-m',
            'uvicorn',
            '--factory',
            'academy.config:create_app',
            '--host',
            '127.0.0.1',
            '--port',
            str(port),
            '--log-level',
            'warning',
        ],
        env={
            **os.environ,
            ENV_PERSISTENCE: 'sqlalchemy',
            ENV_DATABASE_URL: database,
            ENV_SECRET_KEY: SECRET_KEY,
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    base_url = f'http://127.0.0.1:{port}'
    try:
        _await_health(process, base_url)
        yield base_url
    finally:
        process.terminate()
        # `communicate` and not `wait`: it drains *and closes* the stdout pipe, which the garbage
        # collector would otherwise close during finalisation -- an error, since warnings are.
        try:
            process.communicate(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover -- a server that will not stop
            process.kill()
            process.communicate()


def _await_health(process: subprocess.Popen[str], base_url: str) -> None:
    """Block until the server answers, or fail with whatever it printed instead."""
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ''
            raise AssertionError(f'the server exited during startup with {process.returncode}:\n{output}')
        try:
            with httpx.Client(timeout=2) as client:
                if client.get(f'{base_url}/healthz').status_code == 200:
                    return
        except httpx.HTTPError:
            time.sleep(0.2)

    raise AssertionError(f'the server did not answer within {STARTUP_TIMEOUT_SECONDS}s')  # pragma: no cover


@pytest.fixture
async def sheet(server: str, launch_options: LaunchOptions) -> AsyncIterator[Page]:
    """A real Chromium page, signed in as the demo teacher and showing the grade sheet.

    Signed in through the form rather than by planting a cookie: the point of this tier is that
    the browser does what a person does, and a planted cookie would skip the one step where a real
    ``Set-Cookie`` and a real redirect have to work together.
    """
    async with async_playwright() as playwright:
        browser = await _launch(playwright, launch_options)
        try:
            page = await browser.new_page()
            page.set_default_timeout(ACTION_TIMEOUT_MS)

            await page.goto(f'{server}/sign-in')
            await page.fill('input[name="email"]', DEMO_TEACHER.email)
            await page.fill('input[name="password"]', 'the password is not checked')
            await page.click('button[type="submit"]')

            await page.goto(f'{server}/sections/{DEMO_SECTION}/grades')
            yield page
        finally:
            await browser.close()


def _row() -> str:
    """The selector for the demo student's row."""
    return f'#student-{DEMO_STUDENT.person_id}'


async def test_htmx_loads_and_takes_the_configuration_the_page_gives_it(sheet: Page) -> None:
    """The override, read back out of the running library rather than out of the HTML.

    Every other test proves the meta tag is *rendered* correctly. This proves htmx *parsed* it: a
    tag that was malformed, or a key htmx renamed, would leave the defaults in place and be
    invisible everywhere else.
    """
    handling = await sheet.evaluate('() => window.htmx && window.htmx.config.responseHandling')

    assert handling is not None, 'htmx did not load, or exposes no config'
    rules = {rule['code']: rule for rule in handling}
    assert rules['4..']['swap'] is True, handling
    assert rules['5..']['swap'] is False, handling


async def test_a_refused_request_is_shown_in_the_page_error_region(sheet: Page) -> None:
    """ADR-0021's central claim, observed rather than inferred.

    The failure is a stale CSRF token, which is what a page left open across a cookie expiry
    actually has -- and which produces a real 403 through the same error boundary as everything
    else. htmx's *default* behaviour would be to drop that response on the floor, leaving a page
    that looks like the button did nothing.

    Three assertions, each ruling out a different way of being wrong:

    * the message appears at all -- so a 4xx was swapped, which the default rules forbid;
    * it appears in ``#academy-errors`` and not in the student's row -- so ``HX-Retarget`` was
      honoured, and a failed submission did not eat the row it came from;
    * the row still shows the student and no grade -- so nothing was recorded.
    """
    await sheet.evaluate("""
        () => {
            for (const field of document.querySelectorAll('input[name="csrf_token"]')) {
                field.value = 'a-stale-token';
            }
        }
    """)

    await sheet.fill(f'{_row()} input[name="grade"]', ACCEPTED_GRADE)
    await sheet.click(f'{_row()} button[type="submit"]')

    errors = sheet.locator('#academy-errors')
    await errors.wait_for(state='visible')
    message = await errors.inner_text()

    assert message.strip(), 'the error region was swapped but is empty'
    assert 'this site' in message.lower(), message

    row_text = await sheet.locator(_row()).inner_text()
    assert DEMO_STUDENT.full_name in row_text
    assert 'this site' not in row_text.lower(), 'the error replaced the row instead of the error region'


async def test_the_browser_refuses_an_out_of_range_grade_before_any_request_is_made(
    sheet: Page,
) -> None:
    """The finding that writing this file produced, kept as a test.

    ``<input type="number" max="10">`` means a browser never sends 11 at all: the constraint is
    enforced client-side and the form does not submit. That is good -- the domain's 0..10 scale is
    stated once in the input and once in ``Grade``, and the user is told immediately rather than
    after a round trip.

    It is worth pinning because it is easy to remove by accident and because it explains something
    a reader would otherwise find puzzling: the 422 that
    ``tests/integration/test_web.py`` asserts so carefully is reachable only from a client that is
    not a browser. Both paths are real, and only one of them is reachable from this page.
    """
    posts: list[str] = []
    sheet.on('request', lambda request: posts.append(request.url) if request.method == 'POST' else None)

    await sheet.fill(f'{_row()} input[name="grade"]', REFUSED_GRADE)
    await sheet.click(f'{_row()} button[type="submit"]')
    # Long enough that a request would have been observed if one were going to be made.
    await asyncio.sleep(1)

    assert posts == [], posts
    assert not await sheet.locator(f'{_row()} input[name="grade"]').evaluate('field => field.checkValidity()')


async def test_the_page_reports_no_javascript_errors(server: str, launch_options: LaunchOptions) -> None:
    """htmx failing to load at all would make every swap silently stop happening.

    A 404 on ``/static/htmx.min.js`` is already asserted over HTTP; what is not, is that the file
    the server sends parses and runs. A page that threw on load would leave every form doing a
    full-page POST, which looks almost right and is not the adapter under test.
    """
    async with async_playwright() as playwright:
        browser = await _launch(playwright, launch_options)
        try:
            page = await browser.new_page()
            failures: list[str] = []
            page.on('pageerror', lambda error: failures.append(str(error)))

            await page.goto(f'{server}/sign-in')
            await page.wait_for_function('() => window.htmx !== undefined')

            assert failures == []
        finally:
            await browser.close()
