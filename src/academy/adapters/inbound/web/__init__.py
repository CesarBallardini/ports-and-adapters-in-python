"""The browser and JSON driving adapter: htmx over Jinja2, and the same use cases as JSON.

Four seams, the same ones the CLI drew (ADR-0011, ADR-0021):

* ``rendering`` owns the htmx contract and imports no route;
* ``dependencies`` owns the port-per-route rule and knows no template;
* ``errors`` owns the single error boundary and classifies nothing itself;
* ``app`` owns the control flow and is one of only two places that may name a ``Scope``.

``create_app`` here takes an already-wired container. The factory a deployment names is
``academy.config.create_app``, which reads the environment first.
"""

from academy.adapters.inbound.web.app import create_app

__all__ = ['create_app']
