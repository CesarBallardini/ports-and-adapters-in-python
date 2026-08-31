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
# Two database URLs, not one, and the split is a security boundary rather than a convenience
# (ADR-0018): the application connects with a role that can read and write rows and cannot
# touch the schema, while migrations connect with one that owns it.
#
# Comments rather than attribute docstrings: the check-docstring-first hook reads a string
# literal after a module-level assignment as a second module docstring.
ENV_PERSISTENCE: Final = 'ACADEMY_PERSISTENCE'
ENV_DATABASE_URL: Final = 'ACADEMY_DATABASE_URL'
ENV_MIGRATION_DATABASE_URL: Final = 'ACADEMY_MIGRATION_DATABASE_URL'
ENV_UPLOAD_DIRECTORY: Final = 'ACADEMY_UPLOAD_DIRECTORY'
ENV_IMPORT_INLINE_THRESHOLD: Final = 'ACADEMY_IMPORT_INLINE_THRESHOLD_BYTES'
ENV_IMPORT_MAX_BYTES: Final = 'ACADEMY_IMPORT_MAX_BYTES'


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
    if value is None:
        return None

    trimmed = value.strip()
    return trimmed or None


class ConfigurationError(Exception):
    """A deployment described something the composition root cannot build.

    Deliberately not an ``ApplicationError``: nothing here is a use case failing, and there is
    no HTTP status to map it to (ADR-0012). It is raised while the process is starting, before
    anything can serve a request, and the right response to it is a process that refuses to
    start rather than one that fails on the first request to touch the missing piece.
    """


