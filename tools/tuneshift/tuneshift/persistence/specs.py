"""Allowlist specs describing which tables the plan/apply engine may mutate.

Lives in ``persistence`` (not ``planapply``) so both the persistence layer
(``db.Database`` spec-driven CRUD methods) and the plan/apply engine can share
one source of truth without ``db.py`` importing ``planapply`` (which would
recreate the dependency cycle broken in ARCH-M2). Only identifiers drawn from a
``TableSpec`` are ever interpolated into SQL; every value is parameterized.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TableSpec:
    """Allowlist describing a table the apply engine may write."""

    name: str
    pk: tuple[str, ...]
    columns: tuple[str, ...]

    @property
    def all_columns(self) -> tuple[str, ...]:
        return (*self.pk, *self.columns)


# Tables the plan/apply engine is permitted to mutate. Extend deliberately as
# new routed mutations are added.
TABLE_SPECS: dict[str, TableSpec] = {
    "playlist_track_mappings": TableSpec(
        name="playlist_track_mappings",
        pk=("playlist_id", "track_id", "platform"),
        columns=("platform_track_id", "source", "user_approved"),
    ),
    # NOTE: playlist_track_prefs is intentionally NOT routed through plan/apply.
    # Preferences are configuration set directly via the `prefs` CLI, not a
    # playlist mutation. Its storage now uses a surrogate id PK with a nullable
    # (playlist_id, target) logical key enforced by a COALESCE unique index -
    # shapes the generic engine's NULL-unsafe `col = ?` WHERE and
    # `ON CONFLICT(raw-columns)` arbiter cannot address. A future task that wants
    # planned pref changes must add NULL-safe key handling before re-listing it.
    # Global default lock lives on platform_tracks (spec section 8, AC-L1). A routed
    # self-heal (planapply/heal.py, AC-L3) re-binds the locked id and refreshes
    # the same-recording fingerprint, so both are writable through plan/apply.
    "platform_tracks": TableSpec(
        name="platform_tracks",
        pk=("track_id", "platform"),
        columns=("platform_track_id", "status", "user_approved", "fingerprint"),
    ),
    # A sync push may find-or-create + link the remote playlist at apply time.
    # That link is a LOCAL write, so it is journaled (see ``_apply_remote``) and
    # therefore must be a known table so rollback can reverse it.
    "platform_playlists": TableSpec(
        name="platform_playlists",
        pk=("playlist_id", "platform"),
        columns=("platform_playlist_id",),
    ),
    # Enrichment overwrites of matcher-read fields are routed + journaled
    # (routing table row "Enrichment metadata overwrite"). Only the fields the
    # matcher actually reads are writable through plan/apply.
    "tracks": TableSpec(
        name="tracks",
        pk=("id",),
        columns=(
            "isrc",
            "duration_seconds",
            "album_artist",
            "album_type",
            "label",
            "release_date",
            "audio_modes",
        ),
    ),
}
