"""What each route is allowed to touch, asserted against the routes that actually exist.

The mirror of ``test_cli_commands.py``, and the same property under a different framework: **a
handler takes one driving port, never the** :class:`~academy.config.container.Scope`. There it was
a table of ``Command[PortT]`` rows the type checker refused to mispair; here it is a set of
FastAPI dependencies, which the type checker cannot police the same way -- so these tests do.

The risk is concrete rather than stylistic. A scope carries every repository as well as every use
case, so a route holding one could answer ``GET /sections/{id}/grades`` out of ``scope.histories``
with no use case, no ``AccessGuard`` and no authorization, and every hand-written test of that
route would still pass. What follows refuses the possibility.

These read the **routers** rather than the assembled application, and that is deliberate. FastAPI
0.141 wraps an included router in an internal ``_IncludedRouter`` and applies router-level
dependencies at request time rather than merging them into each route, so walking ``app.routes``
would mean asserting our architecture through three of the framework's private attributes -- a
test that goes red on somebody else's refactor. The routers are ours. That the application
includes them is asserted where it belongs, over HTTP, in ``tests/integration/test_web.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Annotated, get_args, get_origin, get_type_hints

import pytest
from fastapi import APIRouter, FastAPI, Request
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from academy.adapters.inbound.web import dependencies
from academy.adapters.inbound.web.routers import api, auth, grades, records
from academy.application.ports.inbound.grading import ManageGrades
from academy.application.ports.inbound.imports import ImportData
from academy.application.ports.inbound.records import ViewStudentRecords
from academy.config.container import Container, Scope

pytestmark = pytest.mark.unit

# A resolved annotation is a *typing form* -- `Annotated[...]`, `str | None`, a class -- and only
# sometimes a class, so `type` would be wrong and `object` is the honest width. This is the one
# annotation in the file that is deliberately that loose, and the alias says so rather than
# leaving a bare `object` to be read as carelessness.
type TypeForm = object

# Every driving port there is. A route may name at most one; naming two would be a route that can
# do two jobs, which is where "while I am here" features come from.
DRIVING_PORTS: tuple[TypeForm, ...] = (ManageGrades, ViewStudentRecords, ImportData)

# The routers the application includes, and the surface each one marks. Written out rather than
# discovered, because a router added and never included here should fail these tests loudly
# instead of quietly not being checked.
ROUTERS: tuple[tuple[str, APIRouter, Callable[..., None]], ...] = (
    ('auth', auth.router, dependencies.web_surface),
    ('grades', grades.router, dependencies.web_surface),
    ('records', records.router, dependencies.web_surface),
    ('api', api.router, dependencies.api_surface),
)

ALL_ROUTERS = tuple(router for _, router, _ in ROUTERS)


def _routes(router: APIRouter) -> Iterator[APIRoute]:
    """Every API route a router declares."""
    for route in router.routes:
        if isinstance(route, APIRoute):
            yield route


def _all_routes() -> Iterator[tuple[APIRouter, APIRoute]]:
    """Every route in the adapter, with the router that declares it."""
    for router in ALL_ROUTERS:
        for route in _routes(router):
            yield router, route


def _dependants(dependant: Dependant) -> Iterator[Dependant]:
    """One dependency and everything it pulls in, transitively."""
    yield dependant
    for sub in dependant.dependencies:
        yield from _dependants(sub)


def _router_dependencies(router: APIRouter) -> set[Callable[..., object]]:
    """The callables a router applies to every one of its routes."""
    return {depends.dependency for depends in router.dependencies if depends.dependency is not None}


def _annotations(function: Callable[..., object]) -> dict[str, TypeForm]:
    """Resolved parameter types, with ``Annotated`` metadata intact.

    ``from __future__ import annotations`` makes every annotation in the adapter a string, so
    reading ``__annotations__`` directly would compare against ``'Grades'`` and pass for the wrong
    reason. This resolves them the way the framework itself does.
    """
    return dict(get_type_hints(function, include_extras=True))


def _underlying(annotation: TypeForm) -> TypeForm:
    """The type inside an ``Annotated[...]``, or the annotation itself."""
    return get_args(annotation)[0] if get_origin(annotation) is Annotated else annotation


def _named_ports(route: APIRoute) -> set[TypeForm]:
    """The driving ports this route's signature asks for."""
    return {
        underlying
        for annotation in _annotations(route.endpoint).values()
        if (underlying := _underlying(annotation)) in DRIVING_PORTS
    }


