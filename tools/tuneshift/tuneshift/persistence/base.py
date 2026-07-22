"""Persistence foundation: connection lifecycle, schema constants, and
shared row/entity helpers used by every persistence mixin.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from tuneshift.matching.normalize import _WHITESPACE_RE
from tuneshift.models import (
    Album,
    Artist,
    Playlist,
    Track,
)

_SCHEMA_VERSION = 22

_TRACK_EDITABLE_COLUMNS = frozenset({"title", "artist", "album"})

_TRACK_FIRST_CLASS_COLUMNS = frozenset(
    {
        "album_artist",
        "album_type",
        "label",
        "recording_date",
        "release_date",
        "remaster_year",
        "audio_modes",
        "audio_quality",
        "tidal_version",
        "language",
        "composer",
        "availability",
        "quarantine_state",
        "quarantine_reason",
        "energy",
        "valence",
    }
)

_TRACK_JSON_FIELD_COLUMNS = frozenset({"audio_modes"})

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    artist TEXT NOT NULL,
    album TEXT,
    norm_title TEXT NOT NULL,
    norm_artist TEXT NOT NULL,
    norm_album TEXT,
    duration_seconds INTEGER,
    isrc TEXT,
    energy REAL,
    valence REAL,
    tempo REAL,
    key TEXT,
    themes TEXT,
    metadata JSON,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    mb_recording_id TEXT,
    mb_release_group_id TEXT,
    confidence_tier TEXT,
    confidence_score REAL,
    resolved_at TEXT,
    artist_id INTEGER REFERENCES artists(id),
    album_id INTEGER REFERENCES albums(id),
    album_artist TEXT,
    album_type TEXT,
    label TEXT,
    recording_date TEXT,
    release_date TEXT,
    remaster_year INTEGER,
    audio_modes TEXT,
    audio_quality TEXT,
    tidal_version TEXT,
    language TEXT,
    composer TEXT,
    availability TEXT,
    quarantine_state TEXT,
    quarantine_reason TEXT,
    field_provenance TEXT
);

CREATE TABLE IF NOT EXISTS platform_tracks (
    id INTEGER PRIMARY KEY,
    track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    platform_track_id TEXT NOT NULL,
    platform_title TEXT,
    platform_artist TEXT,
    platform_album TEXT,
    match_score INTEGER,
    is_divergent INTEGER NOT NULL DEFAULT 0,
    divergence_note TEXT,
    status TEXT NOT NULL DEFAULT 'matched',
    user_approved INTEGER NOT NULL DEFAULT 0,
    unavailable INTEGER NOT NULL DEFAULT 0,
    fingerprint TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(track_id, platform)
);

CREATE TABLE IF NOT EXISTS playlists (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    narrative TEXT,
    collection TEXT,
    goal TEXT,
    playlist_type TEXT,
    weights TEXT,
    mood_profile TEXT,
    curation_constraints TEXT,
    preferences TEXT,
    auto_reorder INTEGER NOT NULL DEFAULT 0,
    reorder_arc TEXT NOT NULL DEFAULT 'wave',
    tidal_folder_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS playlist_tracks (
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    version_override TEXT,
    PRIMARY KEY (playlist_id, position)
);

CREATE TABLE IF NOT EXISTS platform_playlists (
    id INTEGER PRIMARY KEY,
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    platform_playlist_id TEXT NOT NULL,
    last_synced_at TEXT,
    UNIQUE(playlist_id, platform)
);

CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY,
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    action TEXT NOT NULL,
    tracks_added INTEGER DEFAULT 0,
    tracks_removed INTEGER DEFAULT 0,
    tracks_reordered INTEGER DEFAULT 0,
    tracks_unavailable INTEGER DEFAULT 0,
    divergences_flagged INTEGER DEFAULT 0,
    timestamp TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY,
    track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    confidence REAL NOT NULL,
    raw_data TEXT,
    is_current INTEGER NOT NULL DEFAULT 1,
    superseded_by INTEGER REFERENCES evidence(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS playlist_pins (
    id INTEGER PRIMARY KEY,
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    pin_type TEXT NOT NULL,
    group_id TEXT,
    group_order INTEGER,
    UNIQUE(playlist_id, track_id)
);

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS concept_rule_acceptances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    rule_key TEXT NOT NULL,
    rule_text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(playlist_id, track_id, rule_key)
);

CREATE TABLE IF NOT EXISTS artists (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    norm_name TEXT NOT NULL,
    sort_name TEXT,
    bio TEXT,
    identity JSON,
    tags JSON DEFAULT '[]',
    identity_confidence TEXT DEFAULT 'unconfirmed',
    genres JSON DEFAULT '[]',
    origin TEXT,
    active_start INTEGER,
    active_end INTEGER,
    mb_artist_id TEXT,
    tidal_artist_id INTEGER,
    spotify_artist_uri TEXT,
    lastfm_url TEXT,
    wikipedia_url TEXT,
    enrichment_sources JSON DEFAULT '[]',
    verified INTEGER DEFAULT 0,
    enriched_at TEXT,
    verified_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS albums (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    norm_title TEXT NOT NULL,
    artist_id INTEGER NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
    release_date TEXT,
    release_type TEXT DEFAULT 'album',
    edition TEXT DEFAULT 'original',
    genres JSON DEFAULT '[]',
    mb_release_group_id TEXT,
    tidal_album_id INTEGER,
    spotify_album_uri TEXT,
    enriched_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(norm_title, artist_id, edition)
);

CREATE INDEX IF NOT EXISTS idx_tracks_identity
    ON tracks(norm_title, norm_artist, norm_album);
CREATE INDEX IF NOT EXISTS idx_platform_tracks_lookup
    ON platform_tracks(track_id, platform);
CREATE INDEX IF NOT EXISTS idx_playlist_tracks_order
    ON playlist_tracks(playlist_id, position);
CREATE INDEX IF NOT EXISTS idx_tracks_isrc ON tracks(isrc) WHERE isrc IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_evidence_track ON evidence(track_id, is_current);
CREATE UNIQUE INDEX IF NOT EXISTS idx_artists_norm ON artists(norm_name);
CREATE INDEX IF NOT EXISTS idx_artists_mb ON artists(mb_artist_id) WHERE mb_artist_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_albums_artist ON albums(artist_id);

CREATE TABLE IF NOT EXISTS banned_artists (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    norm_name TEXT NOT NULL UNIQUE,
    reason TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS batch_history (
    id INTEGER PRIMARY KEY,
    playlist_id INTEGER NOT NULL,
    plan_json TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT (datetime('now')),
    reverted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_batch_history_playlist ON batch_history(playlist_id);

CREATE TABLE IF NOT EXISTS track_edits (
    id INTEGER PRIMARY KEY,
    track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    field TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    edited_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_track_edits_track ON track_edits(track_id);

CREATE TABLE IF NOT EXISTS collections (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS playlist_collections (
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    PRIMARY KEY (playlist_id, collection_id)
);

CREATE TABLE IF NOT EXISTS tidal_folders (
    id INTEGER PRIMARY KEY,
    tidal_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    parent_tidal_id TEXT,
    last_synced_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS track_platform_metadata (
    id INTEGER PRIMARY KEY,
    track_id INTEGER NOT NULL REFERENCES tracks(id),
    platform TEXT NOT NULL,
    platform_track_id TEXT NOT NULL,
    release_year INTEGER,
    release_date TEXT,
    genres TEXT,
    audio_qualities TEXT,
    album_name TEXT,
    album_type TEXT,
    explicit INTEGER,
    duration_ms INTEGER,
    popularity INTEGER,
    raw_metadata TEXT,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(track_id, platform)
);

CREATE TABLE IF NOT EXISTS track_tags (
    track_id INTEGER NOT NULL REFERENCES tracks(id),
    tag TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (track_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_track_tags_tag ON track_tags(tag);

CREATE TABLE IF NOT EXISTS match_audits (
    playlist_id INTEGER NOT NULL DEFAULT 0,
    track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    availability TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    audit_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (playlist_id, track_id, platform)
);

CREATE TABLE IF NOT EXISTS artist_aliases (
    class_id INTEGER NOT NULL,
    member TEXT NOT NULL,
    norm_member TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (class_id, member)
);
CREATE INDEX IF NOT EXISTS idx_artist_aliases_norm ON artist_aliases(norm_member);

CREATE TABLE IF NOT EXISTS resolution_queue (
    track_id INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
    state TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    transient_attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    last_error TEXT,
    enqueued_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_resolution_queue_state
    ON resolution_queue(state, next_attempt_at);

CREATE TABLE IF NOT EXISTS track_candidates (
    track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    platform_track_id TEXT NOT NULL,
    captured_metadata TEXT,
    discovery_rank INTEGER NOT NULL DEFAULT 0,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (track_id, platform, platform_track_id)
);

CREATE TABLE IF NOT EXISTS playlist_track_mappings (
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    platform_track_id TEXT NOT NULL,
    source TEXT,
    user_approved INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (playlist_id, track_id, platform)
);

CREATE TABLE IF NOT EXISTS playlist_track_prefs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_id INTEGER REFERENCES playlists(id) ON DELETE CASCADE,
    track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    criterion TEXT NOT NULL,
    strength TEXT NOT NULL,
    target TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
-- A preference is unique per (scope, criterion, target) so multiple targets on
-- one axis coexist (e.g. content avoid karaoke + content avoid instrumental).
-- COALESCE normalises the NULLable playlist_id (NULL = playlist-agnostic
-- per-track scope) and target so uniqueness is NULL-safe.
CREATE UNIQUE INDEX IF NOT EXISTS idx_playlist_track_prefs_scope
    ON playlist_track_prefs(
        COALESCE(playlist_id, -1), track_id, criterion, COALESCE(target, '')
    );

CREATE TABLE IF NOT EXISTS apply_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id TEXT NOT NULL,
    table_name TEXT NOT NULL,
    row_key TEXT NOT NULL,
    op TEXT NOT NULL,
    prior_value TEXT,
    new_value TEXT,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_apply_journal_plan
    ON apply_journal(plan_id, id);
"""

