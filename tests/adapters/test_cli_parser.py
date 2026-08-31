"""The grammar, tested without a container: argv in, narrowed values out.

Two things are worth pinning here and they are not the same thing. One is the grammar itself --
which commands exist, what they require, what they default to -- because that is a published
interface the moment anyone writes a script against it. The other is :class:`Args`, which is the
line where argparse's ``Any`` stops; a regression there would not fail the type checkers, because
``Any`` is assignable to everything.

The join between the grammar and the handlers is asserted here, because it is a fact about the
grammar's command names. What each handler is *bound to* lives in ``test_cli_commands.py``, which
is where the container is needed.
"""

from __future__ import annotations

import argparse

import pytest

from academy.adapters.inbound.cli.commands import HANDLERS
from academy.adapters.inbound.cli.parser import Args, build_parser


def _parse(*argv: str) -> Args:
    return Args(build_parser().parse_args(argv))


@pytest.mark.unit
@pytest.mark.parametrize(
    ('argv', 'command'),
    [
        (('grades', 'list', 'S1', '--as', 'a@b.c'), 'grades list'),
        (('grades', 'record', 'S1', 'P1', '7', '--as', 'a@b.c'), 'grades record'),
        (('records', 'show', 'P1', '--as', 'a@b.c'), 'records show'),
        (('records', 'wards', '--as', 'a@b.c'), 'records wards'),
        (('import', 'template', '--output', 'out.xlsx', '--as', 'a@b.c'), 'import template'),
        (('import', 'run', 'grades.csv', '--as', 'a@b.c'), 'import run'),
        (('import', 'job', 'J1', '--as', 'a@b.c'), 'import job'),
        (('config', 'show'), 'config show'),
    ],
)
def test_every_command_names_itself(argv: tuple[str, ...], command: str) -> None:
    assert _parse(*argv).text('command') == command


@pytest.mark.unit
def test_every_command_the_grammar_offers_has_a_handler() -> None:
    """The two halves of the adapter meet in one table, and this is what checks the join.

    ``parser.py`` names a command and imports no handler; ``commands.py`` names a handler and
    parses nothing. A command added to one and not the other is a ``KeyError`` in someone's
    terminal unless it is a failure here first.
    """
    parser = build_parser()
    commands = _commands_of(parser)

    assert commands - {'config show'} == set(HANDLERS)


@pytest.mark.unit
@pytest.mark.parametrize(
    'argv',
    [
        ('grades', 'list', 'S1'),
        ('grades', 'record', 'S1', 'P1', '7'),
        ('records', 'show', 'P1'),
        ('records', 'wards'),
        ('import', 'template', '--output', 'out.xlsx'),
        ('import', 'run', 'grades.csv'),
        ('import', 'job', 'J1'),
    ],
)
def test_every_command_that_reaches_a_use_case_requires_an_actor(argv: tuple[str, ...]) -> None:
    """ADR-0020: ``--as`` has no default, so an actor is never invisible at the call site."""
    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args(argv)

    assert exit_info.value.code == 2


@pytest.mark.unit
def test_config_show_needs_no_actor() -> None:
    """It reads no records and reaches no use case, so there is no one to be."""
    assert _parse('config', 'show').text('command') == 'config show'


@pytest.mark.unit
def test_the_actor_lands_under_a_name_that_is_not_a_python_keyword() -> None:
    """``--as`` would be ``args.as``, which does not parse. The ``dest`` is load-bearing."""
    assert _parse('records', 'wards', '--as', 'dana@example.edu').text('actor') == 'dana@example.edu'


@pytest.mark.unit
def test_an_import_defaults_to_a_grade_sheet_and_a_template_to_xlsx() -> None:
    template = _parse('import', 'template', '--output', 'out.xlsx', '--as', 'a@b.c')
    run = _parse('import', 'run', 'grades.csv', '--as', 'a@b.c')

    assert template.text('kind') == 'grade_sheet'
    assert template.text('file_format') == 'xlsx'
    assert run.text('kind') == 'grade_sheet'


