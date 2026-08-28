# Actors and use cases

The functional specification of **academy**, derived from [`01-description.md`](./01-description.md)
and expressed as actors and use cases. This document is written *before* the application
layer, because in ports and adapters the use cases **are** the driving ports: every entry in
the catalogue below becomes an operation on an inbound port, and every "needs" column becomes
an outbound port.

> **Why this document exists.** A hexagon is only as good as the boundary you draw, and the
> boundary is drawn by asking *who* uses the system and *what for* — not by asking what tables
> it has. Everything in `src/academy/application/` is traceable to a row here.

## 1. Actors

An actor is a **role**, not a person. A single `Person` record carries every role its human
holds, so one human can appear below as three different actors at once — a mother who teaches
and is herself studying is a Guardian, a Teacher and a Student. This is precisely why
authorization here is relationship-based rather than role-based: knowing *which* actor is
acting is not enough, the system must know *which relationship* connects that actor to the
record being touched.

### 1.1 Primary actors (human, initiate use cases)

| # | Actor | Goal | Notes |
|---|-------|------|-------|
| A-1 | **Administrative employee** | Keep the academic structure and people records correct | The only actor that manages structure. Explicitly **cannot** modify grades. |
| A-2 | **Teacher** | Teach sections and record their students' grades | Scoped to sections they teach; must hold a credential for the subject. |
| A-3 | **Student** | See their own academic standing | Read-only, self scope. |
| A-4 | **Guardian** | Watch over the academic standing of their wards | Read-only, and only while the ward is under the age of majority. |

### 1.2 Secondary actors (systems that drive the application)

| # | Actor | Role |
|---|-------|------|
| A-5 | **Scheduler** | Fires periodic reconciliation of stored graduations against computed truth (spec §6). |
| A-6 | **Import worker** | Executes import jobs that were too large to run inline. |

Both are *driving* actors: they enter through an inbound adapter — a CLI entry point, a job
runner — and call exactly the same use cases a human would. That is the point. A use case does
not know whether a human, a cron job or a test invoked it.

## 2. Use case catalogue

`Needs` lists the outbound ports the use case requires. `Auth` is the rule enforced through
the domain's `AccessPolicy`.

### 2.1 People and identity — Administrative employee

| UC | Use case | Auth | Needs |
|----|----------|------|-------|
| UC-01 | Register a person with initial roles | admin | `PersonRepository`, `IdGenerator`, `Clock`, `UnitOfWork` |
| UC-02 | Update a person's personal data | admin | `PersonRepository`, `UnitOfWork` |
| UC-03 | Grant or revoke a role | admin | `PersonRepository`, `UnitOfWork` |
| UC-04 | Delete a person | admin | `PersonRepository`, dependency probes, `UnitOfWork` |
| UC-05 | Set the global age of majority | admin | `ConfigurationRepository`, `UnitOfWork` |

UC-04 is refused when dependents exist (spec §9). That refusal is a domain rule, so the check
lives in a domain service — not in the repository, and not in the router.

### 2.2 Academic structure — Administrative employee

| UC | Use case | Auth | Needs |
|----|----------|------|-------|
| UC-06 | Create a degree program | admin | `ProgramRepository`, `IdGenerator`, `UnitOfWork` |
| UC-07 | Create a plan for a program | admin | `PlanRepository`, `IdGenerator`, `UnitOfWork` |
| UC-08 | Activate a plan | admin | `PlanRepository`, `UnitOfWork` |
| UC-09 | Add a subject to a plan | admin | `PlanRepository`, `SubjectRepository`, `IdGenerator`, `UnitOfWork` |
| UC-10 | Create a credential | admin | `CredentialRepository`, `IdGenerator`, `UnitOfWork` |
| UC-11 | Associate a subject with a credential | admin | `SubjectRepository`, `CredentialRepository`, `UnitOfWork` |
| UC-12 | Grant a credential to a teacher | admin | `PersonRepository`, `CredentialRepository`, `UnitOfWork` |
| UC-13 | Open an academic term | admin | `TermRepository`, `UnitOfWork` |
| UC-14 | Create a course section | admin | `SectionRepository`, `SubjectRepository`, `PersonRepository`, `IdGenerator`, `UnitOfWork` |
| UC-15 | Assign or replace a section's teacher | admin | as UC-14 |
| UC-16 | Delete a course section | admin | `SectionRepository`, `GradeRepository`, `AcademicHistoryRepository`, `UnitOfWork` |

UC-08 keeps exactly one plan active per program and **grandfathers the existing cohort**:
students keep the plan they enrolled under. UC-14 and UC-15 **hard-enforce teacher
qualification** — no credential for the subject, no assignment — which transitively gates every
write to a grade. UC-16 is the single spec'd exception to block-if-dependents: it first
transfers the section's grades into each student's academic history.

### 2.3 Enrollment — Administrative employee

