"""The classification table is a specification, so it is asserted against ADR-0012 line by line.

Two of these tests are the ones that matter, and neither checks a mapping. They check that the
table stays *total*: that no application error can be added without being classified, and that a
domain error added upstream still lands somewhere sensible. A table nobody can forget to update
is the only reason to have a table at all.
"""

from __future__ import annotations

import pytest

from academy.adapters.inbound.cli.exit_codes import ExitCode, for_failure
from academy.adapters.inbound.error_status import INTERNAL_ERROR_STATUS, Failure, classify, http_status
from academy.application.errors import (
    ApplicationError,
    AuthorizationError,
    ConflictError,
    MalformedSpreadsheetError,
    NotFoundError,
    PayloadTooLargeError,
)
from academy.application.jobs import JobStateError
from academy.domain.academics.course_section import AlreadyEnrolledError
from academy.domain.academics.degree_program import DuplicatePlanError, PlanNotFoundError
from academy.domain.academics.term import InvalidTermError
from academy.domain.grades.grade import InvalidGradeError
from academy.domain.graduation.graduation import GraduationStateError
from academy.domain.people.email import InvalidEmailError
from academy.domain.services.enrollment_service import (
    DuplicateSubjectEnrollmentError,
    NotAStudentError,
    SubjectNotInPlanError,
)
from academy.domain.services.grading_service import StudentNotEnrolledError
from academy.domain.shared.errors import DomainError

# ADR-0012's table, restated as data by hand. Restated on purpose: a test that derived the
# expectation from the implementation would pass whatever the implementation said.
#
# Plain tuples rather than `pytest.param`, so the list stays usable as data -- `_error_for` reads
# it to find an example of each `Failure`, and a `param`'s values are typed as `object` because
# they may hold pytest's own sentinels.
CLASSIFICATIONS: list[tuple[Exception, Failure]] = [
    (InvalidGradeError('11 is not a grade'), Failure.VALIDATION),
    (InvalidTermError('not a term'), Failure.VALIDATION),
    (InvalidEmailError('not an email'), Failure.VALIDATION),
    (MalformedSpreadsheetError('not a spreadsheet'), Failure.VALIDATION),
    (NotFoundError('person', 'x'), Failure.NOT_FOUND),
    (PlanNotFoundError('no such plan'), Failure.NOT_FOUND),
    (ConflictError('taken'), Failure.CONFLICT),
    (AlreadyEnrolledError('already'), Failure.CONFLICT),
    (DuplicatePlanError('twice'), Failure.CONFLICT),
    (DuplicateSubjectEnrollmentError('twice'), Failure.CONFLICT),
    (JobStateError('already running'), Failure.CONFLICT),
    (GraduationStateError('already revoked'), Failure.CONFLICT),
    (AuthorizationError('no relation grants it'), Failure.FORBIDDEN),
    (PayloadTooLargeError(10, 5), Failure.TOO_LARGE),
    (NotAStudentError('not a student'), Failure.RULE),
    (SubjectNotInPlanError('not in the plan'), Failure.RULE),
    (StudentNotEnrolledError('not enrolled'), Failure.RULE),
]

HTTP_STATUSES = [
    pytest.param(Failure.VALIDATION, 422, id='422'),
    pytest.param(Failure.NOT_FOUND, 404, id='404'),
    pytest.param(Failure.CONFLICT, 409, id='409'),
    pytest.param(Failure.FORBIDDEN, 403, id='403'),
    pytest.param(Failure.TOO_LARGE, 413, id='413'),
    pytest.param(Failure.RULE, 400, id='400'),
]

EXIT_CODES = [
    pytest.param(Failure.VALIDATION, ExitCode.VALIDATION, id='3'),
    pytest.param(Failure.NOT_FOUND, ExitCode.NOT_FOUND, id='4'),
    pytest.param(Failure.CONFLICT, ExitCode.CONFLICT, id='5'),
    pytest.param(Failure.FORBIDDEN, ExitCode.FORBIDDEN, id='6'),
    pytest.param(Failure.TOO_LARGE, ExitCode.TOO_LARGE, id='7'),
    pytest.param(Failure.RULE, ExitCode.RULE, id='8'),
]