@pytest.mark.unit
def test_an_unknown_importer_is_refused_by_the_grammar() -> None:
    """The choices come from ``ImportKind``, so ``--kind`` cannot offer one that does not exist."""
    with pytest.raises(SystemExit):
        build_parser().parse_args(('import', 'run', 'f.csv', '--kind', 'astrology', '--as', 'a@b.c'))


@pytest.mark.unit
def test_flags_are_false_when_absent_and_true_when_given() -> None:
    plain = _parse('import', 'run', 'f.csv', '--as', 'a@b.c')
    rehearsal = _parse('import', 'run', 'f.csv', '--as', 'a@b.c', '--dry-run', '--json')

    assert (plain.flag('dry_run'), plain.flag('as_json')) == (False, False)
    assert (rehearsal.flag('dry_run'), rehearsal.flag('as_json')) == (True, True)


@pytest.mark.unit
def test_an_omitted_option_is_absent_rather_than_empty() -> None:
    """``--section`` unset must not become ``''``: the importer should not have to tell them apart."""
    assert _parse('import', 'run', 'f.csv', '--as', 'a@b.c').optional_text('section_id') is None
    assert _parse('import', 'run', 'f.csv', '--as', 'a@b.c', '--section', 'S1').optional_text('section_id') == 'S1'


@pytest.mark.unit
def test_a_grade_arrives_as_an_integer() -> None:
    assert _parse('grades', 'record', 'S1', 'P1', '7', '--as', 'a@b.c').number('grade') == 7


@pytest.mark.unit
def test_a_grade_that_is_not_a_number_is_refused_by_the_grammar_not_by_the_domain() -> None:
    """``eleven`` is not a number and never reaches a use case; ``11`` is, and does."""
    with pytest.raises(SystemExit):
        build_parser().parse_args(('grades', 'record', 'S1', 'P1', 'eleven', '--as', 'a@b.c'))

    assert _parse('grades', 'record', 'S1', 'P1', '11', '--as', 'a@b.c').number('grade') == 11


@pytest.mark.unit
def test_narrowing_refuses_a_value_of_the_wrong_shape() -> None:
    """The check is at run time because the value genuinely arrives untyped.

    A cast would type-check and be a promise; this is the promise being kept. It can only fail
    through a bug in a parser stanza, which is why it raises rather than exiting 2.
    """
    args = Args(argparse.Namespace(grade='7', section_id=3, dry_run=True))

    with pytest.raises(TypeError):
        args.number('grade')
    with pytest.raises(TypeError):
        args.text('section_id')
    with pytest.raises(TypeError):
        args.optional_text('section_id')


@pytest.mark.unit
def test_a_boolean_is_not_accepted_where_a_number_is_wanted() -> None:
    """``bool`` is a subclass of ``int``, so the obvious check would let ``--dry-run`` be a grade."""
    with pytest.raises(TypeError):
        Args(argparse.Namespace(grade=True)).number('grade')


@pytest.mark.unit
def test_a_missing_attribute_is_a_type_error_not_an_attribute_error() -> None:
    """One failure mode for the whole boundary, whatever went wrong upstream of it."""
    with pytest.raises(TypeError):
        Args(argparse.Namespace()).text('nothing')

    assert Args(argparse.Namespace()).optional_text('nothing') is None
    assert Args(argparse.Namespace()).flag('nothing') is False


def _commands_of(parser: argparse.ArgumentParser) -> set[str]:
    """Every ``command`` string the grammar can produce, found by walking the subparsers."""
    found: set[str] = set()
    for action in parser._actions:  # noqa: SLF001 -- argparse exposes its tree no other way
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            for subparser in action.choices.values():
                command = subparser.get_default('command')
                if isinstance(command, str):
                    found.add(command)
                found |= _commands_of(subparser)
    return found
