"""Edit command: correct track metadata without touching the database directly.

Supports direct field edits (``--title``/``--artist``/``--album``) on a single
track and a ``--strip-album-from-title`` cleanup that removes a trailing
parenthetical when it merely repeats the track's album name. ``--dry-run``
previews every change without writing.
"""

import re
import sys

from tuneshift.db import Database, normalize_title


def handle_edit(args, db: Database) -> int:
    """Dispatch to the strip cleanup or a direct field edit."""
    if getattr(args, "strip_album_from_title", False):
        return _handle_strip(args, db)
    return _handle_field_edit(args, db)


def _handle_field_edit(args, db: Database) -> int:
    track_id = getattr(args, "track_id", None)
    if track_id is None:
        print("Specify a track id to edit", file=sys.stderr)
        return 1

    track = db.get_track(track_id)
    if track is None:
        print(f"Track id not found: {track_id}", file=sys.stderr)
        return 1

    fields: dict[str, str | None] = {}
    for name in ("title", "artist", "album"):
        value = getattr(args, name, None)
        if value is not None:
            fields[name] = value

    # Energy/valence are numeric audio features (AC8 manual override), written
    # with explicit "manual" provenance so they outrank estimated values and are
    # never silently overwritten by re-enrichment.
    audio_fields: dict[str, float] = {}
    for name in ("energy", "valence"):
        value = getattr(args, name, None)
        if value is not None:
            if not 0.0 <= value <= 1.0:
                print(f"{name} must be between 0.0 and 1.0", file=sys.stderr)
                return 1
            audio_fields[name] = float(value)

    if not fields and not audio_fields:
        print(
            "Nothing to edit. Provide --title/--artist/--album, "
            "--energy/--valence, or --strip-album-from-title",
            file=sys.stderr,
        )
        return 1

    for required in ("title", "artist"):
        if required in fields and not (fields[required] or "").strip():
            print(f"{required.capitalize()} cannot be empty", file=sys.stderr)
            return 1

    if getattr(args, "dry_run", False):
        for name, value in fields.items():
            print(f'[dry-run] track {track_id}: {name} -> "{value}"')
        for name, number in audio_fields.items():
            print(f"[dry-run] track {track_id}: {name} -> {number}")
        return 0

    changed = db.update_track(track_id, **fields) if fields else 0
    if audio_fields:
        db.set_track_fields(track_id, audio_fields, source="manual")
        changed += len(audio_fields)
    if changed == 0:
        print(f"Track {track_id} already has those values; nothing changed.")
    else:
        print(f"Updated track {track_id} ({changed} field(s) changed).")
    return 0


def _handle_strip(args, db: Database) -> int:
    dry_run = getattr(args, "dry_run", False)

    tracks = _strip_targets(args, db)
    if tracks is None:
        return 1

    changed = 0
    for track in tracks:
        new_title = _strip_album_suffix(track.title, track.album)
        if not new_title or new_title == track.title:
            continue
        changed += 1
        if dry_run:
            print(f'[dry-run] "{track.title}" -> "{new_title}"')
        else:
            db.update_track(track.id, title=new_title)
            print(f'"{track.title}" -> "{new_title}"')

    if changed == 0:
        print("No titles needed stripping.")
    return 0


def _strip_targets(args, db: Database):
    """Return the list of tracks to consider, or None on an input error."""
    track_id = getattr(args, "track_id", None)
    if track_id is not None:
        track = db.get_track(track_id)
        if track is None:
            print(f"Track id not found: {track_id}", file=sys.stderr)
            return None
        return [track]

    playlist_name = getattr(args, "playlist", None)
    if not playlist_name:
        print(
            "Specify a track id or --playlist for --strip-album-from-title",
            file=sys.stderr,
        )
        return None

    playlist = db.find_playlist_by_name(playlist_name)
    if not playlist:
        print(f"Playlist not found: {playlist_name}", file=sys.stderr)
        return None
    return db.get_playlist_tracks(playlist.id)


_TRAILING_PAREN = re.compile(r"\s*\(([^()]*)\)\s*$")


def _strip_album_suffix(title: str, album: str | None) -> str | None:
    """Return the title with a trailing "(album)" removed, or None if no match.

    The parenthetical is only removed when its contents normalize to the same
    value as the album, so genuine qualifiers like "(feat. ...)" or "(Live)"
    are preserved.
    """
    if not album:
        return None
    match = _TRAILING_PAREN.search(title)
    if not match:
        return None
    if normalize_title(match.group(1)) != normalize_title(album):
        return None
    stripped = title[: match.start()].strip()
    return stripped or None
