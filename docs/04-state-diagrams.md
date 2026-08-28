# State diagrams

The lifecycles implied by the [use cases](./02-actors-and-use-cases.md) and the
[sequence diagrams](./03-sequence-diagrams.md). A sequence diagram shows one run through the
system; a state diagram shows every run an object can have over its whole life.

They matter here for one specific reason: **a state machine tells you what must be stored and
what can be computed.** Get that wrong and you either persist something that can go stale, or
recompute something that needed to be a matter of record. Two of the diagrams below are the
result of deliberately choosing opposite answers.

## 1. Stored versus computed state — the summary

| Concept | Stored or computed | Why |
|---------|--------------------|-----|
| Import job status | **stored** | a second process advances it; it must survive a restart |
| Plan activation | **stored** | an administrative act with an intent that outlives the data |
| Graduation | **stored** | a dated, auditable conferral that supports revocation |
| Course section existence | **stored** | it owns enrollments |
| Subject pass/fail | **computed** | derived from the best grade; cannot go stale |
| Guardianship | **computed** | derived from age against the global age of majority |

The two computed rows are the interesting ones, and §5 and §6 explain what that costs and
buys.

## 2. ImportJob — the only state machine the application owns

```mermaid
stateDiagram-v2
    direction LR
    [*] --> PENDING : submit, file at or above threshold
    PENDING --> RUNNING : worker picks it up
    RUNNING --> DONE : every row processed
    RUNNING --> FAILED : file unreadable, or storage lost
    DONE --> [*]
    FAILED --> [*]

    note right of PENDING
        Bytes already in FileStorage.
        The queue carries only the job id,
        never the payload.
    end note
    note right of DONE
        DONE means the run finished,
        not that every row succeeded.
        Rejected rows live in the result.
    end note
```

Two subtleties worth stating, because both are easy to get wrong:

**`DONE` is not `ok`.** A run that rejected 30 of 100 rows is `DONE` — it completed. Whether
the outcome was acceptable is `ImportResultDto.ok()`, a separate question. Collapsing the two
would make a partially-rejected import indistinguishable from an unreadable file.

**The inline path has no states at all.** Below the size threshold the import runs
synchronously and never becomes an `ImportJob`. That is why the diagram starts with a
guarded transition rather than at submission: the state machine exists only to coordinate two
processes, so when there is only one process, there is nothing to coordinate.

```mermaid
stateDiagram-v2
    direction LR
    state size_check <<choice>>
    [*] --> size_check : upload
    size_check --> Inline : below threshold
    size_check --> PENDING : at or above threshold
    Inline --> [*] : result returned in the response
    PENDING --> [*] : result polled later
```

## 3. Plan — activation within a degree program

```mermaid
stateDiagram-v2
    direction LR
    [*] --> Draft : create plan
    Draft --> Draft : add subject
    Draft --> Active : activate, only if it has subjects
    Active --> Superseded : another plan of the program is activated
    Superseded --> Active : reactivated
    Superseded --> [*] : no cohort left, may be deleted

    note right of Superseded
        Still valid and still referenced.
        Its cohort keeps completing it.
    end note
```

The state that carries the design decision is **`Superseded`, not `Deleted`**. Replacing a
plan grandfathers the students already enrolled under it, so the old plan has to remain
readable and enforceable for years after it stops being offered.

`Draft --> Active` is guarded on the plan having at least one subject. An empty plan is
vacuously completed, which would make every enrolled student an instant graduation candidate
— the guard is why `GraduationService.is_eligible` can raise `EmptyPlanError` and know it is
a genuine data fault rather than a normal case.

## 4. Graduation — a stored, revocable conferral

```mermaid
stateDiagram-v2
    direction LR
    [*] --> Eligible : passed every subject in the plan
    Eligible --> Conferred : administrator confers, dated
    Conferred --> Revoked : revoke
    Revoked --> Conferred : reissue
    Conferred --> Conferred : reconciliation confirms
    Conferred --> Drifted : reconciliation finds no longer eligible

    note right of Eligible
        Computed, not stored.
        It is a query over the transcript.
    end note
    note right of Drifted
        Reported, never auto-corrected.
        Revoking a conferred degree
        is a human decision.
    end note
```