| UC | Use case | Auth | Needs |
|----|----------|------|-------|
| UC-17 | Enroll a student in a degree plan | admin | `PersonRepository`, `PlanRepository`, `EnrollmentRepository`, `UnitOfWork` |
| UC-18 | Enroll a student in a course section | admin | `SectionRepository`, `PlanRepository`, `Clock`, `UnitOfWork` |
| UC-19 | Withdraw a student from a course section | admin | `SectionRepository`, `GradeRepository`, `UnitOfWork` |

UC-18 admits the student only when the subject is in their plan, the section runs in the
current term, and they are not already enrolled in another section of the same subject.

### 2.4 Grading — Teacher

| UC | Use case | Auth | Needs |
|----|----------|------|-------|
| UC-20 | List my course sections | teacher-of-section | `SectionRepository` |
| UC-21 | List a section's students with their grades | teacher-of-section / admin | `SectionRepository`, `GradeRepository` |
| UC-22 | Record or update a grade | teacher-of-section (**write**) | `GradeRepository`, `Clock`, `UnitOfWork` |
| UC-23 | View the credentials I hold | self | `PersonRepository`, `CredentialRepository` |
| UC-24 | View the subjects I may teach | self | `PersonRepository`, `SubjectRepository` |

UC-22 is the **only** write path to a grade in the entire system, and the only use case where
`Action.WRITE` is ever granted.

### 2.5 Reading one's own record — Student and Guardian

| UC | Use case | Auth | Needs |
|----|----------|------|-------|
| UC-25 | View my grades | self | `GradeRepository` |
| UC-26 | View my academic history | self | `AcademicHistoryRepository` |
| UC-27 | View my enrollment and active plan | self | `EnrollmentRepository`, `PlanRepository` |
| UC-28 | List the students in my care | guardian-of | `GuardianshipRepository`, `Clock` |
| UC-29 | View a ward's grades | guardian-of | `GradeRepository`, `Clock` |
| UC-30 | View a ward's academic history | guardian-of | `AcademicHistoryRepository`, `Clock` |

UC-28 through UC-30 need the `Clock` because guardianship is **computed on read** from the
ward's current age against the global age of majority. The day a ward comes of age these use
cases stop resolving — no scheduled job, no stored transition.

### 2.6 Graduation

| UC | Use case | Actor | Needs |
|----|----------|-------|-------|
| UC-31 | List graduation candidates | admin | `PlanRepository`, `AcademicHistoryRepository` |
| UC-32 | Confer graduation | admin | `GraduationRepository`, `Clock`, `IdGenerator`, `UnitOfWork` |
| UC-33 | Revoke a graduation | admin | `GraduationRepository`, `Clock`, `UnitOfWork` |
| UC-34 | Reissue a graduation | admin | `GraduationRepository`, `Clock`, `UnitOfWork` |
| UC-35 | Reconcile stored graduations against computed truth | **Scheduler** | `GraduationRepository`, `AcademicHistoryRepository`, `PlanRepository`, `Clock` |

### 2.7 Bulk data loading

The reason this repository has the shape it does. Every use case here is invoked by **three
different driving adapters** — an HTTP upload from the htmx UI, a JSON API call, and a CLI
command — over exactly the same code.

| UC | Use case | Actor | Needs |
|----|----------|-------|-------|
| UC-36 | Download an import template | admin / teacher | `SpreadsheetWriter` |
| UC-37 | Import people in bulk | admin | `SpreadsheetReader`, `PersonRepository`, `IdGenerator`, `UnitOfWork` |
| UC-38 | Import subjects into a plan in bulk | admin | `SpreadsheetReader`, `PlanRepository`, `SubjectRepository`, `UnitOfWork` |
| UC-39 | Import enrollments in bulk | admin | `SpreadsheetReader`, `EnrollmentRepository`, `SectionRepository`, `UnitOfWork` |
| UC-40 | Import a grade sheet for a course section | teacher-of-section | `SpreadsheetReader`, `GradeRepository`, `SectionRepository`, `Clock`, `UnitOfWork` |
| UC-41 | Submit an import job and track its progress | admin / teacher | `ImportJobRepository`, `JobQueue`, `FileStorage`, `Clock` |
| UC-42 | Execute a queued import job | **Import worker** | `ImportJobRepository`, `FileStorage`, the UC-37..40 use cases |
| UC-43 | Export a grade listing, academic history or graduate list | admin | `SpreadsheetWriter` |

### 2.8 Cross-cutting

| UC | Use case | Notes |
|----|----------|-------|
| UC-44 | Authenticate an actor | Session cookie on the web surface, Bearer token on the API; both resolve to one `ActorIdentity`. |
| UC-45 | Resolve an actor's relations to a record | The `RelationshipResolver`: reads repositories, feeds the pure `AccessPolicy`. Not user-facing; a collaborator of nearly every use case above. |

## 3. Fully dressed use cases

The catalogue is enough for most entries. Four are elaborated here because they carry the
rules that shape the ports.

### UC-40 — Import a grade sheet for a course section

**Primary actor** Teacher · **Scope** academy · **Level** user goal

**Stakeholders and interests**

