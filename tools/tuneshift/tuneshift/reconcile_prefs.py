"""Deprecated shim: preferences moved to ``tuneshift.matching.preferences``.

Retained so existing imports keep working. New code should import from
``tuneshift.matching.preferences`` (or the ``tuneshift.matching`` package).

The old ``score_version`` scorer was dead (only its own test imported it) and
was removed during the matching overhaul; version scoring now flows through the
shared matching engine.
"""

from tuneshift.matching.preferences import (
    Preferences,
    VersionPreferences,
    resolve_preferences,
)

__all__ = ["Preferences", "VersionPreferences", "resolve_preferences"]
