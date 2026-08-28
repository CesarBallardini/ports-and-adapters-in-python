# Sequence diagrams

One diagram per significant use case from [`02-actors-and-use-cases.md`](./02-actors-and-use-cases.md).
These are the bridge between *what* the system does and *which object does it*: reading a
diagram top to bottom tells you which collaborator holds which responsibility, and the
[class diagram](./06-class-diagram.md) is the result of assigning those responsibilities. The
[state diagrams](./04-state-diagrams.md) cover the lifecycles these sequences move objects through.

## How to read these

Every diagram uses the same five bands, left to right, and **each arrow that crosses a band
boundary crosses a layer of the hexagon**:

| Band | What lives there | Depends on |
|------|------------------|------------|
| **actor** | a human, the scheduler, the import worker | — |
| **inbound adapter** | FastAPI web router, JSON API router, CLI command | application |
| **application** | the use case object, the `RelationshipResolver` | domain, ports |
| **domain** | entities, value objects, domain services, `AccessPolicy` | nothing |
| **port → adapter** | a `Protocol` in `application/ports/outbound/`, satisfied at runtime by an adapter | — |

Participants named `«port»` are interfaces the application owns. What sits behind them is
decided in `config/` and is *invisible* to every diagram below — which is the whole point:
none of these sequences changes when SQLite becomes PostgreSQL, or CSV becomes XLSX.

---

## 1. The shape of every request

Before the specific cases, the generic path. Every single use case in this system follows it.

```mermaid
sequenceDiagram
    autonumber
    actor Actor
    participant IA as Inbound adapter<br/>web / api / cli
    participant UC as Use case<br/>application
    participant DOM as Domain
    participant P as «port»<br/>outbound
    participant AD as Adapter<br/>sqlalchemy / csv / s3

    Actor->>IA: request in the adapter's own idiom<br/>form post, JSON body, argv
    IA->>IA: translate to a command DTO
    IA->>UC: execute command
    UC->>P: load what the rules need
    P->>AD: dynamic dispatch
    AD-->>P: domain objects
    P-->>UC: domain objects
    UC->>DOM: apply the rules
    DOM-->>UC: outcome or DomainError
    UC->>P: persist, commit
    UC-->>IA: result DTO
    IA-->>Actor: HTML fragment, JSON, or exit code
```

Two invariants hold in every diagram that follows:

- **the use case never talks to an adapter**, only to a port;
- **the domain never appears to the right of a port** — it neither loads nor saves itself.

---

## 2. UC-44 / UC-45 — Authenticating and authorizing

Drawn once, in full. Later diagrams collapse it to a single `authorize` arrow.

```mermaid
sequenceDiagram
    autonumber
    actor Teacher
    participant W as Web router
    participant AI as «ActorIdentity»
    participant UC as Use case
    participant RR as RelationshipResolver<br/>application
    participant SR as «SectionRepository»
    participant GR as «GuardianshipRepository»
    participant AP as AccessPolicy<br/>domain, pure

    Teacher->>W: POST /sections/S1/grades<br/>Cookie: session=...
    W->>AI: resolve actor from request
    AI-->>W: PersonId + roles
    W->>UC: RecordGradeCommand
    UC->>RR: relations of actor to owner student
    RR->>SR: does actor teach a section<br/>the student is enrolled in
    SR-->>RR: yes
    RR->>GR: is actor a guardian of the student
    GR-->>RR: no
    RR-->>UC: frozenset Relation.TEACHER_OF_SECTION
    UC->>AP: decide AccessRequest<br/>WRITE on GRADES
    AP-->>UC: AccessDecision.allow
    Note over AP: pure function of its inputs.<br/>Reads no repository, does no I/O.
```

The split is the design point. **Resolving** which relationships hold is I/O, so it lives in
the application (`RelationshipResolver`). **Deciding** what those relationships grant is a
rule, so it lives in the domain (`AccessPolicy`) and stays a pure function — which is what
makes the whole grant matrix unit-testable with no database.

---

## 3. UC-22 — Record a grade

The only write path to a grade in the system.

