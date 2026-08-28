"""Driven ports: what the application needs from the outside world.

Each is a :class:`typing.Protocol`, so adapters conform structurally -- an adapter never
imports a port in order to subclass it, and the dependency arrow points inward only.

Each module states whether its port is sync or async, and why: async when crossing the
port means waiting on something outside the process, sync when it does not (ADR-0005).

Port docstrings are specifications, not descriptions. The contract test suite asserts
exactly what they claim, against every implementation (ADR-0014).
"""
