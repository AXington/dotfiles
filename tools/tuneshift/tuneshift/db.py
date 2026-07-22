"""Database schema, connection management, and query helpers."""

import json
import os
import re
import sqlite3
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tuneshift.matching.normalize import _WHITESPACE_RE
from tuneshift.matching.normalize import normalize_artist as _alias_normalize
from tuneshift.models import (
    Album,
    Artist,
    EffectiveLock,
    PlatformMapping,
    Playlist,
    PlaylistPin,
    Track,
)
from tuneshift.persistence.migrations import run_migrations
from tuneshift.persistence.specs import TableSpec
from tuneshift.types import JournalEntry, ReviewItem

_SCHEMA_VERSION = 22

# Columns editable via update_track. Constrains f-string interpolation in the
# UPDATE statement to a fixed, safe set (no SQL identifier injection).
_TRACK_EDITABLE_COLUMNS = frozenset({"title", "artist", "album"})

# First-class version-selection metadata columns settable via set_track_fields
# (spec section 4.1). Constrains f-string interpolation in the UPDATE to a safe set.
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
# First-class columns whose Python value is a list, stored as a JSON string.
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

# STORED-KEY edition strip. Intentionally NARROWER than
# ``matching.normalize._EDITION_PARENS_RE`` (which also strips Deluxe/Mono/
# Anniversary/Taylor's Version/etc.). This regex feeds the persisted, indexed
# ``norm_title``/``norm_album`` columns and the ``albums`` UNIQUE constraint, so
# broadening it would require a full reindex/backfill migration and could merge
# rows the UNIQUE constraint currently keeps distinct. Do NOT point this at the
# aggressive comparison regex. See tests/matching/test_normalizer_contracts.py.
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