```mermaid
sequenceDiagram
    autonumber
    actor Teacher
    participant W as Web router<br/>htmx
    participant UC as RecordGrade
    participant AUTH as authorize<br/>see §2
    participant UOW as «UnitOfWork»
    participant SR as «SectionRepository»
    participant HR as «AcademicHistoryRepository»
    participant GS as GradingService<br/>domain
    participant CS as CourseSection
    participant AH as AcademicHistory

    Teacher->>W: POST /sections/S1/grades<br/>student=P9 grade=8
    W->>UC: RecordGradeCommand
    UC->>AUTH: WRITE on GRADES owned by P9
    AUTH-->>UC: allowed
    UC->>UOW: begin
    UC->>SR: get S1
    SR-->>UC: CourseSection
    UC->>HR: get history of P9
    HR-->>UC: AcademicHistory
    UC->>GS: record_grade section, teacher, P9, Grade 8, history
    GS->>CS: teacher_id == teacher.id ?
    GS->>CS: is_enrolled P9 ?
    GS->>AH: record GradeEntry
    AH-->>GS: ok
    GS-->>UC: GradeEntry
    UC->>HR: save history
    UC->>UOW: commit
    UC-->>W: GradeRecordedDto<br/>best grade, passed
    W-->>Teacher: 200 + _grade_row.html fragment
```

Note what the use case does **not** do: it does not check that the teacher teaches the
section, nor that the student is enrolled. Those are invariants spanning two aggregates, so
`GradingService` owns them. The use case only orchestrates — load, delegate, save, commit.

`Grade(11)` never reaches this diagram at all: the value object rejects it in its
`__post_init__`, at the adapter's parsing boundary.

---

## 4. UC-18 — Enroll a student in a course section

```mermaid
sequenceDiagram
    autonumber
    actor Admin
    participant API as API router
    participant UC as EnrollStudentInSection
    participant CK as «Clock»
    participant PR as «PersonRepository»
    participant SR as «SectionRepository»
    participant PL as «PlanRepository»
    participant ES as EnrollmentService<br/>domain
    participant CS as CourseSection

    Admin->>API: POST /api/sections/S1/enrollments<br/>student=P9
    API->>UC: EnrollStudentCommand
    UC->>CK: today
    CK-->>UC: date
    UC->>UC: derive current Term from date
    UC->>PR: get P9
    PR-->>UC: Person
    UC->>SR: get S1
    SR-->>UC: CourseSection
    UC->>PL: plan the student enrolled under
    PL-->>UC: Plan
    UC->>SR: subjects P9 already has a section for
    SR-->>UC: frozenset SubjectId
    UC->>ES: enroll section, student, plan, term, enrolled_subject_ids
    ES->>ES: has_role STUDENT
    ES->>ES: plan.has_subject
    ES->>ES: section.term == current_term
    ES->>ES: subject not already taken
    ES->>CS: enroll P9
    CS-->>ES: ok
    ES-->>UC: ok
    UC->>SR: save section
    UC-->>API: EnrollmentDto
    API-->>Admin: 201 JSON
```

The `Clock` is a port here for a reason: "the current term" is derived from today's date, so
without it this use case could only be tested by waiting for the calendar. With it, a test
pins `today` and asserts the `WrongTermError` deterministically.

---

## 5. UC-08 — Activate a plan

```mermaid
sequenceDiagram
    autonumber
    actor Admin
    participant W as Web router
    participant UC as ActivatePlan
    participant UOW as «UnitOfWork»
    participant PR as «ProgramRepository»
    participant DP as DegreeProgram
    participant OLD as Plan previously active
    participant NEW as Plan being activated

    Admin->>W: POST /programs/G1/plans/PL2/activate
    W->>UC: ActivatePlanCommand
    UC->>UOW: begin
    UC->>PR: get G1 with its plans
    PR-->>UC: DegreeProgram
    UC->>DP: activate_plan PL2
    DP->>NEW: subject_ids not empty ?
    DP->>OLD: deactivate
    DP->>NEW: activate
    DP-->>UC: ok
    UC->>PR: save program
    UC->>UOW: commit
    UC-->>W: PlanDto active=true
    W-->>Admin: 200 + _plan_row.html fragment
```

`DegreeProgram` — not the use case — flips both plans, because "exactly one active plan per
program" is an invariant of that aggregate. Putting the two calls in the use case would let
any future caller forget one and break the invariant.

Students already enrolled are untouched: grandfathering is achieved by *not* writing to
enrollments here at all.

---

## 6. UC-16 — Delete a course section

The one deletion that is allowed to have dependents, because it preserves them first.

