# ADR-0017 — Value-object collections are stored as serialised columns

- **Status** Accepted
- **Date** 2026-08-31
- **Extends** [ADR-0006](./0006-sqlalchemy-imperative-mapping.md), which stands: imperative
  mapping for the aggregate roots, Alembic for the schema. This decides the one case ADR-0006
  did not know about.

## Context

ADR-0006 chose imperative mapping and anticipated one cost: "collections that the domain exposes
as read-only tuples need explicit mapping to their private backing attributes, which is fiddly".

Building the adapter showed that for this domain those collections cannot be mapped at all.
Every value object is declared `@dataclass(frozen=True, slots=True)` — `Enrollment`,
`GradeEntry`, `Term`, `Grade`, `Email`, `PersonalData`, and every identifier — and a class with
`__slots__` and no `__weakref__` slot cannot be instrumented by SQLAlchemy:

```
UnmappedInstanceError: Class 'Enrollment' is mapped, but this instance lacks instrumentation.
TypeError: cannot create weak reference to 'Enrollment' object
```

The first appears when an instance the domain built is handed to a session; the second when the
ORM tries to load one. Adding `__weakref__`, or dropping `slots=True`, would mean editing the
copied domain for the convenience of the database — the exact move ADR-0002 exists to forbid.

What is and is not possible was measured, not assumed:

| Construct | `map_imperatively` |
|---|---|
| Aggregate roots — `Person`, `CourseSection`, `AcademicHistory`, `Guardianship` | **works**: ordinary classes, no slots |
| Value objects as **composites** — `PersonalData` over two columns | **works**: a composite is built from columns and never instrumented |
| Value objects in **collections** — `_enrollments`, `_entries`, `_roles`, `_held_credentials` | **impossible**: needs instrumentation, and slots forbid it |

So imperative mapping covers the roots, their scalar columns and their composite value objects,
and cannot cover the four collections.

## Decision

Keep imperative mapping for everything it can express, and store the four collections as
**serialised columns on the aggregate's own row**, through SQLAlchemy `TypeDecorator`s that
convert between JSON and the domain's value objects.

One mechanism, therefore, and one place per collection where the conversion lives. A
`GradeEntry` list becomes a JSON array of objects and comes back as `GradeEntry` instances with
their `Term` and `Grade` reconstructed — so the value objects' own validation runs on every
load, and a row a migration corrupted fails loudly at the repository rather than quietly at the
point of use.

The collections chosen are exactly the ones the domain treats as *inside* an aggregate:
enrollments belong to a section, entries belong to a transcript, roles and held credentials
belong to a person. None is addressable on its own, and no port exposes one as an entity — which
is what makes a column an honest home for it rather than a shortcut.

## Consequences

- **The domain stays exactly as copied.** The verbatim diff still prints nothing, and it does so
  without the database having asked for a single character.
- **Queries that look inside a collection filter in Python.** `for_student`,
  `subjects_enrolled_by`, `teaching_students_of` and `holders_of` load the candidate rows and
  filter them in the repository. They are correct, they are identical on SQLite and PostgreSQL,
  and they read every row of their table. The port docstrings say `subjects_enrolled_by` exists
  so that a student with a long history does not have every section loaded; with this storage it
  loads them and discards most. **That is a real cost and this is where it is recorded.**
- The alternative to that cost is dialect-specific JSON SQL, which would make the two databases
  behave differently in exactly the layer that must not (ADR-0007). If these queries ever become
  slow, the answer is a real child table plus a hand-written mapping for that one collection —
  not JSON operators.
- Writing a collection rewrites it whole. There is no per-element change tracking, so a save is
  a single column update; concurrent edits to one aggregate are last-writer-wins, which the
  `UnitOfWork` port never promised otherwise.

## Alternatives considered

- **Hand-written mapping throughout**, with tables read and written by the repositories and no
  ORM instrumentation at all. Removes the constraint entirely and makes every mapping visible,
  at the cost of abandoning ADR-0006's central choice and writing a `_to_row`/`_to_domain` pair
  per aggregate. Rejected in favour of keeping one documented mapping strategy for the whole
  adapter.
- **Hybrid: roots through the ORM, collections through child tables written by hand.** Keeps the
  queries in SQL, at the cost of two mapping mechanisms inside one repository and a diff of each
  collection on every save. The subtlest of the three to get right, for a gain that only matters
  at a size this system is not.
- **Edit the domain to drop `slots=True`.** One word, and imperative mapping would work
  everywhere. Rejected outright: it is the database reaching into the domain to make itself
  easier, and it would break the diff that proves the domain was not touched.
