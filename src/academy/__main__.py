"""``python -m academy`` -- the CLI adapter's entry point (ADR-0020).

Two lines, and both of them matter. The package's ``__main__`` names an *adapter*, never a use
case: the composition root is the only thing that knows which adapters exist, and this hands
straight to it.

Not covered by the test suite, and correctly so -- the e2e tier runs this exact path in a real
subprocess and asserts the status a shell sees, which is the only way to check it at all.
"""

from academy.adapters.inbound.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
