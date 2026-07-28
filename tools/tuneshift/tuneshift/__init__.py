"""tuneshift: canonical playlist manager with cross-platform distribution."""

__version__ = "0.1.0"


class TuneShiftError(Exception):
    """Base exception for all tuneshift operational errors."""


class PlatformSyncError(TuneShiftError):
    """One or more platform operations failed during sync."""

    def __init__(self, failures: list[str]) -> None:
        self.failures = failures
        msg = "; ".join(failures)
        super().__init__(f"Platform sync failed: {msg}")


class PlatformAuthError(TuneShiftError):
    """Platform authentication failed or session expired."""


class SequenceIntegrityError(TuneShiftError):
    """A sequencer returned a track set that differs from its input.

    Sequencing reorders tracks; it must never add, drop, or duplicate one.
    Raised instead of returning a corrupted playlist, because a silently
    duplicated track looks plausible and gets persisted.
    """

    def __init__(
        self, context: str, missing: list[int], duplicated: list[int], added: list[int]
    ) -> None:
        self.context = context
        self.missing = missing
        self.duplicated = duplicated
        self.added = added
        parts = []
        if missing:
            parts.append(f"dropped={missing}")
        if duplicated:
            parts.append(f"duplicated={duplicated}")
        if added:
            parts.append(f"added={added}")
        detail = " ".join(parts) or "track multiset changed"
        super().__init__(f"{context} corrupted the track set: {detail}")
