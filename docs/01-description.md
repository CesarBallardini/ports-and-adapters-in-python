# Problem description

An academic records system for students, teachers, guardians, and administrative
staff. This document is the consolidated specification: it merges the original problem
statement with every design decision resolved during requirements analysis.
"Manage" means full CRUD (create / read / update / delete).

## 1. Tenancy and authorization model

- **Each person is a tenant.** A person's own academic records (their grades and
  academic history) live in their own tenant.
- **Each person-tenant holds one or more roles**, possibly several at once:
  administrative employee, teacher, student, and guardian. For example, one person can
  be a mother (guardian), a teacher, and a student simultaneously.
- Access to *other* people's records is granted through **relationships** rather than
  through a role held inside a shared tenant (a relationship-based / ReBAC model). The
  relationships are: *self*, *teacher-of-a-course-section*, *guardian-of-a-student*, and
  *administrator*.
- **Access control is record-level, not just resource-type-level.** A grant applies to a
  *subset of records* resolved by relationship: a student reads only their own grades; a
  teacher reads/writes only the grades of students in a course section they teach; a
  guardian reads only their wards' grades.
- The controlled resources are **grades** and **academic history**. Each defines exactly
  two actions: **read** and **write** (write covers creating and updating a grade).

## 2. People and identity

- A **Person** is identified by a surrogate **UUID** primary key and a **unique email**
  (used for login). There is no national-ID dependency. A single Person record holds all
  of that person's roles.
- Stored student data includes personal data and the grades obtained in subjects.
- Every teacher and every guardian must be of legal age (see age of majority below).

## 3. Academic structure

- A **degree program** offers **study plans**. A plan is composed of **subjects**.
  A plan is a **flat set of subjects** (no prerequisites / correlatives): a student may
  take any subject of their plan in any term.
- **For a given degree program, exactly one plan is active at a time.** Activating a plan
  deactivates the previously active one.
- A **subject** has a name and belongs to a plan of a degree program.
- **Academic terms:** there are **two four-month terms per year** (with a non-teaching
  summer break). A term is identified as **(year, number)** with number 1 or 2, rendered
  like `2026-T1`.
- To teach a subject, one or more **course sections** are formed. A course section has
  enrolled students and one teacher, and runs during a single term.

### Enrollment

- A student is enrolled in **one and only one** degree plan.
- A student may be enrolled in one or more subjects, or in none.
- A student may enroll in a course section when **all** of the following hold:
  - the subject is in the student's plan,
  - the section is offered in the current term, and
  - the student is not already enrolled in a section of that subject.
- There are no capacity limits.

## 4. Credentials and teacher qualification

- A **credential** qualifies a teacher to teach certain subjects. An administrative
  employee associates a subject with a credential.
- **Teacher qualification is hard-enforced:** a teacher cannot be assigned to a course
  section for a subject unless they hold a credential associated with that subject.
  Because no-teach implies no-grade, this transitively gates the teacher's write access to
  grades.
- Adding a credential to a teacher may make new subjects available for them to teach from
  then on.

## 5. Grades and academic history

- **Grade scale:** integer **0 to 10**. A subject is **passed** when the grade is **>= 6**.
- **Grade history:** all attempts are kept (across retakes and course sections). The
  **best (highest) grade** for a subject determines pass/fail and counts toward graduation.
- A student's **academic history** is the complete transcript of every subject grade over
  time -- `(subject, best_grade, term, passed)` -- independent of course-section lifecycle.
- When a course section is deleted, its grades are **transferred to each student's academic
  history** (the grade detaches from the section but remains in history).

## 6. Graduation

- The degree program grants a credential to a student who has passed **every** subject in
  their plan.
- **Graduation is a stored conferral event:** an explicit act that produces a dated
  graduation record and issues the credential to the student. It supports revocation and
  reissue. The administrative employee (who pulls the graduate list) performs the
  conferral. The stored records are periodically reconciled against the computed truth
  ("passed every subject in the plan") so they do not drift.

## 7. Guardianship and age of majority

- The **age of majority** is a **single global value** for the whole system, configured by
  an administrative employee.
