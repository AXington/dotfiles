"""Track identity resolution for TuneShift.

Public API:
    resolve_track(store, track_id, ...) -> ResolutionResult
    resolve_playlist(store, playlist_id, ...) -> list[ResolutionResult]
"""

from __future__ import annotations

import signal
from collections.abc import Callable
from typing import TYPE_CHECKING

from tuneshift.identity.models import ResolutionResult, ResolutionStatus, TrackInput
from tuneshift.identity.resolver import IdentityStore, ResolverConfig, TrackResolver

if TYPE_CHECKING:
    from tuneshift.identity.sources.discogs import DiscogsSource
    from tuneshift.identity.sources.musicbrainz import MusicBrainzSource
    from tuneshift.platforms.rate_limiter import RateLimiter


def _track_to_input(track) -> TrackInput:
    """Build TrackInput from a TuneShift Track model."""
    return TrackInput(
        title=track.title,
        artist=track.artist,
        album=track.album,
        isrc=track.isrc,
        duration_ms=track.duration_seconds * 1000 if track.duration_seconds else None,
    )


def resolve_track(
    store: IdentityStore,
    track_id: int,
    *,
    upgrade: bool = False,
    force: bool = False,
    musicbrainz: MusicBrainzSource | None = None,
    discogs: DiscogsSource | None = None,
    rate_limiters: dict[str, RateLimiter] | None = None,
) -> ResolutionResult:
    """Resolve a single track by its database ID."""
    track = store.get_track(track_id)
    if track is None:
        return ResolutionResult(
            track_id=track_id,
            status=ResolutionStatus.FAILED,
            error=f"Track {track_id} not found in database",
        )

    config = ResolverConfig(upgrade_mode=upgrade, force=force)
    resolver = TrackResolver(
        store=store,
        musicbrainz=musicbrainz,
        discogs=discogs,
        config=config,
        rate_limiters=rate_limiters,
    )
    track_input = _track_to_input(track)
    return resolver.resolve(track_id=track_id, track=track_input)


def resolve_playlist(
    store: IdentityStore,
    playlist_id: int,
    *,
    upgrade: bool = False,
    force: bool = False,
    musicbrainz: MusicBrainzSource | None = None,
    discogs: DiscogsSource | None = None,
    rate_limiters: dict[str, RateLimiter] | None = None,
    on_progress: Callable[[int, int, int, ResolutionResult], None] | None = None,
) -> list[ResolutionResult]:
    """Resolve all tracks in a playlist sequentially.

    Handles SIGINT gracefully: returns results resolved so far.
    """
    tracks = store.find_tracks_by_playlist(playlist_id)
    results: list[ResolutionResult] = []
    interrupted = False

    def _handle_sigint(signum, frame):
        nonlocal interrupted
        interrupted = True

    old_handler = signal.signal(signal.SIGINT, _handle_sigint)

    try:
        config = ResolverConfig(upgrade_mode=upgrade, force=force)
        resolver = TrackResolver(
            store=store,
            musicbrainz=musicbrainz,
            discogs=discogs,
            config=config,
            rate_limiters=rate_limiters,
        )

        for i, track in enumerate(tracks):
            if interrupted:
                break

            track_input = _track_to_input(track)
            result = resolver.resolve(track_id=track.id, track=track_input)
            results.append(result)

            if on_progress:
                on_progress(track.id, i, len(tracks), result)

    finally:
        signal.signal(signal.SIGINT, old_handler)

    return results
