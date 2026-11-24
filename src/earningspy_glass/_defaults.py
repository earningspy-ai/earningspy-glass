"""Sensible notebook behaviour, applied when the client is created.

A user should set a ticker and run, not configure Python first. Creating
``EarningSpy()`` therefore:

- draws charts inline in a VS Code notebook (Plotly's own choice everywhere else);
- prints the one-line summary each fetch logs, on the package's own logger only;
- keeps the service's informational notes out of the cell output - they are one
  call away in ``peers.warnings()``.

Logging is left alone when the user configured it. The renderer and the notes
filter are set once, at the first client; anything set after that wins. Importing
the package changes nothing; only creating the client does.
"""

import logging
import os
import sys
import warnings

_PACKAGE_LOGGER = "earningspy_glass"
_applied = False


def _in_notebook_kernel():
    try:
        from IPython import get_ipython
    except ImportError:
        return False
    shell = get_ipython()
    return shell is not None and "IPKernelApp" in getattr(shell, "config", {})


def _inline_charts():
    """Inline charts in a VS Code notebook, where Plotly may fall back to a browser tab."""
    if not _in_notebook_kernel() or not any(k.startswith("VSCODE_") for k in os.environ):
        return
    try:
        import plotly.io as pio
    except ImportError:
        return
    if "vscode" not in str(pio.renderers.default or ""):
        pio.renderers.default = "vscode"


def _fetch_summaries(verbose):
    """The package's INFO lines on stdout, unless the user configured logging."""
    log = logging.getLogger(_PACKAGE_LOGGER)
    if not verbose:
        log.setLevel(logging.WARNING)
        return
    if logging.getLogger().handlers or log.handlers:
        return                                  # the user's own setup decides
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(handler)
    if log.level == logging.NOTSET:
        log.setLevel(logging.INFO)


def _quiet_notes():
    from earningspy_glass.api import PayloadNotice
    warnings.filterwarnings("ignore", category=PayloadNotice)


def apply(verbose=True):
    """Apply the notebook defaults once per process; `verbose=False` silences the
    fetch summaries."""
    global _applied
    if not verbose:
        _fetch_summaries(False)
    if _applied:
        return
    _applied = True
    _inline_charts()
    _fetch_summaries(verbose)
    _quiet_notes()
