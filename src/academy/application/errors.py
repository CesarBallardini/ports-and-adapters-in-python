"""Errors raised by the application layer.

The domain has its own hierarchy, rooted at :class:`academy.domain.shared.errors.DomainError`,
for rule violations it can detect on its own. This module adds the failures that only make
sense once there is a *world* outside the domain: something was not found in storage,
something the caller is not allowed to do, a payload too large to accept.

Both hierarchies are translated to HTTP by the single table in
``academy.adapters.inbound.error_status`` (ADR-0012). Neither knows what a status code is.
"""

from __future__ import annotations


class ApplicationError(Exception):
    """Base class for failures detected by the application layer.

    Inbound adapters catch this and :class:`~academy.domain.shared.errors.DomainError`
    together, which is what guarantees every expected failure is translated rather than
    escaping as a 500.
    """


class NotFoundError(ApplicationError):
    """Raised when a referenced record does not exist.

    Repositories raise this from operations that *require* the record to exist -- ``save``,
    ``delete`` -- while lookups that may legitimately find nothing return ``None`` instead.
    The asymmetry is deliberate: absence is a normal outcome of a search and a broken
    expectation during an update, and conflating the two costs a use case its error handling.
    """

    def __init__(self, entity: str, identifier: object) -> None:
        """Record what was looked for and under which identifier.

        Args:
            entity: Human-readable name of the record type, e.g. ``'person'``.
            identifier: The identifier that did not resolve.
        """
        super().__init__(f'no {entity} with id {identifier!s}')
        self.entity = entity
        self.identifier = identifier


class ConflictError(ApplicationError):
    """Raised when an operation would violate a uniqueness or state constraint.

    Uniqueness is enforced by the repository rather than checked by the use case, because
    a check-then-act pair cannot be made safe against a concurrent caller: only the storage
    layer sees both attempts.
    """

    def __init__(self, message: str) -> None:
        """Describe the constraint that would have been violated."""
        super().__init__(message)


class AuthorizationError(ApplicationError):
    """Raised when the actor's resolved relations do not grant the requested action.

    Carries the policy's own reason so the denial is explainable. That reason is meant for
    logs and for the developer; adapters decide how much of it a caller may see, since
    "there is no such student" and "you may not read this student" are different answers to
    a probing request.
    """

    def __init__(self, reason: str) -> None:
        """Record why access was denied."""
        super().__init__(reason)
        self.reason = reason


class PayloadTooLargeError(ApplicationError):
    """Raised when an uploaded file exceeds the configured size cap.

    The cap exists because the spreadsheet ports take ``bytes`` (ADR-0008): the whole file
    is held in memory, so an unbounded upload is an unbounded allocation.
    """

    def __init__(self, size: int, limit: int) -> None:
        """Record the offending size and the limit it exceeded.

        Args:
            size: Size of the rejected payload, in bytes.
            limit: The configured maximum, in bytes.
        """
        super().__init__(f'payload of {size} bytes exceeds the limit of {limit} bytes')
        self.size = size
        self.limit = limit


class MalformedSpreadsheetError(ApplicationError):
    """Raised when an uploaded file cannot be parsed as a spreadsheet at all.

    This is the single failure every spreadsheet adapter must normalise its library's
    exceptions into (ADR-0008). A use case can therefore handle one error type rather than
    the union of everything ``openpyxl`` and ``csv`` might raise, and the two adapters stay
    interchangeable in the face of bad input as well as good.
    """

    def __init__(self, reason: str) -> None:
        """Record why the file could not be read."""
        super().__init__(reason)
        self.reason = reason