def _size(value: str | None, name: str, default: int) -> int:
    """Read a byte count, or say which variable was not a number.

    A size is the one kind of setting a deployment is likely to get wrong in a way that only
    shows up much later -- ``ACADEMY_IMPORT_MAX_BYTES=16MB`` is a perfectly reasonable thing to
    type and not a number. Refusing it at startup with the variable's name is the whole
    difference between a five-second fix and a puzzling failure on the first large upload.

    Raises:
        ConfigurationError: If the value is not a positive whole number of bytes. Zero is
            refused too: a threshold of zero queues everything and a cap of zero accepts
            nothing, and neither is plausibly what someone meant.
    """
    if value is None:
        return default

    try:
        size = int(value)
    except ValueError as error:
        raise ConfigurationError(f'{name}={value!r} is not a whole number of bytes') from error

    if size <= 0:
        raise ConfigurationError(f'{name}={value!r} must be greater than zero')
    return size


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

    # Where an import stops running inline and gets queued instead (ADR-0009). 256 KiB is
    # roughly a few thousand grade rows: large enough that a teacher's sheet comes back in the
    # same response, small enough that a registrar's cohort file does not hold a request open.
    IMPORT_INLINE_THRESHOLD_BYTES: Final = 256 * 1024

    # The hard cap, above which nothing is accepted at all. It exists because the spreadsheet
    # ports take `bytes`: the whole file is in memory while it is parsed, so this number is a
    # promise about this process's footprint, not a policy about file sizes.
    IMPORT_MAX_BYTES: Final = 16 * 1024 * 1024

    # A file beside the project, so a developer with no environment at all gets a database
    # that survives a restart. Not `:memory:`: a migration against it would build a schema
    # and discard it in the same breath.
    DATABASE_URL: Final = 'sqlite+aiosqlite:///./academy_development.db'

    # Where a queued import's payload is written when storage is durable. Beside the database
    # rather than in a temporary directory: a payload that vanished on reboot would leave a
    # pending job pointing at nothing, which is a failure the worker reports and nobody can fix.
    UPLOAD_DIRECTORY: Final = './academy_uploads'


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
    import_inline_threshold_bytes: int = Defaults.IMPORT_INLINE_THRESHOLD_BYTES
    import_max_bytes: int = Defaults.IMPORT_MAX_BYTES
    database_url: str = Defaults.DATABASE_URL
    upload_directory: str = Defaults.UPLOAD_DIRECTORY
    # None means "the same database, with whatever privileges that URL carries" -- which is
    # what a developer on SQLite has, since SQLite has no roles to separate (ADR-0018).
    migration_database_url: str | None = None


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

    def __init__(
        self,
        persistence: PersistenceBackend = Defaults.PERSISTENCE,
        import_inline_threshold_bytes: int = Defaults.IMPORT_INLINE_THRESHOLD_BYTES,
        import_max_bytes: int = Defaults.IMPORT_MAX_BYTES,
        database_url: str = Defaults.DATABASE_URL,
        migration_database_url: str | None = None,
        upload_directory: str = Defaults.UPLOAD_DIRECTORY,
    ) -> None:
        """Build the configuration a deployment is to run with.

        Args:
            persistence: Which family of persistence adapters to wire.
            import_inline_threshold_bytes: At or above this size an import is queued.
            import_max_bytes: The hard cap on an uploaded payload.
            database_url: How the *application* connects: rows, never schema.
            migration_database_url: How *migrations* connect. ``None`` falls back to
                ``database_url``, which is what a SQLite developer has and what a PostgreSQL
                deployment must not leave unset.
            upload_directory: Where a queued import's payload is written when storage is
                durable. Ignored by the in-memory backend, which keeps payloads in the process.
        """
        self._values = _Values(
            persistence=persistence,
            import_inline_threshold_bytes=import_inline_threshold_bytes,
            import_max_bytes=import_max_bytes,
            database_url=database_url,
            migration_database_url=migration_database_url,
            upload_directory=upload_directory,
        )

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
        return cls(
            persistence=cls._backend(_read(source, ENV_PERSISTENCE)),
            import_inline_threshold_bytes=_size(
                _read(source, ENV_IMPORT_INLINE_THRESHOLD),
                ENV_IMPORT_INLINE_THRESHOLD,
                Defaults.IMPORT_INLINE_THRESHOLD_BYTES,
            ),
            import_max_bytes=_size(
                _read(source, ENV_IMPORT_MAX_BYTES), ENV_IMPORT_MAX_BYTES, Defaults.IMPORT_MAX_BYTES
            ),
            database_url=_read(source, ENV_DATABASE_URL) or Defaults.DATABASE_URL,
            migration_database_url=_read(source, ENV_MIGRATION_DATABASE_URL),
            upload_directory=_read(source, ENV_UPLOAD_DIRECTORY) or Defaults.UPLOAD_DIRECTORY,
        )

    @property
    def database_url(self) -> str:
        """How the application connects to its database.

        The role behind this URL should be able to read and write rows and nothing else
        (ADR-0018). Nothing in the application ever runs DDL, so a role that could is a role
        whose extra privileges only a mistake would ever use.
        """
        return self._values.database_url

    @property
    def migration_database_url(self) -> str:
        """How migrations connect.

        Falls back to :attr:`database_url` when unset, because SQLite has no roles to separate
        and a developer should not have to configure two URLs to get a working database. On
        PostgreSQL, leaving it unset means migrations run as the application role -- which
        works, and gives up the separation the split exists for.
        """
        return self._values.migration_database_url or self._values.database_url

    @property
    def upload_directory(self) -> str:
        """Where a queued import's payload is written, when storage is durable."""
        return self._values.upload_directory

    @property
    def import_inline_threshold_bytes(self) -> int:
        """At or above this many bytes, an import is queued rather than run inline."""
        return self._values.import_inline_threshold_bytes

    @property
    def import_max_bytes(self) -> int:
        """The largest payload this process will accept at all."""
        return self._values.import_max_bytes

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
            return PersistenceBackend(value.lower())
        except ValueError as exc:
            supported = ', '.join(backend.value for backend in PersistenceBackend)
            raise ConfigurationError(
                f'{ENV_PERSISTENCE}={value!r} is not a persistence backend; expected one of: {supported}'
            ) from exc