_REMIX_RE = re.compile(
    r"\s*\((?:remaster(?:ed)?|deluxe edition)[^)]*\)\s*", re.IGNORECASE
)


def normalize_title(value: str | None) -> str | None:
    """Normalize title-like text for the STORED, indexed identity key.

    Deliberately light and STABLE: lowercase, ``&``->``and``, strip
    remaster/deluxe-edition parens, collapse whitespace. It does NOT fold accents
    or strip feat/explicit -- that is the job of the transient comparison key
    (``matching.normalize_title``). Changing this function's output requires
    reindexing every stored ``norm_*`` value; the contract is pinned by
    tests/matching/test_normalizer_contracts.py.
    """
    if value is None:
        return None
    normalized = _REMIX_RE.sub(" ", value.strip().lower())
    normalized = normalized.replace("&", "and")
    normalized = _WHITESPACE_RE.sub(" ", normalized)
    return normalized.strip() or None


def normalize_artist(value: str) -> str:
    """Normalize artist text for the STORED, indexed identity key.

    Light and STABLE (see :func:`normalize_title`): lowercase, ``&``->``and``,
    collapse whitespace, strip a single leading "the ". Does NOT fold accents.
    """
    normalized = value.strip().lower().replace("&", "and")
    normalized = _WHITESPACE_RE.sub(" ", normalized)
    if normalized.startswith("the "):
        normalized = normalized[4:]
    return normalized.strip()


