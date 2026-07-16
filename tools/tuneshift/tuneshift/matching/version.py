"""Source-aware version-class model for intent-fidelity matching.

The legacy version scorer only inspected the *candidate*: it penalised any
result whose title/album carried a "version" keyword (live, karaoke, ...).
That is wrong when the SOURCE is itself a live/karaoke/etc. recording, the
correct match then *shares* that class and must NOT be penalised, while the
studio master becomes the substitute.

This module infers a :class:`VersionProfile` for both the source (canonical)
track and each candidate, then compares them *asymmetrically*:

* studio source + non-studio candidate  -> REJECT   (wrong recording)
* live source   + live candidate         -> MATCH
* live source   + studio candidate        -> SUBSTITUTE (fallback recording)
* live source   + karaoke candidate    -> REJECT   (two different non-studio recordings)
* remaster of the same recording          -> SOFT     (cosmetic, same take)
* explicit source + clean candidate        -> REJECT   (censored lyrics differ)

Per-playlist preferences can override the recording axis: a live-takes
playlist (``prefer:[live]``) elevates a live candidate to MATCH even against a
studio source, and ``avoid:[live]`` hard-rejects live candidates regardless of
the source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from tuneshift.matching import normalize as _norm


class RecordingClass(str, Enum):
    """The "which recording is this" axis (distinct performances)."""

    STUDIO = "studio"
    LIVE = "live"
    KARAOKE = "karaoke"
    INSTRUMENTAL = "instrumental"
    REMIX = "remix"
    ACOUSTIC = "acoustic"
    TRIBUTE = "tribute"
    ALTERED = "altered"
    CONTINUOUS_MIX = "continuous_mix"


# Recording-identity classes in DETECTION PRIORITY order (most specific first);
# each maps to the compiled regex already defined in ``matching.normalize``.
_CLASS_REGEXES: tuple[tuple[RecordingClass, str], ...] = (
    (RecordingClass.KARAOKE, "_KARAOKE_RE"),
    (RecordingClass.INSTRUMENTAL, "_INSTRUMENTAL_RE"),
    (RecordingClass.TRIBUTE, "_TRIBUTE_RE"),
    (RecordingClass.ALTERED, "_SPED_UP_RE"),
    (RecordingClass.CONTINUOUS_MIX, "_CONTINUOUS_MIX_RE"),
    (RecordingClass.REMIX, "_REMIX_RE"),
    (RecordingClass.ACOUSTIC, "_ACOUSTIC_RE"),
    (RecordingClass.LIVE, "_LIVE_RE"),
)

_EXPLICIT_RE = re.compile(r"\b(explicit)\b", re.IGNORECASE)
_CLEAN_RE = re.compile(r"\b(clean(?:\s+version)?|censored|radio safe)\b", re.IGNORECASE)

# Non-studio recording classes are DISTINCT recordings; a studio source must
# never silently accept one, and vice-versa.
_DISTINCT: frozenset[RecordingClass] = frozenset(RecordingClass) - {
    RecordingClass.STUDIO
}


@dataclass(frozen=True)
class VersionProfile:
    """The inferred version identity of a single track (source or candidate)."""

    recording: RecordingClass = RecordingClass.STUDIO
    is_remaster: bool = False
    is_explicit: bool = False
    is_clean: bool = False


def infer_version(
    title: str | None,
    album: str | None = None,
    version: str | None = None,
    *,
    explicit: bool | None = None,
) -> VersionProfile:
    """Infer a :class:`VersionProfile` from a track's title, album and version.

    ``version`` is the platform's STRUCTURED version field (e.g. Tidal's
    ``version`` / ``tidal_version``: "Live", "Radio Edit", "Continuous Mix",
    "Mixed"). Because it is a controlled vocabulary it is trusted for markers
    that would be ambiguous in free text - notably a bare "Mixed"/"Continuous"
    that denotes a continuous DJ mix (M1) but is an ordinary word in a title.

    ``explicit`` is the platform's STRUCTURED explicit boolean. When provided
    (not ``None``) it is authoritative over free-text markers: ``True`` sets
    ``is_explicit``, ``False`` sets ``is_clean``. When ``None`` the lyric axis
    falls back to title/album/version text regex, so callers that do not supply
    the flag score byte-identically to before.
    """
    combined = f"{title or ''} {album or ''}"
    version_text = version or ""
    full = f"{combined} {version_text}".strip()
    recording = RecordingClass.STUDIO
    for cls, regex_name in _CLASS_REGEXES:
        if getattr(_norm, regex_name).search(full):
            recording = cls
            break
    # Structured version field: a bare "Mixed"/"Continuous" is a continuous DJ
    # mix. Only the structured field may assert this (never free-text title).
    if (
        recording is RecordingClass.STUDIO
        and version_text
        and _norm._CONTINUOUS_MIX_VERSION_RE.search(version_text)
    ):
        recording = RecordingClass.CONTINUOUS_MIX
    is_explicit = bool(_EXPLICIT_RE.search(full))
    is_clean = bool(_CLEAN_RE.search(full))
    if explicit is not None:
        # Structured platform flag wins over free-text markers.
        is_explicit = explicit
        is_clean = not explicit
    return VersionProfile(
        recording=recording,
        is_remaster=bool(_norm._REMASTER_RE.search(full)),
        is_explicit=is_explicit,
        is_clean=is_clean,
    )


class VersionVerdict(str, Enum):
    """The outcome of comparing a source profile against a candidate profile."""

    MATCH = "match"  # compatible recording; no version penalty
    SOFT = "soft"  # same recording, cosmetic variant (remaster)
    SUBSTITUTE = "substitute"  # requested class unavailable; fallback recording
    REJECT = "reject"  # wrong recording / censored; must not auto-match


def compare_version(
    source: VersionProfile,
    candidate: VersionProfile,
    *,
    prefer: frozenset[str] = frozenset(),
    avoid: frozenset[str] = frozenset(),
) -> VersionVerdict:
    """Compare a source profile to a candidate profile, honouring preferences.

    ``prefer`` / ``avoid`` are sets of :class:`RecordingClass` *values*
    (e.g. ``{"live"}``) resolved from the effective per-playlist preferences.
    """
    # Explicit user avoidance hard-rejects regardless of the source class.
    if candidate.recording.value in avoid:
        return VersionVerdict.REJECT

    prefers_candidate = candidate.recording.value in prefer

    # --- Recording-class axis ---
    if source.recording == candidate.recording:
        verdict = VersionVerdict.MATCH
    elif prefers_candidate:
        # The playlist explicitly wants this class (e.g. a live-takes playlist),
        # so a live candidate beats the studio master's version penalty.
        verdict = VersionVerdict.MATCH
    elif source.recording == RecordingClass.STUDIO and candidate.recording in _DISTINCT:
        # Studio master requested; candidate is a different recording.
        verdict = VersionVerdict.REJECT
    elif candidate.recording == RecordingClass.STUDIO and source.recording in _DISTINCT:
        # A special recording was requested but only the studio master exists.
        verdict = VersionVerdict.SUBSTITUTE
    else:
        # Two different non-studio recordings (e.g. live vs karaoke).
        verdict = VersionVerdict.REJECT

    # --- Explicit / clean axis (lyrics differ) ---
    if source.is_explicit and candidate.is_clean:
        # "clean never satisfies explicit source"
        return VersionVerdict.REJECT
    if source.is_clean and candidate.is_explicit and verdict is VersionVerdict.MATCH:
        verdict = VersionVerdict.SUBSTITUTE
    # Canonical source with no lyric signal (the common case): rank the two lyric
    # variants by preference so the PREFERRED one wins OUTRIGHT rather than tying.
    # The non-preferred variant is a modified/non-preferred master and drops to a
    # substitute (down-ranked but still findable when it is the only option).
    # Default prefers explicit (clean is in the default avoid set), so a clean
    # edit is down-ranked; a "prefer clean" instead down-ranks the explicit take,
    # so the clean release wins outright. When neither candidate carries a lyric
    # signal (unknown structured flag and no title marker -- the whole gold
    # corpus) nothing fires and scoring is byte-identical to before.
    if (
        verdict is VersionVerdict.MATCH
        and not source.is_clean
        and not source.is_explicit
    ):
        prefers_clean = "clean" in prefer
        if candidate.is_clean and not prefers_clean:
            verdict = VersionVerdict.SUBSTITUTE
        elif candidate.is_explicit and prefers_clean:
            verdict = VersionVerdict.SUBSTITUTE

    # --- Remaster is cosmetic (same recording) ---
    if (
        verdict is VersionVerdict.MATCH
        and candidate.is_remaster
        and not source.is_remaster
    ):
        verdict = VersionVerdict.SOFT

    return verdict


__all__ = [
    "RecordingClass",
    "VersionProfile",
    "VersionVerdict",
    "compare_version",
    "infer_version",
]
