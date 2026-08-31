"""What a shell sees when a command fails.

The CLI's half of ADR-0019: :func:`for_failure` renders the same
:class:`~academy.adapters.inbound.error_status.Failure` the HTTP adapters render as a status
code. Nothing here mentions HTTP, and nothing in ``error_status`` mentions a process.

Exit codes are an interface. A CI job that greps stdout is a job that breaks on a reworded
message, so the codes below are the promise, the text is not, and they are asserted by the e2e
tier against the real process.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import IntEnum
from typing import Final

from academy.adapters.inbound.error_status import Failure


class ExitCode(IntEnum):
    """The exit statuses this CLI produces.

    ``IntEnum`` rather than ``Enum``, because these are handed to ``SystemExit`` and compared
    with integers by every shell that runs them -- this is one of the few places where being an
    ``int`` is the point rather than a leak.

    0 to 2 keep their conventional meanings, which is why the classified failures start at 3:
    argparse exits 2 on a usage error of its own accord, and a CLI that also used 2 for "not
    found" would make a mistyped flag indistinguishable from a missing record.
    """

    OK = 0
    # Unclassified: a bug of ours (ADR-0019). Python's own exit status for an escaping
    # exception is 1 as well, so a traceback and a classification of `None` agree by accident
    # and it is worth them agreeing on purpose.
    ERROR = 1
    USAGE = 2
    VALIDATION = 3
    NOT_FOUND = 4
    CONFLICT = 5
    FORBIDDEN = 6
    TOO_LARGE = 7
    RULE = 8
    # The environment described a deployment that cannot be built. Not a `Failure`: it happens
    # before any use case is reached, and there is no HTTP status for it either -- the process
    # simply refuses to start.
    CONFIGURATION = 9
    # The import ran and did not fully succeed: rows were rejected, or a queued job could not
    # produce a report at all. Not an error -- the command did what it was asked -- but the
    # thing a shell script has to branch on, and the reason `--dry-run` is worth running in CI.
    IMPORT_INCOMPLETE = 10


# ADR-0019's CLI column, as data. Total over `Failure` by construction -- a member added there
# and forgotten here raises `KeyError` in `for_failure`, and a test asserts every member is
# present so that failure happens in the suite rather than in someone's terminal.
#
# Comments rather than attribute docstrings: the check-docstring-first hook reads a string
# literal after a module-level assignment as a second module docstring.
_CODES: Final[Mapping[Failure, ExitCode]] = {
    Failure.VALIDATION: ExitCode.VALIDATION,
    Failure.NOT_FOUND: ExitCode.NOT_FOUND,
    Failure.CONFLICT: ExitCode.CONFLICT,
    Failure.FORBIDDEN: ExitCode.FORBIDDEN,
    Failure.TOO_LARGE: ExitCode.TOO_LARGE,
    Failure.RULE: ExitCode.RULE,
}


def for_failure(failure: Failure | None) -> ExitCode:
    """Render a classification as an exit status.

    Args:
        failure: What :func:`~academy.adapters.inbound.error_status.classify` returned, which is
            ``None`` when the exception was not an expected failure at all.

    Returns:
        The status to exit with. ``ERROR`` for ``None``: an unclassified exception is a bug of
        ours, and the shell should be told that rather than something reassuring.
    """
    return ExitCode.ERROR if failure is None else _CODES[failure]