```mermaid
sequenceDiagram
    autonumber
    actor Admin
    participant W as Web router
    participant UC as DeleteCourseSection
    participant UOW as «UnitOfWork»
    participant SR as «SectionRepository»
    participant HR as «AcademicHistoryRepository»
    participant AH as AcademicHistory

    Admin->>W: POST /sections/S1/delete
    W->>UC: DeleteSectionCommand
    UC->>UOW: begin
    UC->>SR: get S1
    SR-->>UC: CourseSection
    UC->>SR: students of S1
    SR-->>UC: frozenset PersonId
    loop for each enrolled student
        UC->>HR: get history
        HR-->>UC: AcademicHistory
        UC->>AH: detach_section S1
        UC->>HR: save history
    end
    UC->>SR: delete S1
    UC->>UOW: commit
    UC-->>W: SectionDeletedDto
    W-->>Admin: 200 + empty fragment, row removed
```

The grades are not moved anywhere — they already live in `AcademicHistory`. All that happens
is `detach_section`, which nulls the originating-section reference so the transcript outlives
the section. Modelling it that way is why this use case is a loop and a delete rather than a
migration.

The single `commit` at the end matters: if the loop fails halfway, no history is left
half-detached and no section is deleted.

---

## 7. UC-40 — Import a grade sheet, inline path

The flagship. Note how little of it is about files.

```mermaid
sequenceDiagram
    autonumber
    actor Teacher
    participant W as Web router
    participant UC as ImportGradeSheet
    participant SPR as «SpreadsheetReader»
    participant AD as CsvReader or XlsxReader
    participant UOW as «UnitOfWork»
    participant SR as «SectionRepository»
    participant HR as «AcademicHistoryRepository»
    participant GS as GradingService<br/>domain

    Teacher->>W: POST /sections/S1/grades/import<br/>multipart file
    W->>W: reject if larger than the cap
    W->>UC: ImportGradeSheetCommand bytes, dry_run
    UC->>SPR: read_rows bytes
    SPR->>AD: parse
    AD-->>SPR: list of dict str to str
    Note over AD: any parse failure is normalised<br/>to one ValueError here.<br/>openpyxl types never escape.
    SPR-->>UC: rows
    UC->>UC: normalise headers, case and space insensitive
    UC->>UOW: begin
    UC->>SR: get S1
    SR-->>UC: CourseSection
    loop for each row
        alt row is valid
            UC->>HR: get history of student
            UC->>GS: record_grade ...
            GS-->>UC: GradeEntry
            UC->>HR: save history
        else student unknown, not enrolled,<br/>grade out of range, or duplicate row
            UC->>UC: append RowError line, reason
        end
    end
    alt dry_run
        UC->>UOW: rollback
    else
        UC->>UOW: commit
    end
    UC-->>W: ImportResultDto<br/>recorded, skipped, errors
    W-->>Teacher: 200 + _import_result.html fragment
```

Everything that makes this use case worth writing — header normalisation, per-row validation,
partial success, duplicate detection, dry-run — happens **above** the `SpreadsheetReader`
port. The adapter's entire job is `bytes → list[dict[str, str]]`.

That is what lets `tests/acceptance/features/grade_import.feature` run the identical
scenarios against the CSV adapter and the XLSX adapter and assert identical outcomes. If a
rule had leaked into the adapter, the two runs would diverge and the suite would say so.

---

## 8. UC-41 / UC-42 — Import, queued path

Same use case, different place to run it.

```mermaid
sequenceDiagram
    autonumber
    actor Teacher
    participant W as Web router
    participant SUB as SubmitImportJob
    participant FS as «FileStorage»
    participant JR as «ImportJobRepository»
    participant Q as «JobQueue»
    participant WK as Import worker<br/>inbound adapter
    participant RUN as RunImportJob
    participant UC as ImportGradeSheet<br/>see §7

    Teacher->>W: POST /sections/S1/grades/import
    W->>SUB: SubmitImportJobCommand bytes
    alt size below threshold
        SUB->>UC: run inline
        UC-->>SUB: ImportResultDto
        SUB-->>W: result
        W-->>Teacher: 200 + _import_result.html
    else size at or above threshold
        SUB->>FS: put bytes
        FS-->>SUB: storage key
        SUB->>JR: add ImportJob PENDING
        SUB->>Q: enqueue job id
        SUB-->>W: job id
        W-->>Teacher: 202 + _import_job.html<br/>hx-get every 2s
    end

    Q->>WK: deliver job id
    WK->>RUN: RunImportJobCommand
    RUN->>JR: mark RUNNING
    RUN->>FS: get bytes by key
    FS-->>RUN: bytes
    RUN->>UC: same use case, same rules
    UC-->>RUN: ImportResultDto
    RUN->>JR: mark DONE with the result

    loop until terminal state
        Teacher->>W: GET /imports/J7 (htmx poll)
        W->>JR: get J7
        JR-->>W: status + result
        W-->>Teacher: _import_job.html<br/>final swap stops polling
    end
```