def normalize_ban_name(value: str) -> str:
    """Normalize artist name for ban list matching.

    Stricter than normalize_artist: also strips diacritics and punctuation
    so "Beyonce" matches "Beyonce" and "P!nk" matches "Pink".
    """
    import unicodedata

    # NFD decomposition separates base chars from combining marks
    decomposed = unicodedata.normalize("NFD", value)
    # Strip combining marks (accents, diacritics)
    stripped = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    # Remove punctuation (keep alphanumeric and spaces)
    cleaned = re.sub(r"[^\w\s]", "", stripped)
    # Standard normalization
    cleaned = cleaned.strip().lower().replace("&", "and")
    cleaned = _WHITESPACE_RE.sub(" ", cleaned)
    if cleaned.startswith("the "):
        cleaned = cleaned[4:]
    return cleaned.strip()


def get_default_db_path() -> Path:
    """Return DB path, respecting TUNESHIFT_DB env var."""
    env_path = os.environ.get("TUNESHIFT_DB")
    if env_path:
        return Path(env_path)
    return Path(__file__).parent.parent / "tuneshift.db"


class PersistenceBase:
    """Connection lifecycle and shared helpers for persistence mixins."""

    path: Path
    _conn: sqlite3.Connection | None

    @property
    def conn(self) -> sqlite3.Connection:
        """Return the lazily-opened SQLite connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(self.path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def _get_or_create_artist(self, name: str) -> int:
        """Return the artists.id for ``name``, creating the row if absent.

        Idempotent via the ``idx_artists_norm`` UNIQUE(norm_name) constraint, so
        it is consistent with the migration backfill which keys on the same
        normalized column.
        """
        norm = normalize_artist(name)
        self.conn.execute(
            "INSERT OR IGNORE INTO artists (name, norm_name) VALUES (?, ?)",
            (name, norm),
        )
        row = self.conn.execute(
            "SELECT id FROM artists WHERE norm_name = ?", (norm,)
        ).fetchone()
        return int(row["id"])

    def _get_or_create_album(self, title: str, artist_id: int) -> int:
        """Return the albums.id for ``(title, artist_id)`` at the default edition.

        The ``albums`` UNIQUE is ``(norm_title, artist_id, edition)`` and ``edition``
        defaults to ``'original'``; the INSERT omits it (default applies), so the
        SELECT filters ``edition='original'`` to key on the same columns and avoid a
        lookup miss.
        """
        norm = normalize_title(title)
        self.conn.execute(
            "INSERT OR IGNORE INTO albums (title, norm_title, artist_id) VALUES (?, ?, ?)",
            (title, norm, artist_id),
        )
        row = self.conn.execute(
            "SELECT id FROM albums WHERE norm_title = ? AND artist_id = ? AND edition = 'original'",
            (norm, artist_id),
        ).fetchone()
        return int(row["id"])

    def _row_to_playlist(self, row) -> Playlist:
        """Convert a DB row to a Playlist object."""
        # row is a sqlite3.Row, can check keys directly
        keys = row.keys() if hasattr(row, "keys") else []
        tidal_folder = row["tidal_folder_id"] if "tidal_folder_id" in keys else None
        return Playlist(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            auto_reorder=bool(row["auto_reorder"]),
            reorder_arc=row["reorder_arc"],
            tidal_folder_id=tidal_folder,
        )

    def _row_to_track(self, row: sqlite3.Row) -> Track:
        """Convert a DB row to a Track model."""
        cols = set(row.keys())

        def _get(name: str) -> Any:
            return row[name] if name in cols else None

        audio_modes_raw = _get("audio_modes")
        provenance_raw = _get("field_provenance")
        return Track(
            id=row["id"],
            title=row["title"],
            artist=row["artist"],
            album=row["album"],
            duration_seconds=row["duration_seconds"],
            isrc=row["isrc"],
            energy=row["energy"],
            valence=row["valence"],
            tempo=row["tempo"],
            key=row["key"],
            themes=json.loads(row["themes"]) if row["themes"] else [],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            album_artist=_get("album_artist"),
            album_type=_get("album_type"),
            label=_get("label"),
            recording_date=_get("recording_date"),
            release_date=_get("release_date"),
            remaster_year=_get("remaster_year"),
            audio_modes=json.loads(audio_modes_raw) if audio_modes_raw else [],
            audio_quality=_get("audio_quality"),
            tidal_version=_get("tidal_version"),
            language=_get("language"),
            composer=_get("composer"),
            availability=_get("availability"),
            quarantine_state=_get("quarantine_state"),
            quarantine_reason=_get("quarantine_reason"),
            field_provenance=json.loads(provenance_raw) if provenance_raw else {},
        )

    def close(self) -> None:
        """Close the database connection if it is open."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _row_to_artist(self, row: sqlite3.Row) -> Artist:
        """Convert a DB row to an Artist dataclass."""
        return Artist(
            id=row["id"],
            name=row["name"],
            norm_name=row["norm_name"],
            sort_name=row["sort_name"],
            bio=row["bio"],
            identity=json.loads(row["identity"]) if row["identity"] else None,
            tags=json.loads(row["tags"]) if row["tags"] else [],
            identity_confidence=row["identity_confidence"] or "unconfirmed",
            genres=json.loads(row["genres"]) if row["genres"] else [],
            origin=row["origin"],
            active_start=row["active_start"],
            active_end=row["active_end"],
            mb_artist_id=row["mb_artist_id"],
            tidal_artist_id=row["tidal_artist_id"],
            spotify_artist_uri=row["spotify_artist_uri"],
            lastfm_url=row["lastfm_url"],
            wikipedia_url=row["wikipedia_url"],
            enrichment_sources=json.loads(row["enrichment_sources"])
            if row["enrichment_sources"]
            else [],
            verified=bool(row["verified"]),
            enriched_at=row["enriched_at"],
            verified_at=row["verified_at"],
        )

    def _row_to_album(self, row: sqlite3.Row) -> Album:
        """Convert a DB row to an Album dataclass."""
        return Album(
            id=row["id"],
            title=row["title"],
            norm_title=row["norm_title"],
            artist_id=row["artist_id"],
            release_date=row["release_date"],
            release_type=row["release_type"],
            edition=row["edition"],
            genres=json.loads(row["genres"]) if row["genres"] else [],
            mb_release_group_id=row["mb_release_group_id"],
            tidal_album_id=row["tidal_album_id"],
            spotify_album_uri=row["spotify_album_uri"],
            enriched_at=row["enriched_at"],
        )