This diagram is where "stored versus computed" gets interesting, because the answer is
**both**. `Eligible` is computed on demand from the transcript. `Conferred` is stored, because
a graduation needs a date, an issued credential, and an audit trail — none of which a
computation can invent.

Storing it creates the possibility of drift: the record says conferred, the transcript no
longer agrees. UC-35 reconciles the two on a schedule and **reports**, never corrects.
Auto-revoking someone's degree because a grade was edited is not a decision software should
take by itself.

## 5. Subject standing — computed, never stored

```mermaid
stateDiagram-v2
    direction LR
    [*] --> NotTaken
    NotTaken --> Attempted : first grade recorded
    Attempted --> Attempted : retake, another attempt recorded
    Attempted --> Passed : best grade at or above 6

    note right of Passed
        Not a stored flag.
        AcademicHistory.has_passed reads
        max of the attempts, every time.
    end note
```

There is no `Failed` state, and that is deliberate: a student who has not passed is simply
still `Attempted`, and a further retake can change the answer. Modelling failure as a state
would require somebody to decide when it becomes permanent, which the domain never does.

Because standing is recomputed from the attempts, **it cannot go stale.** Recording a retake
requires no cascade — no flag to flip on the enrollment, no counter to bump on the plan, no
"recalculate standing" job. `record()` appends, and every reader derives.

The cost is real and worth naming: `has_passed` scans the transcript on every call. At
academy's scale that is nothing. At a scale where it mattered, the fix is a cached projection
in the *adapter* — the domain rule would not change.

## 6. Guardianship — a state that time changes without anyone acting

The sharpest case in the model, and the reason `Clock` is a port.

```mermaid
stateDiagram-v2
    direction LR
    [*] --> UnderGuardianship : student under the age of majority<br/>with an assigned guardian
    UnderGuardianship --> SelfDetermined : the clock passes the student's birthday
    SelfDetermined --> [*]

    note right of SelfDetermined
        No use case caused this.
        No row was written.
        The same query simply
        answers differently today.
    end note
```

Every other transition in this document is caused by an actor invoking a use case. This one is
caused by **nothing happening**. The student comes of age, and guardian access stops
resolving — with no scheduled job, no birthday trigger, and no stored transition to keep in
sync.

That is only implementable because the rule is evaluated on read, against a `today` supplied
from outside:

- the domain takes `today: date` as an argument and stays deterministic;
- the application supplies it from the `Clock` port;
- a test injects `FixedClock(date(2026, 3, 2))` and asserts that guardian access which worked
  the day before is now denied.

Had the domain called `date.today()` itself, this transition would be untestable except by
changing the system clock. That single consideration is the clearest justification in the
whole codebase for treating time as a port rather than a built-in.

## 7. Actor session

```mermaid
stateDiagram-v2
    direction LR
    [*] --> Anonymous
    Anonymous --> Authenticated : valid credentials
    Authenticated --> Anonymous : logout, or session expiry
    Authenticated --> Authenticated : each request re-resolves relations

    note right of Authenticated
        Identity is cached in the session.
        Relations are not: they are
        resolved per request, because
        the data behind them changes.
    end note
```

The self-transition is the point. **Who you are** is established once per session; **what you
may touch** is recomputed on every request. Caching relations in the session would mean a
teacher removed from a section at 10:00 could still write grades until their cookie expired.

## 8. What these diagrams contribute to the design

| Diagram | Consequence for the code |
|---------|--------------------------|
| ImportJob | `ImportJobRepository` and `JobQueue` ports exist; the payload goes to `FileStorage`, not through the queue |
| Plan | `DegreeProgram` owns activation, so the "exactly one active" invariant has a single enforcement point |
| Graduation | reconciliation is a use case with a **report**, not a corrective write |
| Subject standing | no denormalised pass flag anywhere, so no cache-invalidation logic anywhere |
| Guardianship | `Clock` is an outbound port, and the domain accepts `today` as a parameter |
| Session | authentication is adapter-level; authorization is per request, in `AccessGuard` |

Next: [the class diagram](./06-class-diagram.md), where these responsibilities become classes.