class Database:
    """SQLite database wrapper with schema management."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.path = db_path or get_default_db_path()
        if self.path.exists() and self.path.is_symlink():
            raise ValueError(f"Refusing to open symlinked database: {self.path}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._ensure_schema()

    @property
    def conn(self) -> sqlite3.Connection:
        """Return the lazily-opened SQLite connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(self.path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def _ensure_schema(self) -> None:
        """Create tables if they do not exist."""
        self.conn.executescript(_SCHEMA_SQL)
        self.conn.execute(
            "INSERT OR IGNORE INTO schema_meta (key, value) VALUES (?, ?)",
            ("version", str(_SCHEMA_VERSION)),
        )
        self.conn.commit()
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        """Migrate the database schema to the latest version.

        Delegates to the extracted, versioned migration chain in
        tuneshift.persistence.migrations. db.py remains the public persistence
        entry point, so the documented contract still holds at the API level.
        """
        row = self.conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'version'"
        ).fetchone()
        if row is None:
            return
        run_migrations(self.conn, int(row[0]), _SCHEMA_VERSION)

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

    def insert_track(self, track: Track) -> int:
        """Insert a track and return its ID.

        Links the track to the normalized ``artists``/``albums`` tables at insert
        time (get-or-create), so every runtime-added track carries ``artist_id`` and
        (when an album is present) ``album_id``, not only tracks touched by the
        one-time migration backfill. Gate on AC-D1/AC-D3.
        """
        artist_id: int | None = None
        album_id: int | None = None
        if track.artist:
            artist_id = self._get_or_create_artist(track.artist)
            if track.album:
                album_id = self._get_or_create_album(track.album, artist_id)
        cursor = self.conn.execute(
            """INSERT INTO tracks (
                   title, artist, album, norm_title, norm_artist, norm_album,
                   duration_seconds, isrc, energy, valence, tempo, key, themes, metadata,
                   artist_id, album_id
               )
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                track.title,
                track.artist,
                track.album,
                normalize_title(track.title),
                normalize_artist(track.artist),
                normalize_title(track.album) if track.album else None,
                track.duration_seconds,
                track.isrc,
                track.energy,
                track.valence,
                track.tempo,
                track.key,
                json.dumps(track.themes) if track.themes else None,
                json.dumps(track.metadata) if track.metadata else None,
                artist_id,
                album_id,
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def add_track(self, track: Track) -> int:
        """Insert a track and return its ID."""
        return self.insert_track(track)

    def update_track(self, track_id: int, **fields: str | None) -> int:
        """Update editable identity fields, recomputing normalized columns.

        Only ``title``, ``artist`` and ``album`` may be edited. Normalized
        lookup columns are recomputed for every changed field so identity
        matching stays consistent, and each change is recorded in
        ``track_edits`` for an audit trail. Returns the number of fields
        that actually changed.
        """
        invalid = set(fields) - _TRACK_EDITABLE_COLUMNS
        if invalid:
            raise ValueError(f"Cannot edit track fields: {sorted(invalid)}")

        track = self.get_track(track_id)
        if track is None:
            raise ValueError(f"Track id not found: {track_id}")

        current = {"title": track.title, "artist": track.artist, "album": track.album}
        changes = {k: v for k, v in fields.items() if v != current.get(k)}
        if not changes:
            return 0

        set_clauses: list[str] = []
        params: list[str | None] = []
        for field, value in changes.items():
            # field is constrained to the allowlist above, so interpolation is safe.
            set_clauses.append(f"{field} = ?")
            params.append(value)
            if field == "title":
                set_clauses.append("norm_title = ?")
                params.append(normalize_title(value))
            elif field == "artist":
                set_clauses.append("norm_artist = ?")
                params.append(normalize_artist(value) if value else None)
            elif field == "album":
                set_clauses.append("norm_album = ?")
                params.append(normalize_title(value) if value else None)
        set_clauses.append("updated_at = datetime('now')")

        with self.conn:
            self.conn.execute(
                f"UPDATE tracks SET {', '.join(set_clauses)} WHERE id = ?",  # noqa: S608 - columns from code-controlled allowlist; values parameterized
                (*params, track_id),
            )
            for field, value in changes.items():
                self.conn.execute(
                    "INSERT INTO track_edits (track_id, field, old_value, new_value) "
                    "VALUES (?, ?, ?, ?)",
                    (track_id, field, current.get(field), value),
                )
        return len(changes)

    def get_track_edits(self, track_id: int) -> list[dict]:
        """Return the recorded edit history for a track, newest first."""
        rows = self.conn.execute(
            "SELECT field, old_value, new_value, edited_at FROM track_edits "
            "WHERE track_id = ? ORDER BY id DESC",
            (track_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_track(self, track_id: int) -> Track | None:
        """Fetch a track by ID."""
        row = self.conn.execute(
            "SELECT * FROM tracks WHERE id = ?",
            (track_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_track(row)

    def find_track(self, title: str, artist: str, album: str | None) -> Track | None:
        """Find a track by identity using indexed normalized columns."""
        norm_title = normalize_title(title)
        norm_artist = normalize_artist(artist)
        norm_album = normalize_title(album) if album else None

        if norm_title is None:
            return None

        if norm_album:
            row = self.conn.execute(
                "SELECT * FROM tracks WHERE norm_title = ? AND norm_artist = ? AND norm_album = ?",
                (norm_title, norm_artist, norm_album),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM tracks WHERE norm_title = ? AND norm_artist = ? AND norm_album IS NULL",
                (norm_title, norm_artist),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_track(row)

    def get_resolution_state(
        self,
        track_id: int,
    ) -> tuple[str | None, float | None, str | None]:
        """Get the current resolution state of a track."""
        row = self.conn.execute(
            "SELECT confidence_tier, confidence_score, resolved_at FROM tracks WHERE id = ?",
            (track_id,),
        ).fetchone()
        if row is None:
            return None, None, None
        return row["confidence_tier"], row["confidence_score"], row["resolved_at"]

    def get_isrc(self, track_id: int) -> str | None:
        """Get the ISRC for a track."""
        row = self.conn.execute(
            "SELECT isrc FROM tracks WHERE id = ?", (track_id,)
        ).fetchone()
        return row["isrc"] if row else None

    def store_resolution(
        self,
        track_id: int,
        mb_recording_id: str | None,
        mb_release_group_id: str | None,
        confidence_tier: str,
        confidence_score: float,
        evidence: list[dict],
        isrc: str | None = None,
    ) -> None:
        """Store a successful resolution result."""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()

        with self.conn:
            new_evidence_ids = []
            for evidence_row in evidence:
                cursor = self.conn.execute(
                    """INSERT INTO evidence (track_id, source, evidence_type, confidence, raw_data, is_current)
                       VALUES (?, ?, ?, ?, ?, 1)""",
                    (
                        track_id,
                        evidence_row["source"],
                        evidence_row["evidence_type"],
                        evidence_row["confidence"],
                        evidence_row.get("raw_data"),
                    ),
                )
                new_evidence_ids.append(cursor.lastrowid)

            anchor_id = new_evidence_ids[0] if new_evidence_ids else None
            if anchor_id is not None:
                placeholders = ",".join("?" for _ in new_evidence_ids)
                self.conn.execute(
                    f"""UPDATE evidence
                       SET is_current = 0, superseded_by = ?
                       WHERE track_id = ? AND is_current = 1 AND id NOT IN ({placeholders})""",  # noqa: S608 - placeholders are bound '?' params; values parameterized
                    (anchor_id, track_id, *new_evidence_ids),
                )

            update_sql = """UPDATE tracks SET
                mb_recording_id = ?,
                mb_release_group_id = ?,
                confidence_tier = ?,
                confidence_score = ?,
                resolved_at = ?"""
            params: list[object] = [
                mb_recording_id,
                mb_release_group_id,
                confidence_tier,
                confidence_score,
                now,
            ]
            if isrc is not None:
                update_sql += ", isrc = ?"
                params.append(isrc)
            update_sql += " WHERE id = ?"
            params.append(track_id)
            self.conn.execute(update_sql, params)

    def store_failed_evidence(self, track_id: int, evidence: list[dict]) -> None:
        """Store evidence from a failed resolution attempt."""
        with self.conn:
            for evidence_row in evidence:
                self.conn.execute(
                    """INSERT INTO evidence (track_id, source, evidence_type, confidence, raw_data, is_current)
                       VALUES (?, ?, ?, ?, ?, 1)""",
                    (
                        track_id,
                        evidence_row["source"],
                        evidence_row["evidence_type"],
                        evidence_row["confidence"],
                        evidence_row.get("raw_data"),
                    ),
                )

    def find_unresolved(self, below_tier: str | None = None) -> list[Track]:
        """Find tracks that still need identity resolution."""
        tier_order = {"VERIFIED": 4, "CONFIRMED": 3, "PROBABLE": 2, "UNCERTAIN": 1}

        if below_tier is None:
            rows = self.conn.execute(
                "SELECT * FROM tracks WHERE confidence_tier IS NULL"
            ).fetchall()
        else:
            threshold = tier_order.get(below_tier, 0)
            tiers_below = [
                tier for tier, order in tier_order.items() if order < threshold
            ]
            if not tiers_below:
                rows = self.conn.execute(
                    "SELECT * FROM tracks WHERE confidence_tier IS NULL"
                ).fetchall()
            else:
                placeholders = ",".join("?" for _ in tiers_below)
                rows = self.conn.execute(
                    f"SELECT * FROM tracks WHERE confidence_tier IS NULL OR confidence_tier IN ({placeholders})",  # noqa: S608 - placeholders are bound '?' params; values parameterized
                    tiers_below,
                ).fetchall()

        return [self._row_to_track(row) for row in rows]

    def find_orphaned_tracks(self) -> list[Track]:
        """Tracks invisible to every review surface (BUG-3 / FEAT-3).

        Orphaned == no confidence_tier, no quarantine_state, no resolution_queue
        entry, AND no platform_tracks row at all. "No platform mapping" here means
        the strict case (zero rows): a track reconciled at least once (any
        platform_tracks row, even unavailable/substitute) is NOT orphaned; its
        availability is a separate triage/quarantine concern. This strict
        definition matches the real orphans (tracks added but never resolved),
        which are invisible to both ``triage`` (quarantine-only) and a user who
        never runs ``resolve --all``.
        """
        rows = self.conn.execute(
            """
            SELECT t.* FROM tracks t
            WHERE t.confidence_tier IS NULL
              AND t.quarantine_state IS NULL
              AND NOT EXISTS (SELECT 1 FROM resolution_queue rq WHERE rq.track_id = t.id)
              AND NOT EXISTS (SELECT 1 FROM platform_tracks pt WHERE pt.track_id = t.id)
            ORDER BY t.id
            """
        ).fetchall()
        return [self._row_to_track(row) for row in rows]

    def find_tracks_by_playlist(self, playlist_id: int) -> list[Track]:
        """Find all tracks in a playlist."""
        return self.get_playlist_tracks(playlist_id)

    def find_tracks_by_title_artist(self, title: str, artist: str) -> list[Track]:
        """Find tracks by title and artist using normalized columns."""
        norm_title = normalize_title(title)
        norm_artist = normalize_artist(artist)
        if norm_title is None:
            return []
        rows = self.conn.execute(
            "SELECT * FROM tracks WHERE norm_title = ? AND norm_artist = ?",
            (norm_title, norm_artist),
        ).fetchall()
        return [self._row_to_track(row) for row in rows]

    def search_tracks_by_metadata(
        self,
        intensity_range: tuple[float, float] | None = None,
        stance: str | None = None,
        keywords: list[str] | None = None,
        limit: int = 20,
    ) -> list[Track]:
        """Search tracks using metadata-backed narrative attributes."""
        rows = self.conn.execute(
            "SELECT * FROM tracks ORDER BY updated_at DESC, id DESC"
        ).fetchall()
        normalized_stance = stance.casefold() if stance else None
        normalized_keywords = {
            keyword.casefold().strip()
            for keyword in (keywords or [])
            if keyword and keyword.strip()
        }
        matches: list[tuple[int, Track]] = []

        for row in rows:
            track = self._row_to_track(row)
            metadata = track.metadata or {}

            track_intensity = metadata.get("emotional_intensity", track.energy)
            if intensity_range is not None:
                if track_intensity is None:
                    continue
                try:
                    intensity_value = float(track_intensity)
                except (TypeError, ValueError):
                    continue
                minimum, maximum = intensity_range
                if intensity_value < minimum or intensity_value > maximum:
                    continue

            track_stance = metadata.get("narrator_stance")
            if normalized_stance is not None:
                if not isinstance(track_stance, str):
                    continue
                if track_stance.casefold() != normalized_stance:
                    continue

            overlap_count = 0
            if normalized_keywords:
                term_groups: list[str] = [track.title, track.artist]
                if track.album:
                    term_groups.append(track.album)
                term_groups.extend(track.themes)
                for key in (
                    "vibes",
                    "era_mood",
                    "lastfm_tags",
                    "lyrical_subject",
                    "narrator_stance",
                    "sonic_texture",
                    "space",
                    "groove_feel",
                    "opens_with",
                    "closes_with",
                    "energy_arc_within",
                ):
                    value = metadata.get(key)
                    if isinstance(value, list):
                        term_groups.extend(str(item) for item in value if item)
                    elif value:
                        term_groups.append(str(value))

                haystack = " ".join(term_groups).casefold()
                overlap_count = sum(
                    1 for keyword in normalized_keywords if keyword in haystack
                )
                if overlap_count == 0:
                    continue

            matches.append((overlap_count, track))

        matches.sort(
            key=lambda item: (
                item[0],
                item[1].metadata.get("classification_confidence", 0.0),
                item[1].id or 0,
            ),
            reverse=True,
        )
        return [track for _, track in matches[:limit]]

    def create_playlist(self, name: str, description: str | None = None) -> int:
        """Create a playlist and return its ID."""
        cursor = self.conn.execute(
            "INSERT INTO playlists (name, description) VALUES (?, ?)",
            (name, description),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def list_playlists(self) -> list[Playlist]:
        """List all playlists."""
        rows = self.conn.execute("SELECT * FROM playlists ORDER BY name").fetchall()
        return [self._row_to_playlist(row) for row in rows]

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

    def set_auto_reorder(
        self, playlist_id: int, enabled: bool, arc: str = "wave"
    ) -> None:
        """Enable or disable auto-reorder for a playlist."""
        self.conn.execute(
            "UPDATE playlists SET auto_reorder = ?, reorder_arc = ?, updated_at = datetime('now') WHERE id = ?",
            (int(enabled), arc, playlist_id),
        )
        self.conn.commit()

    def set_narrative(self, playlist_id: int, narrative: str | None) -> None:
        """Set the intended narrative arc description for a playlist."""
        self.conn.execute(
            "UPDATE playlists SET narrative = ?, updated_at = datetime('now') WHERE id = ?",
            (narrative, playlist_id),
        )
        self.conn.commit()

    def get_narrative(self, playlist_id: int) -> str | None:
        """Get the intended narrative arc description for a playlist."""
        row = self.conn.execute(
            "SELECT narrative FROM playlists WHERE id = ?", (playlist_id,)
        ).fetchone()
        return row[0] if row else None

    def set_pin(
        self,
        playlist_id: int,
        track_id: int,
        pin_type: str,
        group_id: str | None = None,
        group_order: int | None = None,
    ) -> None:
        """Pin a track in a playlist (opener, closer, or anchor group)."""
        self.conn.execute(
            """INSERT OR REPLACE INTO playlist_pins
               (playlist_id, track_id, pin_type, group_id, group_order)
               VALUES (?, ?, ?, ?, ?)""",
            (playlist_id, track_id, pin_type, group_id, group_order),
        )
        self.conn.commit()

    def remove_pin(self, playlist_id: int, track_id: int) -> None:
        """Remove a pin from a playlist."""
        self.conn.execute(
            "DELETE FROM playlist_pins WHERE playlist_id = ? AND track_id = ?",
            (playlist_id, track_id),
        )
        self.conn.commit()

    def get_pins(self, playlist_id: int) -> list[PlaylistPin]:
        """Get all pins for a playlist."""
        rows = self.conn.execute(
            "SELECT playlist_id, track_id, pin_type, group_id, group_order "
            "FROM playlist_pins WHERE playlist_id = ? ORDER BY pin_type, group_id, group_order",
            (playlist_id,),
        ).fetchall()
        return [
            PlaylistPin(
                playlist_id=row[0],
                track_id=row[1],
                pin_type=row[2],
                group_id=row[3],
                group_order=row[4],
            )
            for row in rows
        ]

    def transfer_pins(
        self, playlist_id: int, from_track_id: int, to_track_id: int
    ) -> None:
        """Transfer all pins from one track to another within a playlist."""
        with self.conn:
            self.conn.execute(
                """UPDATE playlist_pins SET track_id = ?
                   WHERE playlist_id = ? AND track_id = ?""",
                (to_track_id, playlist_id, from_track_id),
            )

    def merge_tracks(self, keep_id: int, merge_ids: list[int]) -> None:
        """Merge duplicate track rows into a canonical row.

        For each id in ``merge_ids``: reassign its playlist memberships and pins
        to ``keep_id`` (deduplicating within a playlist), delete its auxiliary
        rows (metadata, tags), then delete the track row itself. Runs in a
        single transaction so a failure leaves the database unchanged.

        Playlist positions are rewritten contiguously; the offset technique
        avoids transient UNIQUE(playlist_id, position) collisions during
        reassignment.
        """
        conn = self.conn
        with conn:
            for mid in merge_ids:
                if mid == keep_id:
                    continue
                playlists = [
                    r[0]
                    for r in conn.execute(
                        "SELECT DISTINCT playlist_id FROM playlist_tracks WHERE track_id = ?",
                        (mid,),
                    ).fetchall()
                ]
                for pid in playlists:
                    # Transfer pins where possible; UNIQUE conflicts (keep already
                    # pinned) are ignored and cleaned up by the cascade below.
                    conn.execute(
                        "UPDATE OR IGNORE playlist_pins SET track_id = ? "
                        "WHERE playlist_id = ? AND track_id = ?",
                        (keep_id, pid, mid),
                    )
                    keep_present = conn.execute(
                        "SELECT 1 FROM playlist_tracks WHERE playlist_id = ? "
                        "AND track_id = ? LIMIT 1",
                        (pid, keep_id),
                    ).fetchone()
                    if keep_present:
                        # Avoid a duplicate membership: drop the merge rows.
                        conn.execute(
                            "DELETE FROM playlist_tracks WHERE playlist_id = ? "
                            "AND track_id = ?",
                            (pid, mid),
                        )
                    else:
                        conn.execute(
                            "UPDATE playlist_tracks SET track_id = ? "
                            "WHERE playlist_id = ? AND track_id = ?",
                            (keep_id, pid, mid),
                        )
                    # Reindex positions contiguously without PK collisions.
                    conn.execute(
                        "UPDATE playlist_tracks SET position = position + 1000000 "
                        "WHERE playlist_id = ?",
                        (pid,),
                    )
                    rows = conn.execute(
                        "SELECT rowid FROM playlist_tracks WHERE playlist_id = ? "
                        "ORDER BY position",
                        (pid,),
                    ).fetchall()
                    for idx, (rowid,) in enumerate(rows):
                        conn.execute(
                            "UPDATE playlist_tracks SET position = ? WHERE rowid = ?",
                            (idx, rowid),
                        )
                # Remove auxiliary rows that lack ON DELETE CASCADE.
                conn.execute(
                    "DELETE FROM track_platform_metadata WHERE track_id = ?", (mid,)
                )
                conn.execute("DELETE FROM track_tags WHERE track_id = ?", (mid,))
                # Delete the track; cascade removes remaining platform_tracks,
                # playlist_tracks, playlist_pins, and evidence rows.
                conn.execute("DELETE FROM tracks WHERE id = ?", (mid,))

    def set_playlist_tracks(self, playlist_id: int, track_ids: list[int]) -> None:
        """Set the track order for a playlist, replacing existing rows."""
        self.conn.execute(
            "DELETE FROM playlist_tracks WHERE playlist_id = ?",
            (playlist_id,),
        )
        for position, track_id in enumerate(track_ids):
            self.conn.execute(
                "INSERT INTO playlist_tracks (playlist_id, track_id, position) VALUES (?, ?, ?)",
                (playlist_id, track_id, position),
            )
        self.conn.commit()

    def clear_playlist_tracks(self, playlist_id: int) -> None:
        """Remove all tracks from a playlist without deleting the playlist."""
        self.conn.execute(
            "DELETE FROM playlist_tracks WHERE playlist_id = ?",
            (playlist_id,),
        )
        self.conn.commit()

    def get_playlist_track_ids(self, playlist_id: int) -> list[int]:
        """Return ordered track IDs for a playlist."""
        rows = self.conn.execute(
            "SELECT track_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position",
            (playlist_id,),
        ).fetchall()
        return [row[0] for row in rows]

    def get_playlist_tracks(self, playlist_id: int) -> list[Track]:
        """Get ordered tracks for a playlist."""
        rows = self.conn.execute(
            """SELECT t.* FROM tracks t
               JOIN playlist_tracks pt ON t.id = pt.track_id
               WHERE pt.playlist_id = ?
               ORDER BY pt.position""",
            (playlist_id,),
        ).fetchall()
        return [self._row_to_track(row) for row in rows]

    def get_release_years_for_playlist(self, playlist_id: int) -> dict[int, int | None]:
        """Map each track in a playlist to a best-known release year.

        Reads ``track_platform_metadata.release_year`` (populated per platform).
        When a track has release years from multiple platforms, the earliest
        non-null year is used (the original release, not a later reissue).
        Every track in the playlist is present in the result; a track with no
        recorded year maps to ``None`` so callers can report it as unverifiable.
        """
        rows = self.conn.execute(
            """SELECT pt.track_id AS track_id,
                      MIN(tpm.release_year) AS year
               FROM playlist_tracks pt
               LEFT JOIN track_platform_metadata tpm
                    ON tpm.track_id = pt.track_id
                    AND tpm.release_year IS NOT NULL
               WHERE pt.playlist_id = ?
               GROUP BY pt.track_id""",
            (playlist_id,),
        ).fetchall()
        return {row["track_id"]: row["year"] for row in rows}

    def add_concept_acceptance(
        self, playlist_id: int, track_id: int, rule: str
    ) -> None:
        """Record that a review finding for (track, rule) is accepted.

        Idempotent: re-accepting the same pair is a no-op. The normalized
        ``rule_key`` is what future reviews match against; the raw rule text is
        stored alongside for display.
        """
        from tuneshift.composer.rules import normalize_rule_key

        self.conn.execute(
            """INSERT OR IGNORE INTO concept_rule_acceptances
                   (playlist_id, track_id, rule_key, rule_text)
               VALUES (?, ?, ?, ?)""",
            (playlist_id, track_id, normalize_rule_key(rule), rule),
        )
        self.conn.commit()

    def get_concept_acceptances(self, playlist_id: int) -> set[tuple[int, str]]:
        """Return accepted ``(track_id, rule_key)`` pairs for a playlist."""
        rows = self.conn.execute(
            "SELECT track_id, rule_key FROM concept_rule_acceptances "
            "WHERE playlist_id = ?",
            (playlist_id,),
        ).fetchall()
        return {(row["track_id"], row["rule_key"]) for row in rows}

    def list_concept_acceptances(self, playlist_id: int) -> list[tuple[int, str]]:
        """Return accepted ``(track_id, rule_text)`` pairs for display."""
        rows = self.conn.execute(
            "SELECT track_id, rule_text FROM concept_rule_acceptances "
            "WHERE playlist_id = ? ORDER BY track_id, rule_text",
            (playlist_id,),
        ).fetchall()
        return [(row["track_id"], row["rule_text"]) for row in rows]

    def clear_concept_acceptance(
        self, playlist_id: int, track_id: int, rule: str
    ) -> None:
        """Remove a previously recorded acceptance for (track, rule)."""
        from tuneshift.composer.rules import normalize_rule_key

        self.conn.execute(
            "DELETE FROM concept_rule_acceptances "
            "WHERE playlist_id = ? AND track_id = ? AND rule_key = ?",
            (playlist_id, track_id, normalize_rule_key(rule)),
        )
        self.conn.commit()

    def remove_playlist_track_by_position(
        self, playlist_id: int, position: int
    ) -> None:
        """Remove the track at a specific position and reindex later rows.

        Position-scoped (BUG-7): only the row at ``position`` is removed, so a
        track that legitimately appears at multiple positions keeps its other
        copies. Pins for the track are cleared only when no copy of it remains in
        the playlist (a track pinned while still present elsewhere keeps its pin).
        """
        with self.conn:
            row = self.conn.execute(
                "SELECT track_id FROM playlist_tracks "
                "WHERE playlist_id = ? AND position = ?",
                (playlist_id, position),
            ).fetchone()
            if row is None:
                return
            track_id = row["track_id"]
            self.conn.execute(
                "DELETE FROM playlist_tracks WHERE playlist_id = ? AND position = ?",
                (playlist_id, position),
            )
            self.conn.execute(
                """UPDATE playlist_tracks SET position = position - 1
                   WHERE playlist_id = ? AND position > ?""",
                (playlist_id, position),
            )
            still_present = self.conn.execute(
                "SELECT 1 FROM playlist_tracks "
                "WHERE playlist_id = ? AND track_id = ? LIMIT 1",
                (playlist_id, track_id),
            ).fetchone()
            if still_present is None:
                self.conn.execute(
                    "DELETE FROM playlist_pins WHERE playlist_id = ? AND track_id = ?",
                    (playlist_id, track_id),
                )

    def remove_track_from_playlist(self, playlist_id: int, track_id: int) -> None:
        """Remove track from playlist with cascade cleanup of pins and positions."""
        with self.conn:
            self.conn.execute(
                "DELETE FROM playlist_tracks WHERE playlist_id = ? AND track_id = ?",
                (playlist_id, track_id),
            )
            self.conn.execute(
                "DELETE FROM playlist_pins WHERE playlist_id = ? AND track_id = ?",
                (playlist_id, track_id),
            )
            rows = self.conn.execute(
                "SELECT rowid FROM playlist_tracks WHERE playlist_id = ? ORDER BY position",
                (playlist_id,),
            ).fetchall()
            for idx, (rowid,) in enumerate(rows):
                self.conn.execute(
                    "UPDATE playlist_tracks SET position = ? WHERE rowid = ?",
                    (idx, rowid),
                )

    def upsert_platform_mapping(self, mapping: PlatformMapping) -> None:
        """Insert or update a platform mapping."""
        self.conn.execute(
            """INSERT INTO platform_tracks
               (track_id, platform, platform_track_id, platform_title,
                platform_artist, platform_album, match_score,
                is_divergent, divergence_note, status, user_approved, fingerprint)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(track_id, platform) DO UPDATE SET
                 platform_track_id = excluded.platform_track_id,
                 platform_title = excluded.platform_title,
                 platform_artist = excluded.platform_artist,
                 platform_album = excluded.platform_album,
                 match_score = excluded.match_score,
                 is_divergent = excluded.is_divergent,
                 divergence_note = excluded.divergence_note,
                 status = excluded.status,
                 user_approved = excluded.user_approved,
                 fingerprint = COALESCE(excluded.fingerprint, platform_tracks.fingerprint)""",
            (
                mapping.track_id,
                mapping.platform,
                mapping.platform_track_id,
                mapping.platform_title,
                mapping.platform_artist,
                mapping.platform_album,
                mapping.match_score,
                int(mapping.is_divergent),
                mapping.divergence_note,
                mapping.status,
                int(mapping.user_approved),
                mapping.fingerprint,
            ),
        )
        self.conn.commit()

    def save_match_audit(
        self, track_id: int, platform: str, audit, playlist_id: int = 0
    ) -> None:
        """Persist the explainable MatchAudit for a (playlist, track, platform).

        Stored for every reconcile outcome, including misses, so ``tuneshift
        explain`` can explain a decision without re-running a live search. The
        audit is serialized to JSON via ``MatchAudit.to_json``; availability and
        reason_code are also stored as plain columns for cheap filtering.

        ``playlist_id`` scopes the audit: selection is playlist-dependent, so the
        same (track, platform) can carry a distinct audit per playlist. It
        defaults to the global sentinel ``0`` for legacy/non-playlist call sites.
        """
        if audit is None:
            return
        self.conn.execute(
            """INSERT INTO match_audits
               (playlist_id, track_id, platform, availability, reason_code, audit_json, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(playlist_id, track_id, platform) DO UPDATE SET
                 availability = excluded.availability,
                 reason_code = excluded.reason_code,
                 audit_json = excluded.audit_json,
                 updated_at = excluded.updated_at""",
            (
                playlist_id,
                track_id,
                platform,
                audit.availability,
                audit.reason_code,
                audit.to_json(),
            ),
        )
        self.conn.commit()

    def get_match_audit(self, track_id: int, platform: str, playlist_id: int = 0):
        """Return the persisted ``MatchAudit`` for a (playlist, track, platform).

        ``playlist_id`` defaults to the global sentinel ``0``.
        """
        from tuneshift.matching import MatchAudit

        row = self.conn.execute(
            "SELECT audit_json FROM match_audits "
            "WHERE playlist_id = ? AND track_id = ? AND platform = ?",
            (playlist_id, track_id, platform),
        ).fetchone()
        if row is None:
            return None
        return MatchAudit.from_json(row["audit_json"])

    def get_match_audits_for_track(
        self, track_id: int, playlist_id: int = 0
    ) -> dict[str, object]:
        """Return all persisted audits for a (playlist, track), keyed by platform.

        ``playlist_id`` defaults to the global sentinel ``0``.
        """
        from tuneshift.matching import MatchAudit

        rows = self.conn.execute(
            "SELECT platform, audit_json FROM match_audits "
            "WHERE track_id = ? AND playlist_id = ?",
            (track_id, playlist_id),
        ).fetchall()
        return {
            row["platform"]: MatchAudit.from_json(row["audit_json"]) for row in rows
        }

    def get_review_items(
        self,
        playlist_id: int | None = None,
        platform: str | None = None,
    ) -> list["ReviewItem"]:
        """Return per-(playlist, track, platform) review items from stored audits.

        Joins persisted ``match_audits`` to the tracks and the playlists they
        appear in. One item per distinct (playlist, track, platform) so a track
        pinned twice in a playlist is not double-counted, while the same track in
        two playlists is surfaced under each. Callers filter/cluster with
        :func:`tuneshift.matching.cluster_reviews` and
        :func:`tuneshift.matching.compute_burden`.
        """
        sql = """
            SELECT DISTINCT p.id AS playlist_id, p.name AS playlist_name,
                   t.id AS track_id, t.title, t.artist, t.album,
                   ma.platform, ma.availability, ma.reason_code
            FROM match_audits ma
            JOIN tracks t ON t.id = ma.track_id
            JOIN playlist_tracks pt ON pt.track_id = ma.track_id
            JOIN playlists p ON p.id = pt.playlist_id
            WHERE ma.playlist_id IN (pt.playlist_id, 0)
              AND ma.playlist_id = (
                    SELECT MAX(ma2.playlist_id)
                    FROM match_audits ma2
                    WHERE ma2.track_id = ma.track_id
                      AND ma2.platform = ma.platform
                      AND ma2.playlist_id IN (pt.playlist_id, 0)
                  )
        """
        conditions: list[str] = []
        params: list[object] = []
        if playlist_id is not None:
            conditions.append("p.id = ?")
            params.append(playlist_id)
        if platform is not None:
            conditions.append("ma.platform = ?")
            params.append(platform)
        if conditions:
            sql += " AND " + " AND ".join(conditions)
        sql += " ORDER BY p.name, t.artist, t.title"

        rows = self.conn.execute(sql, params).fetchall()
        return [
            ReviewItem(
                track_id=row["track_id"],
                title=row["title"],
                artist=row["artist"],
                album=row["album"],
                platform=row["platform"],
                availability=row["availability"],
                reason_code=row["reason_code"],
                playlist_id=row["playlist_id"],
                playlist_name=row["playlist_name"],
            )
            for row in rows
        ]

    def get_unavailable_track_ids(
        self, playlist_id: int, platform: str = "tidal"
    ) -> list[int]:
        """Return playlist track ids that are unavailable on ``platform``.

        A track counts as unavailable when its persisted ``match_audit`` for the
        platform records availability ``not_found`` (genuinely absent) or
        ``exact_unavailable`` (known but blocked). Tidal is the availability
        source of truth, so it is the default platform.

        Tracks with no audit for the platform are treated as available (not
        excluded): a playlist that has never been reconciled is unaffected. The
        sequencer uses this to keep unavailable tracks from distorting the arc.

        Audits are playlist-scoped: a playlist-specific audit
        (``playlist_id = this playlist``) takes precedence over the legacy
        global sentinel (``playlist_id = 0``), so one playlist's verdict never
        leaks into another.
        """
        rows = self.conn.execute(
            """
            SELECT DISTINCT pt.track_id
            FROM playlist_tracks pt
            JOIN match_audits ma
              ON ma.track_id = pt.track_id AND ma.platform = ?
             AND ma.playlist_id IN (pt.playlist_id, 0)
            WHERE pt.playlist_id = ?
              AND ma.availability IN ('not_found', 'exact_unavailable')
              AND ma.playlist_id = (
                    SELECT MAX(ma2.playlist_id)
                    FROM match_audits ma2
                    WHERE ma2.track_id = pt.track_id
                      AND ma2.platform = ma.platform
                      AND ma2.playlist_id IN (pt.playlist_id, 0)
                  )
            """,
            (platform, playlist_id),
        ).fetchall()
        return [row[0] for row in rows]

    def get_platform_mapping(
        self, track_id: int, platform: str
    ) -> PlatformMapping | None:
        """Get a platform mapping for a track."""
        row = self.conn.execute(
            "SELECT * FROM platform_tracks WHERE track_id = ? AND platform = ?",
            (track_id, platform),
        ).fetchone()
        if row is None:
            return None
        return PlatformMapping(
            track_id=row["track_id"],
            platform=row["platform"],
            platform_track_id=row["platform_track_id"],
            platform_title=row["platform_title"],
            platform_artist=row["platform_artist"],
            platform_album=row["platform_album"],
            match_score=row["match_score"],
            is_divergent=bool(row["is_divergent"]),
            divergence_note=row["divergence_note"],
            status=row["status"],
            user_approved=bool(row["user_approved"]),
            fingerprint=row["fingerprint"],
        )

    def get_platform_mappings_for_tracks(
        self,
        track_ids: list[int],
        platform: str,
    ) -> dict[int, PlatformMapping]:
        """Batch-load platform mappings for multiple tracks."""
        if not track_ids:
            return {}
        placeholders = ",".join("?" for _ in track_ids)
        rows = self.conn.execute(
            f"SELECT * FROM platform_tracks WHERE track_id IN ({placeholders}) AND platform = ?",  # noqa: S608 - placeholders are bound '?' params; values parameterized
            (*track_ids, platform),
        ).fetchall()
        result: dict[int, PlatformMapping] = {}
        for row in rows:
            result[row["track_id"]] = PlatformMapping(
                track_id=row["track_id"],
                platform=row["platform"],
                platform_track_id=row["platform_track_id"],
                platform_title=row["platform_title"],
                platform_artist=row["platform_artist"],
                platform_album=row["platform_album"],
                match_score=row["match_score"],
                is_divergent=bool(row["is_divergent"]),
                divergence_note=row["divergence_note"],
                status=row["status"],
                user_approved=bool(row["user_approved"]),
                fingerprint=row["fingerprint"],
            )
        return result

    def set_platform_mapping(
        self,
        track_id: int,
        platform: str,
        platform_track_id: str,
        user_approved: bool = False,
        platform_title: str | None = None,
        platform_artist: str | None = None,
        platform_album: str | None = None,
        match_score: int | None = None,
    ) -> None:
        """Set or update a platform mapping for a track."""
        with self.conn:
            self.conn.execute(
                """INSERT INTO platform_tracks
                   (track_id, platform, platform_track_id, platform_title, platform_artist,
                    platform_album, match_score, user_approved)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(track_id, platform) DO UPDATE SET
                   platform_track_id = excluded.platform_track_id,
                   platform_title = excluded.platform_title,
                   platform_artist = excluded.platform_artist,
                   platform_album = excluded.platform_album,
                   match_score = excluded.match_score,
                   user_approved = excluded.user_approved""",
                (
                    track_id,
                    platform,
                    platform_track_id,
                    platform_title,
                    platform_artist,
                    platform_album,
                    match_score,
                    int(user_approved),
                ),
            )

    def delete_platform_mapping(self, track_id: int, platform: str) -> None:
        """Remove a platform mapping for a track."""
        with self.conn:
            self.conn.execute(
                "DELETE FROM platform_tracks WHERE track_id = ? AND platform = ?",
                (track_id, platform),
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

    def set_track_fields(
        self, track_id: int, fields: dict[str, Any], source: str
    ) -> None:
        """Set first-class metadata columns on a track and record provenance.

        ``fields`` keys must be in ``_TRACK_FIRST_CLASS_COLUMNS``. Each updated
        field records ``{"source": source, "at": <utc iso>}`` in the
        ``field_provenance`` JSON column (AC-D4), merged with any existing
        provenance so successive enrichment passes accumulate rather than clobber.
        List-valued columns (audio_modes) are JSON-serialized.
        """
        invalid = set(fields) - _TRACK_FIRST_CLASS_COLUMNS
        if invalid:
            raise ValueError(f"Cannot set track fields: {sorted(invalid)}")
        if not fields:
            return

        row = self.conn.execute(
            "SELECT field_provenance FROM tracks WHERE id = ?", (track_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Track id not found: {track_id}")
        provenance = (
            json.loads(row["field_provenance"]) if row["field_provenance"] else {}
        )

        now = datetime.now(timezone.utc).isoformat()
        set_clauses: list[str] = []
        params: list[Any] = []
        for column, value in fields.items():
            # column is constrained to the allowlist above - safe to interpolate.
            set_clauses.append(f"{column} = ?")
            if column in _TRACK_JSON_FIELD_COLUMNS:
                params.append(json.dumps(value) if value is not None else None)
            else:
                params.append(value)
            provenance[column] = {"source": source, "at": now}
        set_clauses.append("field_provenance = ?")
        params.append(json.dumps(provenance))
        set_clauses.append("updated_at = datetime('now')")

        with self.conn:
            self.conn.execute(
                f"UPDATE tracks SET {', '.join(set_clauses)} WHERE id = ?",  # noqa: S608 - columns from code-controlled allowlist; values parameterized
                (*params, track_id),
            )

    # --- resolution_queue (spec section 4.1a: resumable enrich/resolve worker) ---

    def enqueue_resolution(
        self, track_id: int, next_attempt_at: str | None = None
    ) -> None:
        """Enqueue a track for resolution/enrichment.

        Idempotent per track. Re-enqueuing a track that previously QUARANTINED
        reopens it for another attempt (a re-add/re-import is a user signal to
        retry) and resets its counters; a ``pending`` or ``resolved`` row is left
        untouched so already-resolved work is never needlessly redone.
        """
        with self.conn:
            self.conn.execute(
                """INSERT INTO resolution_queue (track_id, state, next_attempt_at)
                   VALUES (?, 'pending', ?)
                   ON CONFLICT(track_id) DO UPDATE SET
                       state = 'pending',
                       attempts = 0,
                       transient_attempts = 0,
                       last_error = NULL,
                       next_attempt_at = excluded.next_attempt_at,
                       updated_at = datetime('now')
                   WHERE resolution_queue.state = 'quarantined'""",
                (track_id, next_attempt_at),
            )

    def approve_resolution(self, track_id: int) -> None:
        """Manually approve/release a quarantined track (AC-D6).

        Clears the quarantine on BOTH sources of truth in one transaction: the
        track's ``quarantine_state`` (which drives selectability) and the
        ``resolution_queue`` row (which drives coverage). Marking the queue row
        ``resolved`` keeps ``coverage_report`` consistent with
        ``get_quarantined_tracks``, an approved track counts as resolved, never
        lingering as quarantined.
        """
        with self.conn:
            self.conn.execute(
                """UPDATE resolution_queue
                   SET state = 'resolved', last_error = NULL,
                       next_attempt_at = NULL, updated_at = datetime('now')
                   WHERE track_id = ?""",
                (track_id,),
            )
            self.conn.execute(
                "UPDATE tracks SET quarantine_state = NULL, "
                "quarantine_reason = NULL WHERE id = ?",
                (track_id,),
            )

    def get_resolution_queue_state(self, track_id: int) -> str | None:
        """Return the resolution_queue state for a track, or None if unqueued.

        Distinct from :meth:`get_resolution_state` (which reads the track's
        identity confidence): this reflects the worker's queue lifecycle
        (pending/resolved/quarantined) that drives the drain loop and coverage.
        """
        row = self.conn.execute(
            "SELECT state FROM resolution_queue WHERE track_id = ?", (track_id,)
        ).fetchone()
        return row["state"] if row else None

    def next_pending_resolution(self) -> int | None:
        """Return the next track_id whose resolution work is due, or None.

        Due means state='pending' and (no backoff set, or backoff has elapsed).
        Ordered by enqueue time so the queue drains FIFO.
        """
        row = self.conn.execute(
            """SELECT track_id FROM resolution_queue
               WHERE state = 'pending'
                 AND (next_attempt_at IS NULL OR next_attempt_at <= datetime('now'))
               ORDER BY enqueued_at
               LIMIT 1"""
        ).fetchone()
        return int(row["track_id"]) if row else None

    def set_resolution_state(
        self,
        track_id: int,
        state: str,
        *,
        last_error: str | None = None,
        next_attempt_at: str | None = None,
        increment_attempts: bool = False,
        increment_transient: bool = False,
    ) -> None:
        """Update a queued track's resolution state (and optional backoff/error).

        ``increment_attempts`` bumps the hard-failure counter that drives the
        quarantine ceiling. ``increment_transient`` bumps a SEPARATE counter used
        only for rate-limit backoff, transient throttling must never consume the
        quarantine budget (AC-D7).
        """
        set_clauses = ["state = ?", "last_error = ?", "updated_at = datetime('now')"]
        params: list[Any] = [state, last_error]
        if next_attempt_at is not None:
            set_clauses.append("next_attempt_at = ?")
            params.append(next_attempt_at)
        if increment_attempts:
            set_clauses.append("attempts = attempts + 1")
        if increment_transient:
            set_clauses.append("transient_attempts = transient_attempts + 1")
        with self.conn:
            self.conn.execute(
                f"UPDATE resolution_queue SET {', '.join(set_clauses)} WHERE track_id = ?",  # noqa: S608 - columns from code-controlled allowlist; values parameterized
                (*params, track_id),
            )

    # --- track_candidates (spec section 4.1a: hydrated top-N platform candidates) ---

    def upsert_track_candidate(
        self,
        track_id: int,
        platform: str,
        platform_track_id: str,
        captured_metadata: dict[str, Any] | None,
        discovery_rank: int = 0,
    ) -> None:
        """Insert or update a hydrated platform candidate for a track.

        ``discovery_rank`` records the candidate's position in the discovery
        order so :meth:`get_track_candidates` can return the set in the same
        order the live gather produced, selection keeps input order for default
        band-ties, so preserving it is what guarantees winner parity (AC-P4).
        """
        payload = (
            json.dumps(captured_metadata) if captured_metadata is not None else None
        )
        with self.conn:
            self.conn.execute(
                """INSERT INTO track_candidates
                       (track_id, platform, platform_track_id, captured_metadata,
                        discovery_rank, fetched_at)
                   VALUES (?, ?, ?, ?, ?, datetime('now'))
                   ON CONFLICT(track_id, platform, platform_track_id)
                   DO UPDATE SET captured_metadata = excluded.captured_metadata,
                                 discovery_rank = excluded.discovery_rank,
                                 fetched_at = excluded.fetched_at""",
                (track_id, platform, platform_track_id, payload, discovery_rank),
            )

    def clear_track_candidates(
        self, track_id: int, platform: str | None = None
    ) -> None:
        """Remove persisted candidates for a track (optionally one platform).

        Called before persisting a fresh candidate set so a refresh REPLACES the
        prior set rather than leaving stale rows (and stale ranks) behind.
        """
        with self.conn:
            if platform is None:
                self.conn.execute(
                    "DELETE FROM track_candidates WHERE track_id = ?", (track_id,)
                )
            else:
                self.conn.execute(
                    "DELETE FROM track_candidates WHERE track_id = ? AND platform = ?",
                    (track_id, platform),
                )

    def get_track_candidates(
        self, track_id: int, platform: str | None = None
    ) -> list[dict[str, Any]]:
        """Return hydrated candidates for a track, optionally filtered by platform.

        Ordered by ``discovery_rank`` (then a stable id tiebreak) so callers see
        the persisted set in the original discovery order, the ordering
        selection relies on for default band-tie parity (AC-P4).
        """
        query = "SELECT * FROM track_candidates WHERE track_id = ?"
        params: list[Any] = [track_id]
        if platform is not None:
            query += " AND platform = ?"
            params.append(platform)
        query += " ORDER BY discovery_rank, platform, platform_track_id"
        rows = self.conn.execute(query, params).fetchall()
        result = []
        for row in rows:
            result.append(
                {
                    "track_id": row["track_id"],
                    "platform": row["platform"],
                    "platform_track_id": row["platform_track_id"],
                    "captured_metadata": (
                        json.loads(row["captured_metadata"])
                        if row["captured_metadata"]
                        else None
                    ),
                    "discovery_rank": row["discovery_rank"],
                    "fetched_at": row["fetched_at"],
                }
            )
        return result

    def hydrate_identity_metadata(
        self,
        track_id: int,
        *,
        isrc: str | None = None,
        duration_seconds: int | None = None,
        album: str | None = None,
        confidence_tier: str | None = None,
        confidence_score: float | None = None,
        mb_recording_id: str | None = None,
        mb_release_group_id: str | None = None,
        source: str = "resolver",
    ) -> dict[str, Any]:
        """Promote a resolved candidate's core identity metadata onto the track.

        This is the single source of truth for turning a "resolved" verdict into
        populated ``tracks`` columns (spec AC-D2). It is deliberately conservative:

        * ``isrc``/``duration_seconds``/``album`` use **fill-NULL** semantics,
          a field is written only when the track's current value is NULL/empty,
          so a prior user edit or an earlier higher-signal hydration is never
          clobbered. Idempotent: re-running promotes nothing new.
        * ``confidence_tier``/``confidence_score`` and the MusicBrainz ids are the
          resolver's to own, so they are updated whenever provided, and
          ``resolved_at`` is stamped so the track drops out of ``find_unresolved``.

        Provenance for each *newly filled* column is recorded in
        ``field_provenance`` (AC-D4) so downstream passes can see the source.
        Returns the map of columns actually written (empty when a no-op).
        """
        row = self.conn.execute(
            "SELECT isrc, duration_seconds, album, field_provenance "
            "FROM tracks WHERE id = ?",
            (track_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Track id not found: {track_id}")

        written: dict[str, Any] = {}
        # Fill-NULL only: never overwrite a value the track already holds.
        if isrc and not row["isrc"]:
            written["isrc"] = isrc
        if duration_seconds and not row["duration_seconds"]:
            written["duration_seconds"] = duration_seconds
        if album and not row["album"]:
            written["album"] = album
            written["norm_album"] = normalize_title(album)

        provenance = (
            json.loads(row["field_provenance"]) if row["field_provenance"] else {}
        )
        now = datetime.now(timezone.utc).isoformat()
        for column in written:
            if column == "norm_album":
                continue
            provenance[column] = {"source": source, "at": now}

        set_clauses: list[str] = [f"{col} = ?" for col in written]
        params: list[Any] = list(written.values())

        # Resolution owns identity confidence + MB linkage; refresh when provided.
        if confidence_tier is not None:
            set_clauses.append("confidence_tier = ?")
            params.append(confidence_tier)
        if confidence_score is not None:
            set_clauses.append("confidence_score = ?")
            params.append(confidence_score)
        if mb_recording_id is not None:
            set_clauses.append("mb_recording_id = ?")
            params.append(mb_recording_id)
        if mb_release_group_id is not None:
            set_clauses.append("mb_release_group_id = ?")
            params.append(mb_release_group_id)

        if not set_clauses:
            return {}

        if confidence_tier is not None or confidence_score is not None:
            set_clauses.append("resolved_at = ?")
            params.append(now)
        set_clauses.append("field_provenance = ?")
        params.append(json.dumps(provenance))
        set_clauses.append("updated_at = datetime('now')")

        with self.conn:
            self.conn.execute(
                f"UPDATE tracks SET {', '.join(set_clauses)} WHERE id = ?",  # noqa: S608 - columns from code-controlled allowlist; values parameterized
                (*params, track_id),
            )
        return written

    # --- apply_journal (spec section 7, AC-P4: reversible plan/apply) ---

    def record_journal_entry(
        self,
        *,
        plan_id: str,
        table_name: str,
        row_key: str,
        op: str,
        prior_value: dict[str, Any] | None,
        new_value: dict[str, Any] | None,
    ) -> None:
        """Record one applied write so the plan can be reversed (AC-P4).

        ``prior_value``/``new_value`` are JSON-serialized row snapshots; either
        may be ``None`` (insert has no prior, delete has no new).
        """
        with self.conn:
            self.conn.execute(
                """INSERT INTO apply_journal
                       (plan_id, table_name, row_key, op, prior_value, new_value)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    plan_id,
                    table_name,
                    row_key,
                    op,
                    json.dumps(prior_value) if prior_value is not None else None,
                    json.dumps(new_value) if new_value is not None else None,
                ),
            )

    def get_journal_entries(self, plan_id: str) -> list["JournalEntry"]:
        """Return a plan's journal entries newest-first (reverse-replay order)."""
        rows = self.conn.execute(
            """SELECT id, plan_id, table_name, row_key, op,
                      prior_value, new_value, applied_at
               FROM apply_journal WHERE plan_id = ? ORDER BY id DESC""",
            (plan_id,),
        ).fetchall()
        return [
            JournalEntry(
                id=row["id"],
                plan_id=row["plan_id"],
                table_name=row["table_name"],
                row_key=row["row_key"],
                op=row["op"],
                prior_value=json.loads(row["prior_value"])
                if row["prior_value"]
                else None,
                new_value=json.loads(row["new_value"]) if row["new_value"] else None,
                applied_at=row["applied_at"],
            )
            for row in rows
        ]

    def has_journal(self, plan_id: str) -> bool:
        """Whether any journal entries exist for ``plan_id``."""
        row = self.conn.execute(
            "SELECT 1 FROM apply_journal WHERE plan_id = ? LIMIT 1", (plan_id,)
        ).fetchone()
        return row is not None

    def clear_journal(self, plan_id: str) -> None:
        """Remove a plan's journal entries (after a successful rollback)."""
        with self.conn:
            self.conn.execute("DELETE FROM apply_journal WHERE plan_id = ?", (plan_id,))

    # --- coverage + quarantine surface (spec section 4.4; AC-D1, AC-D6) ---

    # Key first-class fields whose backfill coverage AC-D1 tracks. Fixed
    # allowlist -> safe to interpolate into the fill-rate query below.
    _COVERAGE_FIELDS = (
        "isrc",
        "duration_seconds",
        "album_artist",
        "album_type",
        "label",
        "release_date",
        "audio_modes",
    )

    def coverage_report(self) -> dict[str, Any]:
        """Return backfill coverage and per-field fill rates.

        Coverage uses the AC-D1 denominator ``resolved / (resolved +
        quarantined)``, ``pending`` tracks are excluded so an in-progress
        backfill does not depress the number, and quarantined tracks stay in the
        denominator so quarantine cannot game the floor.
        """
        rows = self.conn.execute(
            "SELECT state, COUNT(*) AS c FROM resolution_queue GROUP BY state"
        ).fetchall()
        counts = {row["state"]: row["c"] for row in rows}
        resolved = counts.get("resolved", 0)
        quarantined = counts.get("quarantined", 0)
        pending = counts.get("pending", 0)
        denom = resolved + quarantined
        coverage = (resolved / denom) if denom else 0.0

        total = self.conn.execute("SELECT COUNT(*) AS c FROM tracks").fetchone()["c"]
        fill: dict[str, float] = {}
        for column in self._COVERAGE_FIELDS:
            if not total:
                fill[column] = 0.0
                continue
            filled = self.conn.execute(
                f"SELECT COUNT(*) AS c FROM tracks "  # noqa: S608 - columns from code-controlled allowlist; values parameterized
                f"WHERE {column} IS NOT NULL AND {column} != ''"
            ).fetchone()["c"]
            fill[column] = filled / total

        return {
            "resolved": resolved,
            "quarantined": quarantined,
            "pending": pending,
            "coverage": coverage,
            "total_tracks": total,
            "field_fill_rates": fill,
        }

    def resolution_status_summary(self) -> dict[str, Any]:
        """Partition the library for the ``resolve --status`` headline.

        Categories are mutually exclusive and sum to ``total`` (quarantine wins
        over a stale tier): ``playable`` (a usable, available mapping),
        ``quarantined`` (unavailable on platform), and ``unresolved`` (no tier
        yet), the last split into tracks that are in a playlist (actionable) vs
        orphaned/no-playlist (library cleanup). Also returns the resolved-tier
        breakdown and a quarantine-reason histogram bucketed by reason prefix.
        """
        total = self.conn.execute("SELECT COUNT(*) AS c FROM tracks").fetchone()["c"]
        quarantined = self.conn.execute(
            "SELECT COUNT(*) AS c FROM tracks WHERE quarantine_state IS NOT NULL"
        ).fetchone()["c"]
        playable = self.conn.execute(
            "SELECT COUNT(*) AS c FROM tracks "
            "WHERE confidence_tier IS NOT NULL AND quarantine_state IS NULL"
        ).fetchone()["c"]
        unresolved_in_playlist = self.conn.execute(
            "SELECT COUNT(*) AS c FROM tracks t "
            "WHERE t.confidence_tier IS NULL AND t.quarantine_state IS NULL "
            "AND EXISTS (SELECT 1 FROM playlist_tracks pt WHERE pt.track_id = t.id)"
        ).fetchone()["c"]
        unresolved_orphaned = self.conn.execute(
            "SELECT COUNT(*) AS c FROM tracks t "
            "WHERE t.confidence_tier IS NULL AND t.quarantine_state IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM playlist_tracks pt WHERE pt.track_id = t.id)"
        ).fetchone()["c"]

        tier_rows = self.conn.execute(
            "SELECT confidence_tier AS tier, COUNT(*) AS c FROM tracks "
            "WHERE confidence_tier IS NOT NULL AND quarantine_state IS NULL "
            "GROUP BY confidence_tier"
        ).fetchall()
        tiers = {row["tier"]: row["c"] for row in tier_rows}

        reason_rows = self.conn.execute(
            """SELECT CASE
                        WHEN instr(quarantine_reason, ':') > 0
                          THEN substr(quarantine_reason, 1, instr(quarantine_reason, ':') - 1)
                        ELSE COALESCE(quarantine_reason, 'unknown')
                      END AS bucket,
                      COUNT(*) AS c
               FROM tracks WHERE quarantine_state IS NOT NULL
               GROUP BY bucket ORDER BY c DESC, bucket"""
        ).fetchall()
        quarantine_reasons = [(row["bucket"], row["c"]) for row in reason_rows]

        return {
            "total": total,
            "playable": playable,
            "quarantined": quarantined,
            "unresolved_in_playlist": unresolved_in_playlist,
            "unresolved_orphaned": unresolved_orphaned,
            "playable_pct": (playable / total) if total else 0.0,
            "tiers": tiers,
            "quarantine_reasons": quarantine_reasons,
        }

    def per_playlist_coverage(self) -> list[dict[str, Any]]:
        """Per-playlist resolution coverage, lowest playable-fraction first.

        Each row partitions the playlist's distinct tracks into ``playable`` /
        ``quarantined`` / ``unresolved`` (same rule as the headline). ``pct`` is
        ``playable / total``. Playlists whose only gap is quarantined-unavailable
        tracks (``unresolved == 0`` and ``quarantined > 0``) are "done as they can
        be"; a nonzero ``unresolved`` is the call to run ``resolve``.
        """
        rows = self.conn.execute(
            """SELECT p.name AS name,
                      COUNT(DISTINCT pt.track_id) AS total,
                      COUNT(DISTINCT CASE
                          WHEN t.confidence_tier IS NOT NULL AND t.quarantine_state IS NULL
                          THEN t.id END) AS playable,
                      COUNT(DISTINCT CASE
                          WHEN t.quarantine_state IS NOT NULL
                          THEN t.id END) AS quarantined,
                      COUNT(DISTINCT CASE
                          WHEN t.confidence_tier IS NULL AND t.quarantine_state IS NULL
                          THEN t.id END) AS unresolved
               FROM playlists p
               JOIN playlist_tracks pt ON pt.playlist_id = p.id
               JOIN tracks t ON t.id = pt.track_id
               GROUP BY p.id, p.name"""
        ).fetchall()
        result = [
            {
                "name": row["name"],
                "total": row["total"],
                "playable": row["playable"],
                "quarantined": row["quarantined"],
                "unresolved": row["unresolved"],
                "pct": (row["playable"] / row["total"]) if row["total"] else 0.0,
            }
            for row in rows
        ]
        result.sort(key=lambda r: (r["pct"], r["name"]))
        return result

    def get_quarantined_tracks(self) -> list[dict[str, Any]]:
        """List quarantined tracks with machine-readable reasons (AC-D6)."""
        rows = self.conn.execute(
            """SELECT t.id, t.title, t.artist, t.quarantine_reason,
                      rq.last_error
               FROM tracks t
               LEFT JOIN resolution_queue rq ON rq.track_id = t.id
               WHERE t.quarantine_state IS NOT NULL
               ORDER BY t.artist, t.title"""
        ).fetchall()
        return [
            {
                "track_id": row["id"],
                "title": row["title"],
                "artist": row["artist"],
                "reason": row["quarantine_reason"] or row["last_error"] or "",
            }
            for row in rows
        ]

    def get_selectable_track_ids(self, playlist_id: int) -> list[int]:
        """Return a playlist's track ids that are eligible for selection.

        Quarantined tracks (``quarantine_state`` set) are excluded until they
        are resolved or manually approved (AC-D6). Order is preserved.
        """
        rows = self.conn.execute(
            """SELECT pt.track_id
               FROM playlist_tracks pt
               JOIN tracks t ON t.id = pt.track_id
               WHERE pt.playlist_id = ?
                 AND t.quarantine_state IS NULL
               ORDER BY pt.position""",
            (playlist_id,),
        ).fetchall()
        return [row[0] for row in rows]

    # --- playlist_track_mappings (spec section 4.1a: per-playlist release override) ---

    def set_playlist_track_mapping(
        self,
        playlist_id: int,
        track_id: int,
        platform: str,
        platform_track_id: str,
        *,
        source: str | None = None,
        user_approved: bool = False,
    ) -> None:
        """Set the per-playlist platform release for a track (upsert on PK)."""
        with self.conn:
            self.conn.execute(
                """INSERT INTO playlist_track_mappings
                       (playlist_id, track_id, platform, platform_track_id,
                        source, user_approved, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                   ON CONFLICT(playlist_id, track_id, platform)
                   DO UPDATE SET platform_track_id = excluded.platform_track_id,
                                 source = excluded.source,
                                 user_approved = excluded.user_approved,
                                 updated_at = excluded.updated_at""",
                (
                    playlist_id,
                    track_id,
                    platform,
                    platform_track_id,
                    source,
                    1 if user_approved else 0,
                ),
            )

    def get_playlist_track_mapping(
        self, playlist_id: int, track_id: int, platform: str
    ) -> dict[str, Any] | None:
        """Return the per-playlist release override for a track, or None."""
        row = self.conn.execute(
            """SELECT * FROM playlist_track_mappings
               WHERE playlist_id = ? AND track_id = ? AND platform = ?""",
            (playlist_id, track_id, platform),
        ).fetchone()
        if row is None:
            return None
        return {
            "playlist_id": row["playlist_id"],
            "track_id": row["track_id"],
            "platform": row["platform"],
            "platform_track_id": row["platform_track_id"],
            "source": row["source"],
            "user_approved": bool(row["user_approved"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    # --- two-level identity-lock resolution (spec section 8, AC-L1/L4) ---

    def get_effective_lock(
        self, track_id: int, platform: str, playlist_id: int | None = None
    ) -> EffectiveLock | None:
        """Resolve the effective identity lock for a track on a platform (AC-L1/L4).

        A lock is two-level: a per-playlist override (``playlist_track_mappings``
        with ``user_approved=1``) takes precedence over the library-wide default
        lock (``platform_tracks`` with ``user_approved=1``). Returns ``None`` when
        neither level is locked. The result carries the composite identity
        (platform-id + ISRC + fingerprint) so selection can honour the lock even
        after a platform re-ID.

        Only an ``user_approved`` mapping is a lock; an auto-matched (unapproved)
        per-playlist row does NOT shadow a global lock, it falls through to the
        global default.
        """
        track = self.get_track(track_id)
        isrc = track.isrc if track is not None else None
        global_mapping = self.get_platform_mapping(track_id, platform)

        if playlist_id is not None:
            pl = self.get_playlist_track_mapping(playlist_id, track_id, platform)
            if pl is not None and pl["user_approved"] and pl["platform_track_id"]:
                # A per-playlist override reuses the global mapping's fingerprint
                # only when it points at the SAME release; otherwise the override
                # is a distinct release and carries no cached fingerprint yet.
                fingerprint = None
                if (
                    global_mapping is not None
                    and global_mapping.platform_track_id == pl["platform_track_id"]
                ):
                    fingerprint = global_mapping.fingerprint
                return EffectiveLock(
                    platform_track_id=pl["platform_track_id"],
                    scope="playlist",
                    isrc=isrc,
                    fingerprint=fingerprint,
                    status="matched",
                )

        if (
            global_mapping is not None
            and global_mapping.user_approved
            and global_mapping.platform_track_id
        ):
            return EffectiveLock(
                platform_track_id=global_mapping.platform_track_id,
                scope="global",
                isrc=isrc,
                fingerprint=global_mapping.fingerprint,
                status=global_mapping.status or "matched",
                is_divergent=global_mapping.is_divergent,
                divergence_note=global_mapping.divergence_note,
                match_score=global_mapping.match_score,
            )
        return None

    def get_global_locks(self) -> list[dict]:
        """All library-wide default locks (``platform_tracks`` ``user_approved=1``).

        Returns one dict per (track, platform) lock with the track title/artist
        for display, ordered by track then platform. Drives ``locks list``
        (AC-CLI4) at global scope.
        """
        rows = self.conn.execute(
            """SELECT pt.track_id, pt.platform, pt.platform_track_id, pt.status,
                      t.title, t.artist
               FROM platform_tracks pt
               JOIN tracks t ON t.id = pt.track_id
               WHERE pt.user_approved = 1 AND pt.platform_track_id != ''
               ORDER BY t.title, pt.platform"""
        ).fetchall()
        return [
            {
                "track_id": r["track_id"],
                "platform": r["platform"],
                "platform_track_id": r["platform_track_id"],
                "status": r["status"],
                "title": r["title"],
                "artist": r["artist"],
            }
            for r in rows
        ]

    def get_playlist_locks(self, playlist_id: int) -> list[dict]:
        """All per-playlist override locks for a playlist (``user_approved=1``).

        Returns one dict per (track, platform) override lock. These win over the
        global default for the playlist; ``locks list --playlist`` renders both
        layers with precedence (AC-CLI4).
        """
        rows = self.conn.execute(
            """SELECT m.track_id, m.platform, m.platform_track_id,
                      t.title, t.artist
               FROM playlist_track_mappings m
               JOIN tracks t ON t.id = m.track_id
               WHERE m.playlist_id = ? AND m.user_approved = 1
                     AND m.platform_track_id != ''
               ORDER BY t.title, m.platform""",
            (playlist_id,),
        ).fetchall()
        return [
            {
                "track_id": r["track_id"],
                "platform": r["platform"],
                "platform_track_id": r["platform_track_id"],
                "title": r["title"],
                "artist": r["artist"],
            }
            for r in rows
        ]

    # --- playlist_track_prefs (spec section 4.1a: most-specific preference scope) ---

    def set_playlist_track_pref(
        self,
        playlist_id: int | None,
        track_id: int,
        criterion: str,
        strength: str,
        target: str | None = None,
    ) -> None:
        """Upsert one preference at the ``(playlist_id, track_id, criterion,
        target)`` scope.

        A ``None`` ``playlist_id`` denotes a playlist-agnostic per-track
        preference (it applies to the track on every playlist). Keying on
        ``target`` (not just ``criterion``) is what lets multiple targets coexist
        on one axis, e.g. ``content avoid karaoke`` and ``content avoid
        instrumental``, instead of the second overwriting the first. Re-setting
        the same ``(scope, criterion, target)`` replaces its strength in place.

        Uses a NULL-safe delete-then-insert (``IS`` matches NULL) rather than
        ``ON CONFLICT`` so the ``COALESCE`` unique index and the NULLable
        ``playlist_id`` are both honoured.
        """
        with self.conn:
            self.conn.execute(
                """DELETE FROM playlist_track_prefs
                   WHERE playlist_id IS ? AND track_id = ?
                     AND criterion = ? AND target IS ?""",
                (playlist_id, track_id, criterion, target),
            )
            self.conn.execute(
                """INSERT INTO playlist_track_prefs
                       (playlist_id, track_id, criterion, strength, target)
                   VALUES (?, ?, ?, ?, ?)""",
                (playlist_id, track_id, criterion, strength, target),
            )

    def get_playlist_track_prefs(
        self, playlist_id: int | None, track_id: int
    ) -> list[dict[str, Any]]:
        """Return the preferences stored at exactly this scope for the track.

        ``playlist_id=None`` returns the playlist-agnostic per-track rows (see
        :meth:`get_track_global_prefs`); a concrete id returns only that
        playlist's own rows (NULL rows are a distinct scope and never leak in).
        """
        rows = self.conn.execute(
            """SELECT criterion, strength, target FROM playlist_track_prefs
               WHERE playlist_id IS ? AND track_id = ?
               ORDER BY criterion, target""",
            (playlist_id, track_id),
        ).fetchall()
        return [
            {
                "criterion": r["criterion"],
                "strength": r["strength"],
                "target": r["target"],
            }
            for r in rows
        ]

    def get_track_global_prefs(self, track_id: int) -> list[dict[str, Any]]:
        """Return the playlist-agnostic per-track preferences (NULL playlist).

        These apply to the track on every playlist and form the folded successor
        to the retired ``tracks.preferences`` blob (FL3 decision #4).
        """
        return self.get_playlist_track_prefs(None, track_id)

    def remove_playlist_track_pref(
        self,
        playlist_id: int | None,
        track_id: int,
        criterion: str,
        target: str | None = None,
    ) -> bool:
        """Delete preference(s) for a criterion at the given scope.

        With ``target`` omitted, every target on the criterion is removed; with a
        ``target`` given, only that exact ``(criterion, target)`` row. Returns
        True if at least one row was deleted. NULL-safe on ``playlist_id``.
        """
        with self.conn:
            if target is None:
                cur = self.conn.execute(
                    """DELETE FROM playlist_track_prefs
                       WHERE playlist_id IS ? AND track_id = ? AND criterion = ?""",
                    (playlist_id, track_id, criterion),
                )
            else:
                cur = self.conn.execute(
                    """DELETE FROM playlist_track_prefs
                       WHERE playlist_id IS ? AND track_id = ?
                         AND criterion = ? AND target IS ?""",
                    (playlist_id, track_id, criterion, target),
                )
        return cur.rowcount > 0

    def update_track_metadata(self, track_id: int, meta: dict) -> None:
        """Update a track's audio metadata fields from enrichment data."""
        # Remap LLM field name to internal field name
        if "confidence" in meta and "classification_confidence" not in meta:
            meta["classification_confidence"] = meta.pop("confidence")
        updates = []
        params = []
        if "tempo" in meta:
            updates.append("tempo = ?")
            params.append(meta["tempo"])
        if "key" in meta:
            updates.append("key = ?")
            params.append(meta["key"])
        # The explicit "key in meta and meta[key]" form is kept over dict.get():
        # callers (and tests) may pass mapping-like objects whose __contains__ is
        # authoritative, so collapsing to .get() would change truthiness semantics.
        if "duration_seconds" in meta and meta["duration_seconds"]:  # noqa: RUF019
            updates.append("duration_seconds = ?")
            params.append(meta["duration_seconds"])
        if "isrc" in meta and meta["isrc"]:  # noqa: RUF019
            updates.append("isrc = ?")
            params.append(meta["isrc"])
        # Store extra fields in metadata JSON
        _METADATA_KEYS = (
            "key_scale",
            "energy",
            "valence",
            "themes",
            "vibes",
            "instruments",
            "density",
            "era_mood",
            "emotional_intensity",
            "lyrical_subject",
            "narrator_stance",
            "sonic_texture",
            "space",
            "groove_feel",
            "opens_with",
            "closes_with",
            "energy_arc_within",
            "classification_confidence",
        )
        track = self.get_track(track_id)
        if track:
            existing_meta = dict(track.metadata) if track.metadata else {}
            for k in _METADATA_KEYS:
                if k in meta:
                    existing_meta[k] = meta[k]
            if existing_meta != (track.metadata or {}):
                updates.append("metadata = ?")
                import json as _json

                params.append(_json.dumps(existing_meta))
        if updates:
            params.append(track_id)
            self.conn.execute(
                f"UPDATE tracks SET {', '.join(updates)} WHERE id = ?",  # noqa: S608 - columns from code-controlled allowlist; values parameterized
                params,
            )
            self.conn.commit()

    def close(self) -> None:
        """Close the database connection if it is open."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def add_track_to_playlist(
        self, playlist_id: int, track_id: int, position: int
    ) -> None:
        """Add a track at a specific position (upsert, no error on conflict)."""
        self.conn.execute(
            """INSERT OR REPLACE INTO playlist_tracks (playlist_id, track_id, position)
               VALUES (?, ?, ?)""",
            (playlist_id, track_id, position),
        )
        self.conn.commit()

    def link_platform_playlist(
        self,
        playlist_id: int,
        platform: str,
        platform_playlist_id: str,
        *,
        commit: bool = True,
    ) -> None:
        """Link a canonical playlist to a platform playlist.

        Uses ON CONFLICT DO UPDATE rather than INSERT OR REPLACE so re-linking an
        already-synced playlist preserves ``last_synced_at`` (INSERT OR REPLACE
        deletes the row and resets the timestamp to NULL, making status report
        "never synced" for a genuinely-synced playlist -- BUG-6b).

        ``commit=False`` links within a caller-managed transaction (e.g. the
        plan/apply push) so the link is journaled and reversible with the push.
        """
        self.conn.execute(
            """INSERT INTO platform_playlists (playlist_id, platform, platform_playlist_id)
               VALUES (?, ?, ?)
               ON CONFLICT(playlist_id, platform) DO UPDATE SET
               platform_playlist_id = excluded.platform_playlist_id""",
            (playlist_id, platform, platform_playlist_id),
        )
        if commit:
            self.conn.commit()

    def get_linked_platforms(self, playlist_id: int) -> list[str]:
        """Return platform names linked to this playlist."""
        rows = self.conn.execute(
            "SELECT platform FROM platform_playlists WHERE playlist_id = ?",
            (playlist_id,),
        ).fetchall()
        return [row["platform"] for row in rows]

    def get_platform_playlist_id(self, playlist_id: int, platform: str) -> str | None:
        """Get the platform-specific playlist ID."""
        row = self.conn.execute(
            "SELECT platform_playlist_id FROM platform_playlists WHERE playlist_id = ? AND platform = ?",
            (playlist_id, platform),
        ).fetchone()
        return row["platform_playlist_id"] if row else None

    # --- ARCH-M3: named query methods -------------------------------------
    # These route raw SQL out of the command/planapply/library layers so the
    # SQL surface stays inside the persistence boundary (enforced by
    # tests/test_lint_regressions.py::test_no_conn_execute_leak). Write methods
    # here DO NOT commit: every current caller composes them inside a
    # caller-managed transaction and commits explicitly, so an internal commit
    # would change transaction granularity. Callers that want autocommit pass
    # ``commit=True``.

    def all_track_ids(self) -> list[int]:
        """Return every track id in the library."""
        return [row[0] for row in self.conn.execute("SELECT id FROM tracks")]

    def get_resolution_attempts(
        self, track_id: int, *, transient: bool = False
    ) -> int:
        """Return the stored (hard or transient) attempt count for a queued track.

        Returns 0 when the track has no resolution_queue row. ``transient`` reads
        the rate-limit backoff counter instead of the hard-failure counter.
        """
        counter = "transient_attempts" if transient else "attempts"
        row = self.conn.execute(
            f"SELECT {counter} AS n FROM resolution_queue WHERE track_id = ?",  # noqa: S608 - counter is a code-controlled literal; value parameterized
            (track_id,),
        ).fetchone()
        return int(row["n"]) if row else 0

    def get_track_position(self, playlist_id: int, track_id: int) -> int | None:
        """Return a track's position in a playlist, or None if it is not present."""
        row = self.conn.execute(
            "SELECT position FROM playlist_tracks WHERE playlist_id = ? AND track_id = ?",
            (playlist_id, track_id),
        ).fetchone()
        return int(row["position"]) if row else None

    def get_playlist_track_positions(self, playlist_id: int) -> list[int]:
        """Return the ordered positions of every row in a playlist."""
        return [
            row["position"]
            for row in self.conn.execute(
                "SELECT position FROM playlist_tracks WHERE playlist_id = ? "
                "ORDER BY position",
                (playlist_id,),
            )
        ]

    def get_max_playlist_position(self, playlist_id: int) -> int:
        """Return the highest position in a playlist, or 0 if the playlist is empty."""
        row = self.conn.execute(
            "SELECT MAX(position) FROM playlist_tracks WHERE playlist_id = ?",
            (playlist_id,),
        ).fetchone()
        return (row[0] if row else 0) or 0

    def get_playlist_reorder_config(
        self, playlist_id: int
    ) -> tuple[int, str | None] | None:
        """Return ``(auto_reorder, reorder_arc)`` for a playlist, or None if absent."""
        row = self.conn.execute(
            "SELECT auto_reorder, reorder_arc FROM playlists WHERE id = ?",
            (playlist_id,),
        ).fetchone()
        if row is None:
            return None
        return row["auto_reorder"], row["reorder_arc"]

    def get_playlist_name(self, playlist_id: int) -> str | None:
        """Return a playlist's name, or None if no such playlist exists."""
        row = self.conn.execute(
            "SELECT name FROM playlists WHERE id = ?", (playlist_id,)
        ).fetchone()
        return row["name"] if row else None

    def get_playlist_name_description(
        self, playlist_id: int
    ) -> tuple[str, str | None] | None:
        """Return ``(name, description)`` for a playlist, or None if absent."""
        row = self.conn.execute(
            "SELECT name, description FROM playlists WHERE id = ?", (playlist_id,)
        ).fetchone()
        if row is None:
            return None
        return row["name"], row["description"]

    def get_track_title(self, track_id: int) -> str | None:
        """Return a track's title, or None if no such track exists."""
        row = self.conn.execute(
            "SELECT title FROM tracks WHERE id = ?", (track_id,)
        ).fetchone()
        return row["title"] if row else None

    def get_platform_track_ids(
        self, platform: str, *, approved_only: bool = False
    ) -> list[int]:
        """Return track ids mapped on a platform, ordered by track_id.

        ``approved_only`` restricts to rows with ``user_approved = 1``.
        """
        if approved_only:
            rows = self.conn.execute(
                "SELECT track_id FROM platform_tracks "
                "WHERE platform = ? AND user_approved = 1 ORDER BY track_id",
                (platform,),
            )
        else:
            rows = self.conn.execute(
                "SELECT track_id FROM platform_tracks WHERE platform = ? "
                "ORDER BY track_id",
                (platform,),
            )
        return [row["track_id"] for row in rows]

    def get_platform_tracks_columns(self) -> list[str]:
        """Return the platform_tracks column names in schema order."""
        return [
            row[1]
            for row in self.conn.execute("PRAGMA table_info(platform_tracks)")
        ]

    def get_platform_tracks_for_track(self, track_id: int) -> list[sqlite3.Row]:
        """Return every platform_tracks row for a track (all columns)."""
        return self.conn.execute(
            "SELECT * FROM platform_tracks WHERE track_id = ?", (track_id,)
        ).fetchall()

    def get_latest_batch_history(self) -> sqlite3.Row | None:
        """Return the most recent un-reverted batch_history row, or None."""
        return self.conn.execute(
            "SELECT id, playlist_id, plan_json FROM batch_history "
            "WHERE reverted_at IS NULL ORDER BY applied_at DESC LIMIT 1"
        ).fetchone()

    def get_batch_history_entry(self, history_id: int) -> sqlite3.Row | None:
        """Return a batch_history row by id, or None if it does not exist."""
        return self.conn.execute(
            "SELECT id, playlist_id, plan_json FROM batch_history WHERE id = ?",
            (history_id,),
        ).fetchone()

    def list_batch_history_entries(
        self, *, playlist_id: int | None = None, limit: int = 20
    ) -> list[sqlite3.Row]:
        """Return recent batch_history rows, newest first.

        Scoped to ``playlist_id`` when given (returning all of that playlist's
        history); otherwise the ``limit`` most recent rows across all playlists.
        """
        if playlist_id is not None:
            return self.conn.execute(
                "SELECT id, playlist_id, applied_at, reverted_at, plan_json "
                "FROM batch_history WHERE playlist_id = ? ORDER BY applied_at DESC",
                (playlist_id,),
            ).fetchall()
        return self.conn.execute(
            "SELECT id, playlist_id, applied_at, reverted_at, plan_json "
            "FROM batch_history ORDER BY applied_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

    def set_track_metadata(
        self, track_id: int, metadata: dict, *, commit: bool = False
    ) -> None:
        """Overwrite a track's full metadata JSON blob.

        Distinct from :meth:`update_track_metadata`, which remaps individual
        audio fields; this writes the entire ``metadata`` column verbatim.
        """
        self.conn.execute(
            "UPDATE tracks SET metadata = ? WHERE id = ?",
            (json.dumps(metadata), track_id),
        )
        if commit:
            self.conn.commit()

    def append_playlist_track(
        self, playlist_id: int, track_id: int, position: int, *, commit: bool = False
    ) -> None:
        """Insert a playlist row at ``position`` (plain INSERT, errors on conflict).

        Unlike :meth:`add_track_to_playlist` (INSERT OR REPLACE), this refuses to
        silently overwrite an occupied position, so callers that pre-check for a
        free slot surface a genuine conflict instead of clobbering a row.
        """
        self.conn.execute(
            "INSERT INTO playlist_tracks (playlist_id, track_id, position) "
            "VALUES (?, ?, ?)",
            (playlist_id, track_id, position),
        )
        if commit:
            self.conn.commit()

    def insert_playlist_track_if_absent(
        self, playlist_id: int, track_id: int, position: int, *, commit: bool = False
    ) -> None:
        """INSERT OR IGNORE a playlist row (no-op if the position is taken)."""
        self.conn.execute(
            "INSERT OR IGNORE INTO playlist_tracks (playlist_id, track_id, position) "
            "VALUES (?, ?, ?)",
            (playlist_id, track_id, position),
        )
        if commit:
            self.conn.commit()

    def shift_playlist_position(
        self,
        playlist_id: int,
        from_position: int,
        to_position: int,
        *,
        commit: bool = False,
    ) -> None:
        """Move the row currently at ``from_position`` to ``to_position``."""
        self.conn.execute(
            "UPDATE playlist_tracks SET position = ? "
            "WHERE playlist_id = ? AND position = ?",
            (to_position, playlist_id, from_position),
        )
        if commit:
            self.conn.commit()

    def set_playlist_track_position(
        self, playlist_id: int, track_id: int, position: int, *, commit: bool = False
    ) -> None:
        """Set a specific track's position within a playlist."""
        self.conn.execute(
            "UPDATE playlist_tracks SET position = ? "
            "WHERE playlist_id = ? AND track_id = ?",
            (position, playlist_id, track_id),
        )
        if commit:
            self.conn.commit()

    def remove_playlist_track(
        self, playlist_id: int, track_id: int, *, commit: bool = False
    ) -> None:
        """Delete every row for a track_id within a playlist."""
        self.conn.execute(
            "DELETE FROM playlist_tracks WHERE playlist_id = ? AND track_id = ?",
            (playlist_id, track_id),
        )
        if commit:
            self.conn.commit()

    def delete_playlist(self, playlist_id: int, *, commit: bool = False) -> None:
        """Delete a playlist row by id."""
        self.conn.execute(
            "DELETE FROM playlists WHERE id = ?", (playlist_id,)
        )
        if commit:
            self.conn.commit()

    def clear_all_tidal_folder_assignments(self, *, commit: bool = False) -> None:
        """Clear every playlist's cached Tidal folder id (full re-sync prelude)."""
        self.conn.execute("UPDATE playlists SET tidal_folder_id = NULL")
        if commit:
            self.conn.commit()

    # --- ARCH-M3: spec-driven generic CRUD (plan/apply journal engine) -----
    # The plan/apply engine mutates arbitrary allowlisted tables; these methods
    # own the SQL construction so planapply/apply.py holds no raw SQL. Table and
    # column identifiers come only from a TableSpec allowlist; every value is
    # parameterized. Callers validate the payload columns before calling and
    # manage the surrounding transaction (no internal commit).

    def read_spec_columns(
        self,
        spec: TableSpec,
        columns: Sequence[str],
        pk_values: dict[str, Any],
    ) -> sqlite3.Row | None:
        """SELECT ``columns`` from a spec table for one primary-key tuple.

        ``columns`` must be a subset of ``spec.all_columns`` (guarded here as
        defense in depth; callers already derive them from the spec).
        """
        unknown = set(columns) - set(spec.all_columns)
        if unknown:
            raise ValueError(
                f"Columns {sorted(unknown)} not in spec for {spec.name!r}"
            )
        where = " AND ".join(f"{col} = ?" for col in spec.pk)
        col_list = ", ".join(columns)
        return self.conn.execute(
            f"SELECT {col_list} FROM {spec.name} WHERE {where}",  # noqa: S608 - identifiers from spec allowlist; values parameterized
            tuple(pk_values[col] for col in spec.pk),
        ).fetchone()

    def insert_spec_row(
        self, spec: TableSpec, proposed: dict[str, Any], *, commit: bool = False
    ) -> None:
        """Insert a spec row, upserting on the primary key.

        Only columns present in ``proposed`` are written; on PK conflict the
        non-PK supplied columns are updated (or DO NOTHING when none remain).
        """
        cols = [c for c in spec.all_columns if c in proposed]
        placeholders = ", ".join("?" for _ in cols)
        col_list = ", ".join(cols)
        updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in spec.pk)
        pk_list = ", ".join(spec.pk)
        conflict = (
            f" ON CONFLICT({pk_list}) DO UPDATE SET {updates}"
            if updates
            else f" ON CONFLICT({pk_list}) DO NOTHING"
        )
        sql = f"INSERT INTO {spec.name} ({col_list}) VALUES ({placeholders}){conflict}"  # noqa: S608 - identifiers from spec allowlist; values parameterized
        self.conn.execute(sql, tuple(proposed[c] for c in cols))
        if commit:
            self.conn.commit()

    def update_spec_row(
        self, spec: TableSpec, proposed: dict[str, Any], *, commit: bool = False
    ) -> None:
        """Update only the supplied non-PK columns of an existing spec row."""
        set_cols = [c for c in spec.columns if c in proposed]
        if not set_cols:
            return
        assignments = ", ".join(f"{c} = ?" for c in set_cols)
        where = " AND ".join(f"{col} = ?" for col in spec.pk)
        params = [proposed[c] for c in set_cols] + [proposed[col] for col in spec.pk]
        self.conn.execute(
            f"UPDATE {spec.name} SET {assignments} WHERE {where}",  # noqa: S608 - identifiers from spec allowlist; values parameterized
            tuple(params),
        )
        if commit:
            self.conn.commit()

    def delete_spec_row(
        self, spec: TableSpec, pk_values: dict[str, Any], *, commit: bool = False
    ) -> None:
        """Delete a spec row identified by its primary-key tuple."""
        where = " AND ".join(f"{col} = ?" for col in spec.pk)
        self.conn.execute(
            f"DELETE FROM {spec.name} WHERE {where}",  # noqa: S608 - identifiers from spec allowlist; values parameterized
            tuple(pk_values[col] for col in spec.pk),
        )
        if commit:
            self.conn.commit()

    def mark_playlist_synced(self, playlist_id: int, platform: str) -> None:
        """Record that a playlist was successfully pushed to a platform.

        Call this ONLY after the platform push has succeeded, so the stored
        sync timestamp never claims a playlist is mirrored when the push failed.
        """
        self.conn.execute(
            "UPDATE platform_playlists SET last_synced_at = datetime('now') "
            "WHERE playlist_id = ? AND platform = ?",
            (playlist_id, platform),
        )
        self.conn.commit()

    def get_last_synced(self, playlist_id: int, platform: str) -> str | None:
        """Return the last successful push timestamp, or None if never synced."""
        row = self.conn.execute(
            "SELECT last_synced_at FROM platform_playlists WHERE playlist_id = ? AND platform = ?",
            (playlist_id, platform),
        ).fetchone()
        return row["last_synced_at"] if row else None

    def find_playlist_by_name(self, name: str) -> Playlist | None:
        """Find a playlist by exact name."""
        row = self.conn.execute(
            "SELECT * FROM playlists WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_playlist(row)

    def set_goal(self, playlist_id: int, goal: str | None) -> None:
        """Set the goal for a playlist."""
        self.conn.execute(
            "UPDATE playlists SET goal = ? WHERE id = ?", (goal, playlist_id)
        )
        self.conn.commit()

    def get_goal(self, playlist_id: int) -> str | None:
        """Get the goal for a playlist."""
        row = self.conn.execute(
            "SELECT goal FROM playlists WHERE id = ?", (playlist_id,)
        ).fetchone()
        return row[0] if row else None

    def set_collection(self, playlist_id: int, collection: str | None) -> None:
        """Set the collection a playlist belongs to (e.g., Pride, Laurel Canyon)."""
        self.conn.execute(
            "UPDATE playlists SET collection = ? WHERE id = ?",
            (collection, playlist_id),
        )
        self.conn.commit()

    def get_collection(self, playlist_id: int) -> str | None:
        """Get the collection a playlist belongs to."""
        row = self.conn.execute(
            "SELECT collection FROM playlists WHERE id = ?", (playlist_id,)
        ).fetchone()
        return row[0] if row else None

    def list_collections(self) -> list[str]:
        """List all distinct collections."""
        rows = self.conn.execute(
            "SELECT DISTINCT collection FROM playlists WHERE collection IS NOT NULL ORDER BY collection"
        ).fetchall()
        return [r[0] for r in rows]

    def get_playlists_in_collection(self, collection: str) -> list:
        """Get all playlists belonging to a collection."""
        return [
            p for p in self.list_playlists() if self.get_collection(p.id) == collection
        ]

    def set_weights(self, playlist_id: int, weights: dict | None) -> None:
        """Set the weights for a playlist."""
        val = json.dumps(weights) if weights else None
        self.conn.execute(
            "UPDATE playlists SET weights = ? WHERE id = ?", (val, playlist_id)
        )
        self.conn.commit()

    def get_weights(self, playlist_id: int) -> dict | None:
        """Get the weights for a playlist."""
        row = self.conn.execute(
            "SELECT weights FROM playlists WHERE id = ?", (playlist_id,)
        ).fetchone()
        return json.loads(row[0]) if row and row[0] else None

    def set_constraints(self, playlist_id: int, constraints: dict | None) -> None:
        """Set the curation constraints for a playlist."""
        val = json.dumps(constraints) if constraints else None
        self.conn.execute(
            "UPDATE playlists SET curation_constraints = ? WHERE id = ?",
            (val, playlist_id),
        )
        self.conn.commit()

    def get_constraints(self, playlist_id: int) -> dict | None:
        """Get the curation constraints for a playlist."""
        row = self.conn.execute(
            "SELECT curation_constraints FROM playlists WHERE id = ?", (playlist_id,)
        ).fetchone()
        return json.loads(row[0]) if row and row[0] else None

    def set_preferences(self, playlist_id: int, prefs: dict | None) -> None:
        """Set the preferences for a playlist."""
        val = json.dumps(prefs) if prefs else None
        self.conn.execute(
            "UPDATE playlists SET preferences = ? WHERE id = ?", (val, playlist_id)
        )
        self.conn.commit()

    def get_preferences(self, playlist_id: int) -> dict | None:
        """Get the preferences for a playlist."""
        row = self.conn.execute(
            "SELECT preferences FROM playlists WHERE id = ?", (playlist_id,)
        ).fetchone()
        return json.loads(row[0]) if row and row[0] else None

    def set_global_preferences(self, prefs: dict | None) -> None:
        """Set the account-wide default preferences (schema_meta key/value)."""
        if prefs:
            self.conn.execute(
                "INSERT INTO schema_meta (key, value) VALUES ('global_preferences', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (json.dumps(prefs),),
            )
        else:
            self.conn.execute(
                "DELETE FROM schema_meta WHERE key = 'global_preferences'"
            )
        self.conn.commit()

    def get_global_preferences(self) -> dict | None:
        """Get the account-wide default preferences, or None if unset."""
        row = self.conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'global_preferences'"
        ).fetchone()
        return json.loads(row[0]) if row and row[0] else None

    def add_artist_alias(self, members: Sequence[str]) -> None:
        """Add (or extend) an artist-alias equivalence class.

        ``members`` are raw surface forms (e.g. ``["98 Degrees", "98\u00ba"]``);
        surrounding whitespace is trimmed but case and glyphs are preserved so
        the exact spelling is retained for retrieval query expansion. At least
        two *distinct* raw members are required. If any member's normalized key
        already belongs to an existing class, the new members are merged into it
        (bridging several classes into the lowest ``class_id`` when they
        overlap); duplicate ``(class_id, member)`` rows are ignored. Idempotent.
        """
        trimmed = [m.strip() for m in members if m and m.strip()]
        distinct = set(trimmed)
        if len(distinct) < 2:
            raise ValueError("an alias class needs at least two distinct members")
        norms = {_alias_normalize(m) for m in distinct}
        placeholders = ",".join("?" * len(norms))
        with self.conn:
            rows = self.conn.execute(
                f"SELECT DISTINCT class_id FROM artist_aliases "  # noqa: S608 - placeholders are bound '?' params; values parameterized
                f"WHERE norm_member IN ({placeholders})",
                tuple(norms),
            ).fetchall()
            existing = sorted(r[0] for r in rows)
            if existing:
                target = existing[0]
                for other in existing[1:]:
                    self.conn.execute(
                        "UPDATE OR IGNORE artist_aliases SET class_id = ? "
                        "WHERE class_id = ?",
                        (target, other),
                    )
                    self.conn.execute(
                        "DELETE FROM artist_aliases WHERE class_id = ?", (other,)
                    )
            else:
                target = self.conn.execute(
                    "SELECT COALESCE(MAX(class_id), 0) + 1 FROM artist_aliases"
                ).fetchone()[0]
            for member in distinct:
                self.conn.execute(
                    "INSERT OR IGNORE INTO artist_aliases "
                    "(class_id, member, norm_member) VALUES (?, ?, ?)",
                    (target, member, _alias_normalize(member)),
                )

    def get_artist_alias_classes(self) -> list[frozenset[str]]:
        """Return every user-curated alias class as a frozenset of raw members."""
        rows = self.conn.execute(
            "SELECT class_id, member FROM artist_aliases ORDER BY class_id"
        ).fetchall()
        classes: dict[int, set[str]] = {}
        for class_id, member in rows:
            classes.setdefault(class_id, set()).add(member)
        return [frozenset(members) for members in classes.values()]

    def remove_artist_alias(self, member: str) -> bool:
        """Remove a raw alias member; drop the class if it falls below 2 members.

        Matches ``member`` exactly after trimming surrounding whitespace. Returns
        True if a row was removed, False if the member is absent from the DB
        (e.g. a seed-only member, which is read-only).
        """
        target = (member or "").strip()
        if not target:
            return False
        with self.conn:
            row = self.conn.execute(
                "SELECT class_id FROM artist_aliases WHERE member = ?", (target,)
            ).fetchone()
            if row is None:
                return False
            class_id = row[0]
            self.conn.execute(
                "DELETE FROM artist_aliases WHERE class_id = ? AND member = ?",
                (class_id, target),
            )
            remaining = self.conn.execute(
                "SELECT COUNT(DISTINCT member) FROM artist_aliases WHERE class_id = ?",
                (class_id,),
            ).fetchone()[0]
            if remaining < 2:
                self.conn.execute(
                    "DELETE FROM artist_aliases WHERE class_id = ?", (class_id,)
                )
        return True

    def set_playlist_type(self, playlist_id: int, playlist_type: str | None) -> None:
        """Set the playlist type."""
        self.conn.execute(
            "UPDATE playlists SET playlist_type = ? WHERE id = ?",
            (playlist_type, playlist_id),
        )
        self.conn.commit()

    def get_playlist_type(self, playlist_id: int) -> str | None:
        """Get the playlist type."""
        row = self.conn.execute(
            "SELECT playlist_type FROM playlists WHERE id = ?", (playlist_id,)
        ).fetchone()
        return row[0] if row else None

    def set_mood_profile(self, playlist_id: int, mood_profile: dict | None) -> None:
        """Set the mood profile for a playlist."""
        val = json.dumps(mood_profile) if mood_profile else None
        self.conn.execute(
            "UPDATE playlists SET mood_profile = ? WHERE id = ?", (val, playlist_id)
        )
        self.conn.commit()

    def get_mood_profile(self, playlist_id: int) -> dict | None:
        """Get the mood profile for a playlist."""
        row = self.conn.execute(
            "SELECT mood_profile FROM playlists WHERE id = ?", (playlist_id,)
        ).fetchone()
        return json.loads(row[0]) if row and row[0] else None

    # ---- Artist methods ----

    def get_artist(self, artist_id: int) -> Artist | None:
        """Get an artist by ID."""
        row = self.conn.execute(
            "SELECT * FROM artists WHERE id = ?", (artist_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_artist(row)

    def get_artist_by_name(self, name: str) -> Artist | None:
        """Get an artist by name (normalized lookup)."""
        norm = normalize_artist(name)
        row = self.conn.execute(
            "SELECT * FROM artists WHERE norm_name = ?", (norm,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_artist(row)

    def get_artists_for_playlist(self, playlist_id: int) -> list[Artist]:
        """Get all unique artists in a playlist."""
        rows = self.conn.execute(
            """
            SELECT DISTINCT a.* FROM artists a
            JOIN tracks t ON t.artist_id = a.id
            JOIN playlist_tracks pt ON pt.track_id = t.id
            WHERE pt.playlist_id = ?
            ORDER BY a.name
        """,
            (playlist_id,),
        ).fetchall()
        return [self._row_to_artist(row) for row in rows]

    _UPDATABLE_ARTIST_COLUMNS = frozenset(
        {
            "name",
            "norm_name",
            "sort_name",
            "bio",
            "identity",
            "tags",
            "identity_confidence",
            "genres",
            "origin",
            "active_start",
            "active_end",
            "mb_artist_id",
            "tidal_artist_id",
            "spotify_artist_uri",
            "lastfm_url",
            "wikipedia_url",
            "enrichment_sources",
            "verified",
            "enriched_at",
            "verified_at",
        }
    )

    def update_artist(self, artist_id: int, **fields: Any) -> None:
        """Update artist fields by keyword arguments.

        Field names are validated against an allowlist of updatable columns
        before being interpolated as SQL identifiers, preventing SQL-identifier
        injection via caller-supplied keys.
        """
        json_fields = {"identity", "tags", "genres", "enrichment_sources"}
        sets: list[str] = []
        values: list[Any] = []
        for key, value in fields.items():
            if key not in self._UPDATABLE_ARTIST_COLUMNS:
                raise ValueError(f"Not an updatable artist column: {key!r}")
            sets.append(f"{key} = ?")
            if key in json_fields and not isinstance(value, str):
                values.append(json.dumps(value))
            else:
                values.append(value)
        if not sets:
            return
        sets.append("updated_at = datetime('now')")
        values.append(artist_id)
        self.conn.execute(f"UPDATE artists SET {', '.join(sets)} WHERE id = ?", values)  # noqa: S608 - columns from code-controlled allowlist; values parameterized
        self.conn.commit()

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

    # ---- Album methods ----

    def get_album(self, album_id: int) -> Album | None:
        """Get an album by ID."""
        row = self.conn.execute(
            "SELECT * FROM albums WHERE id = ?", (album_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_album(row)

    def get_albums_by_artist(self, artist_id: int) -> list[Album]:
        """Get all albums by an artist."""
        rows = self.conn.execute(
            "SELECT * FROM albums WHERE artist_id = ? ORDER BY release_date",
            (artist_id,),
        ).fetchall()
        return [self._row_to_album(row) for row in rows]

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

    # ---- Banned Artist methods ----

    def ban_artist(self, name: str, reason: str | None = None) -> int:
        """Add an artist to the global ban list. Returns the ban ID."""
        norm = normalize_ban_name(name)
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO banned_artists (name, norm_name, reason) VALUES (?, ?, ?)",
            (name, norm, reason),
        )
        self.conn.commit()
        return cursor.lastrowid or 0

    def unban_artist(self, name: str) -> bool:
        """Remove an artist from the ban list. Returns True if removed."""
        norm = normalize_ban_name(name)
        cursor = self.conn.execute(
            "DELETE FROM banned_artists WHERE norm_name = ?", (norm,)
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def get_banned_artists(self) -> list[tuple[str, str | None]]:
        """Get all banned artists as (name, reason) tuples."""
        rows = self.conn.execute(
            "SELECT name, reason FROM banned_artists ORDER BY name"
        ).fetchall()
        return [(row[0], row[1]) for row in rows]

    def is_artist_banned(self, name: str) -> bool:
        """Check if an artist name (or segment) is on the ban list."""
        norm = normalize_ban_name(name)
        row = self.conn.execute(
            "SELECT 1 FROM banned_artists WHERE norm_name = ?", (norm,)
        ).fetchone()
        return row is not None

    # ---- Batch History methods ----

    def record_batch(self, playlist_id: int, plan_json: str) -> int:
        """Record an applied batch plan in history. Returns the history ID."""
        cursor = self.conn.execute(
            "INSERT INTO batch_history (playlist_id, plan_json) VALUES (?, ?)",
            (playlist_id, plan_json),
        )
        self.conn.commit()
        return cursor.lastrowid or 0

    def get_batch_history(self, playlist_id: int) -> list[dict]:
        """Get batch history for a playlist."""
        rows = self.conn.execute(
            "SELECT id, plan_json, applied_at, reverted_at "
            "FROM batch_history WHERE playlist_id = ? ORDER BY applied_at DESC",
            (playlist_id,),
        ).fetchall()
        return [
            {"id": r[0], "plan_json": r[1], "applied_at": r[2], "reverted_at": r[3]}
            for r in rows
        ]

    def mark_batch_reverted(self, history_id: int) -> None:
        """Mark a batch history entry as reverted."""
        self.conn.execute(
            "UPDATE batch_history SET reverted_at = datetime('now') WHERE id = ?",
            (history_id,),
        )
        self.conn.commit()

    # ---- Collection methods ----

    def create_collection(self, name: str, description: str | None = None) -> int:
        """Create a collection. Returns the ID."""
        self.conn.execute(
            "INSERT OR IGNORE INTO collections (name, description) VALUES (?, ?)",
            (name, description),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id FROM collections WHERE name = ?", (name,)
        ).fetchone()
        return row[0]

    def delete_collection(self, name: str) -> bool:
        """Delete a collection and all its playlist associations."""
        row = self.conn.execute(
            "SELECT id FROM collections WHERE name = ?", (name,)
        ).fetchone()
        if not row:
            return False
        self.conn.execute(
            "DELETE FROM playlist_collections WHERE collection_id = ?", (row[0],)
        )
        self.conn.execute("DELETE FROM collections WHERE id = ?", (row[0],))
        self.conn.commit()
        return True

    def tag_playlist(self, playlist_id: int, collection_name: str) -> None:
        """Add a collection tag to a playlist."""
        col_id = self.create_collection(collection_name)
        self.conn.execute(
            "INSERT OR IGNORE INTO playlist_collections (playlist_id, collection_id) VALUES (?, ?)",
            (playlist_id, col_id),
        )
        self.conn.commit()

    def untag_playlist(self, playlist_id: int, collection_name: str) -> bool:
        """Remove a collection tag from a playlist."""
        row = self.conn.execute(
            "SELECT id FROM collections WHERE name = ?", (collection_name,)
        ).fetchone()
        if not row:
            return False
        cursor = self.conn.execute(
            "DELETE FROM playlist_collections WHERE playlist_id = ? AND collection_id = ?",
            (playlist_id, row[0]),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def get_playlist_collections(self, playlist_id: int) -> list[str]:
        """Get all collection names for a playlist."""
        rows = self.conn.execute(
            "SELECT c.name FROM collections c "
            "JOIN playlist_collections pc ON pc.collection_id = c.id "
            "WHERE pc.playlist_id = ? ORDER BY c.name",
            (playlist_id,),
        ).fetchall()
        return [r[0] for r in rows]

    def get_collection_playlists(self, collection_name: str) -> list:
        """Get all playlists in a collection."""
        rows = self.conn.execute(
            "SELECT p.* FROM playlists p "
            "JOIN playlist_collections pc ON pc.playlist_id = p.id "
            "JOIN collections c ON c.id = pc.collection_id "
            "WHERE c.name = ? ORDER BY p.name",
            (collection_name,),
        ).fetchall()
        return [self._row_to_playlist(r) for r in rows]

    def list_collections_with_counts(self) -> list[tuple[str, int]]:
        """List all collections with playlist counts."""
        rows = self.conn.execute(
            "SELECT c.name, COUNT(pc.playlist_id) FROM collections c "
            "LEFT JOIN playlist_collections pc ON pc.collection_id = c.id "
            "GROUP BY c.id ORDER BY c.name"
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    # ---- Tidal Folder cache methods ----

    def cache_tidal_folder(
        self, tidal_id: str, name: str, parent_tidal_id: str | None = None
    ) -> None:
        """Cache a Tidal folder's metadata."""
        self.conn.execute(
            "INSERT OR REPLACE INTO tidal_folders (tidal_id, name, parent_tidal_id, last_synced_at) "
            "VALUES (?, ?, ?, datetime('now'))",
            (tidal_id, name, parent_tidal_id),
        )
        self.conn.commit()

    def get_tidal_folder_by_name(self, name: str) -> dict | None:
        """Look up a cached Tidal folder by name."""
        row = self.conn.execute(
            "SELECT tidal_id, name, parent_tidal_id FROM tidal_folders WHERE name = ?",
            (name,),
        ).fetchone()
        if not row:
            return None
        return {"tidal_id": row[0], "name": row[1], "parent_tidal_id": row[2]}

    def get_cached_tidal_folders(self) -> list[dict]:
        """Get all cached Tidal folders."""
        rows = self.conn.execute(
            "SELECT tidal_id, name, parent_tidal_id FROM tidal_folders ORDER BY name"
        ).fetchall()
        return [{"tidal_id": r[0], "name": r[1], "parent_tidal_id": r[2]} for r in rows]

    def remove_tidal_folder_cache(self, tidal_id: str) -> None:
        """Remove a folder from the cache."""
        self.conn.execute("DELETE FROM tidal_folders WHERE tidal_id = ?", (tidal_id,))
        self.conn.commit()

    def set_playlist_tidal_folder(
        self, playlist_id: int, tidal_folder_id: str | None
    ) -> None:
        """Set or clear the Tidal folder assignment for a playlist."""
        self.conn.execute(
            "UPDATE playlists SET tidal_folder_id = ? WHERE id = ?",
            (tidal_folder_id, playlist_id),
        )
        self.conn.commit()

    def get_playlists_by_tidal_folder(self, tidal_folder_id: str) -> list:
        """Get all playlists assigned to a Tidal folder."""
        rows = self.conn.execute(
            "SELECT * FROM playlists WHERE tidal_folder_id = ? ORDER BY name",
            (tidal_folder_id,),
        ).fetchall()
        return [self._row_to_playlist(r) for r in rows]

    def clear_tidal_folder_assignments(self, tidal_folder_id: str) -> int:
        """Clear folder assignments for all playlists in a folder. Returns count."""
        cursor = self.conn.execute(
            "UPDATE playlists SET tidal_folder_id = NULL WHERE tidal_folder_id = ?",
            (tidal_folder_id,),
        )
        self.conn.commit()
        return cursor.rowcount

    # ---- Platform Metadata methods ----

    def upsert_track_platform_metadata(
        self, track_id: int, platform: str, platform_track_id: str, **fields
    ) -> None:
        """Insert or update platform metadata for a track."""
        import json as _json

        # Serialize JSON fields
        for key in ("genres", "audio_qualities", "raw_metadata"):
            if key in fields and not isinstance(fields[key], str):
                fields[key] = _json.dumps(fields[key])

        existing = self.conn.execute(
            "SELECT id FROM track_platform_metadata WHERE track_id = ? AND platform = ?",
            (track_id, platform),
        ).fetchone()

        if existing:
            sets = ", ".join(f"{k} = ?" for k in fields)
            vals = [*fields.values(), track_id, platform]
            self.conn.execute(
                f"UPDATE track_platform_metadata SET {sets}, fetched_at = datetime('now') "  # noqa: S608 - columns from code-controlled allowlist; values parameterized
                f"WHERE track_id = ? AND platform = ?",
                vals,
            )
        else:
            cols = ["track_id", "platform", "platform_track_id", *fields.keys()]
            placeholders = ", ".join("?" * len(cols))
            vals = [track_id, platform, platform_track_id, *fields.values()]
            self.conn.execute(
                f"INSERT INTO track_platform_metadata ({', '.join(cols)}) VALUES ({placeholders})",  # noqa: S608 - columns from code-controlled allowlist; values parameterized
                vals,
            )
        self.conn.commit()

    def get_track_platform_metadata(self, track_id: int, platform: str) -> dict | None:
        """Get platform metadata for a track."""
        import json as _json

        row = self.conn.execute(
            "SELECT * FROM track_platform_metadata WHERE track_id = ? AND platform = ?",
            (track_id, platform),
        ).fetchone()
        if not row:
            return None
        cols = [
            d[1]
            for d in self.conn.execute(
                "PRAGMA table_info(track_platform_metadata)"
            ).fetchall()
        ]
        result = dict(zip(cols, row, strict=True))
        for key in ("genres", "audio_qualities", "raw_metadata"):
            if result.get(key) and isinstance(result[key], str):
                try:
                    result[key] = _json.loads(result[key])
                except _json.JSONDecodeError:
                    pass
        return result

    # ---- Track Tag methods ----

    def add_track_tag(self, track_id: int, tag: str, source: str = "manual") -> None:
        """Add a tag to a track."""
        self.conn.execute(
            "INSERT OR IGNORE INTO track_tags (track_id, tag, source) VALUES (?, ?, ?)",
            (track_id, tag, source),
        )
        self.conn.commit()

    def remove_track_tag(self, track_id: int, tag: str) -> bool:
        """Remove a tag from a track."""
        cursor = self.conn.execute(
            "DELETE FROM track_tags WHERE track_id = ? AND tag = ?",
            (track_id, tag),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def get_track_tags(self, track_id: int) -> list[str]:
        """Get all tags for a track."""
        rows = self.conn.execute(
            "SELECT tag FROM track_tags WHERE track_id = ? ORDER BY tag",
            (track_id,),
        ).fetchall()
        return [r[0] for r in rows]

    def find_tracks_by_tag(self, *tags: str) -> list:
        """Find tracks matching ALL given tags."""
        if not tags:
            return []
        placeholders = ", ".join("?" * len(tags))
        rows = self.conn.execute(
            f"SELECT t.* FROM tracks t "  # noqa: S608 - placeholders are bound '?' params; values parameterized
            f"WHERE (SELECT COUNT(*) FROM track_tags tt WHERE tt.track_id = t.id AND tt.tag IN ({placeholders})) = ?",
            [*tags, len(tags)],
        ).fetchall()
        return [self._row_to_track(r) for r in rows]

    def list_all_track_tags(self) -> list[tuple[str, int]]:
        """List all tags with usage counts."""
        rows = self.conn.execute(
            "SELECT tag, COUNT(*) FROM track_tags GROUP BY tag ORDER BY COUNT(*) DESC"
        ).fetchall()
        return [(r[0], r[1]) for r in rows]