def _methods(route: APIRoute) -> frozenset[str]:
    """The HTTP methods a route answers.

    ``APIRoute.methods`` is typed ``set[str] | None`` because Starlette's base class allows a
    route with none. Ours always have some; narrowing here rather than at each of the three call
    sites keeps that assumption in one place.
    """
    return frozenset(route.methods or ())


def _route_for(path: str, method: str) -> APIRoute:
    """The one route with this path and method."""
    for _, route in _all_routes():
        if route.path == path and method in _methods(route):
            return route
    raise AssertionError(f'no route for {method} {path}')


def test_the_adapter_has_the_routes_this_module_claims_to_check() -> None:
    """A guard against the walk finding nothing and every test below passing vacuously."""
    paths = {(route.path, tuple(sorted(_methods(route)))) for _, route in _all_routes()}

    assert ('/sections/{section_id}/grades', ('GET',)) in paths
    assert ('/sections/{section_id}/grades', ('POST',)) in paths
    assert ('/api/sections/{section_id}/grades', ('GET',)) in paths
    assert ('/api/sections/{section_id}/grades', ('POST',)) in paths
    assert ('/sign-in', ('POST',)) in paths
    assert ('/wards', ('GET',)) in paths
    assert ('/students/{student_id}/transcript', ('GET',)) in paths


def test_no_route_takes_a_scope() -> None:
    """The rule, stated over every route the adapter has.

    A route that named a ``Scope`` would type-check, run, and be able to reach every repository in
    the system. There is nothing in FastAPI to stop it, so this is the thing that does.
    """
    offenders = [
        f'{route.path} ({name})'
        for _, route in _all_routes()
        for name, annotation in _annotations(route.endpoint).items()
        if _underlying(annotation) is Scope
    ]

    assert offenders == []


def test_no_route_takes_a_container() -> None:
    """The same rule one level up: a container can open as many scopes as it likes."""
    offenders = [
        f'{route.path} ({name})'
        for _, route in _all_routes()
        for name, annotation in _annotations(route.endpoint).items()
        if _underlying(annotation) is Container
    ]

    assert offenders == []


def test_exactly_one_dependency_in_the_whole_adapter_produces_a_scope() -> None:
    """Two places may name a ``Scope``; only one of them is a dependency.

    Everything else depends on a *port* built from that one. A second scope-producing dependency
    would also mean two scopes per request -- two sessions, two transactions -- which is a
    correctness problem before it is an architectural one.
    """
    producers = {
        dependant.call
        for _, route in _all_routes()
        for dependant in _dependants(route.dependant)
        if dependant.call is not None and _returns_scope(dependant.call)
    }

    assert producers == {dependencies.scope}


def _returns_scope(call: Callable[..., object]) -> bool:
    """Whether this dependency yields or returns a ``Scope``.

    ``scope`` is an async generator, so its annotation is ``AsyncIterator[Scope]`` rather than
    ``Scope``; both shapes count, because both hand a scope to whatever asked for one.
    """
    try:
        returned = get_type_hints(call).get('return')
    except NameError, TypeError:  # pragma: no cover -- a builtin or a partial, neither of ours
        return False
    return returned is Scope or Scope in get_args(returned)


@pytest.mark.parametrize(
    ('path', 'method', 'port'),
    [
        ('/sections/{section_id}/grades', 'GET', ManageGrades),
        ('/sections/{section_id}/grades', 'POST', ManageGrades),
        ('/api/sections/{section_id}/grades', 'GET', ManageGrades),
        ('/api/sections/{section_id}/grades', 'POST', ManageGrades),
        ('/wards', 'GET', ViewStudentRecords),
        ('/students/{student_id}/transcript', 'GET', ViewStudentRecords),
    ],
)
def test_each_route_names_exactly_the_driving_port_it_needs(path: str, method: str, port: TypeForm) -> None:
    """One row per route, saying which capability it may reach. No route may reach two."""
    assert _named_ports(_route_for(path, method)) == {port}


def test_the_two_grade_routers_reach_the_same_port() -> None:
    """ADR-0011's claim that the browser and the JSON API "call identical objects".

    Not a rewording of the test above: that one checks each route against a row someone wrote
    down, while this compares the two surfaces to *each other*. A change that widened the API's
    access and was dutifully recorded in both rows would pass there and fail here.
    """
    for method in ('GET', 'POST'):
        browser = _named_ports(_route_for('/sections/{section_id}/grades', method))
        machine = _named_ports(_route_for('/api/sections/{section_id}/grades', method))
        assert browser == machine, method


