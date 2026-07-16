"""Artist matching: score and rank artist search results against a source.

Artist selection used to be a blind ``artists[0]`` off the platform's search
ranking. This module scores candidates so the *right* artist is chosen by name,
corroborated (never overridden) by enrichment — genres, popularity, followers.

Missing enrichment is always neutral: a platform that returns no genres or
popularity for an artist must not be penalized relative to one that does. Name
similarity dominates; enrichment only breaks ties and lightly corroborates.

Like album scoring, this is new capability with no legacy byte-parity contract,
so signals live purely in the normalized distance model.
"""

from __future__ import annotations

from tuneshift.matching.engine import (
    Distance,
    Recommendation,
    RecommendationThresholds,
    recommend,
)
from tuneshift.matching.normalize import artist_set_overlap, normalize_artist
from tuneshift.matching.penalties import SignalPenalty
from tuneshift.matching.similarity import ratio

_W_NAME = 8  # name similarity dominates
_W_GENRE = 2  # corroboration only
_W_POPULARITY = 1  # tiebreak only

ARTIST_THRESHOLDS = RecommendationThresholds(
    auto_max=0.15,
    suggest_max=0.35,
    ask_max=0.60,
    gap_min=0.05,
)


def _signed_points(penalty: float, weight: int) -> int:
    return -round(penalty * weight)


def _name_signal(source_name: str, candidate_name: str) -> SignalPenalty:
    src = normalize_artist(source_name)
    cand = normalize_artist(candidate_name)
    if not src or not cand:
        return SignalPenalty("artist:name", 0, 1.0, _W_NAME)
    if src == cand:
        return SignalPenalty("artist:name", 0, 0.0, _W_NAME)
    # Use the stronger of fuzzy string similarity and order-independent
    # multi-artist set overlap, so "Jay-Z & Alicia Keys" matches
    # "Alicia Keys & Jay-Z" without the reordering tanking the score, while
    # single-artist scoring is unchanged (disjoint singles overlap to 0.0, so
    # the fuzzy ratio wins).
    similarity = max(ratio(src, cand), artist_set_overlap(source_name, candidate_name))
    penalty = 1.0 - similarity
    return SignalPenalty(
        "artist:name", _signed_points(penalty, _W_NAME), penalty, _W_NAME
    )


def _genre_signal(
    source_genres: list[str] | None, candidate_genres: list[str] | None
) -> SignalPenalty:
    if not source_genres or not candidate_genres:
        return SignalPenalty("artist:genre", 0, 0.0, 0)  # missing -> neutral
    src = {g.strip().lower() for g in source_genres if g}
    cand = {g.strip().lower() for g in candidate_genres if g}
    if not src or not cand:
        return SignalPenalty("artist:genre", 0, 0.0, 0)
    overlap = len(src & cand) / len(src)
    penalty = 1.0 - overlap
    return SignalPenalty(
        "artist:genre", _signed_points(penalty, _W_GENRE), penalty, _W_GENRE
    )


def _popularity_signal(popularity: int | None, followers: int | None) -> SignalPenalty:
    """Light tiebreak: more popular/followed artists are marginally preferred.

    Neutral (zero weight) when neither signal is present, so obscure-but-correct
    artists are never disadvantaged on platforms that omit the data.
    """
    if popularity is None and followers is None:
        return SignalPenalty("artist:popularity", 0, 0.0, 0)
    if popularity is not None:
        penalty = 1.0 - max(0, min(100, popularity)) / 100.0
    else:
        # Log-ish bucketing of follower counts into [0, 1].
        followers = max(0, followers or 0)
        penalty = (
            1.0 if followers == 0 else max(0.0, 1.0 - min(1.0, followers / 1_000_000))
        )
    return SignalPenalty(
        "artist:popularity",
        _signed_points(penalty, _W_POPULARITY),
        penalty,
        _W_POPULARITY,
    )


def score_artist_match(
    source_name: str,
    candidate: object,
    *,
    source_genres: list[str] | None = None,
) -> Distance:
    """Score a candidate artist (an ``ArtistResult``) against the source name."""
    distance = Distance()
    distance.add(_name_signal(source_name, getattr(candidate, "name", "")))
    distance.add(_genre_signal(source_genres, getattr(candidate, "genres", None)))
    distance.add(
        _popularity_signal(
            getattr(candidate, "popularity", None),
            getattr(candidate, "followers", None),
        )
    )
    return distance


def classify_artist_results(distances: list[float]) -> str:
    """Map a list of artist distances to a confidence label."""
    if not distances:
        return "not_found"
    ordered = sorted(distances)
    best = Distance([SignalPenalty("_", 0, ordered[0], 1)])
    runner_up = ordered[1] if len(ordered) > 1 else None
    action = recommend(best, runner_up=runner_up, thresholds=ARTIST_THRESHOLDS)
    return {
        Recommendation.AUTO: "high",
        Recommendation.SUGGEST: "medium",
        Recommendation.ASK: "low",
        Recommendation.REJECT: "not_found",
    }[action]


__all__ = ["ARTIST_THRESHOLDS", "classify_artist_results", "score_artist_match"]
