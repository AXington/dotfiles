"""Data models for tuneshift."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Track:
    """A canonical track in the library."""

    id: int | None = None
    title: str = ""
    artist: str = ""
    album: str | None = None
    duration_seconds: int | None = None
    isrc: str | None = None
    energy: float | None = None
    valence: float | None = None
    tempo: float | None = None
    key: str | None = None
    themes: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    album_artist: str | None = None
    album_type: str | None = None
    label: str | None = None
    recording_date: str | None = None
    release_date: str | None = None
    remaster_year: int | None = None
    audio_modes: list[str] = field(default_factory=list)
    audio_quality: str | None = None
    tidal_version: str | None = None
    language: str | None = None
    composer: str | None = None
    mb_work_id: str | None = None
    availability: str | None = None
    quarantine_state: str | None = None
    quarantine_reason: str | None = None
    field_provenance: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlatformMapping:
    """Maps a canonical track to a platform-specific ID."""

    track_id: int
    platform: str
    platform_track_id: str
    platform_title: str | None = None
    platform_artist: str | None = None
    platform_album: str | None = None
    match_score: int | None = None
    is_divergent: bool = False
    divergence_note: str | None = None
    status: str = "matched"
    user_approved: bool = False
    fingerprint: str | None = None


@dataclass
class Playlist:
    """A canonical playlist."""

    id: int | None = None
    name: str = ""
    description: str | None = None
    auto_reorder: bool = False
    reorder_arc: str = "wave"
    tidal_folder_id: str | None = None


@dataclass
class PlatformPlaylist:
    """Links a canonical playlist to a platform."""

    playlist_id: int
    platform: str
    platform_playlist_id: str
    last_synced_at: str | None = None


@dataclass
class TrackResult:
    """A search result from any platform.

    ``available`` and ``tier_restricted`` carry the availability signal the
    platform exposes (Spotify ``is_playable``/``available_markets``; Tidal
    ``allowStreaming``/``premium_streaming_only``). They are optional so call
    sites that don't need availability are unaffected: ``available=None`` means
    "unknown", never "blocked", only an explicit ``False`` denotes blocked.
    """

    platform_id: str
    title: str
    artist: str
    album: str
    duration_seconds: int | None = None
    isrc: str | None = None
    available: bool | None = None
    tier_restricted: bool = False
    audio_modes: list[str] | None = None
    audio_quality: str | None = None
    explicit: bool | None = None
    tidal_version: str | None = None
    media_metadata_tags: list[str] | None = None
    album_artist: str | None = None
    album_type: str | None = None
    recording_date: str | None = None
    release_date: str | None = None
    remaster_year: int | None = None
    language: str | None = None
    composer: str | None = None
    mb_work_id: str | None = None


# Fields snapshotted when persisting a search result as a reusable candidate
# (everything version selection can score on), excluding ``platform_id`` which
# is stored as its own column. Used by both the resolver (library `resolve`) and
# reconcile so the capture/reconstruct round-trip stays in one place.
_CANDIDATE_METADATA_FIELDS = (
    "title",
    "artist",
    "album",
    "duration_seconds",
    "isrc",
    "available",
    "tier_restricted",
    "audio_modes",
    "audio_quality",
    "explicit",
    "tidal_version",
    "media_metadata_tags",
    "album_artist",
    "album_type",
    "recording_date",
    "release_date",
    "remaster_year",
    "language",
    "composer",
    "mb_work_id",
)


def capture_candidate_metadata(result: "TrackResult") -> dict[str, Any]:
    """Snapshot every version-selection-relevant field off a search result.

    Persisted as ``track_candidates.captured_metadata`` so a later scoring pass
    can reconstruct the candidate without another live search (AC-X3/AC-P4).
    ``platform_id`` is intentionally excluded, it is stored in its own column.
    """
    return {name: getattr(result, name) for name in _CANDIDATE_METADATA_FIELDS}


def trackresult_from_metadata(
    platform_id: str, metadata: dict[str, Any] | None
) -> "TrackResult":
    """Rebuild a :class:`TrackResult` from persisted candidate metadata.

    Inverse of :func:`capture_candidate_metadata`. Ignores unknown keys (e.g. the
    ``match_score`` the resolver attaches) so extra annotations never break
    reconstruction, and tolerates a partial snapshot (missing fields fall back to
    the dataclass defaults).
    """
    payload = metadata or {}
    kwargs = {
        name: payload[name] for name in _CANDIDATE_METADATA_FIELDS if name in payload
    }
    return TrackResult(platform_id=platform_id, **kwargs)


@dataclass(frozen=True)
class EffectiveLock:
    """The resolved two-level identity lock for a (track, platform, playlist)
    (AC-L1/L4). ``scope`` is ``"playlist"`` when a per-playlist override applies,
    else ``"global"`` for the library-wide default lock. Carries the composite
    identity (platform-id + ISRC + fingerprint) so callers can build an
    :class:`~tuneshift.matching.selection.IdentityLock` that survives a platform
    re-ID.
    """

    platform_track_id: str
    scope: str
    isrc: str | None = None
    fingerprint: str | None = None
    status: str = "matched"
    is_divergent: bool = False
    divergence_note: str | None = None
    match_score: int | None = None


@dataclass
class PlaylistInfo:
    """Playlist metadata from any platform."""

    platform_id: str
    name: str
    num_tracks: int


@dataclass
class AlbumResult:
    """An album search result from any platform."""

    platform_id: str
    title: str
    artist: str
    track_count: int = 0
    release_year: int | None = None


@dataclass
class ArtistResult:
    """An artist search result from any platform.

    Enrichment fields (``popularity``, ``genres``, ``followers``) are optional:
    platforms populate what they expose and leave the rest ``None``/empty. The
    artist scorer treats missing enrichment as neutral, never as a mismatch.
    """

    platform_id: str
    name: str
    popularity: int | None = None
    genres: list[str] = field(default_factory=list)
    followers: int | None = None


@dataclass
class PlaylistPin:
    """A pinned track position or adjacency constraint."""

    playlist_id: int
    track_id: int
    pin_type: str  # "opener", "closer", "anchor", "position"
    group_id: str | None = None  # for adjacency groups
    group_order: int | None = (
        None  # position within group, or target index for "position" pins
    )


@dataclass
class Artist:
    """A normalized artist entity in the library."""

    id: int | None = None
    name: str = ""
    norm_name: str = ""
    sort_name: str | None = None
    bio: str | None = None
    identity: dict[str, Any] | None = None
    tags: list[str] = field(default_factory=list)
    identity_confidence: str = "unconfirmed"
    genres: list[str] = field(default_factory=list)
    origin: str | None = None
    active_start: int | None = None
    active_end: int | None = None
    mb_artist_id: str | None = None
    tidal_artist_id: int | None = None
    spotify_artist_uri: str | None = None
    lastfm_url: str | None = None
    wikipedia_url: str | None = None
    enrichment_sources: list[str] = field(default_factory=list)
    verified: bool = False
    enriched_at: str | None = None
    verified_at: str | None = None


@dataclass
class Album:
    """A normalized album entity in the library."""

    id: int | None = None
    title: str = ""
    norm_title: str = ""
    artist_id: int | None = None
    release_date: str | None = None
    release_type: str = "album"
    edition: str = "original"
    genres: list[str] = field(default_factory=list)
    mb_release_group_id: str | None = None
    tidal_album_id: int | None = None
    spotify_album_uri: str | None = None
    enriched_at: str | None = None


@dataclass
class BannedArtist:
    """An artist on the global ban list."""

    id: int | None = None
    name: str = ""
    norm_name: str = ""
    reason: str | None = None
