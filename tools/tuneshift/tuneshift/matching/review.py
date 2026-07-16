"""Review clustering and review-burden reporting.

Long, ordered libraries produce many tracks that a single run cannot place
confidently: ambiguous top candidates, or genuine hard failures. Prompting the
user once per track (the historical ``sync`` flow) does not scale — Alice's
libraries are large and the same decision recurs across dozens of tracks by the
same artist or from the same problematic compilation.

This module turns raw per-track outcomes into:

- **Clusters** — items grouped by *why* they need review and *who* they concern,
  so a reviewer makes one decision for a whole group instead of N identical
  prompts.
- **Review burden** — the headline metrics Alice signs off on: how many tracks
  per thousand need a human, and what fraction of playlists sailed through with
  zero intervention.

It is pure logic over plain data objects so it is trivially testable and has no
DB or network dependency; callers (the ``review`` command) supply the items.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tuneshift.matching.audit import Availability, ReasonCode, describe_reason
from tuneshift.matching.normalize import normalize_artist

#: Result states that require a human decision.
AMBIGUOUS_STATES = frozenset({Availability.AMBIGUOUS})
#: Result states that are outright failures worth surfacing (the track could not
#: be placed, or a durable lock is now unavailable and held).
HARD_FAIL_STATES = frozenset({Availability.NOT_FOUND})
#: Reason codes that, even under a non-failing availability, still merit review.
HARD_FAIL_REASONS = frozenset({ReasonCode.LOCK_HELD})

# Review-kind labels.
AMBIGUOUS = "ambiguous"
HARD_FAIL = "hard_fail"


@dataclass(frozen=True)
class ReviewItem:
    """One track outcome that may require human review."""

    track_id: int
    title: str
    artist: str
    album: str | None
    platform: str
    availability: str
    reason_code: str
    playlist_id: int | None = None
    playlist_name: str | None = None


@dataclass
class ReviewCluster:
    """A group of review items sharing a reason and an artist.

    Presenting the cluster lets a reviewer resolve the whole group at once
    (e.g. "all 12 tracks by this artist flagged as ambiguous").
    """

    reason_code: str
    artist: str
    items: list[ReviewItem] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.items)

    @property
    def summary(self) -> str:
        return f"{self.size} × {self.artist} — {describe_reason(self.reason_code)}"


@dataclass
class ReviewBurden:
    """Headline review-burden metrics for a sign-off gate."""

    total_tracks: int
    ambiguous: int
    hard_fail: int
    total_playlists: int
    zero_intervention_playlists: int

    @property
    def needs_review(self) -> int:
        return self.ambiguous + self.hard_fail

    @property
    def per_1000(self) -> float:
        """Tracks needing review per 1,000 tracks (0.0 when nothing reviewed)."""
        if self.total_tracks == 0:
            return 0.0
        return round(self.needs_review / self.total_tracks * 1000, 1)

    @property
    def zero_intervention_pct(self) -> float:
        """Percent of playlists that needed no human intervention."""
        if self.total_playlists == 0:
            return 100.0
        return round(self.zero_intervention_playlists / self.total_playlists * 100, 1)


def review_kind(availability: str, reason_code: str) -> str | None:
    """Classify an outcome as ``ambiguous``, ``hard_fail``, or None (no review).

    A clean match, a healthy lock, or an acceptable substitute needs no review.
    """
    if availability in AMBIGUOUS_STATES:
        return AMBIGUOUS
    if availability in HARD_FAIL_STATES or reason_code in HARD_FAIL_REASONS:
        return HARD_FAIL
    return None


def needs_review(item: ReviewItem) -> bool:
    return review_kind(item.availability, item.reason_code) is not None


def cluster_reviews(items: list[ReviewItem]) -> list[ReviewCluster]:
    """Group review-needing items by (reason_code, normalized artist).

    Only items that actually need review are clustered. Clusters are returned
    largest-first so the reviewer tackles the highest-leverage group first;
    ties break by reason code then artist for deterministic output.
    """
    grouped: dict[tuple[str, str], ReviewCluster] = {}
    for item in items:
        if not needs_review(item):
            continue
        key = (item.reason_code, normalize_artist(item.artist or ""))
        cluster = grouped.get(key)
        if cluster is None:
            cluster = ReviewCluster(
                reason_code=item.reason_code, artist=item.artist or ""
            )
            grouped[key] = cluster
        cluster.items.append(item)
    return sorted(
        grouped.values(),
        key=lambda c: (-c.size, c.reason_code, normalize_artist(c.artist)),
    )


def compute_burden(items: list[ReviewItem], *, total_tracks: int) -> ReviewBurden:
    """Compute review-burden metrics from all outcomes.

    ``items`` is every reconcile outcome considered (matched or not);
    ``total_tracks`` is the denominator (typically ``len(items)``, passed
    explicitly so callers can scope it deliberately).
    """
    ambiguous = 0
    hard_fail = 0
    playlists: set[int] = set()
    dirty_playlists: set[int] = set()
    for item in items:
        if item.playlist_id is not None:
            playlists.add(item.playlist_id)
        kind = review_kind(item.availability, item.reason_code)
        if kind == AMBIGUOUS:
            ambiguous += 1
        elif kind == HARD_FAIL:
            hard_fail += 1
        if kind is not None and item.playlist_id is not None:
            dirty_playlists.add(item.playlist_id)
    total_playlists = len(playlists)
    zero_intervention = total_playlists - len(dirty_playlists)
    return ReviewBurden(
        total_tracks=total_tracks,
        ambiguous=ambiguous,
        hard_fail=hard_fail,
        total_playlists=total_playlists,
        zero_intervention_playlists=zero_intervention,
    )
