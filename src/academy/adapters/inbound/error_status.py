"""The one place an expected failure is classified, for every inbound adapter (ADR-0012).

Handlers contain **no** ``except DomainError``. They call a use case, let it raise, and let the
adapter's single error boundary consult :func:`classify` -- which is what stops two surfaces
disagreeing about what a conflict is, and what stopped the reference application accumulating
thirty hand-written translation sites that did not agree with each other.

The table classifies; it does not render (ADR-0019). :class:`Failure` is the vocabulary both
renderings share, because "this was forbidden" is the only part of the answer that is genuinely
protocol-independent -- ``403`` is an HTTP fact and exit ``6`` is a shell fact, and neither
belongs in the other's adapter. :func:`http_status` is the HTTP rendering, fixed by ADR-0012's
table; the CLI's is :class:`~academy.adapters.inbound.cli.exit_codes.ExitCode`.

:func:`classify` returning ``None`` means **a bug of ours**: an exception nobody predicted, which
becomes a 500 to an HTTP client and exit 1 to a shell. It is deliberately not given a friendly
status, because a bug that looks like a user error is a bug nobody fixes.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Final

from academy.application.errors import (
    AuthorizationError,
    ConflictError,
    MalformedSpreadsheetError,
    NotFoundError,
    PayloadTooLargeError,
)
from academy.application.jobs import JobStateError
from academy.domain.academics.course_section import AlreadyEnrolledError
from academy.domain.academics.degree_program import DuplicatePlanError, PlanNotFoundError
from academy.domain.academics.subject import InvalidSubjectError
from academy.domain.academics.term import InvalidTermError
from academy.domain.grades.grade import InvalidGradeError
from academy.domain.graduation.graduation import GraduationStateError
from academy.domain.people.age_of_majority import InvalidAgeOfMajorityError
from academy.domain.people.email import InvalidEmailError
from academy.domain.people.personal_data import InvalidPersonalDataError
from academy.domain.services.enrollment_service import DuplicateSubjectEnrollmentError
from academy.domain.shared.errors import DomainError


class Failure(Enum):
    """What kind of expected failure this was, in vocabulary no protocol owns.

    An enum rather than a status code, because the members have to survive being rendered as an
    HTTP status, as a process exit code and -- when the worker lands -- as a retry decision. The
    member is the classification; each adapter owns its own rendering of it (ADR-0019).
    """

    VALIDATION = 'validation'
    NOT_FOUND = 'not_found'
    CONFLICT = 'conflict'
    FORBIDDEN = 'forbidden'
    TOO_LARGE = 'too_large'
    RULE = 'rule'


# The table. **Ordered, most specific first**, and matched by `isinstance`, so a subclass never
# has to be remembered in a second place and adding one to the domain cannot silently change how
# its parent is classified.
#
# `DomainError` is last and is the catch-all for the domain hierarchy: the domain is copied and
# may grow an error without this repository being consulted (ADR-0002), and "a rule was broken"
# is the right answer for one nobody has thought about yet. `ApplicationError` has no catch-all
# on purpose -- it is ours, and a new member of it that nobody classified should fail a test
# rather than become a 500.
#
# Comments rather than attribute docstrings: the check-docstring-first hook reads a string
# literal after a module-level assignment as a second module docstring.
_TABLE: Final[tuple[tuple[type[Exception], Failure], ...]] = (
    # Value objects refusing their input. Every one of these is someone typing `eleven` into a
    # grade box, which is unprocessable content rather than a broken rule.
    (InvalidGradeError, Failure.VALIDATION),
    (InvalidTermError, Failure.VALIDATION),
    (InvalidSubjectError, Failure.VALIDATION),
    (InvalidEmailError, Failure.VALIDATION),
    (InvalidPersonalDataError, Failure.VALIDATION),
    (InvalidAgeOfMajorityError, Failure.VALIDATION),
    # A file that is not a spreadsheet at all. The same answer for the same reason: the request
    # was well-formed and its content was not.
    (MalformedSpreadsheetError, Failure.VALIDATION),
    (NotFoundError, Failure.NOT_FOUND),
    (PlanNotFoundError, Failure.NOT_FOUND),
    (ConflictError, Failure.CONFLICT),
    (AlreadyEnrolledError, Failure.CONFLICT),
    (DuplicatePlanError, Failure.CONFLICT),
    (DuplicateSubjectEnrollmentError, Failure.CONFLICT),
    # State machines refusing a transition. A job that is already running and a graduation that
    # is already revoked are both "not from where you are", which is what 409 means.
    (JobStateError, Failure.CONFLICT),
    (GraduationStateError, Failure.CONFLICT),
    (AuthorizationError, Failure.FORBIDDEN),
    (PayloadTooLargeError, Failure.TOO_LARGE),
    (DomainError, Failure.RULE),
)

# ADR-0012's table, verbatim. Kept as data next to the classification so the two cannot drift,
# and so the ADR can be checked against the code by reading them side by side.
_HTTP_STATUS: Final[Mapping[Failure, int]] = {
    Failure.VALIDATION: 422,
    Failure.NOT_FOUND: 404,
    Failure.CONFLICT: 409,
    Failure.FORBIDDEN: 403,
    Failure.TOO_LARGE: 413,
    Failure.RULE: 400,
}

# What an unclassified exception becomes over HTTP. Named rather than inlined because the CLI has
# the same concept under a different number, and both are "we did not predict this".
INTERNAL_ERROR_STATUS: Final = 500


def classify(error: BaseException) -> Failure | None:
    """Say what kind of expected failure this is.

    Args:
        error: The exception a use case raised.

    Returns:
        The classification, or ``None`` if this exception is not an expected failure at all --
        which means it is a bug, and the caller should render it as one rather than as something
        the user did.
    """
    for error_type, failure in _TABLE:
        if isinstance(error, error_type):
            return failure
    return None


def http_status(error: BaseException) -> int:
    """Render a failure as the HTTP status ADR-0012 assigns it.

    Args:
        error: The exception a use case raised.

    Returns:
        The mapped status, or ``500`` for anything unclassified -- a genuine bug, which is
        allowed to look like one.
    """
    failure = classify(error)
    return INTERNAL_ERROR_STATUS if failure is None else _HTTP_STATUS[failure]
