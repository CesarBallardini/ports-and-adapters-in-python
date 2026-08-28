"""Adapters: everything that touches the world outside the hexagon.

Split by the direction the call travels, not by technology:

* ``inbound`` -- driving adapters. The web UI, the JSON API, the CLI, the job worker.
  They translate a protocol into a call on a driving port.
* ``outbound`` -- driven adapters. Persistence, spreadsheets, storage, email, the clock.
  They satisfy a driven port using some concrete technology.

May depend on :mod:`academy.application` and :mod:`academy.domain`, never the reverse.
"""
