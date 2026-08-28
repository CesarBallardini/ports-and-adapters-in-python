"""The student-to-plan association, which the domain deliberately does not model.

`EnrollmentService.enroll` takes the student's `Plan` as a *parameter*. The domain therefore
never says which plan a given student is on -- it only says what follows once you know. That
is not an omission to be fixed: it is what keeps the enrollment rule a pure function, and
under ADR-0002 the domain is not ours to change in any case.

So the association is owned here, in the application layer, as a record of an administrative
act rather than an academic rule. It is stored, and it stores the **plan** as it was at the
moment of enrollment -- which is precisely how grandfathering (``docs/04-state-diagrams.md``
§3) is implemented. Activating a newer plan writes nothing here, so the existing cohort keeps
completing the plan they enrolled under, without a migration and without a cohort flag.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from academy.domain.shared.ids import PersonId, PlanId, ProgramId


@dataclass(frozen=True, slots=True)
class PlanEnrollment:
    """A student's enrollment in one degree plan.

    A student is enrolled in exactly one plan (spec §3), so this is a one-to-one record
    keyed by student.

    Attributes:
        student_id: The enrolled student.
        program_id: The degree program, kept alongside the plan so that reading a student's
            enrollment does not require searching every program for the plan that owns it.
        plan_id: The plan **as of the enrollment date**. Never rewritten when the program
            activates a different plan.
        enrolled_on: When the enrollment was recorded.
    """

    student_id: PersonId
    program_id: ProgramId
    plan_id: PlanId
    enrolled_on: date
