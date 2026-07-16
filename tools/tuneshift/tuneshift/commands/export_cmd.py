"""Export command: export playlists in multiple formats."""

import csv
import io
import json
import sys
from pathlib import Path

from tuneshift.db import Database


def handle_export(args, db: Database) -> int:
    """Export a playlist to a file in the specified format."""
    playlist = db.find_playlist_by_name(args.playlist)
    if not playlist:
        print(f"Playlist not found: {args.playlist}", file=sys.stderr)
        return 1

    tracks = db.get_playlist_tracks(playlist.id)
    if not tracks:
        print(f'Playlist "{playlist.name}" is empty.', file=sys.stderr)
        return 1

    fmt = args.format
    output_path = args.output

    content = _render(playlist, tracks, fmt, db)

    if output_path == "-":
        sys.stdout.write(content)
    else:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f'Exported "{playlist.name}" ({len(tracks)} tracks) to {path}')

    return 0


def _render(playlist, tracks, fmt: str, db: Database) -> str:
    """Render playlist in the given format."""
    if fmt == "text":
        return _render_text(playlist, tracks, db)
    elif fmt == "csv":
        return _render_csv(tracks, db)
    elif fmt == "json":
        return _render_json(playlist, tracks, db)
    elif fmt == "soundiiz":
        return _render_soundiiz(tracks, db)
    elif fmt == "tunemymusic":
        return _render_tunemymusic(tracks, db)
    else:
        return _render_text(playlist, tracks, db)


def _render_text(playlist, tracks, db: Database) -> str:
    """Human-readable text format (same as playlists/*.txt)."""
    tidal_id = db.get_platform_playlist_id(playlist.id, "tidal")
    ytm_id = db.get_platform_playlist_id(playlist.id, "ytmusic")

    lines = [f"# {playlist.name}"]
    lines.append(f"# {len(tracks)} tracks")
    if tidal_id:
        lines.append(f"# Tidal playlist ID: {tidal_id}")
    if ytm_id:
        lines.append(f"# YouTube Music playlist ID: {ytm_id}")
    lines.append("")

    for i, track in enumerate(tracks, 1):
        album_part = f" [{track.album}]" if track.album else ""
        lines.append(f"    {i}. {track.artist} - {track.title}{album_part}")

    lines.append("")
    return "\n".join(lines)


def _render_csv(tracks, db: Database) -> str:
    """Standard CSV with all metadata."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["Position", "Title", "Artist", "Album", "ISRC", "Duration_s", "BPM", "Key"]
    )

    for i, track in enumerate(tracks, 1):
        writer.writerow(
            [
                i,
                track.title,
                track.artist,
                track.album or "",
                track.isrc or "",
                track.duration_seconds or "",
                track.tempo or "",
                track.key or "",
            ]
        )

    return output.getvalue()


def _render_json(playlist, tracks, db: Database) -> str:
    """JSON export with full metadata."""
    tidal_id = db.get_platform_playlist_id(playlist.id, "tidal")
    ytm_id = db.get_platform_playlist_id(playlist.id, "ytmusic")

    data = {
        "name": playlist.name,
        "track_count": len(tracks),
        "platforms": {},
        "tracks": [],
    }
    if tidal_id:
        data["platforms"]["tidal"] = tidal_id
    if ytm_id:
        data["platforms"]["ytmusic"] = ytm_id

    for i, track in enumerate(tracks, 1):
        data["tracks"].append(
            {
                "position": i,
                "title": track.title,
                "artist": track.artist,
                "album": track.album,
                "isrc": track.isrc,
                "duration_seconds": track.duration_seconds,
                "bpm": track.tempo,
                "key": track.key,
            }
        )

    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _render_soundiiz(tracks, db: Database) -> str:
    """Soundiiz-compatible CSV (Title, Artist, Album, ISRC)."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Title", "Artist", "Album", "ISRC"])

    for track in tracks:
        writer.writerow(
            [
                track.title,
                track.artist,
                track.album or "",
                track.isrc or "",
            ]
        )

    return output.getvalue()


def _render_tunemymusic(tracks, db: Database) -> str:
    """TuneMyMusic-compatible CSV (Track Name, Artist Name, Album Name)."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Track Name", "Artist Name", "Album Name"])

    for track in tracks:
        writer.writerow(
            [
                track.title,
                track.artist,
                track.album or "",
            ]
        )

    return output.getvalue()
