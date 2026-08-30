# Documentation

Written in the order it should be read. Each document answers one question, and each is the
input to the next — the design is derived, not decreed.

| # | Document | Question it answers |
|---|----------|---------------------|
| 01 | [Description](./01-description.md) | What problem does the system solve, and under what rules? |
| 02 | [Actors and use cases](./02-actors-and-use-cases.md) | Who uses it, and what for? |
| 03 | [Sequence diagrams](./03-sequence-diagrams.md) | For each use case, which object does what? |
| 04 | [State diagrams](./04-state-diagrams.md) | What lifecycles do those objects have, and what must be stored versus computed? |
| 05 | [Domain model](./05-domain-model.md) | What are the entities, value objects and invariants? |
| 06 | [Class diagram](./06-class-diagram.md) | What classes result from assigning those responsibilities, in which layer? |
| — | [Decisions](./decisions/README.md) | Why each structural choice was made, and what was rejected |

The path from 02 to 06 is the classic object-oriented analysis and design sequence: use cases
give the behaviour, sequence diagrams assign the responsibilities, and the class diagram is what
falls out. Doing it in that order is why there is no `AcademyManager` god object anywhere in
this codebase — no responsibility was ever assigned to a class chosen in advance.
