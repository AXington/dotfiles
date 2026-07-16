"""Curation engine: trim and analyze playlist contents."""

from dataclasses import dataclass, field

from tuneshift.curation.context import PlaylistContext
from tuneshift.curation.scoring import score_track_contribution
from tuneshift.sequencer.metadata import TrackMetadata


@dataclass
class CurationResult:
    keep: list[TrackMetadata]
    cut: list[TrackMetadata] = field(default_factory=list)
    reasoning: dict[int, str] = field(default_factory=dict)


def curate_trim(
    tracks: list[TrackMetadata],
    ctx: PlaylistContext,
    constraints: dict,
) -> CurationResult:
    """Trim playlist to meet constraints, cutting lowest-scoring tracks first."""
    # Score all tracks
    scored = []
    for track in tracks:
        scores = score_track_contribution(track, ctx, tracks)
        avg_score = sum(scores.values()) / len(scores) if scores else 0.5
        scored.append((track, avg_score))

    # Sort by score descending (best tracks first)
    scored.sort(key=lambda x: x[1], reverse=True)

    # Determine how many to keep based on constraints
    target_count = None
    hard_limit_count = len(tracks)  # default: keep all

    if "track_count" in constraints:
        tc = constraints["track_count"]
        target_count = tc.get("target", len(tracks))
        hard_limit_count = tc.get("hard_limit", len(tracks)) or len(tracks)

    if "duration" in constraints:
        dc = constraints["duration"]
        hard_limit_ms = (dc.get("hard_limit_minutes") or 999) * 60 * 1000
        # Greedily add tracks until duration exceeded
        duration_limited = []
        total_ms = 0
        for track, score in scored:
            track_ms = track.duration_ms or 0
            if total_ms + track_ms > hard_limit_ms:
                continue
            duration_limited.append((track, score))
            total_ms += track_ms
        scored = duration_limited
        hard_limit_count = min(hard_limit_count, len(scored))

    # Apply track count limit
    keep_count = min(hard_limit_count, len(scored))
    if target_count:
        keep_count = min(
            keep_count,
            target_count + (constraints.get("track_count", {}).get("tolerance", 0)),
        )

    keep = [t for t, _ in scored[:keep_count]]
    cut = [t for t in tracks if t not in keep]
    reasoning = {t.track_id: f"score={s:.2f}" for t, s in scored[:keep_count]}

    return CurationResult(keep=keep, cut=cut, reasoning=reasoning)


def curate_analyze(
    tracks: list[TrackMetadata],
    ctx: PlaylistContext,
) -> dict:
    """Analyze playlist coverage without making changes."""
    scores = {}
    for track in tracks:
        track_scores = score_track_contribution(track, ctx, tracks)
        scores[track.track_id] = {
            "title": track.title,
            "artist": track.artist,
            "dimensions": track_scores,
            "average": sum(track_scores.values()) / len(track_scores),
        }

    # Sort by average score
    ranked = sorted(scores.items(), key=lambda x: x[1]["average"])

    return {
        "scores": scores,
        "weakest": [{"track_id": tid, **data} for tid, data in ranked[:5]],
        "strongest": [{"track_id": tid, **data} for tid, data in ranked[-5:]],
    }
