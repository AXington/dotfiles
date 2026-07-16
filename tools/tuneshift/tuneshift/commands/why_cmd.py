"""Deprecated module path for the match-decision explainer.

The command was renamed ``why`` -> ``explain`` (AC-CLI3). This shim re-exports
the public names from :mod:`tuneshift.commands.explain_cmd` so any lingering
imports keep working for one release. Import from ``explain_cmd`` in new code.
"""

from tuneshift.commands.explain_cmd import (  # noqa: F401
    handle_explain,
    handle_why,
)
