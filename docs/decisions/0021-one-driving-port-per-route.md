# ADR-0021 — One driving port per route, and one error boundary

- **Status** Accepted
- **Date** 2026-08-31
- **Extends** ADR-0011, ADR-0019
- **Implements** the inbound-adapter shape ADR-0020 established for the CLI

## Context

The CLI was the first inbound adapter and settled a shape: four modules along the seam that
matters, a single error boundary, and a handler that receives **one driving port and never the
`Scope`**. The web adapter is the second, and the question it has to answer is whether that shape
was a property of the architecture or a property of `argparse`.

A `Scope` carries every repository as well as every use case. A route holding one could answer
`GET /sections/{id}/grades` by reading `scope.histories` directly — no use case, no `AccessGuard`,
no authorization — and every hand-written test of that route would still pass. This is not a
hypothetical: it is one line, and it is the shortest path when a template needs a field a DTO
does not carry.

FastAPI adds a second question. The CLI had one rendering; the web adapter has two — HTML for a
browser and JSON for a script — over the same use cases. ADR-0019 says the classification of a
failure is shared and its *rendering* is not, and this is where that first has two renderings to
be right about.

And a third, particular to htmx. **htmx 2 does not swap a non-2xx response.** Its default
`responseHandling` is `[{code:"204",swap:false},{code:"[23]..",swap:true},{code:"[45]..",swap:false,error:true}]`,
so a route that honestly answers 403 — which is exactly what ADR-0012's table says to answer —
produces a page where nothing visibly happens. The tempting fix is to return 200 with an error in
the body, which throws away the status every non-browser client depends on.

## Decision

**One driving port per route, expressed as one FastAPI dependency per port.**

```python
Grades = Annotated[ManageGrades, Depends(grade_management)]

@router.get('/sections/{section_id}/grades')
async def grade_sheet(section_id: str, grades: Grades, actor: CurrentActor) -> Response: ...
```

- **Exactly two places may name a `Scope`**: the `scope` dependency itself, and the lifespan that
  owns the container it comes from. `tests/adapters/test_web_dependencies.py` fails if a third
  appears, if a route names a `Scope` or a `Container`, or if a route names two driving ports.
- The one exception is **sign-in**, which depends on `PersonRepository` directly. Authentication
  is not a use case and has no driving port (ADR-0010), so there is nothing else to depend on. It
  is named narrowly — one repository, not a scope — and a test asserts it stays the only one.

**One error boundary, registered as exception handlers.** No route contains `except DomainError`.
`errors.install` registers handlers for `ApplicationError` and `DomainError` that call
`error_status.http_status()` and add no classification of their own. Anything the table does not
classify is not handled at all: it propagates, Starlette answers 500, and the traceback survives —
the same decision the CLI makes when it lets an unclassified error exit 1.

**The rendering is chosen by the router, not sniffed.** Each router's dependency marks
`request.state` with a `Surface`, and the error boundary reads it back through an `isinstance`
narrowing. Not `Accept` negotiation and not `path.startswith('/api/')`: the router already knows
which it is — that is what makes it a different router — and a string test on the path is the kind
of thing that is written once, copied, and then wrong everywhere at the same time.

**htmx's swap rule is overridden once**, in `rendering.HTMX_RESPONSE_HANDLING`, rendered into
`base.html`'s `htmx-config` meta tag:

| code | swap | error | why |
|---|---|---|---|
| `204` | no | | nothing to swap |
| `[23]..` | yes | | the ordinary case |
| `4..` | **yes** | yes | so an honest 403 or 422 is *seen*; this is the override |
| `5..` | no | yes | a 500's body is a traceback or an apology; neither belongs in a page |

Every failure response to an htmx request also carries `HX-Retarget: #academy-errors` and
`HX-Reswap: innerHTML`, so an error lands in the page's error region rather than replacing the row
that caused it.

## Consequences

- A route cannot reach storage except through a use case, and the check is mechanical rather than
  a reviewer's attention.
- Failures keep their real status **and** become visible. The two are usually traded against each
  other; here neither is given up, at the cost of one meta tag that has to be right.
- The meta tag is generated from the Python constant, so the two cannot drift — and a unit test
  reads the vendored `htmx.min.js` to notice if an upgrade changes the defaults being overridden.
- Adding a route means adding a dependency alias, which is a deliberate widening of what routes
  may reach rather than a parameter someone tacked on.
- Two renderings of one classification means two places a new `Failure` has to look right. They
  share the status and differ only in body, which is the smallest that difference can be.
- The dependency-per-port rule is more verbose than injecting the scope once. That verbosity is
  the decision: it is what makes the reach of each route visible in its signature.

## Alternatives considered

- **Inject the `Scope` and rely on review.** Less code, and it gives up the only mechanical check
  there is. The reference application did this and accumulated exactly the leaks described above.
- **Negotiate the rendering from `Accept`.** Standard, and wrong here: a browser sends
  `Accept: */*` on an htmx request, so the JSON and HTML surfaces would be distinguished by a
  header the client does not control carefully.
- **Return 200 for expected failures so htmx swaps them.** Widely done, and it makes the API
  useless to anything that reads status codes — which is every client that is not a browser.
- **Handle htmx's swap rule per route** with `HX-Retarget` and hand-written 200s. This is what
  ADR-0011 predicted would be "re-derived, slightly differently, in every delete handler".
