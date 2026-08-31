"""The dispatch table: one port per subcommand, and nothing wired that nobody reaches.

These test the *binding*, not the behaviour. What each command does is asserted end to end in
``tests/integration/test_cli.py`` against a real database; what is asserted here is the thing that
file cannot see — that a handler is handed exactly the driving port it declared and never the
scope it came out of.

That is not tidiness. A :class:`~academy.config.container.Scope` carries every repository as well
as every use case, so a handler holding one could read a transcript straight out of
``scope.histories`` and never call a use case at all — a business rule leaking into an adapter,
one line away. The type checker refuses a mispaired row; these refuse the rest.
"""

from __future__ import annotations

import argparse
import inspect
from collections.abc import AsyncIterator
from uuid import UUID

import pytest

from academy.adapters.inbound.cli import commands
from academy.adapters.inbound.cli.commands import HANDLERS, Command
from academy.adapters.inbound.cli.parser import Args
from academy.adapters.inbound.cli.render import Output
from academy.application.dtos import Actor
from academy.application.ports.inbound.grading import ManageGrades
from academy.application.ports.inbound.imports import ImportData
from academy.application.ports.inbound.records import ViewStudentRecords
from academy.config.container import Container, Scope
from academy.config.settings import Settings
from academy.domain.shared.ids import PersonId

# The driving ports a subcommand may be bound to. Spelled as a union rather than as `type`, so
# that a row bound to something that is not a driving port at all -- a repository, the scope --
# would not type-check in the table below either.
type DrivingPort = ManageGrades | ViewStudentRecords | ImportData

ANYONE = Actor(person_id=PersonId(UUID(int=1)))


@pytest.fixture
async def scope() -> AsyncIterator[Scope]:
    """A real scope over the in-memory backend, which is all this file needs.

    Real rather than a stand-in, because the assertion it serves is about what the composition
    root actually produces. Nothing here calls a use case, so the store stays empty.
    """
    container = Container(Settings())
    try:
        async with container.request_scope() as opened:
            yield opened
    finally:
        await container.aclose()


@pytest.mark.unit
@pytest.mark.parametrize(
    ('command', 'port'),
    [
        pytest.param('grades list', ManageGrades, id='grades-list'),
        pytest.param('grades record', ManageGrades, id='grades-record'),
        pytest.param('records show', ViewStudentRecords, id='records-show'),
        pytest.param('records wards', ViewStudentRecords, id='records-wards'),
        pytest.param('import template', ImportData, id='import-template'),
        pytest.param('import run', ImportData, id='import-run'),
        pytest.param('import job', ImportData, id='import-job'),
    ],
)
async def test_each_command_is_handed_exactly_one_driving_port(
    command: str, port: type[DrivingPort], scope: Scope
) -> None:
    """Each row of the table says which port its subcommand may touch, and no handler sees more.

    The type checker already refuses a mispaired row -- ``Command(Scope.grade_management,
    records_show)`` does not compile. This asserts the other half at run time: that what the
    accessor actually produces satisfies the port the handler asked for, which is a statement
    about the *composition root* rather than about an annotation.
    """
    bound = HANDLERS[command]
    assert isinstance(bound, Command)
    assert isinstance(bound.port(scope), port)


@pytest.mark.unit
async def test_a_command_hands_its_handler_the_port_and_nothing_else(scope: Scope) -> None:
    """The binder's whole job, asserted directly: resolve, then delegate.

    The handler sees the port, the actor and the arguments. It does not see -- and has no way to
    ask for -- the scope those came out of.
    """
    seen: list[tuple[object, Actor, Args]] = []
    resolved = object()
    args = Args(argparse.Namespace())

    async def handler(port: object, actor: Actor, arguments: Args) -> Output:
        seen.append((port, actor, arguments))
        return Output(lines=('done',))

    command = Command(lambda _scope: resolved, handler)
    output = await command(scope, ANYONE, args)

    assert seen == [(resolved, ANYONE, args)]
    assert output.lines == ('done',)


@pytest.mark.unit
async def test_the_port_is_resolved_once_per_invocation_and_not_shared(scope: Scope) -> None:
    """A use case is built per command, so two runs cannot share one unit of work.

    ``Scope.grade_management`` builds a fresh ``GradeManagement`` each call, and each of those
    holds its own unit of work -- which refuses re-entry while active. A binder that resolved the
    port once and cached it would turn two overlapping invocations into a ``RuntimeError``.
    """
    grades = HANDLERS['grades list']
    assert isinstance(grades, Command)

    assert grades.port(scope) is not grades.port(scope)


@pytest.mark.unit
def test_every_handler_written_is_a_handler_reachable() -> None:
    """No orphan handlers, and no handler wired under a name the grammar does not produce.

    A handler that is defined and never bound is dead code the type checker is happy with: it
    compiles, it is tested if someone tests it directly, and no command line can ever reach it.
    """
    written = {
        name
        for name, value in vars(commands).items()
        if inspect.iscoroutinefunction(value) and not name.startswith('_')
    }
    wired = {command.handle.__name__ for command in HANDLERS.values() if isinstance(command, Command)}

    assert written == wired


@pytest.mark.unit
def test_no_handler_is_bound_to_two_commands_by_accident() -> None:
    """Each subcommand has its own handler; a copy-paste in the table would show up here.

    ``import template`` and ``import run`` share a *port* and must not share a *handler* -- and
    the type checker cannot tell the difference, because both pairings are well typed.
    """
    handlers = [command.handle for command in HANDLERS.values() if isinstance(command, Command)]

    assert len(set(handlers)) == len(HANDLERS)