The branch is about *where* the work runs, never about *what* it does — both arms call the
same `ImportGradeSheet` object. Choosing `JobQueue`'s inline adapter in tests collapses the
whole right-hand side, so the queued path is testable without a broker.

---

## 9. UC-32 — Confer graduation

```mermaid
sequenceDiagram
    autonumber
    actor Admin
    participant W as Web router
    participant UC as ConferGraduation
    participant CK as «Clock»
    participant ID as «IdGenerator»
    participant HR as «AcademicHistoryRepository»
    participant PL as «PlanRepository»
    participant GR as «GraduationRepository»
    participant GS as GraduationService<br/>domain

    Admin->>W: POST /graduations student=P9
    W->>UC: ConferGraduationCommand
    UC->>HR: history of P9
    HR-->>UC: AcademicHistory
    UC->>PL: plan P9 enrolled under
    PL-->>UC: Plan
    UC->>CK: today
    CK-->>UC: date
    UC->>ID: next GraduationId
    ID-->>UC: GraduationId
    UC->>GS: confer id, student, program, credential, history, plan, on
    GS->>GS: is_eligible - passed every subject in plan
    alt not eligible
        GS-->>UC: NotEligibleForGraduationError
        UC-->>W: 409
    else eligible
        GS-->>UC: Graduation
        UC->>GR: add graduation
        UC-->>W: GraduationDto
        W-->>Admin: 201 + _graduation_row.html
    end
```

Both the date and the identifier arrive through ports. That is what makes a graduation
record assertable in a test: `FixedClock(date(2026, 3, 1))` and a deterministic id generator
turn an otherwise unrepeatable event into an exact expected value.

---

## 10. UC-35 — Scheduled reconciliation

The third driving adapter. Same use cases, no human.

```mermaid
sequenceDiagram
    autonumber
    participant SCH as Scheduler<br/>cron or beat
    participant CLI as Job entry point<br/>inbound adapter
    participant UC as ReconcileGraduations
    participant GR as «GraduationRepository»
    participant HR as «AcademicHistoryRepository»
    participant PL as «PlanRepository»
    participant GS as GraduationService<br/>domain

    SCH->>CLI: python -m academy reconcile-graduations
    CLI->>UC: ReconcileGraduationsCommand
    UC->>GR: all active graduations
    GR-->>UC: list of Graduation
    loop for each graduation
        UC->>HR: history of its student
        UC->>PL: plan of its student
        UC->>GS: is_eligible history, plan
        alt still eligible
            GS-->>UC: true
        else no longer eligible
            GS-->>UC: false
            UC->>UC: append Drift record
        end
    end
    UC-->>CLI: ReconciliationReportDto
    CLI-->>SCH: exit 0, or 1 if drift found
```

Nothing here is new. The scheduler is simply a third way in, next to the browser and the JSON
API, and it reaches the identical objects — which is the practical payoff of having drawn the
boundary in §1.

---

## 11. What these diagrams decided

Reading them together, the responsibilities fall out, and they are the input to the
[class diagram](./06-class-diagram.md):

| Responsibility | Assigned to | Why (GRASP) |
|---|---|---|
| Enforce a rule inside one aggregate | the entity — `CourseSection.enroll`, `Grade.__post_init__` | *Information Expert*: it holds the data the rule reads |
| Enforce a rule spanning aggregates | a domain service — `EnrollmentService`, `GradingService` | no single entity owns the whole rule |
| Keep "one active plan per program" | `DegreeProgram` | *Information Expert* over its plans |
| Decide what a relationship grants | `AccessPolicy` | pure rule, so it belongs in the domain |
| Discover which relationships hold | `RelationshipResolver` | needs I/O, so it cannot be in the domain |
| Load, delegate, save, commit | the use case | *Controller* for a system operation |
| Translate HTTP or argv into a command | the inbound adapter | *Pure Fabrication*, keeps protocol out of the core |
| Turn bytes into rows, rows into bytes | the outbound adapter | *Indirection* behind the spreadsheet ports |
| Choose which adapter is used | `config/` composition root | the only place allowed to know both sides |
