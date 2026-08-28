"""The ports: the edges of the hexagon.

Two kinds, distinguished by who calls whom:

* :mod:`~academy.application.ports.inbound` -- driving ports. What the outside world may
  ask the application to do. Implemented by the application, called by adapters.
* :mod:`~academy.application.ports.outbound` -- driven ports. What the application needs
  from the outside world. Called by the application, implemented by adapters.

The direction of the *call* differs; the direction of the *dependency* never does. Both
are defined here, inside the hexagon, and both are stated in terms of the domain -- never
in terms of a database row or an HTTP body.
"""
