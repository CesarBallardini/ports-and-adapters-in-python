"""The deployment choices, read from the environment exactly once.

Three rules, and they are the whole of the configuration story:

1. **Configuration lives in environment variables**, all prefixed ``ACADEMY_``. There is no
   config file and no config service, so ``env | grep ACADEMY_`` shows a deployment's entire
   configuration, and a container image needs nothing mounted into it to be configured.
2. **The environment is read at start time, once.** :meth:`Settings.from_env` runs while the
   process is coming up; the result is frozen and handed to
   :class:`~academy.config.container.Container`. Exporting a variable afterwards changes
   nothing, which is what stops two requests in the same process from disagreeing.
3. **Every default lives in :class:`Defaults`.** A variable that is unset -- or set to nothing
   at all -- is not an error: it takes the value named there, and that class is the single
   answer to "what happens if I set nothing?".
4. **One configuration object, and every datum is a property of it.** :class:`Settings` is
   built once from :class:`Defaults` and the environment, and everything a deployment can
   choose is read off it -- ``settings.persistence``, never a second lookup somewhere else.
   Properties rather than public attributes, so the answer stays read-only and so a value that
   later becomes computed (assembled from two variables, say) changes here and nowhere else.

Settings are data, not behaviour: a record of what a deployment asked for. Nothing below the
composition root ever reads an environment variable, which is what keeps a use case
reproducible -- it cannot behave differently because a variable was exported.

The environment is read through an injected mapping rather than by reaching for ``os.environ``
at the point of use (ADR-0015). A test describes a deployment by passing a dict; it does not
mutate the process it is running in.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Self

# The environment, as everything here reads it: names to values, and nothing writable. Named
# rather than spelled out at each of the three call sites, because it is one concept -- "a
# deployment's environment" -- and because `os.environ` is only one of the things that satisfy
# it. A test passes a dict, and the type says that is not a workaround.
type Environ = Mapping[str, str]

# The variables a deployment sets. The prefix is `ACADEMY_` for every one of them, so
# `env | grep ACADEMY_` shows the whole of a deployment's configuration.
#
# `ACADEMY_DATABASE_URL` is deliberately absent: nothing in the composition root can act on it
# until the SQLAlchemy adapter exists (Phase B), and a setting nobody reads is a setting that
# can be wrong without anything noticing. The integration suite reads that variable directly
# for now (ADR-0007); it moves in here when there is an engine to hand it to.
#
# Comments rather than attribute docstrings: the check-docstring-first hook reads a string
# literal after a module-level assignment as a second module docstring.
ENV_PERSISTENCE: Final = 'ACADEMY_PERSISTENCE'


def _read(source: Environ, name: str) -> str | None:
    """One variable, or ``None`` when a deployment did not really set it.

    Blank counts as unset. ``ACADEMY_PERSISTENCE=`` in a compose file, a CI matrix leg that
    leaves a value empty, and ``export ACADEMY_PERSISTENCE=`` in a shell all mean "I have no
    opinion" -- nobody writes an empty value meaning to ask for a backend named ``''``, and
    failing a deployment for saying nothing would be a trap rather than a check.

    Whitespace-only goes the same way, for the same reason a stray space around a real value
    is trimmed: it is quoting noise, not intent.

    Every datum reads its variable through here, so this rule cannot come out differently for
    the next one.

    Args:
        source: The environment being read.
        name: The variable to read.

    Returns:
        The value, or ``None`` if it is absent or blank.
    """
    value = source.get(name)
    if value is None or not value.strip():
        return None
    return value


class ConfigurationError(Exception):
    """A deployment described something the composition root cannot build.

    Deliberately not an ``ApplicationError``: nothing here is a use case failing, and there is
    no HTTP status to map it to (ADR-0012). It is raised while the process is starting, before
    anything can serve a request, and the right response to it is a process that refuses to
    start rather than one that fails on the first request to touch the missing piece.
    """


class PersistenceBackend(StrEnum):
    """Which family of persistence adapters the composition root should wire.

    The enum is the whole list of answers, so an unreadable value fails at startup with the
    alternatives named, rather than falling through to a default that silently stores nothing
    durable.
    """

    MEMORY = 'memory'
    SQLALCHEMY = 'sqlalchemy'


class Defaults:
    """What a deployment gets for every variable it does not set.

    One class, so "run it with no environment at all and this is what you get" is a single
    screen rather than a hunt through field initialisers. It is also the list to read before
    changing one: a default is a decision about how the system behaves for everyone who has
    not thought about it, and those are worth seeing together.

    Class attributes rather than a dataclass instance: these are constants, there is never a
    second set of them, and ``Defaults.PERSISTENCE`` reads as the name of a value rather than
    as a lookup on an object someone has to construct first.

    ``Final``, so a test that "just overrides a default for a moment" is a type error. A test
    that wants different settings builds different :class:`Settings`.
    """

    # In-memory persistence: the only backend with an adapter today, and the one that makes a
    # bare `make run` and the whole test suite work with no environment at all. It stops being
    # the sane default the moment the SQLAlchemy adapter lands and durability is expected.
    PERSISTENCE: Final = PersistenceBackend.MEMORY


@dataclass(frozen=True, slots=True)
class _Values:
    """The resolved configuration, as plain data.

    Private: it is :class:`Settings`' storage, never its interface. Keeping the two apart is
    what lets a datum stop being a stored value -- computed from two variables, read lazily,
    derived from another -- without a single caller changing, because callers only ever touch
    the property.

    Frozen and comparable, which is where :class:`Settings` gets its equality and its ``repr``
    from: one line each, and neither can forget a datum that is added later.
    """

    persistence: PersistenceBackend = Defaults.PERSISTENCE


class Settings:
    """The configuration object: everything a deployment gets to choose, in one place.

    Built once at startup -- from the environment by :meth:`from_env`, or directly in a test --
    and read through properties from then on. There are no public attributes and no setters, so
    a configuration cannot be edited after the process has started and two requests cannot
    disagree about what it says.

    One datum so far, and it grows as the adapters that need configuring land. Adding one is
    three lines: a field on :class:`_Values`, a property here, and a line in :meth:`from_env`.

    Every datum defaults through :class:`Defaults` rather than restating a literal, so
    ``Settings()`` and a process started with an empty environment are the same thing by
    construction and cannot drift apart.
    """

    def __init__(self, persistence: PersistenceBackend = Defaults.PERSISTENCE) -> None:
        """Build the configuration a deployment is to run with.

        Args:
            persistence: Which family of persistence adapters to wire. Defaults to
                :attr:`Defaults.PERSISTENCE`.
        """
        self._values = _Values(persistence=persistence)

    @classmethod
    def from_env(cls, environ: Environ | None = None) -> Self:
        """Read the configuration a deployment described.

        Args:
            environ: Where to read it from. Defaults to ``os.environ``; a test passes its
                own mapping instead of exporting variables into the process it shares with
                every other test.

        Returns:
            The configuration object, with defaults filled in for anything unset.

        Raises:
            ConfigurationError: If ``ACADEMY_PERSISTENCE`` names a backend that does not exist.
        """
        source = os.environ if environ is None else environ
        return cls(persistence=cls._backend(_read(source, ENV_PERSISTENCE)))

    @property
    def persistence(self) -> PersistenceBackend:
        """Which family of persistence adapters the composition root wires.

        Read by :class:`~academy.config.container.Container` at startup and by nothing else:
        once the container has built the graph, no code below it can tell which backend it
        got, which is the property ADR-0003 exists to protect.
        """
        return self._values.persistence

    def __eq__(self, other: object) -> bool:
        """Whether two configurations say the same thing.

        ``object`` is the annotation ``__eq__`` is required to take, and the ``isinstance``
        narrows it immediately -- the one place in this package where the parameter type is
        not the type actually wanted.
        """
        if not isinstance(other, Settings):
            return NotImplemented
        return self._values == other._values

    def __hash__(self) -> int:
        """Hash the resolved values, so a configuration can key a cache or a fixture."""
        return hash(self._values)

    def __repr__(self) -> str:
        """Show every datum, which is what makes a startup log worth reading."""
        return f'Settings({self._values!r})'

    @staticmethod
    def _backend(value: str | None) -> PersistenceBackend:
        """Parse a backend name, or say what the acceptable ones were.

        A variable that was not really set takes the default -- :func:`_read` has already
        turned absent and blank alike into ``None`` -- while an *unreadable* one is an error.
        The difference is deliberate: saying nothing is a deployment accepting our choice,
        while ``ACADEMY_PERSISTENCE=postgres`` is a deployment asking for something specific,
        and silently ignoring that would start a process nobody asked for.

        Raises:
            ConfigurationError: If ``value`` is not one of the backends, with the list of
                those that are -- a typo is the likeliest cause and the message should be
                enough to fix it without reading this file.
        """
        if value is None:
            return Defaults.PERSISTENCE

        try:
            return PersistenceBackend(value.strip().lower())
        except ValueError as exc:
            supported = ', '.join(backend.value for backend in PersistenceBackend)
            raise ConfigurationError(
                f'{ENV_PERSISTENCE}={value!r} is not a persistence backend; expected one of: {supported}'
            ) from exc
