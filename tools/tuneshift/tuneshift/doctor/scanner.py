"""Doctor scanner: classify Tidal mapping issues for tracks.

The scanner validates each mapped track against the Tidal API and classifies
problems into the issue types defined in :mod:`tuneshift.doctor.plan`. It reuses
the enrichment rate limiter and retry infrastructure so a full-DB scan paces
itself and never aborts on transient rate limits.

The scan is strictly read-only against the database: it fetches fresh data from
Tidal for diagnosis but never writes. Metadata caching is the job of
``tuneshift enrich``, not the doctor.
"""

from __future__ import annotations

import re
import sys

from tuneshift.db import Database, normalize_artist, normalize_title
from tuneshift.doctor.plan import PlanItem
from tuneshift.enrichment import platform_metadata
from tuneshift.enrichment.retry import (
    PermanentAPIError,
    RetryConfig,
    RetryStats,
    is_permanent,
)
from tuneshift.enrichment.retry import retry_api_call as _retry_api_call

PLATFORM = "tidal"


def detect_orphaned(db: Database) -> list:
    """Tracks invisible to every review surface (BUG-3 / FEAT-3).

    Thin, DB-only wrapper over :meth:`Database.find_orphaned_tracks`: a track
    with no tier, no quarantine, no queue entry, and no platform mapping never
    resolves and never surfaces for review. Read-only; requires no platform login.
    """
    return db.find_orphaned_tracks()


# Duration delta (seconds) above which a mapped track is flagged as a possible
# wrong version. 15s tolerates remaster/master differences without noise.
DURATION_TOLERANCE_S = 15

# Keywords that indicate a non-canonical edition. A mapped title containing one
# of these when the canonical title does not is a version-mismatch signal.
_VERSION_KEYWORDS = (
    "remix",
    "live",
    "extended",
    "instrumental",
    "acoustic",
    "demo",
    "radio edit",
    "single version",
    "edit",
    "reprise",
    "remaster",
    "re-master",
    "mix)",
    "version)",
)
_PAREN_RE = re.compile(r"[\(\[].*?[\)\]]")


def _canonical_key(title: str, artist: str) -> tuple[str, str]:
    return (normalize_title(title) or "", normalize_artist(artist))


def _has_extra_version_keyword(canonical_title: str, platform_title: str) -> bool:
    """True if the platform title carries an edition keyword the canonical lacks."""
    if not platform_title:
        return False
    plat = platform_title.lower()
    canon = (canonical_title or "").lower()
    for kw in _VERSION_KEYWORDS:
        if kw in plat and kw not in canon:
            return True
    return False


def detect_duplicates(
    db: Database,  # noqa: ARG001 - uniform detector signature
    tracks: list,
    playlist_name: str,
    next_id: int,
) -> tuple[list[PlanItem], int]:
    """Detect canonical tracks that collapse to the same normalized identity.

    Groups the given tracks by (norm_artist, norm_title); any group with more
    than one distinct track row yields a single ``duplicate`` plan item whose
    ``merge_track_ids`` lists the rows to fold into the primary.
    """
    groups: dict[tuple[str, str], list] = {}
    for track in tracks:
        if track.id is None:
            continue
        key = _canonical_key(track.title, track.artist)
        groups.setdefault(key, []).append(track)

    items: list[PlanItem] = []
    for group in groups.values():
        if len(group) < 2:
            continue
        # Primary = oldest row (lowest id is a stable proxy for oldest).
        ordered = sorted(group, key=lambda t: t.id)
        primary = ordered[0]
        merge_ids = [t.id for t in ordered[1:]]
        items.append(
            PlanItem(
                id=next_id,
                track_id=primary.id,
                playlist=playlist_name,
                title=primary.title,
                artist=primary.artist,
                issue="duplicate",
                keep_track_id=primary.id,
                merge_track_ids=merge_ids,
                confidence=100,
                resolution="auto",
                note=f"{len(group)} rows share identity; merging {len(merge_ids)}",
            )
        )
        next_id += 1
    return items, next_id


