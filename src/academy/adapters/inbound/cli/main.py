"""The entry point: argv in, an exit status out, and one error boundary in between.

The whole of the adapter's control flow is here and it is deliberately small. Parse, build the
container the environment describes, open one scope, resolve the actor ``--as`` named, call one
handler, print what it produced, exit with what it decided.

**There is exactly one place that catches an error**, and it consults the shared table rather
than deciding anything (ADR-0012, ADR-0019). Anything the table does not classify is a bug of
ours and is allowed to escape with its traceback intact -- Python exits 1 for an uncaught
exception, which is the same status :class:`~academy.adapters.inbound.cli.exit_codes.ExitCode`
assigns to "we did not predict this", so the two agree on purpose rather than by luck.

``main`` takes its argv, its streams and its environment as arguments, all defaulted. That is not
testing scaffolding: it is the same rule the composition root follows (ADR-0015), and it means
the CLI can be driven in-process by a test that never spawns anything, while the e2e tier spawns
the real thing and checks the status a shell would see.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Sequence
from typing import TextIO

from academy.adapters.inbound.cli import render
from academy.adapters.inbound.cli.commands import HANDLERS
from academy.adapters.inbound.cli.exit_codes import ExitCode, for_failure
from academy.adapters.inbound.cli.identity import actor_for
from academy.adapters.inbound.cli.parser import Args, build_parser
from academy.adapters.inbound.cli.render import Output
from academy.adapters.inbound.error_status import Failure, classify
from academy.application.errors import ApplicationError
from academy.config.container import Container
from academy.config.settings import ConfigurationError, Environ, Settings
from academy.domain.shared.errors import DomainError


def main(
    argv: Sequence[str] | None = None,
    *,
    out: TextIO | None = None,
    err: TextIO | None = None,
    environ: Environ | None = None,
) -> ExitCode:
    """Run one command line.

    Args:
        argv: The arguments, without the program name. ``None`` reads ``sys.argv``.
        out: Where results are printed. ``None`` means ``sys.stdout``, resolved now rather than
            at import time so that a test's redirection is honoured.
        err: Where failures are reported. ``None`` means ``sys.stderr``.
        environ: The deployment's environment. ``None`` means ``os.environ``.

    Returns:
        The exit status, as an :class:`~academy.adapters.inbound.cli.exit_codes.ExitCode`.
        Results go to ``out`` and failures to ``err``, so a shell can pipe one without catching
        the other.
    """
    stdout = sys.stdout if out is None else out
    stderr = sys.stderr if err is None else err

    # argparse exits 2 by itself on a usage error, after printing its own message. Nothing here
    # improves on that, and catching it would only mean reprinting it.
    args = Args(build_parser().parse_args(argv))

    try:
        output = asyncio.run(_execute(args, environ))
    except ConfigurationError as error:
        # Before any use case, so not a `Failure`: the deployment described something that
        # cannot be built, and the right answer is a process that refuses to start.
        print(f'configuration: {error}', file=stderr)
        return ExitCode.CONFIGURATION
    except (ApplicationError, DomainError) as error:
        failure = classify(error)
        print(f'{_label(failure)}: {error}', file=stderr)
        return for_failure(failure)

    _write(output, stdout, as_json=args.flag('as_json'))
    return output.exit_code


async def _execute(args: Args, environ: Environ | None) -> Output:
    """Build what this command needs, run it, and give back what it produced.

    The container is built per invocation and closed again, because a CLI *is* one invocation:
    the "process lifetime" half of the composition root and the process happen to be the same
    thing here, which is exactly the case ``Container`` was written to also serve.
    """
    command = args.text('command')
    settings = Settings.from_env(environ)

    # The one command with no actor and no scope. It reads no records, so building a container --
    # opening a connection pool, creating an upload directory -- would be work done to print
    # something already in hand.
    if command == 'config show':
        return render.configuration(settings)

    container = Container(settings)
    try:
        async with container.request_scope() as scope:
            actor = await actor_for(scope.people, args.text('actor'))
            return await HANDLERS[command](scope, actor, args)
    finally:
        await container.aclose()


def _write(output: Output, stream: TextIO, *, as_json: bool) -> None:
    """Print one command's result, in the rendering the caller asked for.

    ``--json`` is an interface and the human lines are not: a script that grepped the prose would
    break on a reworded message, so the two renderings are produced side by side from the same
    :class:`~academy.adapters.inbound.cli.render.Output` rather than one being scraped from the
    other.
    """
    if as_json:
        print(json.dumps(output.data, indent=2, sort_keys=True), file=stream)
        return
    for line in output.lines:
        print(line, file=stream)


def _label(failure: Failure | None) -> str:
    """Name the failure on the first word of the error line.

    Stable, lower-case and one word, so a message stays readable and a log stays greppable while
    the sentence after it is free to change.
    """
    return 'error' if failure is None else failure.value
