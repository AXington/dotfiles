"""Platform metadata enrichment: fetch rich catalog data from Tidal."""

from __future__ import annotations

import json
import sys

from tuneshift.db import Database
from tuneshift.enrichment.retry import (
    PermanentAPIError,
    RetryConfig,
    RetryStats,
    is_permanent,
    retry_api_call,
)
from tuneshift.platforms.rate_limiter import RateLimiter

# tidalapi's exception taxonomy is unreliable for best-effort sub-fetches:
# it re-labels TooManyRequests (429) as "Album unavailable" and raises
# ObjectNotFound/AssetNotAvailable for delisted albums. Catch the common base
# so best-effort album/artist lookups never propagate. Fall back to a private
# subclass if tidalapi is unavailable so the except clause always compiles.
try:
    from tidalapi.exceptions import ObjectNotFound as _TidalObjectNotFound
    from tidalapi.exceptions import TidalAPIError as _TidalAPIError

    try:
        from tidalapi.exceptions import AssetNotAvailable as _TidalAssetNotAvailable
    except Exception:  # pragma: no cover - older tidalapi lacks this class
        _TidalAssetNotAvailable = _TidalObjectNotFound
except Exception:  # pragma: no cover - tidalapi is always present in prod

    class _TidalAPIError(Exception):
        pass

    class _TidalObjectNotFound(_TidalAPIError):
        pass

    class _TidalAssetNotAvailable(_TidalAPIError):
        pass


# A delisted album surfaces as either ObjectNotFound or AssetNotAvailable; both
# are the "stale_album" signal and must be caught before the best-effort swallow.
_STALE_ALBUM_ERRORS = (_TidalObjectNotFound, _TidalAssetNotAvailable)
_BEST_EFFORT_ERRORS = (OSError, RuntimeError, AttributeError, _TidalAPIError)

# Tidal rate limiter: adaptive mode, start at 2 req/sec, adjusts from headers
_tidal_limiter = RateLimiter(max_per_second=2.0, adaptive=True)


def enrich_playlist_from_tidal(
    db: Database,
    playlist_id: int,
    refresh: bool = False,
    stale_days: int = 30,  # noqa: ARG001 - public API parameter accepted for compatibility
    *,
    max_retries: int = 3,
    stats: RetryStats | None = None,
    client: object | None = None,
    quiet: bool = False,
) -> tuple[int, int, int]:
    """Fetch Tidal metadata for all tracks on a playlist.

    Retries transient failures (429, 5xx, timeouts) per track with backoff;
    permanent failures (track not found) are skipped immediately. Rate limits
    never abort the run.

    Returns (enriched_count, skipped_count, failed_count).
    """
    from tuneshift.platforms.tidal import TidalClient

    if client is None:
        client = TidalClient()
        if not client.load_session():
            print("Not logged in to Tidal. Run: tuneshift login tidal", file=sys.stderr)
            return 0, 0, 0

    config = RetryConfig(max_retries=max_retries)
    tracks = db.get_playlist_tracks(playlist_id)
    enriched = 0
    skipped = 0
    failed = 0

    for i, track in enumerate(tracks):
        if not quiet:
            print(
                f"  [{i + 1}/{len(tracks)}] {track.title} - {track.artist}...",
                end="",
                flush=True,
            )

        # Get platform mapping
        mapping = db.get_platform_mapping(track.id, "tidal")
        if not mapping or not mapping.platform_track_id:
            if not quiet:
                print(" skip (no Tidal mapping)")
            skipped += 1
            continue

        # Check if already fetched (and not stale)
        if not refresh:
            existing = db.get_track_platform_metadata(track.id, "tidal")
            if existing:
                if not quiet:
                    print(" skip (cached)")
                skipped += 1
                continue

        # Fetch from Tidal with retry. The rate limiter wait happens inside
        # the retried callable so each attempt is paced.
        def _do_fetch(track_id=mapping.platform_track_id):
            _tidal_limiter.wait()
            return _fetch_tidal_track_metadata(client, track_id)

        try:
            meta = retry_api_call(_do_fetch, config=config, stats=stats)
            if meta:
                db.upsert_track_platform_metadata(
                    track.id, "tidal", mapping.platform_track_id, **meta
                )
                # AC10/AC11: derive the atmos-available tag from the captured
                # metadata. The upsert alone never wrote tags -- so historically
                # even `enrich --catalog` printed "ATMOS" but never surfaced the
                # tag the spec requires.
                derive_tags(db, track.id)
                enriched += 1
                qualities = meta.get("audio_qualities", [])
                atmos = (
                    "ATMOS"
                    if "DOLBY_ATMOS"
                    in (qualities if isinstance(qualities, list) else [])
                    else ""
                )
                if not quiet:
                    print(f" ok ({meta.get('release_year', '?')}) {atmos}")
            else:
                if not quiet:
                    print(" no data")
                skipped += 1
        except PermanentAPIError as exc:
            if not quiet:
                print(f" skip (not found: {exc})")
            skipped += 1
        except Exception as exc:
            if is_permanent(exc):
                if not quiet:
                    print(" skip (not found)")
                skipped += 1
            else:
                if not quiet:
                    print(f" failed after retries ({exc})")
                failed += 1

    return enriched, skipped, failed