- *Teacher*: enter a term's worth of grades in one action instead of a hundred clicks.
- *Student*: every accepted row is correct, and a partially bad file must not silently drop rows.
- *Administrative employee*: the operation is as auditable as manual entry.

**Preconditions** The actor is authenticated and is the teacher of the section. The section
exists and runs in a term that is open.

**Postconditions** Every valid row is recorded as a grade attempt for its student in this
section. No invalid row is recorded. The result reports each rejected row with its line number
and reason.

**Main success scenario**

1. Teacher requests the grade-sheet template for the section (UC-36); it comes back pre-filled
   with the enrolled students and an empty grade column.
2. Teacher uploads the completed file.
3. System measures the file and chooses inline or queued execution (UC-41).
4. System parses the file through the `SpreadsheetReader` port, obtaining rows of strings.
5. System normalises the header row, case- and space-insensitively.
6. For each row: resolve the student, validate the grade is an integer 0..10, and check the
   student is enrolled in this section.
7. System records one grade attempt per valid row.
8. System commits once and returns counts of recorded, skipped and rejected rows, plus the
   per-row error list.

**Extensions**

- 2a. File is not a readable workbook → rejected with one clear error. *The adapter normalises
  the parsing library's exception zoo into a single failure; the use case never sees an
  `openpyxl` type.*
- 4a. File exceeds the configured size cap → `PayloadTooLargeError`.
- 6a. Student not found, not enrolled, or grade out of range → row rejected, import continues.
- 6b. The same student appears twice in the file → the later row is rejected as a duplicate.
- 8a. Dry-run mode was requested → the transaction is rolled back and only the report returned.

**Special requirements** The rules in steps 5–8 live in the **use case**, not in the adapter.
Swapping CSV for XLSX must not change a single one of them — that is the property the port
exists to guarantee, and `tests/acceptance/features/grade_import.feature` runs the same
scenarios against both adapters to prove it.

### UC-41 — Submit an import job and track its progress

**Primary actor** Administrative employee or Teacher

**Main success scenario**

1. Actor uploads a file.
2. System measures it. **Below the configured threshold** it runs the import inline
   (UC-37..40) and returns the result fragment directly.
3. **At or above the threshold** the system stores the bytes through `FileStorage`, records a
   pending `ImportJob`, hands it to the `JobQueue`, and returns the job id.
4. The htmx UI polls the job fragment until the job reaches a terminal state.
5. On completion the same result report is rendered as in the inline case.

**Special requirements** Steps 2 and 3 differ only in *where* the work runs. Both paths call
the identical use case object, so the report, the validation rules and the tests are shared.

### UC-08 — Activate a plan

**Primary actor** Administrative employee

**Main success scenario**

1. Actor selects a plan of a degree program.
2. System deactivates the program's currently active plan, if any.
3. System marks the selected plan active.

**Postconditions** Exactly one plan is active for the program. Students already enrolled keep
their previous plan — the new plan applies only to enrollments made after activation.

**Extensions**

- 2a. The selected plan is already active → no change, reported as such.
- 3a. The plan has no subjects → refused. An empty plan can never be completed, and would make
  every enrolled student instantly a graduation candidate.

### UC-22 — Record or update a grade

**Primary actor** Teacher

**Preconditions** The actor teaches the section and therefore holds a credential for the
subject — enforced back at UC-14, which is why no credential check is needed here.

**Main success scenario**

1. Teacher selects a student in one of their sections and submits an integer grade 0..10.
2. System records a new grade **attempt**; earlier attempts are retained.
3. System reports the student's best grade for the subject and whether it is now a pass (>= 6).

**Extensions**

- 1a. Grade outside 0..10 → refused by the domain value object.
- 1b. Student is not enrolled in this section → refused.
- 1c. The section has been deleted → its grades already moved to academic history; refused.

## 4. From use cases to ports

The catalogue yields exactly the port set the application layer needs. Nothing else may appear
in `application/ports/outbound/`.

| Port | Sync/async | Why | Adapters |
|------|-----------|-----|----------|
| `*Repository`, per aggregate | **async** | persistence is I/O | in-memory, SQLAlchemy |
| `UnitOfWork` | **async** | wraps a database transaction | in-memory, SQLAlchemy |
| `Clock` | sync | reading a clock is not I/O | system clock, fixed clock |
| `IdGenerator` | sync | generating a UUID is CPU work | uuid4, deterministic sequence |
| `SpreadsheetReader` | sync | parsing bytes already in memory is CPU work | stdlib `csv`, `openpyxl` |
| `SpreadsheetWriter` | sync | same | stdlib `csv`, `openpyxl` |
| `FileStorage` | **async** | moving bytes to disk or S3 is I/O | local filesystem, S3 |
| `JobQueue` | **async** | handing work to another process is I/O | inline, threaded, external |
| `EmailSender` | **async** | SMTP is I/O | SMTP, recording fake |
| `ActorIdentity` | **async** | may consult a repository | cookie session, bearer token |

The sync/async split is a documented rule, not a habit: a port is async when crossing it means
waiting on something outside the process, and sync when it does not. Each port module states
which it is and why, in its docstring.