def _scan_one_track(
    db: Database,
    client,
    track,
    playlist_name: str,
    next_id: int,
    *,
    config: RetryConfig,
    stats: RetryStats,
) -> PlanItem | None:
    """Validate a single mapped track; return a PlanItem if an issue is found.

    Read-only: fetches from Tidal but never writes to the database.
    """
    mapping = db.get_platform_mapping(track.id, PLATFORM)
    if not mapping or not mapping.platform_track_id:
        # Unmapped tracks are handled by detect_unmapped (local, no API cost).
        return None

    def _fetch(pid=mapping.platform_track_id):
        platform_metadata._tidal_limiter.wait()
        return platform_metadata.fetch_track_report(client, pid)

    try:
        report = _retry_api_call(_fetch, config=config, stats=stats)
    except PermanentAPIError:
        return _unavailable_item(next_id, track, playlist_name, mapping)
    except Exception as exc:
        if is_permanent(exc):
            return _unavailable_item(next_id, track, playlist_name, mapping)
        # Transient error survived all retries: report as unavailable-unknown so
        # the operator sees it, rather than silently dropping the track.
        return PlanItem(
            id=next_id,
            track_id=track.id,
            playlist=playlist_name,
            title=track.title,
            artist=track.artist,
            issue="unavailable",
            current_platform_id=mapping.platform_track_id,
            resolution="manual",
            note=f"could not verify (transient error: {exc})",
        )

    if not report.get("available", True):
        return _unavailable_item(next_id, track, playlist_name, mapping)

    # stale_album takes priority over version mismatch: it's a concrete
    # delisting signal, and the fix is metadata-only.
    if report.get("album_stale"):
        return PlanItem(
            id=next_id,
            track_id=track.id,
            playlist=playlist_name,
            title=track.title,
            artist=track.artist,
            issue="stale_album",
            current_platform_id=mapping.platform_track_id,
            note="album delisted; metadata-only fix",
        )

    if _is_version_mismatch(track, report, mapping):
        return PlanItem(
            id=next_id,
            track_id=track.id,
            playlist=playlist_name,
            title=track.title,
            artist=track.artist,
            issue="version_mismatch",
            current_platform_id=mapping.platform_track_id,
            note=_version_note(track, report),
        )

    return None


def _is_version_mismatch(track, report: dict, mapping) -> bool:
    plat_title = report.get("title") or (mapping.platform_title or "")
    if _has_extra_version_keyword(track.title, plat_title):
        return True
    db_dur = getattr(track, "duration_seconds", None)
    plat_dur = report.get("duration_seconds")
    if db_dur and plat_dur and abs(int(db_dur) - int(plat_dur)) > DURATION_TOLERANCE_S:
        return True
    return False


def _version_note(track, report: dict) -> str:
    db_dur = getattr(track, "duration_seconds", None)
    plat_dur = report.get("duration_seconds")
    if db_dur and plat_dur:
        return f"duration {plat_dur}s vs expected {db_dur}s"
    plat_title = report.get("title") or ""
    return f"mapped to edition: {plat_title}"


def _unavailable_item(next_id: int, track, playlist_name: str, mapping) -> PlanItem:
    return PlanItem(
        id=next_id,
        track_id=track.id,
        playlist=playlist_name,
        title=track.title,
        artist=track.artist,
        issue="unavailable",
        current_platform_id=mapping.platform_track_id,
        note="track not found on Tidal",
    )


def scan_tracks(
    db: Database,
    client,
    tracks: list,
    playlist_name: str,
    *,
    max_retries: int = 3,
    stats: RetryStats | None = None,
    quiet: bool = False,
    start_id: int = 1,
) -> tuple[list[PlanItem], int]:
    """Scan a list of tracks; return (plan_items, next_id).

    Runs local detectors (unmapped, duplicate) plus a per-track API validation
    (unavailable, stale_album, version_mismatch). Strictly read-only against
    the database.
    """
    config = RetryConfig(max_retries=max_retries)
    stats = stats or RetryStats()
    items: list[PlanItem] = []
    next_id = start_id

    # Duplicates first: a merge subsumes its non-keep rows, so those tracks must
    # not also receive independent unmapped/version/unavailable items (applying
    # the merge deletes them, which would strand any later per-track fix).
    dupes, next_id = detect_duplicates(db, tracks, playlist_name, next_id)
    items.extend(dupes)
    merged_ids = {tid for item in dupes for tid in item.merge_track_ids}
    remaining = [t for t in tracks if t.id not in merged_ids]

    unmapped, next_id = detect_unmapped(db, remaining, playlist_name, next_id)
    items.extend(unmapped)

    total = len(remaining)
    for i, track in enumerate(remaining):
        if not quiet:
            print(
                f"  [{i + 1}/{total}] {track.title} - {track.artist}...",
                end="\r",
                file=sys.stderr,
                flush=True,
            )
        found = _scan_one_track(
            db,
            client,
            track,
            playlist_name,
            next_id,
            config=config,
            stats=stats,
        )
        if found is not None:
            items.append(found)
            next_id += 1

    if not quiet:
        print(file=sys.stderr)
    return items, next_id


def detect_unmapped(
    db: Database, tracks: list, playlist_name: str, next_id: int
) -> tuple[list[PlanItem], int]:
    """Detect tracks with no usable Tidal mapping (local, no API cost)."""
    if not tracks:
        return [], next_id
    mappings = db.get_platform_mappings_for_tracks(
        [t.id for t in tracks if t.id is not None], PLATFORM
    )
    items: list[PlanItem] = []
    for track in tracks:
        if track.id is None:
            continue
        mapping = mappings.get(track.id)
        if mapping and mapping.platform_track_id:
            continue
        items.append(
            PlanItem(
                id=next_id,
                track_id=track.id,
                playlist=playlist_name,
                title=track.title,
                artist=track.artist,
                issue="unmapped",
                note="no Tidal mapping",
            )
        )
        next_id += 1
    return items, next_id