def enrich_all_playlists(
    db: Database,
    refresh: bool = False,
    stale_days: int = 30,
    *,
    max_retries: int = 3,
    dry_run: bool = False,
) -> int:
    """Enrich Tidal platform metadata for every playlist sequentially.

    Rate limits cause wait-and-retry within each track's timeout budget; the
    run never aborts entirely. Prints a summary at the end.

    Returns a process exit code (0 on success).
    """
    from tuneshift.platforms.tidal import TidalClient

    playlists = db.list_playlists()

    if dry_run:
        total_tracks = 0
        to_fetch = 0
        for playlist in playlists:
            tracks = db.get_playlist_tracks(playlist.id)
            total_tracks += len(tracks)
            for track in tracks:
                mapping = db.get_platform_mapping(track.id, "tidal")
                if not mapping or not mapping.platform_track_id:
                    continue
                if not refresh and db.get_track_platform_metadata(track.id, "tidal"):
                    continue
                to_fetch += 1
        print(f"Dry run: {len(playlists)} playlists, {total_tracks} tracks total.")
        print(
            f"Would fetch metadata for {to_fetch} tracks (~{to_fetch} Tidal API calls)."
        )
        return 0

    client = TidalClient()
    if not client.load_session():
        print("Not logged in to Tidal. Run: tuneshift login tidal", file=sys.stderr)
        return 1

    stats = RetryStats()
    total_enriched = 0
    total_skipped = 0
    total_failed = 0

    for playlist in playlists:
        print(f"\n{playlist.name}:")
        enriched, skipped, failed = enrich_playlist_from_tidal(
            db,
            playlist.id,
            refresh=refresh,
            stale_days=stale_days,
            max_retries=max_retries,
            stats=stats,
            client=client,
        )
        total_enriched += enriched
        total_skipped += skipped
        total_failed += failed

    print("\n" + "=" * 50)
    print(
        f"Enriched {total_enriched} tracks, "
        f"skipped {total_skipped} (cached/no mapping), "
        f"failed {total_failed} (retries exhausted)"
    )
    print(f"Rate limit handling: {stats.summary()}")
    return 0


