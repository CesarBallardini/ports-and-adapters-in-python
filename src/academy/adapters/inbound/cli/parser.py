"""The argv grammar, and the one place where untyped parsing stops.

argparse, from the standard library (ADR-0020): the hexagon core has no dependencies, every
adapter family is an optional extra, and this is the one adapter that needs neither. A subcommand
tree this size is what argparse is for, and writing it by hand keeps visible the single thing a
driving adapter exists to do -- turn one protocol's vocabulary into a command object.

Nothing here calls a use case. The parser produces strings; :mod:`.commands` turns them into
commands and calls a port. Keeping the two apart is what makes the grammar testable without a
container and the handlers testable without argv.
"""

from __future__ import annotations

import argparse
from typing import Final

from academy.application.jobs import ImportKind

# What `add_subparsers` returns, named once. typeshed exposes no public spelling of it, so the
# private name is unavoidable -- but it is unavoidable in *one* place rather than in the four
# functions below, and there is a single line to change if a public alias ever appears.
type SubParsers = argparse._SubParsersAction[argparse.ArgumentParser]  # noqa: SLF001

# The importers a command line may name. Derived from the enum rather than restated, so a new
# importer becomes available to the CLI by existing -- and so `--kind` cannot offer one the
# application does not have.
#
# Comments rather than attribute docstrings: the check-docstring-first hook reads a string
# literal after a module-level assignment as a second module docstring.
IMPORT_KINDS: Final = tuple(kind.value for kind in ImportKind)

# The formats a template may be asked for. Not derived from `SpreadsheetFormats`, because that is
# built by the composition root from a deployment's wiring and this list is parsed before any
# container exists; a format named here that the deployment did not wire is refused by
# `writer_for` with the formats it does have.
TEMPLATE_FORMATS: Final = ('csv', 'xlsx')


class Args:
    """The parsed command line, narrowed to real types.

    ``argparse`` hands back a :class:`~argparse.Namespace` whose every attribute is ``Any``, and
    an adapter that read straight off it would pass ``Any`` into a command object -- silently
    losing exactly the checking this repository runs two type checkers to get. This is where the
    untyped world ends, and it ends with a runtime check rather than a cast, because the values
    genuinely arrive untyped and a cast would only be a promise.

    A failure here is a bug in the parser stanza, not something an operator typed: argparse has
    already refused anything the grammar did not allow.
    """

    def __init__(self, namespace: argparse.Namespace) -> None:
        """Wrap a parsed namespace.

        Args:
            namespace: What :meth:`argparse.ArgumentParser.parse_args` returned.
        """
        # `object` and deliberately not `Any`, which is the one distinction that makes this class
        # worth having: `Any` would let every reader pass an unchecked value straight through,
        # while `object` supports nothing at all until something narrows it -- so the accessors
        # below are not a convenience over the dict, they are the only way through it.
        self._values: dict[str, object] = vars(namespace)

    def text(self, name: str) -> str:
        """Read a required string argument.

        Raises:
            TypeError: If the parser did not produce a string for it.
        """
        value = self._values.get(name)
        if not isinstance(value, str):
            raise TypeError(f'{name} should have been parsed as a string, got {value!r}')
        return value

    def optional_text(self, name: str) -> str | None:
        """Read a string argument that may have been omitted.

        Raises:
            TypeError: If the parser produced something that is neither a string nor ``None``.
        """
        value = self._values.get(name)
        if value is not None and not isinstance(value, str):
            raise TypeError(f'{name} should have been parsed as a string or omitted, got {value!r}')
        return value

    def number(self, name: str) -> int:
        """Read an integer argument.

        Raises:
            TypeError: If the parser did not produce an integer for it.
        """
        value = self._values.get(name)
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError(f'{name} should have been parsed as an integer, got {value!r}')
        return value

    def flag(self, name: str) -> bool:
        """Read a boolean flag, absent meaning ``False``."""
        return self._values.get(name) is True


def build_parser() -> argparse.ArgumentParser:
    """Build the whole command grammar.

    Returns:
        A parser whose ``command`` attribute names the handler to run, e.g. ``'grades list'``.
        Dispatching on that string rather than on a callable stashed by ``set_defaults`` keeps
        the mapping from grammar to handler in one readable table in :mod:`.commands`, and keeps
        the parser free of any import from it.
    """
    parser = argparse.ArgumentParser(
        prog='academy',
        description='Academic records, from the command line. One of four drivers over the same use cases.',
        epilog='Exit codes: 0 ok, 1 bug, 2 usage, 3 validation, 4 not found, 5 conflict, '
        '6 forbidden, 7 too large, 8 rule, 9 configuration, 10 import incomplete.',
    )
    groups = parser.add_subparsers(dest='group', required=True, metavar='GROUP')

    _grades(groups)
    _records(groups)
    _imports(groups)
    _config(groups)
    return parser


