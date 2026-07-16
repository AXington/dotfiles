"""Restore a playlist from an exported JSON snapshot (BUG-1 backup/restore).

Reconstructs canonical playlist membership from the JSON that
`export --format json` emits. Restore is DB-only and canonical-first: platform
mappings are NOT restored (they re-resolve); a later `sync` redistributes.
Idempotent: tracks already present are skipped. New tracks enqueue for
resolution via the library-first add path (mirrors ``commands/add_cmd``).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from tuneshift.db import Database
from tuneshift.models import Track


def handle_import_json(args, db: Database) -> int:
    path = Path(args.file)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        return 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"Invalid JSON: {exc}", file=sys.stderr)
        return 1

    name = getattr(args, "into", None) or data.get("name")
    if not name:
        print("JSON has no playlist name and no --into given", file=sys.stderr)
        return 1

    playlist = db.find_playlist_by_name(name)
    if playlist is None:
        playlist_id = db.create_playlist(name)  # returns int id
    else:
        playlist_id = playlist.id

    existing = {
        (t.title.casefold(), t.artist.casefold())
        for t in db.get_playlist_tracks(playlist_id)
    }
    added = 0
    already = 0
    for entry in data.get("tracks", []):
        title = (entry.get("title") or "").strip()
        artist = (entry.get("artist") or "").strip()
        if not title or not artist:
            continue
        if (title.casefold(), artist.casefold()) in existing:
            already += 1
            continue

        # Library-first add path: find-or-create the canonical track, append it,
        # then enqueue resolution (identity/platform mapping re-resolves).
        album = entry.get("album")
        found = db.find_track(title, artist, album)
        track_id = (
            found.id
            if found is not None
            else db.add_track(Track(title=title, artist=artist, album=album))
        )
        position = len(db.get_playlist_tracks(playlist_id)) + 1
        db.add_track_to_playlist(playlist_id, track_id, position)
        db.enqueue_resolution(track_id)

        existing.add((title.casefold(), artist.casefold()))
        added += 1

    print(f'Restored "{name}": {added} track(s) added, {already} already present.')
    return 0