def _descendants(root: type[BaseException]) -> set[type[BaseException]]:
    """Every subclass of ``root``, however deep, excluding ``root`` itself."""
    found: set[type[BaseException]] = set()
    for subclass in root.__subclasses__():
        found.add(subclass)
        found |= _descendants(subclass)
    return found


@pytest.mark.unit
@pytest.mark.parametrize(
    ('error', 'expected'),
    [pytest.param(error, failure, id=type(error).__name__) for error, failure in CLASSIFICATIONS],
)
def test_the_table_classifies_each_error_as_the_adr_says(error: Exception, expected: Failure) -> None:
    assert classify(error) == expected


@pytest.mark.unit
@pytest.mark.parametrize(('failure', 'status'), HTTP_STATUSES)
def test_each_failure_renders_as_the_http_status_adr_0012_assigns(failure: Failure, status: int) -> None:
    assert http_status(_error_for(failure)) == status


@pytest.mark.unit
@pytest.mark.parametrize(('failure', 'code'), EXIT_CODES)
def test_each_failure_renders_as_its_documented_exit_code(failure: Failure, code: ExitCode) -> None:
    assert for_failure(failure) == code


@pytest.mark.unit
def test_an_unclassified_exception_is_treated_as_a_bug_not_as_something_the_user_did() -> None:
    bug = TypeError('an adapter passed the wrong thing')

    assert classify(bug) is None
    assert http_status(bug) == INTERNAL_ERROR_STATUS
    assert for_failure(None) == ExitCode.ERROR


@pytest.mark.unit
def test_every_application_error_is_classified_explicitly() -> None:
    """The guard on our own hierarchy, which has no catch-all (ADR-0019).

    A new ``ApplicationError`` that nobody added to the table would otherwise become a 500 and an
    exit 1 -- a bug report about a thing that works.
    """
    assert _unclassified(ApplicationError) == []


@pytest.mark.unit
def test_every_domain_error_classifies_through_the_rule_fallback() -> None:
    """The guard on the *copied* hierarchy, which may grow without this repository (ADR-0002).

    There is nothing to update when it does -- that is the point of the fallback -- so this test
    fails only if the fallback is ever removed.
    """
    assert _unclassified(DomainError) == []


@pytest.mark.unit
def test_every_failure_has_both_renderings() -> None:
    """Neither table may lag behind the enum, and a missing entry is a ``KeyError`` at run time."""
    for failure in Failure:
        assert http_status(_error_for(failure)) > 0
        assert for_failure(failure) >= ExitCode.VALIDATION


@pytest.mark.unit
def test_the_classified_exit_codes_leave_the_conventional_ones_alone() -> None:
    """0, 1 and 2 mean what a shell already thinks they mean (ADR-0020).

    argparse exits 2 on a usage error of its own accord, so a classified failure that also used 2
    would make a mistyped flag indistinguishable from a missing record.
    """
    classified = {for_failure(failure) for failure in Failure}

    assert classified.isdisjoint({ExitCode.OK, ExitCode.ERROR, ExitCode.USAGE})


def _error_for(failure: Failure) -> Exception:
    """An example error the table above says is classified as ``failure``."""
    return next(error for error, expected in CLASSIFICATIONS if expected is failure)


def _unclassified(root: type[BaseException]) -> list[str]:
    """The names of ``root``'s subclasses that the table does not classify.

    Built with ``__new__`` rather than by constructing each one: these classes take different
    arguments and none of them is interesting here, since ``classify`` dispatches on type.
    """
    return [error.__name__ for error in _descendants(root) if classify(error.__new__(error)) is None]
