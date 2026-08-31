"""The command-line driving adapter: the smallest way into the application.

The first inbound adapter this repository builds, and chosen for that on purpose. It is small
enough that what a driving adapter *is* stays visible -- a translation from one protocol's
vocabulary into a command object, and nothing else -- before a web framework arrives to hide it.

It is also the fourth driver in the README's third claim, *a use case should not know who called
it*, and the first that can actually demonstrate it: `academy import run` reaches the same
``ImportService`` the acceptance suite drives, with no HTTP anywhere in the process.

Four modules, split along the seam that matters:

* :mod:`.parser` owns the grammar and knows no use case;
* :mod:`.commands` owns the handlers and knows no argv;
* :mod:`.render` owns the two output shapes and touches no domain object;
* :mod:`.main` owns the control flow and holds the one error boundary.

Run it as ``python -m academy`` or as the ``academy`` console script (ADR-0020).
"""

from academy.adapters.inbound.cli.exit_codes import ExitCode
from academy.adapters.inbound.cli.main import main

__all__ = ['ExitCode', 'main']