def fetch_track_report(client, platform_track_id: str) -> dict:
    """Fetch a track from Tidal and return a structured validation report.

    Used by both enrichment (for the metadata payload) and the doctor scanner
    (for issue classification). The primary track fetch propagates transient
    errors (429, 5xx, timeouts) and permanent errors (ObjectNotFound) so the
    caller's retry logic can react. Secondary album/artist lookups are
    best-effort: a delisted album (ObjectNotFound) is reported as
    ``album_stale``; other album/artist errors are swallowed.

    Returns a dict with keys:
        available (bool), duration_seconds (int|None), title (str),
        album_id (str|None), album_stale (bool), metadata (dict|None).

    Raises PermanentAPIError for an unparseable id; propagates tidalapi
    ObjectNotFound / TooManyRequests / network errors from the track fetch.
    """
    try:
        track_id_int = int(platform_track_id)
    except (ValueError, TypeError) as exc:
        raise PermanentAPIError(f"Invalid Tidal track id: {platform_track_id}") from exc

    # Primary fetch: let transient/permanent errors propagate to retry_api_call
    track = client._session.track(track_id_int)
    if not track:
        return {
            "available": False,
            "duration_seconds": None,
            "title": "",
            "album_id": None,
            "album_stale": False,
            "metadata": None,
        }

    # Audio qualities
    audio_qualities = []
    if hasattr(track, "audio_quality"):
        audio_qualities.append(track.audio_quality)
    if hasattr(track, "audio_modes") and track.audio_modes:
        audio_qualities.extend(track.audio_modes)

    # Album info (need full album for release date) - best effort, but a
    # delisted album (ObjectNotFound) is a reportable "stale_album" signal.
    album = track.album if hasattr(track, "album") else None
    release_year = None
    release_date = None
    album_name = None
    album_id = None
    album_stale = False
    album_type = None
    if album:
        album_name = album.name
        album_id = str(album.id) if getattr(album, "id", None) is not None else None
        try:
            full_album = client._session.album(album.id)
            if full_album.release_date:
                release_date = str(full_album.release_date.date())
                release_year = full_album.release_date.year
            raw_type = getattr(full_album, "type", None)
            album_type = str(raw_type).lower() if raw_type else None
        except _STALE_ALBUM_ERRORS:
            album_stale = True
        except _BEST_EFFORT_ERRORS:
            pass

    # Artist genres (fetch from artist if available) - best effort
    genres = []
    if hasattr(track, "artist") and track.artist:
        try:
            artist_obj = client._session.artist(track.artist.id)
            if hasattr(artist_obj, "roles") and artist_obj.roles:
                genres = [
                    r.category for r in artist_obj.roles if hasattr(r, "category")
                ]
        except _BEST_EFFORT_ERRORS:
            pass

    duration_s = getattr(track, "duration", None)
    metadata = {
        "release_year": release_year,
        "release_date": release_date,
        "genres": genres,
        "audio_qualities": audio_qualities,
        "album_name": album_name,
        "album_type": album_type,
        "explicit": getattr(track, "explicit", None),
        "duration_ms": duration_s * 1000 if duration_s else None,
        "popularity": getattr(track, "popularity", None),
        "raw_metadata": json.dumps(
            {
                "id": platform_track_id,
                "audio_quality": getattr(track, "audio_quality", None),
                "audio_modes": getattr(track, "audio_modes", None),
            }
        ),
    }

    return {
        "available": bool(getattr(track, "available", True)),
        "duration_seconds": int(duration_s) if duration_s else None,
        "title": getattr(track, "name", "") or "",
        "album_id": album_id,
        "album_stale": album_stale,
        "metadata": metadata,
    }


def _fetch_tidal_track_metadata(client, platform_track_id: str) -> dict | None:
    """Fetch just the metadata payload for a track (enrichment path).

    Thin wrapper over :func:`fetch_track_report` preserving the historical
    return contract (metadata dict, or None when the track is missing).
    """
    report = fetch_track_report(client, platform_track_id)
    return report["metadata"]


