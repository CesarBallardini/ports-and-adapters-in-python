"""Graduation eligibility and conferral."""

from __future__ import annotations

from datetime import date

from academy.domain.academics.plan import Plan
from academy.domain.grades.academic_history import AcademicHistory
from academy.domain.graduation.graduation import Graduation
from academy.domain.people.person import Person
from academy.domain.shared.errors import DomainError
from academy.domain.shared.ids import CredentialId, GraduationId, ProgramId


class NotEligibleForGraduationError(DomainError):
    """Raised when conferring graduation on a student who has not passed every subject."""


class EmptyPlanError(DomainError):
    """Raised when checking graduation against a plan that has no subjects."""


class GraduationService:
    """Computes graduation eligibility and confers the graduation event."""

    def is_eligible(self, history: AcademicHistory, plan: Plan) -> bool:
        """Return whether the student has passed every subject in ``plan``.

        Raises:
            EmptyPlanError: If ``plan`` has no subjects (graduation is undefined).
        """
        subject_ids = plan.subject_ids()
        if not subject_ids:
            raise EmptyPlanError(str(plan.id))
        return all(history.has_passed(subject_id) for subject_id in subject_ids)

    def confer(
        self,
        id: GraduationId,
        student: Person,
        program_id: ProgramId,
        credential_id: CredentialId,
        history: AcademicHistory,
        plan: Plan,
        on: date,
    ) -> Graduation:
        """Confer graduation on an eligible student, issuing the program's credential.

        Args:
            id: The new graduation's identifier.
            student: The graduating student.
            program_id: The degree program granting the credential.
            credential_id: The credential to issue.
            history: The student's academic history.
            plan: The plan the student enrolled under.
            on: The conferral date.

        Returns:
            The conferred graduation record.

        Raises:
            NotEligibleForGraduationError: If the student is not eligible.
        """
        if not self.is_eligible(history, plan):
            raise NotEligibleForGraduationError(str(student.id))
        return Graduation(
            id=id,
            student_id=student.id,
            program_id=program_id,
            credential_id=credential_id,
            conferred_on=on,
        )
