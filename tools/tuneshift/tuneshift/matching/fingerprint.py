"""Durable track fingerprint for self-healing locks.

A *fingerprint* is a stable identity signature for a canonical track that
survives a platform re-issuing or delisting a specific track id. When a user
locks a mapping (approves it), we store the fingerprint alongside it. On a later
re-sync, if the locked platform id has gone dead, the fingerprint lets us
re-find *the same recording* — not merely a same-titled substitute — and heal
the lock in place, or hold it as ``exact_unavailable`` if the recording is
genuinely gone. It must never silently drift to a different recording.

Two fingerprints are considered equal when they identify the same recording:

- If both carry an ISRC, the ISRCs must match (ISRC is the strongest signal).
- Otherwise, normalized title and artist must match, the recording/version class
  must match, and durations must fall in the same tolerance bucket.

Duration is compared in a bucket (default ±2s, configurable) so that trivial
metadata jitter between platforms does not defeat a genuine re-match.
"""

from __future__ import annotations

from dataclasses import dataclass

from tuneshift.matching.normalize import normalize_artist, normalize_title
from tuneshift.matching.version import RecordingClass, infer_version

#: Default half-width of the duration-equality bucket, in seconds.
DEFAULT_DURATION_BUCKET_SECONDS = 2


@dataclass(frozen=True)
class TrackFingerprint:
    """A stable identity signature for a recording.

    ``isrc`` is the authoritative axis when present on both sides. The remaining
    fields provide a robust fallback when one side lacks an ISRC (common on
    YouTube Music and for user-entered tracks).
    """

    isrc: str | None
    norm_title: str
    norm_artist: str
    recording_class: str
    duration_seconds: int | None

    def as_dict(self) -> dict:
        return {
            "isrc": self.isrc,
            "norm_title": self.norm_title,
            "norm_artist": self.norm_artist,
            "recording_class": self.recording_class,
            "duration_seconds": self.duration_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TrackFingerprint:
        return cls(
            isrc=data.get("isrc"),
            norm_title=data.get("norm_title", ""),
            norm_artist=data.get("norm_artist", ""),
            recording_class=data.get("recording_class", RecordingClass.STUDIO.value),
            duration_seconds=data.get("duration_seconds"),
        )


def _clean_isrc(isrc: str | None) -> str | None:
    if not isrc:
        return None
    cleaned = isrc.strip().upper().replace("-", "")
    return cleaned or None


def build_fingerprint(
    *,
    title: str | None,
    artist: str | None,
    album: str | None = None,
    isrc: str | None = None,
    duration_seconds: int | None = None,
    recording_class: str | None = None,
) -> TrackFingerprint:
    """Build a :class:`TrackFingerprint` from track/candidate fields.

    ``recording_class`` may be supplied when it has already been resolved (e.g.
    from the identity layer); otherwise it is inferred from title + album.
    """
    if recording_class is None:
        recording_class = infer_version(title, album).recording.value
    return TrackFingerprint(
        isrc=_clean_isrc(isrc),
        norm_title=normalize_title(title or ""),
        norm_artist=normalize_artist(artist or ""),
        recording_class=recording_class,
        duration_seconds=duration_seconds,
    )


def _duration_matches(a: int | None, b: int | None, bucket: int) -> bool:
    # Unknown duration on either side is not evidence of a mismatch — a locked
    # user track may have no duration. Only reject when both are known and far.
    if a is None or b is None:
        return True
    return abs(a - b) <= bucket


def fingerprint_equal(
    a: TrackFingerprint,
    b: TrackFingerprint,
    *,
    duration_bucket_seconds: int = DEFAULT_DURATION_BUCKET_SECONDS,
) -> bool:
    """Return True when two fingerprints identify the same recording.

    ISRC is authoritative: if both sides carry one, they must match and nothing
    else is required. If either side lacks an ISRC, fall back to normalized
    title + artist + recording class + duration bucket.
    """
    if a.isrc and b.isrc:
        return a.isrc == b.isrc
    return (
        a.norm_title == b.norm_title
        and a.norm_artist == b.norm_artist
        and a.recording_class == b.recording_class
        and _duration_matches(
            a.duration_seconds, b.duration_seconds, duration_bucket_seconds
        )
    )
