"""Pin command: manage track pins for sequencer constraints."""

import sys

from tuneshift.db import Database


def handle_pin(args, db: Database) -> int:
    """Manage track pins (opener, closer, position, adjacency groups)."""
    playlist = db.find_playlist_by_name(args.playlist)
    if not playlist:
        print(f"Playlist not found: {args.playlist}", file=sys.stderr)
        return 1

    if getattr(args, "list_pins", False):
        return _list_pins(db, playlist)

    if getattr(args, "remove", None):
        return _remove_pin(db, playlist, args.remove)

    if getattr(args, "opener", None):
        return _set_position_pin(db, playlist, args.opener, "opener")

    if getattr(args, "closer", None):
        return _set_position_pin(db, playlist, args.closer, "closer")

    if getattr(args, "position", None):
        idx, title = args.position[0], args.position[1]
        return _set_index_pin(db, playlist, title, int(idx))

    if getattr(args, "adjacent", None):
        return _set_adjacency(db, playlist, args.adjacent, getattr(args, "group", None))

    if getattr(args, "moment", None):
        return _set_moment_pin(db, playlist, args.moment)

    print(
        "Specify --opener, --closer, --position, --adjacent, --moment, --remove, or --list.",  # noqa: E501
        file=sys.stderr,
    )
    return 1


def _find_track_in_playlist(db: Database, playlist, title: str):
    """Find a track in a playlist by partial title match."""
    tracks = db.get_playlist_tracks(playlist.id)
    title_lower = title.lower()
    matches = [t for t in tracks if title_lower in t.title.lower()]
    if not matches:
        print(f'  Track not found: "{title}"', file=sys.stderr)
        return None
    if len(matches) > 1:
        exact = [t for t in matches if t.title.lower() == title_lower]
        if len(exact) == 1:
            return exact[0]
        print(f'  Ambiguous match for "{title}":', file=sys.stderr)
        for t in matches[:5]:
            print(f"    - {t.title} - {t.artist}", file=sys.stderr)
        return None
    return matches[0]


def _set_position_pin(db: Database, playlist, title: str, pin_type: str) -> int:
    """Pin a track as opener or closer."""
    track = _find_track_in_playlist(db, playlist, title)
    if not track:
        return 1

    # Remove any existing pin of this type for the playlist
    existing_pins = db.get_pins(playlist.id)
    for pin in existing_pins:
        if pin.pin_type == pin_type:
            db.remove_pin(playlist.id, pin.track_id)

    db.set_pin(playlist.id, track.id, pin_type)
    print(f'Pinned "{track.title}" as {pin_type} in "{playlist.name}"')
    return 0


def _set_index_pin(db: Database, playlist, title: str, position: int) -> int:
    """Pin a track to a specific position index.

    If the track is in an adjacency group, promotes the entire group to
    positional pins starting at the given position.
    """
    track = _find_track_in_playlist(db, playlist, title)
    if not track:
        return 1

    # Check if this track is in an adjacency group
    existing_pins = db.get_pins(playlist.id)
    track_pin = next((p for p in existing_pins if p.track_id == track.id), None)

    if track_pin and track_pin.pin_type == "anchor" and track_pin.group_id:
        # Promote the whole group to positional pins
        group_members = sorted(
            [p for p in existing_pins if p.group_id == track_pin.group_id],
            key=lambda p: p.group_order or 0,
        )
        # Find this track's order within the group
        track_order = next(
            (i for i, p in enumerate(group_members) if p.track_id == track.id), 0
        )
        # Assign positions: this track at `position`, others relative
        base_position = position - track_order
        for i, member in enumerate(group_members):
            target_pos = base_position + i
            db.remove_pin(playlist.id, member.track_id)
            # Clear any existing pin at this position
            for pin in existing_pins:
                if (
                    pin.pin_type == "position"
                    and pin.group_order == target_pos
                    and pin.track_id != member.track_id
                ):
                    db.remove_pin(playlist.id, pin.track_id)
            db.set_pin(playlist.id, member.track_id, "position", group_order=target_pos)

        group_desc = ", ".join(
            f'"{db.conn.execute("SELECT title FROM tracks WHERE id = ?", (m.track_id,)).fetchone()[0]}" at {base_position + i}'  # noqa: E501, S608 - builds display text, not SQL; inner query is parameterized
            for i, m in enumerate(group_members)
        )
        print(f'Promoted group "{track_pin.group_id}" to positions: {group_desc}')
        return 0

    # Normal case: single track position pin
    db.remove_pin(playlist.id, track.id)

    # Remove any existing position pin at this index
    for pin in existing_pins:
        if pin.pin_type == "position" and pin.group_order == position:
            db.remove_pin(playlist.id, pin.track_id)

    db.set_pin(playlist.id, track.id, "position", group_order=position)
    print(f'Pinned "{track.title}" at position {position} in "{playlist.name}"')
    return 0


def _set_adjacency(
    db: Database, playlist, titles: list[str], group_name: str | None
) -> int:
    """Pin tracks as an adjacency group."""
    tracks = []
    for title in titles:
        track = _find_track_in_playlist(db, playlist, title)
        if not track:
            return 1
        tracks.append(track)

    group_id = group_name or f"adj-{tracks[0].id}"

    for order, track in enumerate(tracks):
        # Remove any existing pin for this track first
        db.remove_pin(playlist.id, track.id)
        db.set_pin(
            playlist.id, track.id, "anchor", group_id=group_id, group_order=order
        )

    track_names = " -> ".join(f'"{t.title}"' for t in tracks)
    print(f'Pinned adjacency group "{group_id}": {track_names}')
    return 0


def _set_moment_pin(db: Database, playlist, title: str) -> int:
    """Pin a track as a narrative moment (placed at climax)."""
    track = _find_track_in_playlist(db, playlist, title)
    if not track:
        return 1

    # Remove any existing pin for this track first
    db.remove_pin(playlist.id, track.id)

    db.set_pin(playlist.id, track.id, pin_type="moment")
    print(f'Pinned "{track.title}" as moment (will be placed at climax)')
    return 0


def _remove_pin(db: Database, playlist, title: str) -> int:
    """Remove a pin from a track."""
    track = _find_track_in_playlist(db, playlist, title)
    if not track:
        return 1
    db.remove_pin(playlist.id, track.id)
    print(f'Removed pin from "{track.title}" in "{playlist.name}"')
    return 0


def _list_pins(db: Database, playlist) -> int:
    """List all pins for a playlist."""
    pins = db.get_pins(playlist.id)
    if not pins:
        print(f'No pins set for "{playlist.name}"')
        return 0

    print(f'Pins for "{playlist.name}":')
    tracks_map = {t.id: t for t in db.get_playlist_tracks(playlist.id)}

    for pin in pins:
        track = tracks_map.get(pin.track_id)
        name = f"{track.title} - {track.artist}" if track else f"(track {pin.track_id})"
        if pin.pin_type in ("opener", "closer"):
            print(f"  {pin.pin_type}: {name}")
        elif pin.pin_type == "position":
            print(f"  position {pin.group_order}: {name}")
        elif pin.pin_type == "anchor":
            print(f"  anchor [{pin.group_id}#{pin.group_order}]: {name}")
        elif pin.pin_type == "moment":
            print(f"  moment (climax): {name}")

    return 0
