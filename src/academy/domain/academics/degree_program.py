"""Degree program aggregate root."""

from __future__ import annotations

from academy.domain.academics.plan import Plan
from academy.domain.shared.entity import Entity
from academy.domain.shared.errors import DomainError
from academy.domain.shared.ids import PlanId, ProgramId


class PlanNotFoundError(DomainError):
    """Raised when a plan id does not belong to the program."""


class DuplicatePlanError(DomainError):
    """Raised when adding a plan whose id is already in the program."""


class DegreeProgram(Entity[ProgramId]):
    """A degree program that offers study plans, at most one active at a time."""

    def __init__(self, id: ProgramId, name: str, plans: list[Plan] | None = None) -> None:
        """Initialize a degree program.

        Args:
            id: The program's identifier.
            name: The program's name.
            plans: Initial plans (their activation is normalized to keep the invariant).
        """
        self.id = id
        self.name = name
        self._plans: list[Plan] = []
        for plan in plans or []:
            self.add_plan(plan)

    def add_plan(self, plan: Plan) -> None:
        """Add a plan to the program.

        If the added plan is active, every other plan is deactivated so that at most one
        plan is active at a time.

        Raises:
            DuplicatePlanError: If a plan with the same id is already present.
        """
        if any(existing.id == plan.id for existing in self._plans):
            raise DuplicatePlanError(str(plan.id))
        if plan.active:
            for existing in self._plans:
                existing.deactivate()
        self._plans.append(plan)

    def activate_plan(self, plan_id: PlanId) -> None:
        """Activate the plan ``plan_id`` and deactivate every other plan."""
        target = self.plan(plan_id)
        for plan in self._plans:
            plan.deactivate()
        target.activate()

    def active_plan(self) -> Plan | None:
        """Return the currently active plan, or ``None`` if none is active."""
        return next((plan for plan in self._plans if plan.active), None)

    def plan(self, plan_id: PlanId) -> Plan:
        """Return the plan with id ``plan_id``.

        Raises:
            PlanNotFoundError: If no plan with that id belongs to the program.
        """
        for plan in self._plans:
            if plan.id == plan_id:
                return plan
        raise PlanNotFoundError(str(plan_id))

    @property
    def plans(self) -> tuple[Plan, ...]:
        """The program's plans (read-only view)."""
        return tuple(self._plans)