def enrich_track_from_tidal(
    db: Database,
    track_id: int,
    platform_track_id: str,
    *,
    client,
    refresh: bool = False,
) -> list[str]:
    """Capture Atmos/catalog metadata for ONE Tidal-mapped track (AC10/AC11).

    The per-track counterpart to :func:`enrich_playlist_from_tidal` (which is
    playlist-scoped and O(n) per call -- wrong for a per-mapping hook, per the
    review synthesis S1). Fetches the track report, upserts the platform
    metadata, then calls :func:`derive_tags` so the ``atmos-available`` tag is
    written automatically instead of only via the manual ``tag derive`` /
    ``enrich --catalog`` commands.

    Fill-only semantics: skips the network fetch when metadata is already cached
    unless ``refresh=True``, but ALWAYS (re)derives tags so a track that gained
    metadata elsewhere still gets tagged. Returns the derived tags. Raises only
    on a truly unexpected error; the caller treats this as best-effort.
    """
    if refresh or db.get_track_platform_metadata(track_id, "tidal") is None:
        _tidal_limiter.wait()
        meta = _fetch_tidal_track_metadata(client, platform_track_id)
        if meta:
            db.upsert_track_platform_metadata(
                track_id, "tidal", platform_track_id, **meta
            )
    return derive_tags(db, track_id)


def derive_tags(db: Database, track_id: int) -> list[str]:
    """Derive tags from platform metadata for a track."""
    meta = db.get_track_platform_metadata(track_id, "tidal")
    if not meta:
        return []

    new_tags: list[str] = []

    # Atmos
    qualities = meta.get("audio_qualities", [])
    if isinstance(qualities, list) and "DOLBY_ATMOS" in qualities:
        new_tags.append("atmos-available")

    # Decade
    year = meta.get("release_year")
    if year:
        decade = (year // 10) * 10
        new_tags.append(f"{decade}s")

    # Album type
    album_type = meta.get("album_type")
    if album_type and album_type != "album":
        new_tags.append(album_type)

    # Explicit
    if meta.get("explicit"):
        new_tags.append("explicit")

    # Apply tags
    for tag in new_tags:
        db.add_track_tag(track_id, tag, source="derived")

    return new_tags


def analyze_playlist(db: Database, playlist_id: int) -> dict:
    """Compute playlist analysis from platform metadata."""
    tracks = db.get_playlist_tracks(playlist_id)
    total = len(tracks)

    years: list[int] = []
    genres_counter: dict[str, int] = {}
    atmos_count = 0
    lossless_count = 0
    tag_counter: dict[str, int] = {}

    for track in tracks:
        meta = db.get_track_platform_metadata(track.id, "tidal")
        if meta:
            if meta.get("release_year"):
                years.append(meta["release_year"])
            qualities = meta.get("audio_qualities", [])
            if isinstance(qualities, list):
                if "DOLBY_ATMOS" in qualities:
                    atmos_count += 1
                if any(
                    q in qualities for q in ("LOSSLESS", "HI_RES_LOSSLESS", "HI_RES")
                ):
                    lossless_count += 1
            genres = meta.get("genres", [])
            if isinstance(genres, list):
                for g in genres:
                    genres_counter[g] = genres_counter.get(g, 0) + 1

        # Track tags
        tags = db.get_track_tags(track.id)
        for t in tags:
            tag_counter[t] = tag_counter.get(t, 0) + 1

    # Era breakdown
    era = ""
    if years:
        min_year, max_year = min(years), max(years)
        if max_year - min_year <= 5:
            era = f"{min_year}-{max_year}"
        else:
            from collections import Counter

            decade_counts = Counter((y // 10) * 10 for y in years)
            era = ", ".join(
                f"{d}s ({c}/{len(years)})" for d, c in decade_counts.most_common(3)
            )

    # Top genres
    top_genres = sorted(genres_counter.items(), key=lambda x: -x[1])[:5]

    # Top tags
    top_tags = sorted(tag_counter.items(), key=lambda x: -x[1])[:10]

    return {
        "total_tracks": total,
        "enriched_tracks": len(years),
        "era": era,
        "top_genres": top_genres,
        "atmos_pct": (atmos_count / total * 100) if total else 0,
        "lossless_pct": (lossless_count / total * 100) if total else 0,
        "top_tags": top_tags,
    }