def test_the_authentication_routes_reach_no_driving_port() -> None:
    """Signing in happens before any use case, and reaches none of them."""
    for path, method in (('/sign-in', 'GET'), ('/sign-in', 'POST'), ('/sign-out', 'POST')):
        assert _named_ports(_route_for(path, method)) == set(), f'{method} {path}'


def test_only_the_sign_in_route_holds_a_repository() -> None:
    """The one documented exception, and a check that it stays one.

    Authentication has no driving port -- ADR-0010 puts credential verification at the adapter
    edge -- so the sign-in route depends on a repository directly. Every *other* route reaches
    storage only through a use case, and this is what would notice a second one appearing.
    """
    holders = {
        route.path
        for _, route in _all_routes()
        for dependant in _dependants(route.dependant)
        if dependant.call is dependencies.people_for_sign_in
    }

    assert holders == {'/sign-in'}


@pytest.mark.parametrize(('name', 'router', 'marker'), ROUTERS, ids=[name for name, _, _ in ROUTERS])
def test_every_router_marks_its_surface(name: str, router: APIRouter, marker: Callable[..., None]) -> None:
    """A router that forgot to mark its surface would render its errors as the wrong thing.

    On the router rather than on each route, precisely so a route added later cannot forget it.
    """
    assert marker in _router_dependencies(router), name


def test_only_the_browser_routers_enforce_csrf() -> None:
    """The asymmetry ADR-0010 implies, asserted rather than left to a comment.

    A cookie is attached by the browser to a cross-site request; an ``Authorization`` header is
    not, so the JSON router has nothing to forge and a check there would suggest a protection
    nobody is actually getting.
    """
    from academy.adapters.inbound.web.csrf import enforce

    enforcing = {name for name, router, _ in ROUTERS if enforce in _router_dependencies(router)}

    assert enforcing == {'auth', 'grades', 'records'}


def test_every_dependency_alias_is_used_by_some_route() -> None:
    """No alias defined and never spelled.

    An unused alias is dead code the type checker is happy with, and worse than dead: it is a
    widening of what routes *may* reach that nobody is currently reaching, which reads as
    permission the next person is entitled to take.

    ``ScopeDependency`` is the deliberate exception -- it exists for other *dependencies* to build
    on and is asserted separately, below, to be spelled by no route at all.
    """
    aliases = {
        name: _underlying(value)
        for name, value in vars(dependencies).items()
        if get_origin(value) is Annotated and name != 'ScopeDependency'
    }
    used = {
        _underlying(annotation) for _, route in _all_routes() for annotation in _annotations(route.endpoint).values()
    }

    assert {name for name, produced in aliases.items() if produced not in used} == set()


def test_the_scope_alias_is_spelled_by_no_route() -> None:
    """``ScopeDependency`` is for dependencies to build on, and never for a route to ask for."""
    assert get_origin(dependencies.ScopeDependency) is Annotated
    assert _underlying(dependencies.ScopeDependency) is Scope
    assert [route.path for _, route in _all_routes() if Scope in _named_annotations(route)] == []


def _named_annotations(route: APIRoute) -> set[TypeForm]:
    """Every underlying type a route's signature names."""
    return {_underlying(annotation) for annotation in _annotations(route.endpoint).values()}


# ---------------------------------------------------------------------------------------------
# The guards on what the composition root is supposed to have put on the app
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('resolve', 'missing'),
    [
        pytest.param(dependencies.container_of, 'container', id='container'),
        pytest.param(dependencies.credentials_of, 'credentials', id='credentials'),
        pytest.param(dependencies.templates_of, 'templates', id='templates'),
    ],
)
def test_an_application_assembled_by_hand_fails_loudly(resolve: Callable[[Request], object], missing: str) -> None:
    """``app.state`` is untyped, so these three ``isinstance`` checks are where that ``Any`` stops.

    They raise rather than becoming a status because the failure is a programming error, not a
    request error: the application was built without going through the composition root, which no
    request can cause and none can fix. A 500 with a traceback is the honest answer.

    Asserted because the alternative -- ``getattr`` returning ``None`` and the failure surfacing
    forty lines later as ``AttributeError: 'NoneType' has no attribute 'request_scope'`` -- is the
    error message this guard exists to replace.
    """
    bare = FastAPI()

    # Names what is missing *and* what to use instead: an error that said only "not configured"
    # would leave the reader with three candidates and no next step.
    with pytest.raises(RuntimeError, match=rf'{missing}.*academy\.config\.create_app'):
        resolve(Request({'type': 'http', 'method': 'GET', 'path': '/', 'headers': [], 'app': bare}))