def _actor_options(parser: argparse.ArgumentParser) -> None:
    """Add the options every command that reaches a use case needs.

    ``--as`` is required and has no default (ADR-0020): the operator names the person they are
    acting for, every time, so it appears in the shell history and in whatever audits it. A
    default would make the actor invisible at exactly the call site where it matters most.
    """
    parser.add_argument(
        '--as',
        dest='actor',
        required=True,
        metavar='EMAIL',
        help='act as this person; their current roles are read on every invocation',
    )
    _output_options(parser)


def _output_options(parser: argparse.ArgumentParser) -> None:
    """Add the options that decide how a result is printed."""
    parser.add_argument(
        '--json',
        dest='as_json',
        action='store_true',
        help='print the result as JSON, for a script rather than a person',
    )


def _grades(groups: SubParsers) -> None:
    """Add the ``grades`` group: UC-21 and UC-22."""
    grades = groups.add_parser('grades', help="a course section's grades").add_subparsers(
        dest='action', required=True, metavar='ACTION'
    )

    listing = grades.add_parser('list', help="list a section's roster with each student's standing (UC-21)")
    listing.add_argument('section_id', metavar='SECTION_ID')
    _actor_options(listing)
    listing.set_defaults(command='grades list')

    record = grades.add_parser('record', help='record one grade for one student (UC-22)')
    record.add_argument('section_id', metavar='SECTION_ID')
    record.add_argument('student_id', metavar='STUDENT_ID')
    record.add_argument('grade', type=int, metavar='GRADE', help='0 to 10; the domain decides, not this parser')
    _actor_options(record)
    record.set_defaults(command='grades record')


def _records(groups: SubParsers) -> None:
    """Add the ``records`` group: UC-26, UC-28 and UC-30."""
    records = groups.add_parser('records', help='student transcripts').add_subparsers(
        dest='action', required=True, metavar='ACTION'
    )

    show = records.add_parser('show', help="read a student's transcript (UC-26, UC-30)")
    show.add_argument('student_id', metavar='STUDENT_ID')
    _actor_options(show)
    show.set_defaults(command='records show')

    wards = records.add_parser('wards', help='list the students currently in your care (UC-28)')
    _actor_options(wards)
    wards.set_defaults(command='records wards')


def _imports(groups: SubParsers) -> None:
    """Add the ``import`` group: UC-36 and UC-40 to UC-42."""
    imports = groups.add_parser('import', help='bulk load from a spreadsheet').add_subparsers(
        dest='action', required=True, metavar='ACTION'
    )

    template = imports.add_parser('template', help='download an import template (UC-36)')
    template.add_argument('--kind', choices=IMPORT_KINDS, default=ImportKind.GRADE_SHEET.value)
    template.add_argument('--format', dest='file_format', choices=TEMPLATE_FORMATS, default='xlsx')
    template.add_argument('--section', dest='section_id', metavar='SECTION_ID', help='pre-fill the enrolled students')
    template.add_argument('--output', dest='output', required=True, metavar='PATH', help='where to write the file')
    _actor_options(template)
    template.set_defaults(command='import template')

    run = imports.add_parser('run', help='import a spreadsheet, inline or queued by size (UC-40, UC-41)')
    run.add_argument('path', metavar='FILE')
    run.add_argument('--kind', choices=IMPORT_KINDS, default=ImportKind.GRADE_SHEET.value)
    run.add_argument('--section', dest='section_id', metavar='SECTION_ID')
    run.add_argument(
        '--dry-run',
        dest='dry_run',
        action='store_true',
        help='do the whole import and roll it back, reporting what it would have done',
    )
    _actor_options(run)
    run.set_defaults(command='import run')

    job = imports.add_parser('job', help="read a queued import's state (UC-41)")
    job.add_argument('job_id', metavar='JOB_ID')
    _actor_options(job)
    job.set_defaults(command='import job')


def _config(groups: SubParsers) -> None:
    """Add the ``config`` group.

    The only command that takes no ``--as``: it reads no records, reaches no use case and has no
    actor to be. It exists because "what am I actually running with?" is the first question of
    every support conversation.
    """
    config = groups.add_parser('config', help='this deployment as it was configured').add_subparsers(
        dest='action', required=True, metavar='ACTION'
    )
    show = config.add_parser('show', help='print every setting, defaults resolved')
    _output_options(show)
    show.set_defaults(command='config show')