- **Underage students have an assigned guardian; a student of legal age has none.**
  Guardianship is **computed on read**: it is derived from the person's current age against
  the global age of majority. There is no stored transition and no scheduled job; once a
  student reaches the age of majority, no guardian applies and guardian access no longer
  resolves.
- A guardian may be any designated person (not necessarily a relative) and must be of
  legal age. A student's parent fulfills the guardian role for that student.
- A student of legal age exercises, over themselves, all the powers a guardian would have.

## 8. Roles and permissions

### 8.1 Grade and academic-history access

Access to the **grades** and **academic history** resources, per role, scoped by
relationship (record-level):

| Role | Read | Write | Scope |
|------|------|-------|-------|
| Student | yes | no | only their own grades and their own academic history |
| Teacher | yes | yes | only students enrolled in a course section the teacher teaches, and only for that subject |
| Guardian | yes | no | grades and academic history of the students in their care (their wards) |
| Administrative employee | yes | no | may read grade listings (per course section) and academic histories, and produce graduation lists; **cannot modify grades** |

Notes:
- A student reads *all* of their own grade records (record-level self scope).
- A guardian's read access follows the *guardian-of* relationship across person-tenants to
  the ward's grades and history.
- Teachers do **not** get full academic-history access; they stay scoped to the students in
  the course sections they teach.

### 8.2 Administrative employee

An administrative employee can:

- associate a subject with a credential (so a teacher holding it can teach the subject);
- manage teachers, students, guardians, degree programs, degree plans, subjects, and
  course sections;
- associate a subject with a degree program / plan / term;
- set the (global) age of majority;
- activate / deactivate a plan of a degree program (exactly one active per program);
- obtain a grade listing for a course section;
- delete a course section (its grades move to each student's academic history first);
- obtain the list of graduated students and their credential, and confer graduation;
- add a credential to a teacher.

The administrative employee has **no** access to modify students' grades.

### 8.3 Teacher

A teacher can:

- in a course section they teach, assign a grade to a student (write);
- in a course section they teach, list the students with their grades (read);
- view the list of credentials they hold;
- view the list of subjects they are allowed to teach.

### 8.4 Student

A student can:

- view their own grades and academic history (read-only).

### 8.5 Guardian

A guardian can:

- view (read-only) the grades and academic history of all the students in their care.

## 9. Lifecycle rules

- **Deletion is blocked when dependents exist.** An entity may be hard-deleted only if it
  has no dependent records; otherwise the delete is refused. The single spec'd exception is
  deleting a course section, which first moves its grades to each student's academic
  history.
- **Plan replacement grandfathers the old cohort.** Students keep completing the plan they
  enrolled under; a newly activated plan applies only to students who enroll after
  activation. The previous plan becomes inactive but remains valid and referenced for its
  cohort.

## 10. Candidate domain entities

Person; RoleAssignment; DegreeProgram; Plan; Subject; Term; CourseSection; Enrollment;
Grade; AcademicHistory; Graduation; Credential; Guardianship.

## 11. Design rationale

Why the non-obvious choices were made, for future implementers:

- **Person = tenant -> ReBAC.** Because almost every operation crosses persons (a teacher
  grades students, a guardian reads wards, an admin acts on everyone), roles alone cannot
  express access; permissions must follow relationships and apply at the record level.
- **Best grade + full transcript.** Keeping every attempt and letting the best grade count
  aligns cleanly with the "transfer grades to academic history on course-section deletion"
  rule -- history is the durable transcript, sections are transient.
- **Hard-enforced qualification.** Since teaching is the only path to writing a grade,
  enforcing the credential at course-section assignment transitively guarantees that only
  qualified teachers can grade -- no separate grade-time check needed.
- **Graduation as a stored event.** A dated, revocable conferral gives a real graduation
  date and an auditable credential, at the cost of a conferral workflow and periodic
  reconciliation against the computed "passed every subject" truth so the record cannot
  drift.
- **Block-if-dependents deletion.** In an academic system, grades and graduations must
  persist; refusing to delete entities that still have dependents preserves history and
  referential integrity. Course-section deletion is the deliberate exception, and it
  preserves grades by moving them to history first.
- **Computed-on-read guardianship + global age of majority.** Deriving guardianship from
  current age against one global value keeps access decisions always consistent with no
  stored transition to maintain and nothing to run on a birthday.
