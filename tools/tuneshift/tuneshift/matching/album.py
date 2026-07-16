"""Album matching: score and rank album search results against a source.

Album selection used to be a blind ``albums[0]`` after an edition-only sort,
with a separate 0.75-ratio ``_album_name_matches`` gate. This module replaces
both with an engine-backed scorer that ranks candidates on title, artist,
edition, release-year and track-count evidence, and a classifier that gates
acceptance so a poor best-candidate is reported as ``not_found`` instead of
being silently picked.

Unlike the track scorer, album scoring has no legacy byte-parity contract; it
is a new capability, so the signals are expressed purely in the normalized
distance model (0.0 = perfect, 1.0 = worst).
"""

from __future__ import annotations

from tuneshift.matching.engine import (
    Distance,
    Recommendation,
    RecommendationThresholds,
    recommend,
)
from tuneshift.matching.normalize import normalize_artist, normalize_title
from tuneshift.matching.penalties import SignalPenalty
from tuneshift.matching.similarity import ratio

# Relative importance of each album signal (numerator weights).
_W_TITLE = 5
_W_ARTIST = 4
_W_EDITION = 1
_W_YEAR = 1
_W_TRACK_COUNT = 1

# Edition keyword -> distance cost (non-standard editions are less preferred).
_EDITION_COSTS: tuple[tuple[str, int], ...] = (
    ("deluxe", 10),
    ("expanded", 10),
    ("anniversary", 6),
    ("special edition", 6),
    ("super deluxe", 10),
    ("remaster", 3),
)

# Album acceptance ladder (distances run larger than the track scorer's, so
# these are tuned independently of the track thresholds).
ALBUM_THRESHOLDS = RecommendationThresholds(
    auto_max=0.20,
    suggest_max=0.40,
    ask_max=0.65,
    gap_min=0.05,
)


def _signed_points(penalty: float, weight: int) -> int:
    """A readable signed contribution for explainability/breakdown."""
    return -round(penalty * weight)


def _title_signal(source_album: str, candidate_album: str) -> SignalPenalty:
    src = normalize_title(source_album)
    cand = normalize_title(candidate_album)
    if not src or not cand:
        return SignalPenalty("album:title", 0, 0.0, 0)
    penalty = 0.0 if src == cand else 1.0 - ratio(src, cand)
    return SignalPenalty(
        "album:title", _signed_points(penalty, _W_TITLE), penalty, _W_TITLE
    )


def _artist_signal(source_artist: str, candidate_artist: str) -> SignalPenalty:
    src = normalize_artist(source_artist)
    cand = normalize_artist(candidate_artist)
    if not src or not cand:
        return SignalPenalty("album:artist", 0, 0.0, 0)
    penalty = 0.0 if src == cand else 1.0 - ratio(src, cand)
    return SignalPenalty(
        "album:artist", _signed_points(penalty, _W_ARTIST), penalty, _W_ARTIST
    )


def edition_cost(album_name: str) -> int:
    """Total edition-penalty cost for an album title (0 for a standard edition).

    Centralizes edition knowledge that used to be duplicated across the
    reconcile tiebreak and album-name heuristics.
    """
    lowered = (album_name or "").lower()
    return sum(c for kw, c in _EDITION_COSTS if kw in lowered)


def _edition_signal(candidate_album: str) -> SignalPenalty:
    cost = edition_cost(candidate_album)
    penalty = min(1.0, cost / 10.0)
    return SignalPenalty(
        "album:edition", _signed_points(penalty, _W_EDITION), penalty, _W_EDITION
    )


def _year_signal(source_year: int | None, candidate_year: int | None) -> SignalPenalty:
    if not source_year or not candidate_year:
        return SignalPenalty("album:year", 0, 0.0, 0)  # missing -> neutral
    diff = abs(source_year - candidate_year)
    penalty = min(1.0, diff / 10.0)
    return SignalPenalty(
        "album:year", _signed_points(penalty, _W_YEAR), penalty, _W_YEAR
    )


def _track_count_signal(
    source_count: int | None, candidate_count: int | None
) -> SignalPenalty:
    if not source_count or not candidate_count:
        return SignalPenalty("album:track_count", 0, 0.0, 0)  # missing -> neutral
    diff = abs(source_count - candidate_count)
    penalty = min(1.0, diff / max(source_count, 1))
    return SignalPenalty(
        "album:track_count",
        _signed_points(penalty, _W_TRACK_COUNT),
        penalty,
        _W_TRACK_COUNT,
    )


def score_album_match(
    source_album: str,
    source_artist: str,
    candidate: object,
    *,
    source_year: int | None = None,
    source_track_count: int | None = None,
) -> Distance:
    """Score a candidate album (an ``AlbumResult``) against the source.

    Missing enrichment (year, track count) contributes a zero-weight neutral
    signal so it never penalizes a candidate that simply lacks the data.
    """
    distance = Distance()
    distance.add(_title_signal(source_album, getattr(candidate, "title", "")))
    distance.add(_artist_signal(source_artist, getattr(candidate, "artist", "")))
    distance.add(_edition_signal(getattr(candidate, "title", "")))
    distance.add(_year_signal(source_year, getattr(candidate, "release_year", None)))
    distance.add(
        _track_count_signal(source_track_count, getattr(candidate, "track_count", None))
    )
    return distance


def classify_album_results(distances: list[float]) -> str:
    """Map a list of album distances to a confidence label.

    Returns ``high`` / ``medium`` / ``low`` / ``not_found`` from the best
    (smallest) distance, honoring the runner-up gap for a confident top pick.
    """
    if not distances:
        return "not_found"
    ordered = sorted(distances)
    best = Distance([SignalPenalty("_", 0, ordered[0], 1)])
    runner_up = ordered[1] if len(ordered) > 1 else None
    action = recommend(best, runner_up=runner_up, thresholds=ALBUM_THRESHOLDS)
    return {
        Recommendation.AUTO: "high",
        Recommendation.SUGGEST: "medium",
        Recommendation.ASK: "low",
        Recommendation.REJECT: "not_found",
    }[action]


__all__ = [
    "ALBUM_THRESHOLDS",
    "classify_album_results",
    "edition_cost",
    "score_album_match",
]
