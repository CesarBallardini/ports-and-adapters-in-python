# ADR-0016 — A grade sheet is readable only by someone who may read every person on it

- **Status** Accepted
- **Date** 2026-08-30

## Context

Every other authorization question in academy has an obvious owner. A transcript belongs to a
student, a guardianship names a ward, a grade is written about one person — so the guard is
asked about *that* person, and `AccessPolicy` decides what the actor's relations grant over
their records (ADR-0003).

`list_section_grades` (UC-21) has no such owner. A grade sheet is one page naming a teacher and
every student on the roster, and the relations that could authorize it are spread across all of
them. The question "who owns this resource" has no single answer, and the two obvious ways to
force one are both wrong in ways that only show up in a specific shape of data.

This is not hypothetical. The spec is explicit that one human may hold several roles at once —
a mother who is also a teacher and also a student — which is precisely the condition under
which the shortcuts fail.

## Decision

The actor must be granted `READ` on `GRADES` for **every person the sheet names**: the section's
teacher, and each enrolled student. One resolution per person, all of them required.

```python
for owner_id in (section.teacher_id, *sorted(section.students(), key=str)):
    await self._guard.require(actor, Action.READ, ResourceType.GRADES, owner_id)
```

Two consequences follow from the same conjunction, and both are deliberate:

- An **empty section** is readable by anyone the policy would let read an empty set of people —
  in practice its teacher and an administrator, because the loop still runs over the teacher.
- The section is loaded **before** the guard runs, because who may read it is derived from the
  section itself. `NotFoundError` therefore precedes `AuthorizationError` for a section id that
  does not exist. That is the lesser leak: the alternative is an authorization check with
  nothing to check against.

For the two actors UC-21 actually names, the conjunction is satisfied by a single relation each
— `SELF` for the section's own teacher, `ADMINISTRATOR` for a registrar — so the common paths
cost one decision, not one per student.

## Consequences

- A guardian cannot read a grade sheet, ever, even the sheet of a section their ward is the only
  student in. They read their ward's transcript instead (UC-30), which is the record that is
  actually theirs to see.
- A teacher can read only their own sections' sheets, which is the rule the spec states plainly
  and the one a reader expects.
- The cost is one relation resolution per person on the roster, bounded by section size. Each
  resolution is a repository read, so a large section is a linear number of small queries — the
  place to look first if listing a sheet ever gets slow.
- The rule is stated once, in `GradeManagement._require_readable`, and the docstring there is
  the specification the tests assert. Both holes below are covered by tests, so a future
  simplification that reintroduces either one fails immediately.

## Alternatives considered

- **Check only the students.** The natural reading of "a grade sheet is about students". It
  lets the guardian of the only student on a one-student roster read the entire sheet including
  the teacher's identity and, worse, says *nothing at all* about a section with no students —
  an empty sheet would be world-readable.
- **Check only the teacher.** Equally natural: "a sheet belongs to whoever teaches it". It lets
  a teacher read any section taught by someone who happens to be enrolled as a student in one of
  *their* sections, because the resolver would find `TEACHER_OF_SECTION` between them. The spec's
  insistence that one human may be a teacher and a student at once turns this from a curiosity
  into a real leak.
- **Introduce a `Section` resource type with its own grant matrix.** Honest, and it would make
  the question answerable in the policy rather than in the use case. Rejected for now because it
  adds a second axis to a matrix a regulator may be asked to read (ADR-0003), to express a rule
  that the existing axis already expresses correctly — and because the conjunction, unlike a new
  resource type, cannot drift out of step with the per-person rules it is built from.
