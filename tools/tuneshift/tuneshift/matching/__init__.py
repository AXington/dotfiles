"""Track matching: normalization, scoring, and classification.

This package supersedes the former single-file ``matching.py``. Its public
surface is re-exported here unchanged so every existing importer
(``from tuneshift.matching import ...``) keeps working byte-for-byte while the
internals are split across focused modules:

- ``normalize``, string normalization + the shared version-keyword regexes.
- ``track``, the legacy track scorers and confidence classifier.

Later chunks add ``similarity``, ``penalties``, ``engine``, ``confidence``,
``preferences``, ``album``, ``artist``, ``version``, ``identity`` and
``audit`` modules; they will be re-exported from here as they land.
"""

from tuneshift.matching.album import (
    ALBUM_THRESHOLDS,
    classify_album_results,
    edition_cost,
    score_album_match,
)
from tuneshift.matching.artist import (
    ARTIST_THRESHOLDS,
    classify_artist_results,
    score_artist_match,
)
from tuneshift.matching.audit import (
    Availability,
    CriterionOutcome,
    MatchAudit,
    ReasonCode,
    RejectedCandidate,
    SignalContribution,
    describe_availability,
    describe_reason,
)
from tuneshift.matching.confidence import classify_scores
from tuneshift.matching.fingerprint import (
    DEFAULT_DURATION_BUCKET_SECONDS,
    TrackFingerprint,
    build_fingerprint,
    fingerprint_equal,
)
from tuneshift.matching.normalize import (
    artist_set_overlap,
    fold_accents,
    is_remaster,
    normalize_artist,
    normalize_title,
    split_artists,
    strip_album_from_title,
)
from tuneshift.matching.preferences import (
    Preferences,
    VersionPreferences,
    edition_buckets,
    preference_sort_bias,
    resolve_preferences,
    scoring_intent,
    version_intent,
)
from tuneshift.matching.review import (
    ReviewBurden,
    ReviewCluster,
    ReviewItem,
    cluster_reviews,
    compute_burden,
    needs_review,
    review_kind,
)
from tuneshift.matching.track import (
    classify_results,
    duration_penalty,
    duration_proximity_bonus,
    score_match,
    score_match_with_version,
    score_track_match,
    version_penalty,
)
from tuneshift.matching.version import (
    RecordingClass,
    VersionProfile,
    VersionVerdict,
    compare_version,
    infer_version,
)

__all__ = [
    "ALBUM_THRESHOLDS",
    "ARTIST_THRESHOLDS",
    "DEFAULT_DURATION_BUCKET_SECONDS",
    "Availability",
    "CriterionOutcome",
    "MatchAudit",
    "Preferences",
    "ReasonCode",
    "RecordingClass",
    "RejectedCandidate",
    "ReviewBurden",
    "ReviewCluster",
    "ReviewItem",
    "SignalContribution",
    "TrackFingerprint",
    "VersionPreferences",
    "VersionProfile",
    "VersionVerdict",
    "artist_set_overlap",
    "build_fingerprint",
    "classify_album_results",
    "classify_artist_results",
    "classify_results",
    "classify_scores",
    "cluster_reviews",
    "compare_version",
    "compute_burden",
    "describe_availability",
    "describe_reason",
    "duration_penalty",
    "duration_proximity_bonus",
    "edition_buckets",
    "edition_cost",
    "fingerprint_equal",
    "fold_accents",
    "infer_version",
    "is_remaster",
    "needs_review",
    "normalize_artist",
    "normalize_title",
    "preference_sort_bias",
    "resolve_preferences",
    "review_kind",
    "score_album_match",
    "score_artist_match",
    "score_match",
    "score_match_with_version",
    "score_track_match",
    "scoring_intent",
    "split_artists",
    "strip_album_from_title",
    "version_intent",
    "version_penalty",
]
